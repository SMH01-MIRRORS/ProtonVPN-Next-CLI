//go:build windows

package main

import (
	"fmt"
	"net/netip"
	"os"
	"os/exec"
	"strings"
	"syscall"
	"time"

	"pvpn-engine/wfp"

	"github.com/amnezia-vpn/amneziawg-go/tun"
)

func setupInterface(ifaceName string, addr string) error {
	// Addr is typically in CIDR format, e.g., "10.2.0.2/32"
	parts := strings.Split(addr, "/")
	ip := parts[0]
	// If it's a /32, mask is 255.255.255.255
	mask := "255.255.255.255"
	if len(parts) > 1 && parts[1] == "24" {
		mask = "255.255.255.0"
	}

	var err error
	var output []byte
	
	// Retry loop: Wintun interface might take a few seconds to register with Windows Networking
	for i := 0; i < 20; i++ {
		cmd := exec.Command("netsh", "interface", "ipv4", "set", "address",
			fmt.Sprintf("name=%s", ifaceName),
			"static", ip, mask)
		cmd.SysProcAttr = &syscall.SysProcAttr{HideWindow: true}
			
		output, err = cmd.CombinedOutput()
		if err == nil {
			return nil
		}
		time.Sleep(500 * time.Millisecond)
	}
	
	return fmt.Errorf("netsh failed to set address after retries: %s (%v)", string(output), err)
}

func setupDNSFirewall(tdev tun.Device, dnsList string) {
	if nativeTun, ok := tdev.(*tun.NativeTun); ok {
		luid := nativeTun.LUID()
		fmt.Fprintf(os.Stderr, "[Engine] Enabling WFP Stateless DNS Block for TUN LUID %d...\n", luid)
		
		var dnsAddrs []netip.Addr
		if dnsList != "" {
			for _, ipStr := range strings.Split(dnsList, ",") {
				ipStr = strings.TrimSpace(ipStr)
				if ipStr == "" {
					continue
				}
				if addr, err := netip.ParseAddr(ipStr); err == nil {
					dnsAddrs = append(dnsAddrs, addr)
				}
			}
		}

		if err := wfp.BlockDNS(luid, dnsAddrs); err != nil {
			fmt.Fprintf(os.Stderr, "[Engine] [WARNING] Failed to enable WFP DNS block: %v\n", err)
		} else {
			fmt.Fprintf(os.Stderr, "[Engine] WFP Stateless DNS Block is ACTIVE. Allowed DNS: %v\n", dnsAddrs)
		}
	} else {
		fmt.Fprintf(os.Stderr, "[Engine] [WARNING] Device is not NativeTun, cannot enable WFP DNS block.\n")
	}
}

