//go:build linux

package main

import (
	"fmt"
	"github.com/vishvananda/netlink"
	"github.com/amnezia-vpn/amneziawg-go/tun"
)

func setupInterface(ifaceName string, addr string) error {
	link, err := netlink.LinkByName(ifaceName)
	if err != nil {
		return fmt.Errorf("failed to find link %s: %w", ifaceName, err)
	}

	ipAddr, err := netlink.ParseAddr(addr)
	if err != nil {
		return fmt.Errorf("failed to parse address %s: %w", addr, err)
	}

	if err := netlink.AddrAdd(link, ipAddr); err != nil {
		return fmt.Errorf("failed to set address: %w", err)
	}

	if err := netlink.LinkSetUp(link); err != nil {
		return fmt.Errorf("failed to bring up link: %w", err)
	}

	return nil
}

func setupDNSFirewall(tdev tun.Device) {
	// Not needed on Linux. Handled via standard routing table and cgroups.
}
