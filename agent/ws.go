package main

import (
	"context"
	"encoding/json"
	"log"
	"math/rand"
	"os"
	"os/exec"
	"sync"
	"time"

	"github.com/gorilla/websocket"
)

const (
	heartbeatInterval = 10 * time.Second
	writeTimeout      = 10 * time.Second
	readTimeout       = 30 * time.Second
	// 重连退避参数
	reconnectMin = 1 * time.Second
	reconnectMax = 60 * time.Second
)

type msgIn struct {
	Type   string `json:"type"`
	Action string `json:"action,omitempty"`
	Iface  string `json:"iface,omitempty"`
}

type pingMsg struct {
	Type   string `json:"type"`
	Ts     int64  `json:"ts"`
	Uptime int64  `json:"uptime"`
}

// runLoop 永不退出，断线后指数退避重连
func runLoop(wsURL string, startedAt time.Time, stop chan os.Signal) {
	delay := reconnectMin
	for {
		select {
		case <-stop:
			return
		default:
		}

		log.Printf("dialing...")
		conn, _, err := websocket.DefaultDialer.Dial(wsURL, nil)
		if err != nil {
			// 加 ±30% jitter 避免 thundering herd
			jitter := time.Duration(float64(delay) * (0.7 + rand.Float64()*0.6))
			log.Printf("dial failed: %v; retry in %s", err, jitter)
			time.Sleep(jitter)
			// 指数退避,上限 60s
			delay *= 2
			if delay > reconnectMax {
				delay = reconnectMax
			}
			continue
		}
		log.Printf("connected")
		delay = reconnectMin // 连接成功,重置退避
		serveConn(conn, startedAt)
		// 断开后从最小延迟开始(刚断不一定是服务端挂,可能只是网络抖动)
		jitter := time.Duration(float64(reconnectMin) * (0.7 + rand.Float64()*0.6))
		log.Printf("disconnected, reconnect in %s", jitter)
		time.Sleep(jitter)
	}
}

// serveConn 处理一次连接的生命周期。返回时连接已关闭
func serveConn(conn *websocket.Conn, startedAt time.Time) {
	defer conn.Close()

	var writeMu sync.Mutex
	sendJSON := func(v any) error {
		writeMu.Lock()
		defer writeMu.Unlock()
		conn.SetWriteDeadline(time.Now().Add(writeTimeout))
		return conn.WriteJSON(v)
	}

	// 立即发一个 ping，让主控立刻看到上线
	if err := sendJSON(pingMsg{
		Type:   "ping",
		Ts:     time.Now().Unix(),
		Uptime: int64(time.Since(startedAt).Seconds()),
	}); err != nil {
		log.Printf("initial ping failed: %v", err)
		return
	}

	done := make(chan struct{})

	// metrics 采样循环（2 秒一次）
	go metricsLoop(done, 2*time.Second, sendJSON)

	// 心跳 goroutine
	go func() {
		t := time.NewTicker(heartbeatInterval)
		defer t.Stop()
		for {
			select {
			case <-done:
				return
			case <-t.C:
				if err := sendJSON(pingMsg{
					Type:   "ping",
					Ts:     time.Now().Unix(),
					Uptime: int64(time.Since(startedAt).Seconds()),
				}); err != nil {
					log.Printf("ping failed: %v", err)
					// 主动关闭连接，让读循环立刻感知到错误退出，而不是等 readTimeout
					conn.Close()
					return
				}
			}
		}
	}()

	// 读循环
	for {
		conn.SetReadDeadline(time.Now().Add(readTimeout))
		_, raw, err := conn.ReadMessage()
		if err != nil {
			log.Printf("read err: %v", err)
			close(done)
			return
		}
		var m msgIn
		if err := json.Unmarshal(raw, &m); err != nil {
			log.Printf("bad msg: %v / %s", err, string(raw))
			continue
		}
		switch m.Type {
		case "pong":
			// 仅用于刷新 read deadline，已在 ReadMessage 上方设置
		case "set_iface":
			setIface(m.Iface)
			log.Printf("iface set to %q (effective: %q)", m.Iface, getIface())
		case "cmd":
			// 独立 goroutine，避免 systemctl 卡住阻塞读循环
			action := m.Action
			go handleCmd(action, sendJSON)
		case "rpc":
			// 不阻塞读循环,每个 RPC 独立 goroutine
			rawCopy := append([]byte(nil), raw...)
			go handleRPC(rawCopy, sendJSON)
		default:
			log.Printf("unknown msg type: %s", m.Type)
		}
	}
}

type ackMsg struct {
	Type    string `json:"type"`
	Action  string `json:"action"`
	Ok      bool   `json:"ok"`
	Message string `json:"message,omitempty"`
}

func handleCmd(action string, send func(any) error) {
	log.Printf("cmd: %s", action)
	var ok bool
	var message string

	switch action {
	case "reload", "restart":
		ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
		defer cancel()
		err := exec.CommandContext(ctx, "systemctl", action, "sing-box").Run()
		if err != nil {
			message = err.Error()
		} else {
			ok = true
		}
	default:
		message = "unknown action"
	}
	_ = send(ackMsg{Type: "ack", Action: action, Ok: ok, Message: message})
}
