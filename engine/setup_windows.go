//go:build windows

package main

import (
	"fmt"
	"os"
	"os/exec"
	"strings"

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

	// On Windows, use netsh to configure the Wintun adapter IP
	cmd := exec.Command("netsh", "interface", "ipv4", "set", "address",
		fmt.Sprintf("name=%s", ifaceName),
		"static", ip, mask)
		
	output, err := cmd.CombinedOutput()
	if err != nil {
		return fmt.Errorf("netsh failed to set address: %s (%v)", string(output), err)
	}
	
	// netsh interface ipv4 set subinterface "name" mtu=1280 store=persistent
	// This is typically optional but good practice if we want to enforce MTU.
	return nil
}

func setupDNSFirewall(tdev tun.Device) {
	if nativeTun, ok := tdev.(*tun.NativeTun); ok {
		luid := nativeTun.LUID()
		fmt.Fprintf(os.Stderr, "[Engine] Enabling WFP Stateless DNS Block for TUN LUID %d...\n", luid)
		if err := wfp.BlockDNS(luid); err != nil {
			fmt.Fprintf(os.Stderr, "[Engine] [WARNING] Failed to enable WFP DNS block: %v\n", err)
		} else {
			fmt.Fprintf(os.Stderr, "[Engine] WFP Stateless DNS Block is ACTIVE.\n")
		}
	} else {
		fmt.Fprintf(os.Stderr, "[Engine] [WARNING] Device is not NativeTun, cannot enable WFP DNS block.\n")
	}
}

