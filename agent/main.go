package main

import (
	"log"
	"net/url"
	"os"
	"os/signal"
	"strconv"
	"strings"
	"syscall"
	"time"
)

func mustEnv(key string) string {
	v := strings.TrimSpace(os.Getenv(key))
	if v == "" {
		log.Fatalf("missing required env: %s", key)
	}
	return v
}

func main() {
	log.SetFlags(log.LstdFlags | log.Lmicroseconds)
	log.Printf("mesh-agent starting, version=%s", Version)

	token := mustEnv("MESH_AGENT_TOKEN")
	endpoint := mustEnv("MESH_AGENT_ENDPOINT")
	nodeIDStr := mustEnv("MESH_AGENT_NODE_ID")

	if _, err := strconv.Atoi(nodeIDStr); err != nil {
		log.Fatalf("MESH_AGENT_NODE_ID must be int, got %q", nodeIDStr)
	}

	// 拼 ws URL:  endpoint + /ws/node?token=...&node_id=...&version=...
	wsURL, err := buildWSURL(endpoint, token, nodeIDStr, Version)
	if err != nil {
		log.Fatalf("invalid endpoint %q: %v", endpoint, err)
	}
	log.Printf("ws endpoint = %s", maskToken(wsURL))

	startedAt := time.Now()
	stop := make(chan os.Signal, 1)
	signal.Notify(stop, syscall.SIGINT, syscall.SIGTERM)

	go runLoop(wsURL, startedAt, stop)

	<-stop
	log.Printf("signal received, exiting")
}

func buildWSURL(endpoint, token, nodeID, version string) (string, error) {
	endpoint = strings.TrimRight(endpoint, "/")
	// 允许用户填 http(s):// 自动转 ws(s)://
	switch {
	case strings.HasPrefix(endpoint, "http://"):
		endpoint = "ws://" + endpoint[len("http://"):]
	case strings.HasPrefix(endpoint, "https://"):
		endpoint = "wss://" + endpoint[len("https://"):]
	}
	u, err := url.Parse(endpoint + "/ws/node")
	if err != nil {
		return "", err
	}
	q := u.Query()
	q.Set("token", token)
	q.Set("node_id", nodeID)
	q.Set("version", version)
	u.RawQuery = q.Encode()
	return u.String(), nil
}

func maskToken(u string) string {
	pu, err := url.Parse(u)
	if err != nil {
		return u
	}
	q := pu.Query()
	if t := q.Get("token"); t != "" {
		if len(t) > 6 {
			q.Set("token", t[:6]+"…")
		} else {
			q.Set("token", "…")
		}
	}
	pu.RawQuery = q.Encode()
	return pu.String()
}
