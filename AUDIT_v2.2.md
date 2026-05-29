# MeshPanel v2.2 发版前遗留问题审计报告

审计日期: 2026-05-29
审计范围: frontend / backend / agent / install.sh / mesh-panel.spec / release.yml / Makefile
共发现约 200 项遗留问题, 分 P0 / P1 / P2 三档。

目录:
- P0 阻塞发版必修 (28 条)
- P1 强烈建议修 (45 条)
- P2 轻微可延后 (~130 条简表)
- 修复路线图 + 决策点 + 验证清单

================================================================
# P0 阻塞发版必修 (28 条)
================================================================

## A. 安全 / 数据完整性 (9 条)

A1. install.sh:106-109 下载 panel 二进制无 sha256 校验 (CI 已生成 .sha256 但脚本没用)
    修复: 同步下 .sha256, sha256sum -c 通过才 mv

A2. install.sh:117-138 systemd unit 无 hardening, panel 默认 root 跑
    修复: 加 NoNewPrivileges / ProtectSystem=strict / ProtectHome / PrivateTmp / UMask=0077, 建 meshpanel 系统用户

A3. backend/api/auth.py:50-58 JWT secret 与业务数据同表, DB 文件权限可能 0644
    修复: DB 文件强制 chmod 0600 或 JWT secret 落独立 data/jwt.key (0600)

A4. backend/api/auth.py:82-88 JWT 30 天无 jti / 无撤销, 改密后旧 token 继续有效
    修复: 改密时旋转 secret 或维护 jti 黑名单

A5. backend/security/crypto.py:31-42 Fernet key 丢失时静默生成新 key, 所有旧密文全报废且无告警
    修复: DB 有 enc:v1: 但 key 缺失时拒绝启动, 提供 rotate-key CLI

A6. backend/deploy/installer.py:171 AGENT_TOKEN 用 shlex.quote 在命令行拼接, 进进程列表/失败日志/deploy_log 入库
    修复: token 走 stdin 喂入或 base64 编码远端解

A7. backend/deploy/scripts.py:178-182 非引号 heredoc <<EOF, 远端 shell 二次展开 token, 含 $ 反引号会注入
    修复: 改 <<'EOF' quoted heredoc, 或 printf 单独写每一行

A8. backend/deploy/installer.py:308-312 push_agent_env heredoc 拼 token, token 含 __EOF__ 或换行破坏边界
    修复: 走 SFTP 或已有 remote_write RPC

A9. backend/firstrun.py:15 默认密码 admin/admin123456 硬编码, 不强制首次登录改密
    修复: 首次启动随机密码并打印到 stderr, 或登录后强制改密

## B. 数据库 / 并发 / 死锁 (5 条)

B1. backend/database.py:10-15 StaticPool 全进程单连接, sweep_loop + HTTP + WS 共抢一条
    修复: 改 QueuePool 提高上限, 或迁 aiosqlite/PostgreSQL

B2. backend/main.py:62-69 host_guard_middleware 每个请求新开 Session 查 panel_domain
    修复: lru_cache + ttl 缓存或事件失效

B3. backend/api/nodes.py:191-227 async def uninstall 内直接同步 paramiko, 阻塞 event loop ~60s
    修复: await asyncio.to_thread(uninstall_node, node) 或路由改 def

B4. backend/api/nodes.py:275 部署并发不加锁, 同节点点两次部署导致 text file busy
    修复: 节点级 lock 或 deploy_status CAS

B5. backend/main.py:22-37 lifespan 迁移失败被 except 吞, 服务仍启动导致凭据假死
    修复: 迁移失败 sys.exit(1), 让 systemd 隔离

## C. API 一致性 (4 条)

C1. backend/api/* 返回格式 4 种风格混用 ({ok,data} / 裸字段 / ok:true+success:false / HTTPException)
    修复: 统一 respond(data) / respond_error(code,msg) 封装

C2. backend/api/nodes.py:266-272 等多处 业务失败用 HTTP 200 + ok:true + success:false
    修复: 业务失败统一 4xx/5xx, 前端按 status 分支

C3. backend/api/* 多处 payload: dict 跳过 Pydantic, 错误吐 raw KeyError
    修复: 每个 endpoint 写 Pydantic 模型, extra=forbid

C4. backend/api/* 错误响应含 type(e).__name__ + e 原文, 暴露内部细节
    修复: 内部细节只入日志, 响应走白名单文案

## D. 前端关键 BUG (6 条)

D1. frontend/src/views/NodeList.vue:185 metrics[id].cpu_pct.toFixed(1) 字段缺失时整卡片崩溃
    修复: (metrics[id].cpu_pct ?? 0).toFixed(1)

D2. frontend/src/views/SSConfigDrawer.vue:172 previewCollapse 未定义就 v-model 绑定, Vue 警告
    修复: const previewCollapse = ref('preview'), 删除冗余 previewVisible

D3. frontend/index.html:6 <title>Sign in</title> 浏览器 tab 标题永远是 Sign in
    修复: 改 MeshPanel, 各页面动态 document.title

D4. frontend/src/views/SogaConfigDrawer.vue:423 + SogaConfEditor.vue:106 window resize 监听在 script setup 顶层, 永不 remove, 内存泄漏
    修复: 移到 onMounted, onBeforeUnmount 移除

D5. frontend/src/views/NodeList.vue:781-797 + 854-857 部署中轮询全表替换, _deploying 状态丢失, 按钮抖动
    修复: 轮询按 id 合并字段, 不整体替换, 或部署期间暂停轮询

D6. frontend/src/api.js + 各组件 拦截器与组件双重 ElMessage.error (10+ 处), 一次网络错弹 2 个 toast
    修复: 拦截器加 _suppressToast flag, 或各组件移除显式 toast

## E. WebSocket / Agent (4 条)

E1. agent/ws.go:16,45-52 重连固定 5s, 无指数退避, N 节点会 thundering herd
    修复: 指数退避 + jitter (1s -> 2s -> 4s -> 60s, ±30%)

E2. agent/metrics.go:1-16 整文件 Linux-only 但无 //go:build linux, macOS 编译产物运行时全 0
    修复: 文件首行加 //go:build linux

E3. agent/metrics.go:467-486 网卡变更 / ifdown 后 rx<prevRx 静默置 0, 没标 hasPrev=false
    修复: 检测复位时 hasPrev=false 跳过本轮

E4. agent/ws.go:84-102 心跳写失败仅 return, 不 close(done), metricsLoop 继续往坏 conn 写, 堆日志
    修复: 心跳失败主动 conn.Close() 或 close(done) (配合 sync.Once)
# MeshPanel v2.2 - P1 强烈建议修 (约 45 条)

## F. 前端中等问题 (15 条)

F1. frontend/src/api.js:13-18 拦截器返回 resp.data, 但各组件混用 resp.data.xxx vs r.folder 风格
    修复: 拦截器统一脱壳到 data.data, 全局一种风格

F2. frontend/src/api.js:21-28 拦截器漏处理 404/500/timeout, 用户看到 raw 英文 message
    修复: 按 status 分类输出友好文案

F3. frontend/src/views/NodeList.vue:854-857 quick refresh 不排除 dialogOpen, 编辑时背后突变
    修复: dialogOpen || detailOpen 时跳过

F4. frontend/src/views/NodeList.vue:857 loadMetrics 无 inflight 锁, 弱网请求堆积乱序
    修复: 维护 metricsInflight flag 或 AbortController

F5. frontend/src/views/NodeList.vue:249-256 部署按钮可双击 (菜单里只看 deploy_status)
    修复: deployNode 入口判断 row._deploying 直接 return

F6. frontend/src/views/NodeList.vue:589-597 countryFilter !== 'all' 时拖动后 nodes.value 全表顺序提交, 用户看着没保存
    修复: countryFilter 非 all 时禁用拖拽

F7. frontend/src/views/NodeList.vue:549 + SettingsDialog.vue:11-20 端口/SSH 端口无 1-65535 校验
    修复: 加 type:integer, min:1, max:65535

F8. frontend/src/views/NodeList.vue:191/200/218 进度条宽度保护不统一 (mem 没保护, swap/disk 函数内保护)
    修复: 统一 Math.max(2, Math.min(100, v))

F9. frontend/src/views/NodeList.vue:663-668 loadLevel 阈值 CPU/内存/磁盘共用, 但语义不同
    修复: 分类型设阈值, 磁盘 90%+ 才警告

F10. frontend/src/views/SSConfigDrawer.vue:167-169 "保存并应用" 直接生效, 客户端会断线, 无二次确认
     修复: ElMessageBox.confirm 后再 apply

F11. frontend/src/views/SettingsDialog.vue:250-258 推送到所有节点串行 for await, N 个节点 N 倍延迟
     修复: Promise.allSettled 并发

F12. frontend/src/views/NodeList.vue:1007/1154 长节点名不截断, 撑破卡片
     修复: 加 overflow:hidden text-overflow:ellipsis + :title 兜底

F13. frontend/src/views/NodeList.vue:134 长 IP/host 截断后无 title, 看不到完整内容
     修复: <span :title="row.host">

F14. frontend/src/views/RouteCard.vue:157-171 直接突变 props.route.rules, Vue 警告
     修复: 改 defineModel 或 emit update:rules

F15. frontend/package.json:4 版本号 1.1.12 与发版目标 v2.2 不匹配
     修复: bump 到 2.2.0

## G. 后端中等问题 (12 条)

G1. backend/api/* 列表接口全部无分页/排序/过滤 (nodes/settings/soga/routes)
    修复: 加 ?limit=&offset=&sort=

G2. backend/api/nodes.py:191/275 + api/soga.py:68/332 长任务零幂等保护
    修复: idempotency-key header 或 per-node lock (参考 soga._scan_locks)

G3. backend/api/nodes.py:103 create_node 无 (host, ssh_port) 唯一约束, 可重复创建
    修复: Index unique(name, host, ssh_port) 或应用层去重

G4. backend/models/node.py:21+51+9 缺关键索引 (kind, agent_status, host)
    修复: 补 ix_nodes_kind_host / ix_nodes_agent_status_last_seen

G5. backend/models/soga.py:21,37,49 外键无 ON DELETE CASCADE, 靠应用层 _cascade_delete_node_soga 兜底
    修复: DDL 层加 ON DELETE CASCADE, 移除应用层 cascade

G6. backend/models/node.py:17/62/71 用 String 存 JSON, 无法 SQL 层 query
    修复: tags / soga_system_probe_rules / ss_config 改 Column(JSON)

G7. backend/database.py:30-62 _ensure_column 用裸 ALTER, 无版本号无回滚
    修复: 引入 Alembic batch 模式

G8. backend/ ws/agents.py 多处 datetime.utcnow() naive utc, Py3.12 已 deprecated
    修复: 统一 datetime.now(timezone.utc)

G9. backend/main.py:38-42 sweep_task cancel 不 await, 慢 client close 无 timeout
    修复: finally await wait_for(sweep_task, 5), close 包 wait_for

G10. backend/deploy/installer.py:18-19 DEPLOY_TIMEOUT=600s / idle 180s 慢链路常超时
     修复: 暴露成 settings, idle->240s, total->900s

G11. backend/deploy/uninstaller.py:12-49 卸载不清 /etc/systemd/system/sing-box.service.d /var/log/sing-box* /tmp/sing-box-*
     修复: 增加 rm -rf 这些路径

G12. backend/api/soga.py 56+ 处 except Exception 静默吞, 错误诊断丢失
     修复: 限定异常类型, 保留日志

## H. Agent 中等问题 (8 条)

H1. agent/ws.go:124-125 handleCmd 同步在读循环里调 exec.Command, systemctl 卡住会阻塞读循环
    修复: exec.CommandContext + 30s 超时, handleCmd 独立 goroutine

H2. agent/ws.go:60-66 sendJSON 全程持锁含网络 I/O, ping 会排队误判超时
    修复: 单写 goroutine + 缓冲 channel 模型

H3. agent/ws.go:34-54 整个 runLoop 无 context, SIGTERM 无优雅关闭, 不发 Close frame
    修复: 引入 ctx + cancel, 监听信号后发 Close frame 再退

H4. agent/metrics.go:42-48 cpuModelOnce 等全局变量在 serveConn 重启时有 data race
    修复: 用 sync.Once, 或改 atomic.Value

H5. agent/metrics.go:301-323/343-349 readMem/readSwap 解析失败 _ 吞, 误报 0
    修复: 显式判断两项都拿到, 否则返 error

H6. agent/metrics.go:215 detectDefaultIface fallback 返 "lo", 流量统计走 loopback
    修复: 返 "", metricsLoop 跳过流量字段

H7. agent/metrics.go:411-425 goss 失败静默置 0, 用户不知 tcp_conn=0 为何
    修复: 限速 log.Print 一次告警

H8. agent/Makefile:14,17,20 缺 -trimpath -buildvcs=false, 二进制内嵌 CI 路径破坏可重现
    修复: go build -trimpath -buildvcs=false -ldflags="..."

## I. 部署 / CI / 打包中等问题 (10 条)

I1. release.yml:11-12 只构 linux-amd64, install.sh:28 对 arm64 直接 die (Graviton/Ampere/Pi 缺位)
    修复: matrix arch [amd64, arm64], 用 ubuntu-22.04-arm 或 manylinux2014_aarch64

I2. release.yml:12 ubuntu-22.04 glibc 2.35, 无法在 CentOS 7/8 Debian 10 运行
    修复: 改用 manylinux2014 容器构建 (glibc 2.17)

I3. release.yml:89 softprops/action-gh-release@v2 未锁 SHA, 可被重指向
    修复: 锁 40 位 SHA (含 checkout/setup-go/setup-node/setup-python)

I4. release.yml:23-27 workflow_dispatch 触发时 GITHUB_REF_NAME 是分支名, VERSION 被污染为 main
    修复: 仅 tag 触发才取版本, 否则用 dev-<sha>

I5. release.yml:69-79 smoke test 仅 /api/health + /, 漏 agent 下载/版本/登录
    修复: 加 curl /api/agent/amd64 检查 ELF + --version 检查

I6. mesh-panel.spec:76 runtime_tmpdir=None onefile 解压到 /tmp 长驻服务每次启动 3s
    修复: runtime_tmpdir=/opt/mesh-panel/.cache, 或改 onedir 发布

I7. mesh-panel.spec:21-48 hiddenimports 漏 starlette/anyio/sqlalchemy.dialects.sqlite.pysqlite
    修复: 加 --collect-all fastapi/starlette, 本地 strace 补齐

I8. install.sh:185+ read -p 未带 -r, 反斜杠被吞
    修复: 统一 read -r -p

I9. install.sh:306-346 mesh port 直接写库, panel 进程持连接, SQLite busy 5s 可能失败
    修复: 先 stop 再写库再 start, 或 POST /api/admin/port 热加载

I10. install.sh:全文 无 trap 清理, Ctrl+C 留 partial unit / partial binary
     修复: trap 'rm -f ${BINARY_PATH}.new' EXIT, 失败时回滚旧二进制
# MeshPanel v2.2 - P2 轻微问题简表 (约 130 条, 可延后)

## 前端 (40+ 条)

- 死代码: api.js:54 agentReload 未引用, SSConfigDrawer.vue:191 previewVisible 写不读, NodeList.vue:753 row._uninstalling 写不读
- CSS 重复: @keyframes spin 在 App.vue 和 NodeList.vue 各一份, .section-title 在 5 个文件重复
- 未使用 CSS: RouteCard.vue 多处 is-system/.kind-sys/.collapse-arrow, SogaConfigDrawer.vue .row-inline/.del-btn, SettingsDialog.vue .port-hint/.state-text
- magic number: NodeList.vue:854(5000)/857(2000)/434(720), api.js:50(620000)/55(90000)/6(30000)
- 中英混用: "agent 离线" vs "Agent 回连", "soga restart" vs "Soga 实例"
- 复制按钮无 execCommand 兜底 (http 非 localhost 失败)
- 暗色模式完全缺失, 硬编码 #fff/#1f2937
- 单一响应式断点 (App 600px, NodeList 640px 不一致), iPhone SE stat-card 挤
- 文案: "config schema" 英文夹中文, "两次不一致" 缺主语, NodeList.vue:282 国家代码无 ISO 白名单
- 进度条 fmtBps 负值不防护, fmtBytes(0)/undefined/NaN 都返 "0 B"
- 大组件未拆分: NodeList.vue 1360 行, SogaConfigDrawer.vue 930 行
- 长列表无虚拟滚动 (100+ 节点 metrics 轮询主线程压力)
- vite.config.js:11 proxy 写死 127.0.0.1:8000
- flagcdn.com 外部依赖, 内网环境国旗 404
- RouteCard.vue:40 :key="i" 数组下标, 删除中间规则 DOM 复用错乱

## 后端 (30+ 条)

- 未使用 import: api/soga.py:13 col, deploy/installer.py:8 Path, deploy/uninstaller.py:3 re, deploy/agent_rpc.py:22 Any
- _ok/_err 在 nodes.py 和 ss_config.py 重复定义
- main.py + run_server.py 启动逻辑高度重复
- print(file=sys.stderr) 调试输出未走 logging
- 函数内重复 import: installer.py:50 import time, singbox_config.py:64/228 ipaddress
- 路径命名 kebab-case 与 snake_case 混用 (/ss-config vs /auth/setup)
- /auth/me 用 _require_user, 与其他 endpoint Depends 风格不一致
- 中间件鉴权 + endpoint 内 _require_user 双轨重复
- 同步 def endpoint 占 threadpool worker, 并发上来 WS 也卡
- get_session generator 异常不 rollback
- enum 字符串无 Literal 约束 (deploy_status / agent_status)
- /auth/status 透露 setup_required, 可探测主机有无人管
- Session.exec(text(f"DELETE WHERE id={id}")) 与 bindparams 风格不一致
- HTTP 方法语义: deploy/reset 用 POST 而非 PATCH

## Agent (10 条)

- ws.go:113-129 rpc 消息走 msgIn dispatch, 解析失败被吞日志
- ws.go:151 ack 消息 _ = send(...) 忽略错误
- metrics.go:262-263 readIfaceBytes ParseUint 错误丢弃, 截断行触发 B3 负 delta
- metrics.go:363-375 readDisk 只统计根分区, Docker overlay 语义不符
- metrics.go:429-437 prev* 局部变量, 重连首轮丢 metrics 2 秒空白
- metrics.go:238/264/290/322 sc.Err() 检查不全
- Makefile:1 VERSION ?= 1.1.12 硬编码默认, 与 release tag 脱节
- Makefile:3 LDFLAGS 未注入 BuildTime/Commit, agent 上报不可追溯
- Makefile:23 clean 同时删 socks-agent-*  和 mesh-agent-*, 前者历史遗留
- rpc.go base64 失败后错误信息不清晰

## install.sh / CI / spec (50+ 条)

- install.sh:55-58 get_latest_tag 用 grep -oP, BusyBox/老 macOS 不带
- install.sh:100-107 无 GitHub 镜像回退 (ghproxy/fastgit)
- install.sh:115-138 unit 无 LimitNOFILE/LimitNPROC/MemoryMax
- install.sh:128-129 Restart=on-failure 无 StartLimitBurst, 崩溃循环可能高频
- install.sh:117 unit 写入非原子 (cat > unit, 断电留半文件)
- install.sh:120-121 缺 After=local-fs.target
- install.sh:322 端口占用检测 grep -qE ":${new}$" IPv6/通配地址不准
- install.sh:223/269/337/429 sleep N 后健康检查无重试循环
- install.sh:262 do_update 直接覆盖, 启动失败无回滚
- install.sh:100-112 下载失败 .new 残留不清
- install.sh:299 rmdir INSTALL_DIR 不清 agent_dist/logs/_MEIxxx
- install.sh:全文 无 SQLite schema migration 检查
- release.yml:48-52 pip install 未启用 cache, 每次重下大包
- release.yml:19-21 setup-go cache 未显式
- release.yml:55-59 backend/version.py 未加 .gitignore, 易污染本地
- release.yml:72-79 smoke kill $PID 无 -TERM/-KILL 兜底
- release.yml:85 无 GPG/cosign 签名, 仅 sha256
- release.yml:89-96 未区分 draft/prerelease, v1.0.0-rc1 应自动 prerelease
- release.yml:全文 无失败通知 (Slack/邮件/Discord)
- release.yml:全文 build agent/frontend/python 串行单 job, 可拆并行
- mesh-panel.spec:14-17 datas 用相对路径, cwd 敏感
- mesh-panel.spec:59 excludes 漏 setuptools/pip/wheel/pytest/docutils/lib2to3
- mesh-panel.spec:74-75 strip=False, 可省 30%+ 体积
- mesh-panel.spec:42-48 try/except 静默吞 collect_all 失败
- mesh-panel.spec:全文 Python 版本未锁定 assert
- config.py:29 os.geteuid Windows 不存在会 AttributeError
- config.py:64 DATA_DIR.mkdir 无 try, 容器 read-only 启动崩
- config.py:51-63 LEGACY_DATA 迁移每次启动都检测, 应一次性标记
- main.py:111-124 FRONTEND_DIST 缺失静默跳过, 用户看到 / 404 无诊断
- firstrun.py:15 DEFAULT_PASSWORD 硬编码 admin123456 (P0 也提了, 此处指文案/日志)
- 整个项目无结构化日志, 无登录/部署/改密/卸载审计日志
- agent_dist/.gitkeep 空目录, 部署按钮 disable 提示缺失
# MeshPanel v2.2 修复路线图

## 总览

- P0 阻塞发版: 28 条 (audit_v2.2/P0-must-fix.md)
- P1 强烈建议: 45 条 (audit_v2.2/P1-recommended.md)
- P2 轻微: ~130 条 (audit_v2.2/P2-minor.md)
- 合计 ~200 条 (审计过程中部分小问题已合并)

## 推荐五阶段路线 (建议在独立分支 release/v2.2 上分阶段提交)

### Phase 1 · 关键安全 + 数据一致性 (1-2 天)
彻底修完才能开始打包。

- A1 sha256 校验 (install.sh)
- A2 systemd hardening + meshpanel 用户
- A3 + A4 + A5 JWT/Fernet 三件套 (DB chmod, jti 撤销, key 丢失保护)
- A6 + A7 + A8 token 走 stdin/SFTP, heredoc 加引号
- A9 agent shell.exec 限制白名单 (或干脆只保留 fs.* 关闭 shell.exec)
- B5 lifespan 迁移失败拒绝启动

提交建议: `fix(security): v2.2 P0 安全加固 (9 条)`

---

### Phase 2 · 后端稳定性 (1 天)
影响生产可用性。

- B1 改 QueuePool 或 aiosqlite
- B2 host_guard 加 lru_cache
- B3 uninstall 走 asyncio.to_thread
- B4 部署节点级 lock / CAS
- C1 + C2 + C3 + C4 API 返回格式统一 + Pydantic schema + 错误响应脱敏

提交建议: `fix(backend): v2.2 P0 稳定性 + API 一致性 (9 条)`

---

### Phase 3 · 前端关键 BUG + Agent (1 天)

前端:
- D1 metrics 空值崩溃
- D2 previewCollapse
- D3 浏览器 title
- D4 resize 监听内存泄漏
- D5 部署轮询竞态
- D6 双重 toast

Agent:
- E1 指数退避
- E2 //go:build linux
- E3 网卡复位
- E4 心跳失败 close(done)

版本号同步:
- frontend/package.json 2.2.0
- agent/version.go 2.2.0 (Makefile 默认值)
- backend/version.py 由 CI 注入 (无需手动)

提交建议: `fix(frontend,agent): v2.2 P0 关键 BUG (10 条)`

---

### Phase 4 · 中等优先级 (2-3 天)
F1-F15 + G1-G12 + H1-H8 + I1-I10 = 45 条。
可按文件粒度分多个 commit:
- `fix(frontend): UI 边界与稳定性 (P1)`
- `fix(backend): API/DB/部署超时 (P1)`
- `fix(agent): WS/metrics 健壮性 (P1)`
- `fix(ci,install): 多架构/glibc/hardening (P1)`

重点: I1 + I2 是 v2.2 是否支持 arm64 + 老发行版的关键决策, 建议这次就一起做。

---

### Phase 5 · 死代码清理 + 文档 (0.5 天)
- 删除 P2 列表里的所有未使用 import / state / CSS
- AUDIT_v2.1.8.md 删除或归档到 docs/audit/
- 更新 README.md 加 arm64 支持说明
- 更新 docs/ 中已过期的描述
- bump 版本号 + git tag v2.2.0
- CI 跑通后 GitHub Release

提交建议: `chore: v2.2 死代码清理 + 文档更新`, 然后 `chore: release v2.2.0`

## 关键决策点 (需要你确认)

D1. arm64 支持: 这次做还是 v2.3? (建议这次做, agent 已经有 arm64 Makefile target)

D2. glibc 兼容: 切 manylinux2014 容器构建? (建议做, 用户群更广)

D3. PyInstaller onefile vs onedir: 改 onedir 体验更好但发布是 tar.gz 不再是单文件 (建议保留 onefile + 加 runtime_tmpdir)

D4. shell.exec RPC: 保留 (允许前端给 agent 跑任意命令) 还是改成白名单方法 (sing-box.reload / agent.restart 几个固定命令)? (强烈建议白名单, A9 的 token 泄漏问题就大幅降低危害)

D5. PostgreSQL 迁移: 这次只优化 SQLite 配置, 还是顺便支持 PG? (建议 v2.2 只优化 SQLite, v2.3 加 PG 支持)

D6. 默认管理员密码: 随机生成打印到 stderr 还是强制首次登录改密? (建议两个都做, 随机生成 + 首次必须改)

## 工作量估算

- 5 个 Phase 顺序做大约 5-7 个工作日
- 如果只做 P0 (Phase 1+2+3 关键部分), 3-4 天可发版
- 全部 P0+P1 完成约 1 周, 加 CI smoke/灰度 1-2 天

## 验证清单 (发版前必跑)

[ ] make all 三架构编译通过
[ ] frontend npm run build 通过
[ ] pyinstaller mesh-panel.spec 在 manylinux2014 容器中通过
[ ] 产物 mesh-panel-linux-amd64 和 linux-arm64 都存在
[ ] sha256 文件存在并校验通过
[ ] 全新机器 curl install.sh 一键安装成功
[ ] 创建管理员 -> 登录 -> 添加节点 -> 部署节点 -> 看到 metrics -> 改 SS 配置应用 -> 卸载节点 一整链路通过
[ ] 关掉 panel 进程模拟崩溃, systemd 自动拉起
[ ] curl -k https://panel/api/health 返回 ok
[ ] mesh port 9000 切换端口验证 (Phase 4 修复后)
[ ] agent 断网 60s 重连成功且有指数退避日志
[ ] 升级路径: 从 v2.1.18 直接 mesh update 到 v2.2 数据不丢
