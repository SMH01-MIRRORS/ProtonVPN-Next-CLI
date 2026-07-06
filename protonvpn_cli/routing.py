import sys
import subprocess
import os
import json
import urllib.request
from typing import Optional

class RoutingManager:
    def __init__(self, elevate_cmd: str):
        self.elevate_cmd = elevate_cmd
        self.state_file = os.path.expanduser("~/.config/protonvpn-next/routing_state.json")
        self.is_windows = sys.platform == "win32"

    def _run_cmd(self, cmd: list) -> str:
        try:
            result = subprocess.run(cmd, check=True, capture_output=True, text=True)
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
        output = subprocess.run(["route", "print", "-4", "0.0.0.0"], capture_output=True, text=True).stdout
        for line in output.split('\n'):
            line = line.strip()
            if line.startswith("0.0.0.0"):
                parts = line.split()
                if len(parts) >= 3:
                    return parts[2] # Gateway
        return None

    def start_vpn(self, vpn_ip: str, engine_path: str, config_path: str, log_path: str, awg_ip: str = "10.2.0.2", awg_iface: str = "awg0"):
        print(f"-> Setting up traffic routing for {vpn_ip}...")
        
        state = {"vpn_ip": vpn_ip, "gw": None, "iface": None, "os": sys.platform}
        
        if self.is_windows:
            self._download_wintun()
            gw = self._get_windows_default_gateway()
            if not gw:
                print("[ERROR] Could not detect default Windows gateway.")
                sys.exit(1)
                
            state["gw"] = gw
            with open(self.state_file, "w") as f:
                json.dump(state, f)
            
            # Windows execution (assumes running as Admin)
            with open(log_path, "w") as log_file:
                proc = subprocess.Popen([engine_path], stdin=open(config_path, "r"), stdout=log_file, stderr=subprocess.STDOUT)
                import time
                time.sleep(1.5)
                self._run_cmd(["route", "ADD", vpn_ip, "MASK", "255.255.255.255", gw])
                self._run_cmd(["route", "ADD", "0.0.0.0", "MASK", "128.0.0.0", awg_ip])
                self._run_cmd(["route", "ADD", "128.0.0.0", "MASK", "128.0.0.0", awg_ip])
                print("Routing configured successfully. All traffic is now secured.")
                print("Press Ctrl+C to disconnect.")
                proc.wait()
            
        else:
            gw, iface = self._get_linux_default_gateway()
            if not gw or not iface:
                print("[ERROR] Could not detect default Linux gateway.")
                sys.exit(1)
                
            state["gw"] = gw
            state["iface"] = iface
            
            with open(self.state_file, "w") as f:
                json.dump(state, f)
            
            script = f"""
{engine_path} < "{config_path}" > "{log_path}" 2>&1 &
ENGINE_PID=$!
# Wait for interface
for i in $(seq 1 30); do
    ip link show {awg_iface} >/dev/null 2>&1 && break
    sleep 0.5
done
ip route add {vpn_ip} via {gw} dev {iface}
ip route add 0.0.0.0/1 dev {awg_iface}
ip route add 128.0.0.0/1 dev {awg_iface}
echo "-> Routing configured successfully. All traffic is now secured."
echo "-> Press Ctrl+C to disconnect, or run './protonvpn-next disconnect' in another terminal."
wait $ENGINE_PID
"""
            subprocess.run([self.elevate_cmd, "sh", "-c", script])

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
        
        if not vpn_ip:
            return
            
        print("-> Tearing down VPN routing...")
        
        if state.get("os") == "win32":
            self._run_cmd([self.elevate_cmd, "route", "DELETE", vpn_ip, "MASK", "255.255.255.255", gw])
            self._run_cmd([self.elevate_cmd, "route", "DELETE", "0.0.0.0", "MASK", "128.0.0.0"])
            self._run_cmd([self.elevate_cmd, "route", "DELETE", "128.0.0.0", "MASK", "128.0.0.0"])
        else:
            if gw and iface:
                subprocess.run([self.elevate_cmd, "ip", "route", "del", vpn_ip, "via", gw, "dev", iface], capture_output=True)
            subprocess.run([self.elevate_cmd, "ip", "route", "del", "0.0.0.0/1"], capture_output=True)
            subprocess.run([self.elevate_cmd, "ip", "route", "del", "128.0.0.0/1"], capture_output=True)
            
        try:
            os.remove(self.state_file)
        except:
            pass

    def _download_wintun(self):
        engine_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "engine")
        wintun_path = os.path.join(engine_dir, "wintun.dll")
        if os.path.exists(wintun_path):
            return
            
        print("-> Downloading wintun.dll for Windows compatibility...")
        try:
            import zipfile
            import io
            url = "https://wintun.net/builds/wintun-0.14.1.zip"
            response = urllib.request.urlopen(url)
            with zipfile.ZipFile(io.BytesIO(response.read())) as zip_ref:
                with zip_ref.open("wintun/bin/amd64/wintun.dll") as zf, open(wintun_path, "wb") as f:
                    f.write(zf.read())
            print("-> Successfully downloaded wintun.dll")
        except Exception as e:
            print(f"[ERROR] Failed to download wintun.dll: {e}")
