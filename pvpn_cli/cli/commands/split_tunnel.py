"""Split tunneling commands: exclude-lan, split-tunneling, novpn, _pid-scanner."""

import os
import subprocess
import sys

from pvpn_cli.cli.config import get_config_dir


def get_split_tunnel_config():
    import json
    config_path = os.path.join(get_config_dir(), "split_tunnel.json")
    if os.path.exists(config_path):
        with open(config_path, "r") as f:
            try:
                return json.load(f)
            except:
                pass
    return {"exclude_lan": False, "split_items": []}

def save_split_tunnel_config(config):
    import json
    config_path = os.path.join(get_config_dir(), "split_tunnel.json")
    with open(config_path, "w") as f:
        json.dump(config, f, indent=4)

def do_exclude_lan(state):
    config = get_split_tunnel_config()
    config["exclude_lan"] = (state.lower() == "on")
    save_split_tunnel_config(config)
    print(f"-> Exclude LAN set to: {'ON' if config['exclude_lan'] else 'OFF'}")

def do_split_tunneling(action, value):
    config = get_split_tunnel_config()
    items = config["split_items"]
    
    if action == "list":
        print("=== Split Tunneling List ===")
        print(f"Exclude LAN: {'ON' if config['exclude_lan'] else 'OFF'}")
        for i, item in enumerate(items):
            print(f"[{i}] {item['type'].upper()}: {item['value']}")
        if not items:
            print("No items added.")
            
    elif action == "add":
        import re
        if not value:
            print("[ERROR] Value required for add.")
            return
            
        is_ip = re.match(r"^(\d{1,3}\.){3}\d{1,3}(/\d{1,2})?$", value)
        is_app = value.startswith("/") or value.startswith("C:\\") or value.endswith(".exe")
        item_type = "ip" if is_ip else ("app" if is_app else "domain")
        
        if any(i['value'] == value for i in items):
            print(f"-> {value} is already in the list.")
            return
            
        items.append({"type": item_type, "value": value})
        save_split_tunnel_config(config)
        print(f"-> Added {item_type} to split tunneling: {value}")
        
    elif action == "remove":
        if not value:
            print("[ERROR] Value or index required for remove.")
            return
        
        try:
            idx = int(value)
            if 0 <= idx < len(items):
                removed = items.pop(idx)
                save_split_tunnel_config(config)
                print(f"-> Removed: {removed['value']}")
                return
        except ValueError:
            pass
            
        for i, item in enumerate(items):
            if item['value'] == value:
                items.pop(i)
                save_split_tunnel_config(config)
                print(f"-> Removed: {value}")
                return
        print(f"[ERROR] Item '{value}' not found.")

def do_novpn(command_args):
    
    import platform
    if platform.system() == "Windows":
        print("[ERROR] novpn is only supported on Linux.")
        sys.exit(1)
        
    cgroup_path = "/sys/fs/cgroup/protonvpn_exclude"
    if not os.path.exists(cgroup_path):
        print("[ERROR] Split tunneling cgroup not found. Is VPN connected?")
        sys.exit(1)
        
    import shutil
    elevate_cmd = "sudo" if not shutil.which("doas") else "doas"
    
    # Add current PID to cgroup, then exec
    pid = os.getpid()
    try:
        subprocess.run([elevate_cmd, "sh", "-c", f"echo {pid} > {cgroup_path}/cgroup.procs"], check=True)
    except subprocess.CalledProcessError:
        print("[ERROR] Failed to add process to cgroup. Make sure you have sudo/doas privileges.")
        sys.exit(1)
        
    print(f"-> Launching bypassing VPN: {' '.join(command_args)}")
    os.execvp(command_args[0], command_args)

def do_pid_scanner():
    import time
    
    # Needs root
    if os.geteuid() != 0:
        sys.exit(1)
        
    cgroup_procs = "/sys/fs/cgroup/protonvpn_exclude/cgroup.procs"
    if not os.path.exists(cgroup_procs):
        sys.exit(1)
        
    config = get_split_tunnel_config()
    apps = [i["value"] for i in config.get("split_items", []) if i["type"] == "app"]
    
    if not apps:
        sys.exit(0)
        
    # We want to match basename or exact path
    import collections
    seen_pids = set()
    
    while True:
        try:
            for pid_dir in os.listdir("/proc"):
                if not pid_dir.isdigit():
                    continue
                    
                pid = pid_dir
                if pid in seen_pids:
                    continue
                    
                exe_path = os.path.join("/proc", pid, "exe")
                try:
                    target = os.readlink(exe_path)
                except OSError:
                    continue
                    
                match = False
                for app in apps:
                    if target == app or target.endswith("/" + app) or target.endswith("\\" + app):
                        match = True
                        break
                        
                if match:
                    try:
                        with open(cgroup_procs, "a") as f:
                            f.write(pid + "\n")
                        seen_pids.add(pid)
                    except OSError:
                        pass
                        
            time.sleep(3)
        except KeyboardInterrupt:
            break
        except Exception:
            time.sleep(3)
