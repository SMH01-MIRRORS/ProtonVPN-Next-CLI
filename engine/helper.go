package main

import (
	"bufio"
	"encoding/base64"
	"encoding/hex"
	"flag"
	"fmt"
	"os"
	"os/signal"
	"strings"
	"syscall"

	"github.com/amnezia-vpn/amneziawg-go/conn"
	"github.com/amnezia-vpn/amneziawg-go/device"
	"github.com/amnezia-vpn/amneziawg-go/tun"
	"github.com/vishvananda/netlink"
)

func main() {
	ifaceName := flag.String("if", "awg0", "Interface name")
	addr := flag.String("addr", "10.2.0.2/32", "Local IP address with CIDR")
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

	fmt.Printf("Starting VPN helper for %s (%s)...\n", *ifaceName, *addr)

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

	// 2. Setup IP address and bring interface UP using netlink
	link, err := netlink.LinkByName(*ifaceName)
	if err != nil {
		fmt.Fprintf(os.Stderr, "Failed to find link %s: %v\n", *ifaceName, err)
		tdev.Close()
		os.Exit(2)
	}

	ipAddr, err := netlink.ParseAddr(*addr)
	if err != nil {
		fmt.Fprintf(os.Stderr, "Failed to parse address %s: %v\n", *addr, err)
		tdev.Close()
		os.Exit(3)
	}

	if err := netlink.AddrAdd(link, ipAddr); err != nil {
		fmt.Fprintf(os.Stderr, "Failed to set address: %v\n", err)
		tdev.Close()
		os.Exit(4)
	}

	if err := netlink.LinkSetUp(link); err != nil {
		fmt.Fprintf(os.Stderr, "Failed to bring up link: %v\n", err)
		tdev.Close()
		os.Exit(5)
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
	fmt.Println("VPN Tunnel is UP and running.")

	// Wait for termination signal
	sigChan := make(chan os.Signal, 1)
	signal.Notify(sigChan, syscall.SIGTERM, syscall.SIGINT)

	<-sigChan
	fmt.Println("Shutting down VPN helper...")
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

func toHex(b64 string) string {
	b, err := base64.StdEncoding.DecodeString(b64)
	if err != nil {
		return b64 // fallback
	}
	return hex.EncodeToString(b)
}
