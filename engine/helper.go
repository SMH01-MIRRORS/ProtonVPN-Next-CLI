package main

import (
	"bufio"
	"encoding/base64"
	"encoding/hex"
	"flag"
	"fmt"
	"io"
	"net"
	"os"
	"os/signal"
	"strconv"
	"strings"
	"syscall"

	"github.com/amnezia-vpn/amneziawg-go/conn"
	"github.com/amnezia-vpn/amneziawg-go/device"
	"github.com/amnezia-vpn/amneziawg-go/tun"
)

func main() {
	ifaceName := flag.String("if", "awg0", "Interface name")
	addr := flag.String("addr", "10.2.0.2/32", "Local IP address with CIDR")
	dnsServers := flag.String("dns", "", "Comma-separated list of DNS servers to allow")
	mtu := flag.Int("mtu", 1280, "Interface MTU")
	flag.Parse()

	// Read config from stdin until delimiter
	var configBuilder strings.Builder
	scanner := bufio.NewScanner(os.Stdin)
	for scanner.Scan() {
		line := scanner.Text()
		if line == "---END---" {
			break
		}
		configBuilder.WriteString(line + "\n")
	}
	config := configBuilder.String()

	// The CLI writes the user's MTU setting into the tunnel config and UAPI has
	// no field to carry it, so the value is taken from there before the
	// interface is created. Without this the setting is silently ignored.
	if configured := mtuFromConfig(config); configured > 0 {
		*mtu = configured
	}

	fmt.Fprintf(os.Stderr, "[Engine] Starting VPN helper for %s (%s, MTU %d)...\n", *ifaceName, *addr, *mtu)

	// 1. Create TUN device
	tdev, err := tun.CreateTUN(*ifaceName, *mtu)
	if err != nil {
		fmt.Fprintf(os.Stderr, "Failed to create TUN: %v\n", err)
		os.Exit(1)
	}

	realName, err := tdev.Name()
	if err == nil {
		*ifaceName = realName
	}

	// 2. Setup IP address, MTU and bring interface UP using OS-specific setup
	if err := setupInterface(*ifaceName, *addr, *mtu); err != nil {
		fmt.Fprintf(os.Stderr, "Failed to setup interface: %v\n", err)
		tdev.Close()
		os.Exit(2)
	}

	// 3. Initialize AmneziaWG device
	logger := device.NewLogger(device.LogLevelVerbose, fmt.Sprintf("(%s) ", *ifaceName))
	dev := device.NewDevice(tdev, conn.NewDefaultBind(), logger)

	// 4. Apply UAPI config
	uapiConfig := configToUapi(config)
	if err := dev.IpcSet(uapiConfig); err != nil {
		fmt.Fprintf(os.Stderr, "Failed to set UAPI config: %v\n", err)
		dev.Close()
		os.Exit(6)
	}

	dev.Up()
	fmt.Fprintf(os.Stderr, "[Engine] VPN Tunnel is UP and running.\n")
	setupDNSFirewall(tdev, *dnsServers)

	// Wait for termination signal
	sigChan := make(chan os.Signal, 1)
	signal.Notify(sigChan, syscall.SIGTERM, syscall.SIGINT)

	// Start IPC Listener
	go func() {
		l, err := net.Listen("tcp", "127.0.0.1:34116")
		if err != nil {
			fmt.Fprintf(os.Stderr, "Failed to listen on IPC port: %v\n", err)
			return
		}
		defer l.Close()
		for {
			c, err := l.Accept()
			if err != nil {
				continue
			}
			go func(conn net.Conn) {
				defer conn.Close()
				data, err := io.ReadAll(conn)
				if err != nil {
					return
				}
				configStr := string(data)
				if strings.TrimSpace(configStr) == "DISCONNECT" {
					dev.Down()
					fmt.Fprintf(os.Stderr, "[Engine] Interface marked DOWN via IPC.\n")
					conn.Write([]byte("OK\n"))
					sigChan <- syscall.SIGTERM
					return
				}
				
				// Apply new config
				uapi := configToUapi(configStr)
				uapi = "replace_peers=true\n" + uapi
				
				dev.Down() 
				if err := dev.IpcSet(uapi); err != nil {
					fmt.Fprintf(os.Stderr, "IPC config update failed: %v\n", err)
					conn.Write([]byte("ERROR: " + err.Error() + "\n"))
					return
				}
				dev.Up()
				fmt.Fprintf(os.Stderr, "[Engine] Configuration updated via IPC. Interface UP.\n")
				conn.Write([]byte("OK\n"))
			}(c)
		}
	}()

	<-sigChan
	fmt.Fprintf(os.Stderr, "[Engine] Shutting down VPN helper...\n")
	dev.Close()
}

func configToUapi(config string) string {
	lines := strings.Split(config, "\n")
	uapi := ""
	inPeer := false

	for _, line := range lines {
		trimmed := strings.TrimSpace(line)
		if trimmed == "" || strings.HasPrefix(trimmed, "#") {
			continue
		}

		if strings.EqualFold(trimmed, "[Interface]") {
			inPeer = false
			continue
		}
		if strings.EqualFold(trimmed, "[Peer]") {
			inPeer = true
			continue
		}

		parts := strings.SplitN(trimmed, "=", 2)
		if len(parts) != 2 {
			continue
		}
		key := strings.ToLower(strings.TrimSpace(parts[0]))
		value := strings.TrimSpace(parts[1])

		switch key {
		case "privatekey":
			uapi += "private_key=" + toHex(value) + "\n"
		case "listenport":
			uapi += "listen_port=" + value + "\n"
		case "publickey":
			if inPeer {
				uapi += "public_key=" + toHex(value) + "\n"
			}
		case "endpoint":
			uapi += "endpoint=" + value + "\n"
		case "allowedips":
			ips := strings.Split(value, ",")
			for _, ip := range ips {
				uapi += "allowed_ip=" + strings.TrimSpace(ip) + "\n"
			}
		case "persistentkeepalive":
			uapi += "persistent_keepalive_interval=" + value + "\n"
		case "jc", "jmin", "jmax", "s1", "s2", "s3", "s4", "h1", "h2", "h3", "h4", "i1", "i2", "i3", "i4", "i5":
			uapi += key + "=" + value + "\n"
		}
	}
	return uapi
}

// The engine takes an MTU flag, but the CLI delivers the user's MTU setting
// through the tunnel config, so the value is parsed out of there. The bounds
// reject a nonsensical value instead of handing it to the interface, because
// the CLI stores whatever the user typed without validating it.
func mtuFromConfig(config string) int {
	const (
		minMTU = 576
		maxMTU = 1500
	)

	for _, line := range strings.Split(config, "\n") {
		parts := strings.SplitN(strings.TrimSpace(line), "=", 2)
		if len(parts) != 2 || !strings.EqualFold(strings.TrimSpace(parts[0]), "mtu") {
			continue
		}
		value, err := strconv.Atoi(strings.TrimSpace(parts[1]))
		if err != nil || value < minMTU || value > maxMTU {
			continue
		}
		return value
	}

	return 0
}

func toHex(b64 string) string {
	b, err := base64.StdEncoding.DecodeString(b64)
	if err != nil {
		return b64 // fallback
	}
	return hex.EncodeToString(b)
}
