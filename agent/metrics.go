package main

import (
	"bufio"
	"fmt"
	"os"
	"os/exec"
	"runtime"
	"strconv"
	"strings"
	"sync/atomic"
	"syscall"
	"time"

	"github.com/dean2021/goss"
)

// metricsMsg 是发给主控的 metrics 消息体
type metricsMsg struct {
	Type     string  `json:"type"`
	Ts       int64   `json:"ts"`
	Iface    string  `json:"iface"`
	RxBps    uint64  `json:"rx_bps"`
	TxBps    uint64  `json:"tx_bps"`
	RxTotal  uint64  `json:"rx_total"`
	TxTotal  uint64  `json:"tx_total"`
	CPUPct   float64 `json:"cpu_pct"`
	CPUModel string  `json:"cpu_model"`
	CPUCores int     `json:"cpu_cores"`
	MemUsed   uint64 `json:"mem_used"`
	MemTotal  uint64 `json:"mem_total"`
	SwapUsed  uint64 `json:"swap_used"`
	SwapTotal uint64 `json:"swap_total"`
	DiskUsed  uint64 `json:"disk_used"`
	DiskTotal uint64 `json:"disk_total"`
	TCPConn   uint64 `json:"tcp_conn"`
	UDPConn   uint64 `json:"udp_conn"`
	Uptime    int64  `json:"uptime_sec"`
	OSPretty  string `json:"os_pretty"`
}

// 静态 CPU 信息，启动时读一次缓存
var (
	cpuModelOnce  string
	cpuCoresOnce  int
	cpuInfoLoaded bool
	osPrettyOnce  string
)

// /etc/os-release: 拼 "Debian 13.5" 这样的字符串. 启动时读一次缓存.
func loadOSPretty() {
	if osPrettyOnce != "" {
		return
	}
	f, err := os.Open("/etc/os-release")
	if err != nil {
		osPrettyOnce = ""
		return
	}
	defer f.Close()
	kv := map[string]string{}
	sc := bufio.NewScanner(f)
	for sc.Scan() {
		line := sc.Text()
		idx := strings.Index(line, "=")
		if idx <= 0 {
			continue
		}
		k := strings.TrimSpace(line[:idx])
		v := strings.TrimSpace(line[idx+1:])
		v = strings.Trim(v, `"`)
		kv[k] = v
	}
	// 优先级: PRETTY_NAME > NAME + VERSION_ID > NAME > ID
	if p := kv["PRETTY_NAME"]; p != "" {
		osPrettyOnce = p
		return
	}
	name := kv["NAME"]
	ver := kv["VERSION_ID"]
	if name != "" && ver != "" {
		osPrettyOnce = name + " " + ver
		return
	}
	if name != "" {
		osPrettyOnce = name
		return
	}
	if id := kv["ID"]; id != "" {
		osPrettyOnce = id
	}
}

func loadCPUInfo() {
	if cpuInfoLoaded {
		return
	}
	cpuInfoLoaded = true
	cpuCoresOnce = runtime.NumCPU()
	f, err := os.Open("/proc/cpuinfo")
	if err != nil {
		cpuModelOnce = "unknown"
		return
	}
	defer f.Close()
	// 先全部读出来，按优先级匹配：model name > Model > Hardware > 兜底
	var modelName, modelArm, hardware string
	sc := bufio.NewScanner(f)
	for sc.Scan() {
		line := sc.Text()
		idx := strings.Index(line, ":")
		if idx < 0 {
			continue
		}
		key := strings.ToLower(strings.TrimSpace(line[:idx]))
		val := strings.Join(strings.Fields(strings.TrimSpace(line[idx+1:])), " ")
		if val == "" {
			continue
		}
		switch key {
		case "model name": // x86 主战场
			if modelName == "" {
				modelName = val
			}
		case "model": // ARM 上才是字符串，x86 上是数字 ID，需过滤纯数字
			if modelArm == "" {
				if _, err := strconv.Atoi(val); err != nil {
					modelArm = val
				}
			}
		case "hardware": // 老 ARM
			if hardware == "" {
				hardware = val
			}
		}
	}
	switch {
	case modelName != "":
		cpuModelOnce = modelName
	case modelArm != "":
		cpuModelOnce = modelArm
	case hardware != "":
		cpuModelOnce = hardware
	default:
		// ARM 云机 /proc/cpuinfo 只有 CPU implementer/part hex ID
		// device-tree/model 树莓派/SBC 有,云机为空
		// 兜底 1: /proc/device-tree/model
		if b, err := os.ReadFile("/proc/device-tree/model"); err == nil {
			s := strings.TrimRight(strings.TrimSpace(string(b)), "\x00")
			if s != "" {
				cpuModelOnce = s
				return
			}
		}
		// 兜底 2: lscpu 的 "Model name" (能识别 Neoverse-N1/Cortex-A72 等)
		if out, err := exec.Command("lscpu").Output(); err == nil {
			for _, line := range strings.Split(string(out), "\n") {
				idx := strings.Index(line, ":")
				if idx < 0 {
					continue
				}
				key := strings.ToLower(strings.TrimSpace(line[:idx]))
				if key == "model name" {
					val := strings.TrimSpace(line[idx+1:])
					if val != "" && val != "-" {
						cpuModelOnce = val
						return
					}
				}
			}
		}
		cpuModelOnce = "unknown"
	}
}

// 当前生效网卡名，原子读写
var currentIface atomic.Value // string

func setIface(name string) {
	name = strings.TrimSpace(name)
	if name == "" {
		name = detectDefaultIface()
	}
	currentIface.Store(name)
}

func getIface() string {
	v := currentIface.Load()
	if v == nil {
		return ""
	}
	return v.(string)
}

// detectDefaultIface 通过 `ip route get 8.8.8.8` 找默认网卡
func detectDefaultIface() string {
	out, err := exec.Command("ip", "route", "get", "8.8.8.8").Output()
	if err == nil {
		// 形如: 8.8.8.8 via 1.2.3.4 dev eth0 src ...
		fields := strings.Fields(string(out))
		for i, f := range fields {
			if f == "dev" && i+1 < len(fields) {
				return fields[i+1]
			}
		}
	}
	// fallback: 找 /proc/net/dev 第一个非 lo 网卡
	if names, err := listIfaces(); err == nil {
		for _, n := range names {
			if n != "lo" {
				return n
			}
		}
	}
	return "lo"
}

func listIfaces() ([]string, error) {
	f, err := os.Open("/proc/net/dev")
	if err != nil {
		return nil, err
	}
	defer f.Close()
	sc := bufio.NewScanner(f)
	var names []string
	for sc.Scan() {
		line := sc.Text()
		idx := strings.Index(line, ":")
		if idx <= 0 {
			continue
		}
		name := strings.TrimSpace(line[:idx])
		if name == "" {
			continue
		}
		names = append(names, name)
	}
	return names, sc.Err()
}

// 读 /proc/net/dev 拿指定网卡的累计 rx/tx 字节数
func readIfaceBytes(name string) (rx, tx uint64, err error) {
	f, err := os.Open("/proc/net/dev")
	if err != nil {
		return 0, 0, err
	}
	defer f.Close()
	sc := bufio.NewScanner(f)
	for sc.Scan() {
		line := sc.Text()
		idx := strings.Index(line, ":")
		if idx <= 0 {
			continue
		}
		if strings.TrimSpace(line[:idx]) != name {
			continue
		}
		fields := strings.Fields(line[idx+1:])
		if len(fields) < 16 {
			return 0, 0, fmt.Errorf("malformed /proc/net/dev line")
		}
		rx, _ = strconv.ParseUint(fields[0], 10, 64)
		tx, _ = strconv.ParseUint(fields[8], 10, 64)
		return rx, tx, nil
	}
	return 0, 0, fmt.Errorf("iface %s not found", name)
}

// /proc/stat 第一行: cpu user nice system idle iowait irq softirq steal ...
func readCPU() (total, idle uint64, err error) {
	f, err := os.Open("/proc/stat")
	if err != nil {
		return 0, 0, err
	}
	defer f.Close()
	sc := bufio.NewScanner(f)
	if !sc.Scan() {
		return 0, 0, fmt.Errorf("empty /proc/stat")
	}
	fields := strings.Fields(sc.Text())
	if len(fields) < 5 || fields[0] != "cpu" {
		return 0, 0, fmt.Errorf("bad /proc/stat: %s", sc.Text())
	}
	var sum uint64
	for i := 1; i < len(fields); i++ {
		v, _ := strconv.ParseUint(fields[i], 10, 64)
		sum += v
	}
	idleV, _ := strconv.ParseUint(fields[4], 10, 64)
	return sum, idleV, nil
}

// /proc/meminfo: 字节
func readMem() (used, total uint64, err error) {
	f, err := os.Open("/proc/meminfo")
	if err != nil {
		return 0, 0, err
	}
	defer f.Close()
	sc := bufio.NewScanner(f)
	var memTotalKB, memAvailKB uint64
	for sc.Scan() {
		line := sc.Text()
		parts := strings.Fields(line)
		if len(parts) < 2 {
			continue
		}
		switch parts[0] {
		case "MemTotal:":
			memTotalKB, _ = strconv.ParseUint(parts[1], 10, 64)
		case "MemAvailable:":
			memAvailKB, _ = strconv.ParseUint(parts[1], 10, 64)
		}
		if memTotalKB > 0 && memAvailKB > 0 {
			break
		}
	}
	total = memTotalKB * 1024
	if memAvailKB > memTotalKB {
		memAvailKB = memTotalKB
	}
	used = (memTotalKB - memAvailKB) * 1024
	return used, total, nil
}

// /proc/meminfo: 读 swap 字节. 没开启 swap 时 total=0
func readSwap() (used, total uint64, err error) {
	f, err := os.Open("/proc/meminfo")
	if err != nil {
		return 0, 0, err
	}
	defer f.Close()
	sc := bufio.NewScanner(f)
	var swapTotalKB, swapFreeKB uint64
	got := 0
	for sc.Scan() {
		line := sc.Text()
		parts := strings.Fields(line)
		if len(parts) < 2 {
			continue
		}
		switch parts[0] {
		case "SwapTotal:":
			swapTotalKB, _ = strconv.ParseUint(parts[1], 10, 64)
			got++
		case "SwapFree:":
			swapFreeKB, _ = strconv.ParseUint(parts[1], 10, 64)
			got++
		}
		if got >= 2 {
			break
		}
	}
	total = swapTotalKB * 1024
	if swapFreeKB > swapTotalKB {
		swapFreeKB = swapTotalKB
	}
	used = (swapTotalKB - swapFreeKB) * 1024
	return used, total, nil
}

// 根分区磁盘使用情况
func readDisk() (used, total uint64, err error) {
	var st syscall.Statfs_t
	if err = syscall.Statfs("/", &st); err != nil {
		return 0, 0, err
	}
	total = st.Blocks * uint64(st.Bsize)
	free := st.Bavail * uint64(st.Bsize)
	if free > total {
		free = total
	}
	used = total - free
	return used, total, nil
}

func readUptime() (int64, error) {
	b, err := os.ReadFile("/proc/uptime")
	if err != nil {
		return 0, err
	}
	parts := strings.Fields(string(b))
	if len(parts) == 0 {
		return 0, fmt.Errorf("empty uptime")
	}
	f, err := strconv.ParseFloat(parts[0], 64)
	if err != nil {
		return 0, err
	}
	return int64(f), nil
}

// readConnState 用 goss (netlink INET_DIAG) 数 IPv4+IPv6 的 TCP/UDP socket 数
// 比 /proc/net/sockstat 的 inuse 准: sockstat 漏算 UDP 的 UNCONN 状态
// goss 任一调用失败,该协议族就计 0,不做 /proc fallback (老 fallback 在十万 socket 机器上要 fork 数秒)
//
// TCP 只计 ESTABLISHED 状态 (跟哪吓服务端 net.Connections 默认显示对齐):
// 高流量代理机 TIME_WAIT 占 80%+,全计会虚高,只看真正在传的连接更有意义.
// UDP 保留总数不过滤: soga 等 UDP 中转都是 UNCONN socket (State != ESTABLISHED),
// 滤了直接归 0.
func countEstablishedTCP(ms []*goss.InetDiagMsg) uint64 {
	var n uint64
	for _, m := range ms {
		if goss.TCPState(m.State) == goss.TCP_ESTABLISHED {
			n++
		}
	}
	return n
}

func readConnState() (tcp, udp uint64) {
	if s, err := goss.ConnectionsWithProtocol(goss.AF_INET, syscall.IPPROTO_TCP); err == nil {
		tcp += countEstablishedTCP(s)
	}
	if s, err := goss.ConnectionsWithProtocol(goss.AF_INET6, syscall.IPPROTO_TCP); err == nil {
		tcp += countEstablishedTCP(s)
	}
	if s, err := goss.ConnectionsWithProtocol(goss.AF_INET, syscall.IPPROTO_UDP); err == nil {
		udp += uint64(len(s))
	}
	if s, err := goss.ConnectionsWithProtocol(goss.AF_INET6, syscall.IPPROTO_UDP); err == nil {
		udp += uint64(len(s))
	}
	return tcp, udp
}

// metricsLoop 每 interval 采一次样并通过 send 推出
// 第一次采样不发（要算 delta）
func metricsLoop(done chan struct{}, interval time.Duration, send func(any) error) {
	loadCPUInfo()
	loadOSPretty()
	var (
		prevRx, prevTx       uint64
		prevTotal, prevIdle  uint64
		prevTs               time.Time
		hasPrev              bool
	)

	t := time.NewTicker(interval)
	defer t.Stop()

	for {
		select {
		case <-done:
			return
		case now := <-t.C:
			iface := getIface()
			if iface == "" {
				iface = detectDefaultIface()
				setIface(iface)
			}

			rx, tx, errN := readIfaceBytes(iface)
			cpuTotal, cpuIdle, errC := readCPU()
			memUsed, memTotal, errM := readMem()
			swapUsed, swapTotal, _ := readSwap()
			diskUsed, diskTotal, _ := readDisk()
			upt, errU := readUptime()
			tcpConn, udpConn := readConnState()

			if errN != nil || errC != nil || errM != nil || errU != nil {
				// 网卡名错也只是 0 速率，不致命；继续采样
			}

			var rxBps, txBps uint64
			var cpuPct float64
			if hasPrev {
				dt := now.Sub(prevTs).Seconds()
				if dt > 0 {
					if rx >= prevRx {
						rxBps = uint64(float64(rx-prevRx) * 8 / dt)
					}
					if tx >= prevTx {
						txBps = uint64(float64(tx-prevTx) * 8 / dt)
					}
				}
				dTotal := cpuTotal - prevTotal
				dIdle := cpuIdle - prevIdle
				if dTotal > 0 {
					cpuPct = float64(dTotal-dIdle) / float64(dTotal) * 100.0
					if cpuPct < 0 {
						cpuPct = 0
					}
					if cpuPct > 100 {
						cpuPct = 100
					}
				}

				_ = send(metricsMsg{
					Type:     "metrics",
					Ts:       now.Unix(),
					Iface:    iface,
					RxBps:    rxBps,
					TxBps:    txBps,
					RxTotal:  rx,
					TxTotal:  tx,
					CPUPct:   cpuPct,
					CPUModel: cpuModelOnce,
					CPUCores: cpuCoresOnce,
					MemUsed:   memUsed,
					MemTotal:  memTotal,
					SwapUsed:  swapUsed,
					SwapTotal: swapTotal,
					DiskUsed:  diskUsed,
					DiskTotal: diskTotal,
					TCPConn:   tcpConn,
					UDPConn:   udpConn,
					Uptime:    upt,
					OSPretty:  osPrettyOnce,
				})
			}
			prevRx, prevTx = rx, tx
			prevTotal, prevIdle = cpuTotal, cpuIdle
			prevTs = now
			hasPrev = true
		}
	}
}
