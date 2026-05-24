# MeshPanel 单二进制重构计划

> 目标:用户从 GitHub Release 下载**一个文件** `mesh-panel`,`chmod +x && ./mesh-panel` 直接跑,
> 自动生成配置、数据库、管理员密码,不接触源码。

## 当前结构

```
/root/mesh-panel/
├── backend/                    Python FastAPI
│   ├── main.py                 入口
│   ├── config.py               BASE_DIR / DATA_DIR / DB_PATH(硬编码相对 __file__)
│   ├── agent_dist/             16M,agent 二进制 amd64/arm64/armv7
│   ├── api/ ws/ models/ ...
│   └── data/                   ← 运行时 sqlite/certs/secret.key 都在这
├── frontend/dist/              2M,build 产物 (Vue + assets)
└── .git/                       干净,base commit = 4354cca (v1.1.12)
```

## 备份

- 文件:`/root/backups/mesh-panel_before_pyinstaller_20260524_182939.tar.gz` (13M)
- Git 回滚:`git -C /root/mesh-panel reset --hard 4354cca`

## 目标产物

- Release 资产:`mesh-panel-linux-amd64`(≈ 80MB 单文件)
- 数据目录:**`/opt/mesh-panel/`**(可被 `$MESH_PANEL_HOME` 覆盖)
- 首跑产物:`/opt/mesh-panel/data/app.db` + `/opt/mesh-panel/data/secret.key` + `/opt/mesh-panel/data/certs/`
- 首跑账号:**admin / admin123456**(直接写 settings 表,不走 setup 流程,不打印,不写文件)
- 旧数据迁移:如果 `/opt/mesh-panel/data/` 不存在但 `/root/mesh-panel/data/` 存在 → 自动 `cp -a`
- agent 二进制:**嵌入主二进制**,运行时从 `sys._MEIPASS/agent_dist/` 读
- 前端 dist:**嵌入主二进制**,运行时从 `sys._MEIPASS/frontend/dist/` 读

---

## 阶段 1:路径/首跑机制(纯 Python,不涉及打包)

### 改动文件

- `backend/config.py`:核心改造
- `backend/security/crypto.py`:`_DATA_DIR` 改读 config
- `backend/api/settings.py`:`CERT_DIR` 已经从 config 拿,自动跟着走
- `backend/deploy/installer.py`:`AGENT_DIST_DIR` 改为可被 PyInstaller 临时路径覆盖

### config.py 新逻辑

```python
import os, sys
from pathlib import Path

def _resource_root() -> Path:
    """只读资源(前端 dist、agent 二进制)。PyInstaller 模式 = _MEIPASS,源码模式 = 项目根。"""
    if getattr(sys, "frozen", False):
        return Path(sys._MEIPASS)  # /tmp/_MEIxxx
    return Path(__file__).resolve().parent.parent  # /root/mesh-panel

def _user_data_root() -> Path:
    """可写数据(sqlite、certs、密钥、config.yaml)。优先 $MESH_PANEL_HOME,否则 ~/.mesh-panel。"""
    env = os.environ.get("MESH_PANEL_HOME")
    if env:
        return Path(env).expanduser()
    return Path.home() / ".mesh-panel"

RESOURCE_ROOT = _resource_root()
BASE_DIR = RESOURCE_ROOT                              # 兼容老引用(只读)
USER_HOME = _user_data_root()
DATA_DIR = USER_HOME / "data"
CONFIG_PATH = USER_HOME / "config.yaml"

DATA_DIR.mkdir(parents=True, exist_ok=True)

DB_PATH = DATA_DIR / "app.db"
DATABASE_URL = f"sqlite:///{DB_PATH}"

# 前端 / agent 资源(只读)
FRONTEND_DIST = RESOURCE_ROOT / "frontend" / "dist"
AGENT_DIST_DIR = RESOURCE_ROOT / "backend" / "agent_dist"  # 源码模式
if getattr(sys, "frozen", False):
    AGENT_DIST_DIR = RESOURCE_ROOT / "agent_dist"          # 打包模式(spec 里 add-data 时这样放)

SSH_CONNECT_TIMEOUT = 8
SSH_EXEC_TIMEOUT = 5
```

### 首跑生成 config.yaml + 管理员密码

新文件:`backend/firstrun.py`,在 `lifespan` 进入时调用一次。

伪逻辑:
1. 如果 `CONFIG_PATH` 不存在 → 生成随机 `jwt_secret` / 管理员 `admin_password` / 写 yaml
2. 启动时把 yaml 里的值喂给 settings 表(只在首跑时,不覆盖已有)
3. **stdout 打印一次性提示**(管理员密码、面板地址),退出前刷新

### 改动点

- `backend/main.py`:`FRONTEND_DIST` 改从 `config` import
- `backend/deploy/installer.py`:`AGENT_DIST_DIR` 改从 `config` import
- `backend/security/crypto.py`:`_DATA_DIR = DATA_DIR`(从 config import)
- `backend/config.py`:整体重写
- `backend/main.py`:`lifespan` 里调 firstrun
- `backend/firstrun.py`:新建

### 验证

```bash
rm -rf /tmp/test-home
MESH_PANEL_HOME=/tmp/test-home python backend/main.py
# 期望:打印密码 + 起在 :8000,/tmp/test-home/ 下生成 config.yaml + data/
```

**完成标志**:无 `data/` 目录的全新环境能跑起来。

---

## 阶段 2:确认前端打包路径在源码模式 OK

阶段 1 已经处理掉 `FRONTEND_DIST` 的路径分支。这阶段只是回归测试:

```bash
cd /root/mesh-panel
python backend/main.py
curl -s http://127.0.0.1:8000/ | head -5      # 应有 <html>
curl -sI http://127.0.0.1:8000/assets/index-*.js | head -1  # 200
```

**完成标志**:现有面板访问正常。

---

## 阶段 3:验证 agent_dist 在源码模式仍可读

```bash
python -c "from backend.config import AGENT_DIST_DIR; import os; print(os.listdir(AGENT_DIST_DIR))"
# 应输出 ['mesh-agent-amd64', 'mesh-agent-arm64', 'mesh-agent-armv7']
```

再在前端"安装 agent"按钮跑一次,看 deploy 流程没坏。

**完成标志**:节点上 agent 部署成功。

---

## 阶段 4:`pyinstaller.spec` + 第一次打包

### 装 PyInstaller

```bash
cd /root/mesh-panel/backend
.venv/bin/pip install pyinstaller
```

### 写 `mesh-panel.spec`(项目根)

```python
# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_all

datas = [
    ('frontend/dist', 'frontend/dist'),
    ('backend/agent_dist', 'agent_dist'),
]

# SQLAlchemy / cryptography / passlib 这种动态导入大户
hidden = []
for pkg in ('sqlalchemy', 'sqlmodel', 'passlib', 'cryptography', 'bcrypt', 'jwt'):
    d, b, h = collect_all(pkg)
    datas += d
    hidden += h

a = Analysis(
    ['backend/main.py'],
    pathex=['backend'],
    datas=datas,
    hiddenimports=hidden + ['email.mime.multipart', 'email.mime.text'],
    excludes=['tkinter', 'matplotlib', 'numpy', 'pandas'],
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, a.binaries, a.datas,
    name='mesh-panel',
    console=True, onefile=True, strip=False, upx=False,
)
```

### 打包 + 测试

```bash
cd /root/mesh-panel
.venv/bin/pyinstaller mesh-panel.spec
ls -lh dist/mesh-panel    # 应 60-90MB

# 干净环境测试
rm -rf /tmp/fresh-home
mkdir /tmp/fresh-test && cp dist/mesh-panel /tmp/fresh-test/
cd /tmp/fresh-test
MESH_PANEL_HOME=/tmp/fresh-home ./mesh-panel
# 期望:打印密码 → 起 :8000 → 浏览器能看到登录页
```

### 可能踩的坑(已知)

1. **uvicorn auto-reload 在打包模式必须关**(spec 入口直接 `uvicorn.run(app, ...)`)
2. **SQLAlchemy dialect** 可能要 `--collect-submodules sqlalchemy.dialects.sqlite`
3. **passlib bcrypt** 需要 `--collect-all passlib`
4. **starlette/fastapi 模板** 通常没问题但要测一次 500 页

**完成标志**:`./mesh-panel` 在新 VPS 一行跑起来,登录 + 节点列表 + 部署 agent 全 OK。

---

## 阶段 5:GitHub Actions Release

`.github/workflows/release.yml`:

```yaml
on:
  push:
    tags: ['v*']
jobs:
  build:
    runs-on: ubuntu-22.04
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: {python-version: '3.11'}
      - uses: actions/setup-node@v4
        with: {node-version: '20'}
      - run: cd frontend && npm ci && npm run build
      - run: cd backend && pip install -r requirements.txt && pip install pyinstaller
      - run: pyinstaller mesh-panel.spec
      - uses: softprops/action-gh-release@v2
        with:
          files: dist/mesh-panel
          name: ${{ github.ref_name }}
          generate_release_notes: true
```

**完成标志**:`git tag v2.0.0 && git push --tags` 后 5-10 分钟 Release 自动出现 `mesh-panel`。

---

## 风险/回滚

- 任何阶段出问题:`git reset --hard 4354cca` 一键回到 v1.1.12
- tar 备份在 `/root/backups/` 可独立解压
- 现有运行的 uvicorn 进程 `proc_a1ee42498e2e` 改 `config.py` 时需要重启,会有 1-2 秒中断

## 当前停在哪

✅ 备份完成 / 计划已写 / **等你点头开干阶段 1**
