package main

import (
	"context"
	"encoding/base64"
	"encoding/json"
	"fmt"
	"log"
	"os"
	"os/exec"
	"path/filepath"
	"time"
)

// RPC 协议(对接 backend/deploy/agent_rpc.py)
//
// 后端 → agent : {"type":"rpc","id":"<uuid>","method":"shell.exec","params":{...}}
// agent → 后端 : {"type":"rpc_resp","id":"<uuid>","ok":true,"result":{...}}
//                {"type":"rpc_resp","id":"<uuid>","ok":false,"error":"..."}
//
// 每个 RPC 在独立 goroutine 处理,不阻塞 ws 读循环。
// shell.exec 用 ctx timeout 硬切;fs.* 用 1MB 上限防爆内存。

const (
	rpcDefaultExecTimeout = 30 * time.Second
	rpcDefaultExecMax     = 30
	rpcMaxFileSize        = 1024 * 1024 // 1MB
)

type rpcReq struct {
	Type   string          `json:"type"`
	ID     string          `json:"id"`
	Method string          `json:"method"`
	Params json.RawMessage `json:"params"`
}

type rpcResp struct {
	Type   string      `json:"type"`
	ID     string      `json:"id"`
	Ok     bool        `json:"ok"`
	Result interface{} `json:"result,omitempty"`
	Error  string      `json:"error,omitempty"`
}

// handleRPC 在独立 goroutine 里跑。raw 是收到的整条 ws 帧。
func handleRPC(raw []byte, send func(any) error) {
	var req rpcReq
	if err := json.Unmarshal(raw, &req); err != nil {
		log.Printf("rpc bad json: %v", err)
		return
	}
	if req.ID == "" || req.Method == "" {
		log.Printf("rpc missing id/method")
		return
	}

	log.Printf("rpc start id=%s method=%s", req.ID, req.Method)
	result, err := dispatchRPC(req.Method, req.Params)
	if err != nil {
		_ = send(rpcResp{Type: "rpc_resp", ID: req.ID, Ok: false, Error: err.Error()})
		log.Printf("rpc fail id=%s method=%s err=%v", req.ID, req.Method, err)
		return
	}
	_ = send(rpcResp{Type: "rpc_resp", ID: req.ID, Ok: true, Result: result})
	log.Printf("rpc ok id=%s method=%s", req.ID, req.Method)
}

func dispatchRPC(method string, paramsRaw json.RawMessage) (interface{}, error) {
	switch method {
	case "shell.exec":
		return rpcShellExec(paramsRaw)
	case "fs.read":
		return rpcFsRead(paramsRaw)
	case "fs.write":
		return rpcFsWrite(paramsRaw)
	case "fs.list":
		return rpcFsList(paramsRaw)
	case "fs.stat":
		return rpcFsStat(paramsRaw)
	default:
		return nil, errf("unknown method: %s", method)
	}
}

// ───────── shell.exec ─────────

type shellExecParams struct {
	Cmd     string `json:"cmd"`
	Timeout int    `json:"timeout"`
}

type shellExecResult struct {
	Rc     int    `json:"rc"`
	Stdout string `json:"stdout"`
	Stderr string `json:"stderr"`
}

func rpcShellExec(raw json.RawMessage) (interface{}, error) {
	var p shellExecParams
	if err := json.Unmarshal(raw, &p); err != nil {
		return nil, err
	}
	if p.Cmd == "" {
		return nil, errf("cmd 不能为空")
	}
	timeout := time.Duration(p.Timeout) * time.Second
	if timeout <= 0 {
		timeout = rpcDefaultExecTimeout
	}
	ctx, cancel := context.WithTimeout(context.Background(), timeout)
	defer cancel()

	cmd := exec.CommandContext(ctx, "/bin/sh", "-c", p.Cmd)
	var stdoutBuf, stderrBuf cappedBuffer
	stdoutBuf.cap = rpcMaxFileSize
	stderrBuf.cap = rpcMaxFileSize
	cmd.Stdout = &stdoutBuf
	cmd.Stderr = &stderrBuf

	rc := 0
	if err := cmd.Run(); err != nil {
		if ctx.Err() == context.DeadlineExceeded {
			return nil, errf("命令超时 (%ds)", int(timeout.Seconds()))
		}
		if ee, ok := err.(*exec.ExitError); ok {
			rc = ee.ExitCode()
		} else {
			return nil, err
		}
	}
	return shellExecResult{Rc: rc, Stdout: stdoutBuf.String(), Stderr: stderrBuf.String()}, nil
}

// ───────── fs.read ─────────

type fsReadParams struct {
	Path    string `json:"path"`
	MaxSize int    `json:"max_size"`
}

type fsReadResult struct {
	ContentB64 string `json:"content_b64"`
}

func rpcFsRead(raw json.RawMessage) (interface{}, error) {
	var p fsReadParams
	if err := json.Unmarshal(raw, &p); err != nil {
		return nil, err
	}
	if p.Path == "" {
		return nil, errf("path 不能为空")
	}
	maxSize := p.MaxSize
	if maxSize <= 0 || maxSize > rpcMaxFileSize {
		maxSize = rpcMaxFileSize
	}
	st, err := os.Stat(p.Path)
	if err != nil {
		return nil, err
	}
	if st.Size() > int64(maxSize) {
		return nil, errf("文件超过上限 %d 字节 (实际 %d)", maxSize, st.Size())
	}
	data, err := os.ReadFile(p.Path)
	if err != nil {
		return nil, err
	}
	return fsReadResult{ContentB64: base64.StdEncoding.EncodeToString(data)}, nil
}

// ───────── fs.write ─────────

type fsWriteParams struct {
	Path       string `json:"path"`
	ContentB64 string `json:"content_b64"`
	Mode       int    `json:"mode"`
}

func rpcFsWrite(raw json.RawMessage) (interface{}, error) {
	var p fsWriteParams
	if err := json.Unmarshal(raw, &p); err != nil {
		return nil, err
	}
	if p.Path == "" {
		return nil, errf("path 不能为空")
	}
	data, err := base64.StdEncoding.DecodeString(p.ContentB64)
	if err != nil {
		return nil, errf("content_b64 解码失败: %v", err)
	}
	if len(data) > rpcMaxFileSize {
		return nil, errf("写入超过上限 %d 字节", rpcMaxFileSize)
	}
	mode := os.FileMode(p.Mode)
	if mode == 0 {
		mode = 0o644
	}
	// 确保父目录存在
	if dir := filepath.Dir(p.Path); dir != "" && dir != "." {
		_ = os.MkdirAll(dir, 0o755)
	}
	if err := os.WriteFile(p.Path, data, mode); err != nil {
		return nil, err
	}
	// WriteFile 不会改既存文件的权限,显式 chmod 一次
	_ = os.Chmod(p.Path, mode)
	return map[string]any{}, nil
}

// ───────── fs.list ─────────

type fsListParams struct {
	Glob string `json:"glob"`
}

type fsListResult struct {
	Items []string `json:"items"`
}

func rpcFsList(raw json.RawMessage) (interface{}, error) {
	var p fsListParams
	if err := json.Unmarshal(raw, &p); err != nil {
		return nil, err
	}
	if p.Glob == "" {
		return nil, errf("glob 不能为空")
	}
	// 兼容后端传的尾斜杠形式 /etc/soga/*/
	pat := p.Glob
	for len(pat) > 1 && pat[len(pat)-1] == '/' {
		pat = pat[:len(pat)-1]
	}
	matches, err := filepath.Glob(pat)
	if err != nil {
		return nil, err
	}
	if matches == nil {
		matches = []string{}
	}
	return fsListResult{Items: matches}, nil
}

// ───────── fs.stat ─────────

type fsStatParams struct {
	Path string `json:"path"`
}

func rpcFsStat(raw json.RawMessage) (interface{}, error) {
	var p fsStatParams
	if err := json.Unmarshal(raw, &p); err != nil {
		return nil, err
	}
	st, err := os.Stat(p.Path)
	if err != nil {
		if os.IsNotExist(err) {
			return map[string]any{"exists": false}, nil
		}
		return nil, err
	}
	return map[string]any{
		"exists": true,
		"size":   st.Size(),
		"mtime":  st.ModTime().Unix(),
		"mode":   int(st.Mode().Perm()),
		"is_dir": st.IsDir(),
	}, nil
}

// ───────── util ─────────

// cappedBuffer 截断超过 cap 字节后的写入,防止 exec 输出爆内存。
type cappedBuffer struct {
	buf []byte
	cap int
}

func (b *cappedBuffer) Write(p []byte) (int, error) {
	if len(b.buf) >= b.cap {
		return len(p), nil
	}
	room := b.cap - len(b.buf)
	if len(p) <= room {
		b.buf = append(b.buf, p...)
		return len(p), nil
	}
	b.buf = append(b.buf, p[:room]...)
	return len(p), nil
}

func (b *cappedBuffer) String() string { return string(b.buf) }

type rpcErr struct{ msg string }

func (e *rpcErr) Error() string { return e.msg }

func errf(format string, args ...any) error {
	return &rpcErr{msg: fmt.Sprintf(format, args...)}
}
