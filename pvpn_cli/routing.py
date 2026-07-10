import sys
import subprocess
import os
import json
import urllib.request
import platform
import socket
from typing import Optional, List, Tuple

def get_config_dir() -> str:
    if "PVPN_CONFIG_DIR" in os.environ:
        return os.environ["PVPN_CONFIG_DIR"]
        
    if platform.system() == "Windows":
        base = os.environ.get("APPDATA", os.path.expanduser("~"))
        d = os.path.join(base, "pvpn-next")
    else:
        orig_user = os.environ.get("SUDO_USER") or os.environ.get("DOAS_USER")
        if not orig_user and os.geteuid() == 0:
            try:
                orig_user = os.getlogin()
            except Exception:
                try:
                    import subprocess
                    orig_user = subprocess.run(["logname"], capture_output=True, text=True).stdout.strip()
                except Exception:
                    pass
        if orig_user and orig_user != "root":
            import pwd
            try:
                home = pwd.getpwnam(orig_user).pw_dir
            except KeyError:
                home = os.path.expanduser("~")
        else:
            home = os.path.expanduser("~")
        d = os.path.join(home, ".config/pvpn-next")
    os.makedirs(d, exist_ok=True)
    return d

class RoutingManager:
    def __init__(self, elevate_cmd: str):
        self.elevate_cmd = elevate_cmd
        self.state_file = os.path.join(get_config_dir(), "routing_state.json")
        self.is_windows = sys.platform == "win32"

    def _elevate(self, cmd: list) -> list:
        if self.elevate_cmd:
            return [self.elevate_cmd] + cmd
        return cmd

    def _run_cmd(self, cmd: list) -> str:
        try:
            kwargs = {"check": True, "capture_output": True, "text": True, "errors": "ignore"}
            if self.is_windows:
                kwargs["creationflags"] = 0x08000000
            result = subprocess.run(cmd, **kwargs)
            return result.stdout.strip()
        except subprocess.CalledProcessError as e:
            print(f"[ERROR] Command failed: {' '.join(cmd)}")
            print(f"Error output: {e.stderr}")
            return ""

    def _get_linux_default_gateway(self) -> tuple[Optional[str], Optional[str]]:
        # Format: default via 192.168.1.1 dev wlp2s0 proto dhcp metric 600
        output = subprocess.run(["ip", "route", "show", "default"], capture_output=True, text=True).stdout
        if not output:
            return None, None
            
        parts = output.split()
        gw = None
        iface = None
        
        try:
            if "via" in parts:
                gw_idx = parts.index("via") + 1
                gw = parts[gw_idx]
            if "dev" in parts:
                dev_idx = parts.index("dev") + 1
                iface = parts[dev_idx]
            return gw, iface
        except IndexError:
            return None, None

    def _get_windows_default_gateway(self) -> Tuple[Optional[str], Optional[str], Optional[str]]:
        kwargs = {"capture_output": True, "text": True, "shell": True, "errors": "ignore"}
        if self.is_windows:
            kwargs["creationflags"] = 0x08000000
        ps_cmd = "Get-NetRoute -DestinationPrefix '0.0.0.0/0' | Sort-Object RouteMetric | Select-Object -First 1 | ForEach-Object { $ip = (Get-NetIPAddress -InterfaceIndex $_.InterfaceIndex -AddressFamily IPv4)[0].IPAddress; $_.NextHop + ',' + $_.InterfaceIndex + ',' + $ip }"
        output = subprocess.run(["powershell", "-NoProfile", "-Command", ps_cmd], **kwargs).stdout.strip()
        if "," in output:
            parts = output.split(",")
            if len(parts) >= 3:
                return parts[0].strip(), parts[1].strip(), parts[2].strip()
        return None, None, None

    def _get_windows_iface_index(self, iface_name: str) -> Optional[str]:
        kwargs = {"capture_output": True, "text": True, "errors": "ignore"}
        if self.is_windows:
            kwargs["creationflags"] = 0x08000000
        try:
            output = subprocess.run(["netsh", "interface", "ipv4", "show", "interfaces"], **kwargs).stdout
            if output:
                for line in output.split('\n'):
                    if iface_name in line:
                        parts = line.split()
                        if len(parts) >= 1:
                            return parts[0]
        except Exception as e:
            print(f"[WARNING] _get_windows_iface_index failed: {e}")
        return None

    def _get_split_config(self):
        config_path = os.path.join(get_config_dir(), "split_tunnel.json")
        if os.path.exists(config_path):
            try:
                with open(config_path, "r") as f:
                    return json.load(f)
            except:
                pass
        return {"exclude_lan": False, "split_items": []}

    def _resolve_ips(self, items):
        ips = []
        apps = []
        for item in items:
            if item["type"] == "ip":
                ips.append(item["value"])
            elif item["type"] == "domain":
                try:
                    resolved = socket.gethostbyname_ex(item["value"])[2]
                    ips.extend(resolved)
                    print(f"-> Resolved domain {item['value']} to {resolved}")
                except socket.gaierror:
                    print(f"[WARNING] Could not resolve domain: {item['value']}")
            elif item["type"] == "app":
                apps.append(item["value"])
        return ips, apps

    def start_vpn(self, vpn_ip: str, engine_path: str, config_path: str, log_path: str, awg_ip: str = "10.2.0.2", awg_iface: str = "awg0", dns_ips: str = "10.2.0.1"):
        print(f"-> Setting up traffic routing for {vpn_ip}...")
        
        split_cfg = self._get_split_config()
        exclude_ips, exclude_apps = self._resolve_ips(split_cfg.get("split_items", []))
        exclude_lan = split_cfg.get("exclude_lan", False)
        dns_list = [ip.strip() for ip in dns_ips.split(",") if ip.strip()]
        
        state = {"vpn_ip": vpn_ip, "gw": None, "iface": None, "os": sys.platform, "ips": exclude_ips, "exclude_lan": exclude_lan, "cgroup_created": False, "dns_list": dns_list}
        
        if self.is_windows:
            gw, phys_idx, phys_ip = self._get_windows_default_gateway()
            if not gw:
                print("[ERROR] Could not detect default Windows gateway.")
                sys.exit(1)
                
            state["gw"] = gw
            with open(self.state_file, "w") as f:
                json.dump(state, f)
            
            # Windows execution (assumes running as Admin)
            client_log_path = log_path.replace("awg.log", "client.log")
            
            # Write DNS debug info BEFORE starting the engine to avoid file lock issues
            with open(client_log_path, "w") as f:
                f.write("--- VPN Startup ---\n")
                if dns_list:
                    f.write("\n--- DNS Debug Information ---\n")
                    try:
                        ps_dns_cmd = "Get-DnsClientServerAddress -AddressFamily IPv4 | Where-Object { $_.ServerAddresses -ne $null } | Select-Object InterfaceAlias, ServerAddresses | Out-String"
                        dns_info = subprocess.run(["powershell", "-NoProfile", "-Command", ps_dns_cmd], capture_output=True, text=True, errors="ignore", creationflags=0x08000000).stdout
                        if dns_info:
                            f.write(f"Physical DNS servers before VPN:\n{dns_info.strip()}\n")
                    except Exception as e:
                        pass
                    f.write("\n--- DNS Setup (Windows) ---\n")

            with open(log_path, "w") as log_file:
                client_log_fd = open(client_log_path, "a")
                # Use subprocess to start engine in background without blocking Python script
                kwargs = {"stdin": open(config_path, "r"), "stdout": log_file, "stderr": client_log_fd, "close_fds": True}
                if self.is_windows:
                    # CREATE_NO_WINDOW (0x08000000) | CREATE_NEW_PROCESS_GROUP (0x00000200)
                    kwargs["creationflags"] = 0x08000200
                    
                    try:
                        self._run_cmd(["netsh", "advfirewall", "firewall", "add", "rule", "name=pvpn-engine", "dir=in", "action=allow", f"program={engine_path}", "enable=yes"])
                        self._run_cmd(["netsh", "advfirewall", "firewall", "add", "rule", "name=pvpn-engine", "dir=out", "action=allow", f"program={engine_path}", "enable=yes"])
                    except:
                        pass
                
                proc = subprocess.Popen([engine_path, "-dns", dns_ips], **kwargs)
                
                iface_idx = None
                for _ in range(15):
                    iface_idx = self._get_windows_iface_index("awg0")
                    if iface_idx:
                        break
                    import time
                    time.sleep(1.0)
                    
                if not iface_idx:
                    print("[WARNING] Wintun interface 'awg0' did not appear in time. Traffic routing might fail.")
                
                # Setup routes
                self._run_cmd(["route", "ADD", vpn_ip, "MASK", "255.255.255.255", gw])
                
                for ip in exclude_ips:
                    self._run_cmd(["route", "ADD", ip, "MASK", "255.255.255.255", gw])
                if exclude_lan:
                    self._run_cmd(["route", "ADD", "10.0.0.0", "MASK", "255.0.0.0", gw])
                    self._run_cmd(["route", "ADD", "172.16.0.0", "MASK", "255.240.0.0", gw])
                    self._run_cmd(["route", "ADD", "192.168.0.0", "MASK", "255.255.0.0", gw])
                
                if iface_idx:
                    self._run_cmd(["route", "ADD", "0.0.0.0", "MASK", "128.0.0.0", "0.0.0.0", "IF", iface_idx])
                    self._run_cmd(["route", "ADD", "128.0.0.0", "MASK", "128.0.0.0", "0.0.0.0", "IF", iface_idx])
                    
                    try:
                        self._run_cmd(["netsh", "interface", "ipv6", "add", "route", "::/0", awg_iface, "metric=1"])
                    except:
                        pass
                    
                    if dns_list:
                        with open(client_log_path, "a") as f:
                            try:
                                # Clean up any stale NRPT rules from old versions
                                clean_cmd = "Get-DnsClientNrptRule | Where-Object { $_.Comment -eq 'PVPN-Next' } | Remove-DnsClientNrptRule -Force"
                                subprocess.run(["powershell", "-NoProfile", "-Command", clean_cmd], creationflags=0x08000000)
                                f.write("Cleared any stale NRPT rules.\n")
                            except Exception as e:
                                pass
                                
                            try:
                                # Assign custom DNS to Wintun
                                self._run_cmd(["netsh", "interface", "ipv4", "set", "dnsservers", f'name="{awg_iface}"', "static", dns_list[0], "primary"])
                                f.write("Set Wintun primary DNS.\n")
                                if len(dns_list) > 1:
                                    for idx, dns_ip in enumerate(dns_list[1:], start=2):
                                        self._run_cmd(["netsh", "interface", "ipv4", "add", "dnsservers", f'name="{awg_iface}"', dns_ip, f"index={idx}"])
                                        f.write(f"Added secondary DNS: {dns_ip}\n")
                            except Exception as e:
                                f.write(f"Exception during Wintun DNS assignment: {e}\n")
                            f.write("--- End DNS Setup ---\n")
                else:
                    self._run_cmd(["route", "ADD", "0.0.0.0", "MASK", "128.0.0.0", awg_ip])
                    self._run_cmd(["route", "ADD", "128.0.0.0", "MASK", "128.0.0.0", awg_ip])
                
                print("-> Routing configured successfully. All traffic is now secured.")
                print("-> VPN is running in the background. Use 'disconnect' to stop.")
            
        else:
            gw, iface = self._get_linux_default_gateway()
            if not gw or not iface:
                print("[ERROR] Could not detect default Linux gateway.")
                sys.exit(1)
                
            state["gw"] = gw
            state["iface"] = iface
            
            # Setup bash commands for excluded IPs and LAN
            split_cmds = []
            for ip in exclude_ips:
                split_cmds.append(f"ip route add {ip} via {gw} dev {iface}")
            if exclude_lan:
                split_cmds.append(f"ip route add 10.0.0.0/8 via {gw} dev {iface}")
                split_cmds.append(f"ip route add 172.16.0.0/12 via {gw} dev {iface}")
                split_cmds.append(f"ip route add 192.168.0.0/16 via {gw} dev {iface}")
                
            if exclude_apps:
                state["cgroup_created"] = True
                # Setup cgroup v2 hierarchy and routing
                split_cmds.extend([
                    "mkdir -p /sys/fs/cgroup/protonvpn_exclude",
                    # Enable fwmark matching for this cgroup
                    "iptables -t mangle -C OUTPUT -m cgroup --path protonvpn_exclude -j MARK --set-mark 51820 2>/dev/null || iptables -t mangle -A OUTPUT -m cgroup --path protonvpn_exclude -j MARK --set-mark 51820",
                    "ip rule add fwmark 51820 table 200",
                    f"ip route add default via {gw} dev {iface} table 200"
                ])
                
            split_cmds_str = "\n".join(split_cmds)
            
            # --- Stateless IPv6 and DNS ---
            # We don't save any state because routes attached to awg0 will automatically vanish when it dies.
            state["ipv6_disabled_originally"] = False
            state["dns_backup"] = False
            
            with open(self.state_file, "w") as f:
                json.dump(state, f)
            client_log_path = log_path.replace("awg.log", "client.log")
            
            dns_setup_script = ""
            if dns_list:
                dns_ips_space = " ".join(dns_list)
                dns_setup_script = f"""
if command -v resolvectl >/dev/null 2>&1; then
    resolvectl dns {awg_iface} {dns_ips_space}
    resolvectl domain {awg_iface} ~\.
    PHYSICAL_DNS=$(resolvectl status {iface} 2>/dev/null | grep 'DNS Servers' | awk '{{print $3, $4, $5}}')
else
    PHYSICAL_DNS=$(grep -Eo '[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+' /etc/resolv.conf 2>/dev/null)
fi

for ip in $PHYSICAL_DNS; do
    if [[ "$ip" != 127.* ]]; then
        ip route add $ip/32 dev {awg_iface} 2>/dev/null || true
    fi
done
"""
                    
            script = f"""
nohup {engine_path} -dns "{dns_ips}" < "{config_path}" > "{log_path}" 2> "{client_log_path}" &
# Wait for interface
for i in $(seq 1 30); do
    ip link show {awg_iface} >/dev/null 2>&1 && break
    sleep 0.5
done
ip -6 route add default dev {awg_iface} metric 1 2>/dev/null || true
{dns_setup_script}
ip route add {vpn_ip} via {gw} dev {iface}
{split_cmds_str}
ip route add 0.0.0.0/1 dev {awg_iface}
ip route add 128.0.0.0/1 dev {awg_iface}
echo "-> Routing configured successfully. All traffic is now secured."
echo "-> VPN is running in the background. Use 'disconnect' to stop."
"""
            subprocess.run(self._elevate(["sh", "-c", script]))
            
            # Launch PID scanner in background if needed
            if exclude_apps:
                scanner_cmd = [sys.executable, os.path.realpath(sys.argv[0]), "_pid-scanner"]
                subprocess.Popen(scanner_cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def teardown_routing(self):
        if not os.path.exists(self.state_file):
            return
            
        try:
            with open(self.state_file, "r") as f:
                state = json.load(f)
        except:
            return
            
        vpn_ip = state.get("vpn_ip")
        exclude_ips = state.get("ips", [])
        exclude_lan = state.get("exclude_lan", False)
        
        if not vpn_ip:
            return
            
        print("-> Tearing down VPN routing...")
        
        try:
            if state.get("os") == "win32":
                gw = state.get("gw")
                self._run_cmd(["route", "DELETE", vpn_ip, "MASK", "255.255.255.255", gw])
                for ip in exclude_ips:
                    self._run_cmd(["route", "DELETE", ip, "MASK", "255.255.255.255", gw])
                if exclude_lan:
                    self._run_cmd(["route", "DELETE", "10.0.0.0", "MASK", "255.0.0.0"])
                    self._run_cmd(["route", "DELETE", "172.16.0.0", "MASK", "255.240.0.0"])
                    self._run_cmd(["route", "DELETE", "192.168.0.0", "MASK", "255.255.0.0"])
                self._run_cmd(["route", "DELETE", "0.0.0.0", "MASK", "128.0.0.0"])
                self._run_cmd(["route", "DELETE", "128.0.0.0", "MASK", "128.0.0.0"])
                
                # Cleanup any stale NRPT rule just in case
                clean_cmd = "Get-DnsClientNrptRule | Where-Object { $_.Comment -eq 'PVPN-Next' } | Remove-DnsClientNrptRule -Force"
                subprocess.run(["powershell", "-NoProfile", "-Command", clean_cmd], creationflags=0x08000000)
            else:
                gw = state.get("gw")
                iface = state.get("iface")
                if gw and iface:
                    subprocess.run(self._elevate(["ip", "route", "del", vpn_ip, "via", gw, "dev", iface]), capture_output=True)
                    for ip in exclude_ips:
                        subprocess.run(self._elevate(["ip", "route", "del", ip, "via", gw, "dev", iface]), capture_output=True)
                    if exclude_lan:
                        subprocess.run(self._elevate(["ip", "route", "del", "10.0.0.0/8", "via", gw, "dev", iface]), capture_output=True)
                        subprocess.run(self._elevate(["ip", "route", "del", "172.16.0.0/12", "via", gw, "dev", iface]), capture_output=True)
                        subprocess.run(self._elevate(["ip", "route", "del", "192.168.0.0/16", "via", gw, "dev", iface]), capture_output=True)
                        
                if state.get("cgroup_created"):
                    subprocess.run(self._elevate(["iptables", "-t", "mangle", "-D", "OUTPUT", "-m", "cgroup", "--path", "protonvpn_exclude", "-j", "MARK", "--set-mark", "51820"]), capture_output=True)
                    subprocess.run(self._elevate(["ip", "rule", "del", "fwmark", "51820", "table", "200"]), capture_output=True)
                    subprocess.run(self._elevate(["rmdir", "/sys/fs/cgroup/protonvpn_exclude"]), capture_output=True)
                
                subprocess.run(self._elevate(["ip", "route", "del", "0.0.0.0/1"]), capture_output=True)
                subprocess.run(self._elevate(["ip", "route", "del", "128.0.0.0/1"]), capture_output=True)
            
            if os.path.exists(self.state_file):
                os.remove(self.state_file)
        except:
            pass
