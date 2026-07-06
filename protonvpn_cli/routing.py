import sys
import subprocess
import os
import json
import urllib.request
import platform
import socket
from typing import Optional, List

def get_config_dir() -> str:
    if "PROTONVPN_CONFIG_DIR" in os.environ:
        return os.environ["PROTONVPN_CONFIG_DIR"]
        
    if platform.system() == "Windows":
        base = os.environ.get("APPDATA", os.path.expanduser("~"))
        d = os.path.join(base, "protonvpn-next")
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
        d = os.path.join(home, ".config/protonvpn-next")
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
            kwargs = {"check": True, "capture_output": True, "text": True}
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

    def _get_windows_default_gateway(self) -> Optional[str]:
        kwargs = {"capture_output": True, "text": True}
        if self.is_windows:
            kwargs["creationflags"] = 0x08000000
        output = subprocess.run(["route", "print", "-4", "0.0.0.0"], **kwargs).stdout
        for line in output.split('\n'):
            line = line.strip()
            if line.startswith("0.0.0.0"):
                parts = line.split()
                if len(parts) >= 3:
                    return parts[2] # Gateway
        return None

    def _get_windows_iface_index(self, iface_name: str) -> Optional[str]:
        kwargs = {"capture_output": True, "text": True}
        if self.is_windows:
            kwargs["creationflags"] = 0x08000000
        output = subprocess.run(["netsh", "interface", "ipv4", "show", "interfaces"], **kwargs).stdout
        for line in output.split('\n'):
            if iface_name in line:
                parts = line.split()
                if len(parts) >= 1:
                    return parts[0]
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

    def start_vpn(self, vpn_ip: str, engine_path: str, config_path: str, log_path: str, awg_ip: str = "10.2.0.2", awg_iface: str = "awg0"):
        print(f"-> Setting up traffic routing for {vpn_ip}...")
        
        split_cfg = self._get_split_config()
        exclude_ips, exclude_apps = self._resolve_ips(split_cfg.get("split_items", []))
        exclude_lan = split_cfg.get("exclude_lan", False)
        
        state = {"vpn_ip": vpn_ip, "gw": None, "iface": None, "os": sys.platform, "ips": exclude_ips, "exclude_lan": exclude_lan, "cgroup_created": False}
        
        if self.is_windows:
            gw = self._get_windows_default_gateway()
            if not gw:
                print("[ERROR] Could not detect default Windows gateway.")
                sys.exit(1)
                
            state["gw"] = gw
            with open(self.state_file, "w") as f:
                json.dump(state, f)
            
            # Windows execution (assumes running as Admin)
            client_log_path = log_path.replace("awg.log", "client.log")
            with open(log_path, "w") as log_file:
                # Use subprocess to start engine in background without blocking Python script
                kwargs = {"stdin": open(config_path, "r"), "stdout": log_file, "stderr": open(client_log_path, "w")}
                if self.is_windows:
                    kwargs["creationflags"] = 0x08000000
                
                proc = subprocess.Popen([engine_path], **kwargs)
                
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
                
                # To prevent routing loop and ensure all traffic goes to Wintun
                if iface_idx:
                    self._run_cmd(["route", "ADD", "0.0.0.0", "MASK", "128.0.0.0", "0.0.0.0", "IF", iface_idx])
                    self._run_cmd(["route", "ADD", "128.0.0.0", "MASK", "128.0.0.0", "0.0.0.0", "IF", iface_idx])
                else:
                    self._run_cmd(["route", "ADD", "0.0.0.0", "MASK", "128.0.0.0", awg_ip])
                    self._run_cmd(["route", "ADD", "128.0.0.0", "MASK", "128.0.0.0", awg_ip])
                
                print("-> Routing configured successfully. All traffic is now secured.")
                print("-> Press Ctrl+C to disconnect.")
                try:
                    proc.wait()
                except KeyboardInterrupt:
                    proc.terminate()
            
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
            
            with open(self.state_file, "w") as f:
                json.dump(state, f)
            client_log_path = log_path.replace("awg.log", "client.log")
            script = f"""
nohup {engine_path} < "{config_path}" > "{log_path}" 2> "{client_log_path}" &
# Wait for interface
for i in $(seq 1 30); do
    ip link show {awg_iface} >/dev/null 2>&1 && break
    sleep 0.5
done
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
        gw = state.get("gw")
        iface = state.get("iface")
        ips = state.get("ips", [])
        exclude_lan = state.get("exclude_lan", False)
        cgroup_created = state.get("cgroup_created", False)
        
        if not vpn_ip:
            return
            
        print("-> Tearing down VPN routing...")
        
        if state.get("os") == "win32":
            self._run_cmd(["route", "DELETE", vpn_ip, "MASK", "255.255.255.255", gw])
            for ip in ips:
                self._run_cmd(["route", "DELETE", ip, "MASK", "255.255.255.255", gw])
            if exclude_lan:
                self._run_cmd(["route", "DELETE", "10.0.0.0", "MASK", "255.0.0.0"])
                self._run_cmd(["route", "DELETE", "172.16.0.0", "MASK", "255.240.0.0"])
                self._run_cmd(["route", "DELETE", "192.168.0.0", "MASK", "255.255.0.0"])
            self._run_cmd(["route", "DELETE", "0.0.0.0", "MASK", "128.0.0.0"])
            self._run_cmd(["route", "DELETE", "128.0.0.0", "MASK", "128.0.0.0"])
        else:
            if gw and iface:
                subprocess.run(self._elevate(["ip", "route", "del", vpn_ip, "via", gw, "dev", iface]), capture_output=True)
                for ip in ips:
                    subprocess.run(self._elevate(["ip", "route", "del", ip, "via", gw, "dev", iface]), capture_output=True)
                if exclude_lan:
                    subprocess.run(self._elevate(["ip", "route", "del", "10.0.0.0/8", "via", gw, "dev", iface]), capture_output=True)
                    subprocess.run(self._elevate(["ip", "route", "del", "172.16.0.0/12", "via", gw, "dev", iface]), capture_output=True)
                    subprocess.run(self._elevate(["ip", "route", "del", "192.168.0.0/16", "via", gw, "dev", iface]), capture_output=True)
                    
            if cgroup_created:
                subprocess.run(self._elevate(["iptables", "-t", "mangle", "-D", "OUTPUT", "-m", "cgroup", "--path", "protonvpn_exclude", "-j", "MARK", "--set-mark", "51820"]), capture_output=True)
                subprocess.run(self._elevate(["ip", "rule", "del", "fwmark", "51820", "table", "200"]), capture_output=True)
                # Note: cgroup directory might not be deleted if processes are still in it, but that's fine.
                subprocess.run(self._elevate(["rmdir", "/sys/fs/cgroup/protonvpn_exclude"]), capture_output=True)
                
            subprocess.run(self._elevate(["ip", "route", "del", "0.0.0.0/1"]), capture_output=True)
            subprocess.run(self._elevate(["ip", "route", "del", "128.0.0.0/1"]), capture_output=True)
            
        try:
            os.remove(self.state_file)
        except:
            pass
