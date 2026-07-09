import os
import sys
import time
import json
import psutil
import subprocess
import shutil
from datetime import datetime

from .routing import get_config_dir, RoutingManager

class Watchdog:
    def __init__(self):
        self.config_dir = get_config_dir()
        self.state_file = os.path.join(self.config_dir, "routing_state.json")
        self.log_file = os.path.join(self.config_dir, "service.log")

    def _log(self, msg: str):
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        try:
            with open(self.log_file, "a") as f:
                f.write(f"[{timestamp}] [Watchdog] {msg}\n")
        except Exception:
            pass

    def install(self):
        """Installs the watchdog via Windows Task Scheduler to run on startup with Highest Privileges."""
        if sys.platform != "win32":
            return

        exe_path = os.path.abspath(sys.argv[0])
        target_dir = os.path.join(os.getenv("APPDATA"), "pvpn-next")
        os.makedirs(target_dir, exist_ok=True)
        target_exe = os.path.join(target_dir, "pvpn-watchdog.exe")

        # Copy the executable if it's not already there or differs
        try:
            shutil.copy2(exe_path, target_exe)
        except Exception as e:
            self._log(f"Failed to copy executable to {target_exe}: {e}")
            target_exe = exe_path # fallback

        # Install via schtasks
        task_name = "PVPN-Next-Watchdog"
        # We delete it first in case it exists to recreate it with potentially updated paths
        subprocess.run(["schtasks", "/Delete", "/TN", task_name, "/F"], capture_output=True, creationflags=0x08000000)
        
        # Create the task to run on login with highest privileges (Admin)
        cmd = [
            "schtasks", "/Create", "/TN", task_name,
            "/TR", f'"{target_exe}" _watchdog',
            "/SC", "ONLOGON", "/RL", "HIGHEST", "/F"
        ]
        
        result = subprocess.run(cmd, capture_output=True, text=True, creationflags=0x08000000)
        if result.returncode == 0:
            self._log("Successfully installed watchdog via Task Scheduler.")
            # Also start it immediately if not running
            subprocess.run(["schtasks", "/Run", "/TN", task_name], capture_output=True, creationflags=0x08000000)
        else:
            self._log(f"Failed to install watchdog: {result.stderr}")

    def run(self):
        """Main monitoring loop."""
        if sys.platform != "win32":
            return

        self._log(f"Watchdog service started (PID: {os.getpid()}).")
        
        while True:
            time.sleep(10)
            
            if not os.path.exists(self.state_file):
                # Clean disconnect, nothing to monitor
                continue

            try:
                # State file exists, so VPN is SUPPOSED to be running.
                # Check if pvpn-engine.exe is running.
                engine_running = False
                for proc in psutil.process_iter(['name']):
                    if proc.info['name'] and 'pvpn-engine' in proc.info['name'].lower():
                        engine_running = True
                        break
                
                if not engine_running:
                    # Crash detected!
                    self._log("CRASH DETECTED: routing_state.json exists but pvpn-engine is not running.")
                    self._log("Initiating automatic cleanup to restore internet connectivity...")
                    
                    routing = RoutingManager("")
                    routing.teardown_routing()
                    
                    if os.path.exists(self.state_file):
                        os.remove(self.state_file)
                        
                    self._log("Cleanup complete. Normal network state restored.")
            except Exception as e:
                self._log(f"Exception in monitor loop: {e}")
