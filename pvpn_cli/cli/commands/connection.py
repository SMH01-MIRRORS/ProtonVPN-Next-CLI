"""Connect, disconnect, and status commands."""

import os
import platform
import sys

from pvpn_cli.database import Database
from pvpn_cli.vpn import ProtonVpnApi

from pvpn_cli.cli.colors import Colors
from pvpn_cli.cli.config import get_config_dir, get_engine_path
from pvpn_cli.cli.elevation import elevate_command_linux, elevate_if_needed_windows
from pvpn_cli.cli.commands.split_tunnel import get_split_tunnel_config


def do_status():
    db = Database()
    session = db.get_session()
    print("=== PVPN CLI Status ===")
    
    print(f"[System] OS:      {platform.system()} {platform.release()}")
    
    routing_file = os.path.join(get_config_dir(), "routing_state.json")
    vpn_running = False
    vpn_ip = "Unknown"
    
    if os.path.exists(routing_file):
        try:
            import json
            with open(routing_file, "r") as f:
                state = json.load(f)
            vpn_ip = state.get("vpn_ip", "Unknown")
        except Exception:
            pass
            
    engine_name = "pvpn-engine.exe" if platform.system() == "Windows" else "pvpn-engine"
    if platform.system() == "Windows":
        try:
            import psutil
            vpn_running = any(proc.info['name'] and engine_name.lower() in proc.info['name'].lower() 
                              for proc in psutil.process_iter(['name']))
        except Exception:
            pass
    else:
        try:
            import subprocess
            vpn_running = subprocess.run(["pgrep", "-f", engine_name], capture_output=True).returncode == 0
        except Exception:
            pass

    daemon_pid = os.path.join(get_config_dir(), "daemon.pid")
    daemon_running = False
    if os.path.exists(daemon_pid):
        try:
            with open(daemon_pid, "r") as f:
                pid = int(f.read().strip())
            if platform.system() == "Windows":
                import psutil
                try:
                    p = psutil.Process(pid)
                    daemon_running = p.is_running() and any(x in p.name().lower() for x in ("pvpn", "python"))
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    daemon_running = False
            else:
                os.kill(pid, 0)
                daemon_running = True
        except Exception:
            pass

    if vpn_running:
        active_server = db.get_setting("active_server_name", "Unknown")
        print(f"[VPN]    State:   CONNECTED (Server: {active_server} | IP: {vpn_ip})")
        real_ip = db.get_setting("current_real_ip", "")
        
        if not real_ip and not daemon_running:
            try:
                import urllib.request
                req = urllib.request.Request("https://1.1.1.1/cdn-cgi/trace", headers={'User-Agent': 'Mozilla/5.0'})
                resp = urllib.request.urlopen(req, timeout=2).read().decode('utf-8')
                for line in resp.split('\n'):
                    if line.startswith('ip='):
                        real_ip = line.split('=')[1].strip()
                        db.set_setting("current_real_ip", real_ip)
                        break
            except Exception:
                pass
                
        if real_ip:
            print(f"[VPN]    Real IP: {real_ip}")
        else:
            print("[VPN]    Real IP: Fetching...")
    else:
        if os.path.exists(routing_file):
            print("[VPN]    State:   CRASHED (Or disconnected uncleanly)")
        else:
            print("[VPN]    State:   DISCONNECTED")
        
    if daemon_running:
        print("[Daemon] State:   RUNNING")
    else:
        print("[Daemon] State:   STOPPED")
        if os.path.exists(daemon_pid):
            try:
                os.remove(daemon_pid)
            except Exception:
                pass
    
    if session and session.get("access_token"):
        print("[Auth]   Status:  LOGGED IN")
        print(f"[Auth]   UID:     {session.get('uid', 'N/A')}")
        api = ProtonVpnApi()
        try:
            tier = api.get_max_tier()
            print(f"[Auth]   Tier:    {tier} (0=Free, 1=Basic, 2=Plus/Visionary)")
        except Exception:
            print("[Auth]   Tier:    Unknown (Network Error)")
    else:
        print("[Auth]   Status:  NOT LOGGED IN")
    
    bypass_val = db.get_setting("api_bypass", "0")
    bypass_map = {
        "0": "Direct (vpn-api.proton.me)",
        "1": "Cloudflare Proxy (api.protonnext.qzz.io)",
        "2": "Netlify Proxy (shimmering-stroopwafel-51675e.netlify.app)",
        "3": "Deno Proxy (quick-bluejay-8760.smh01-mirrors.deno.net)"
    }
    bypass_desc = bypass_map.get(bypass_val, "Unknown")
    print(f"[Network] Bypass: {bypass_desc}")
    
    if session and session.get("wg_certificate"):
        import datetime
        exp = session.get("cert_expires_at")
        if exp:
            dt = datetime.datetime.fromtimestamp(exp).strftime('%Y-%m-%d %H:%M:%S')
            print(f"[Cert]   Status:  Registered (Expires: {dt})")
        else:
            print("[Cert]   Status:  Registered")
    else:
        print("[Cert]   Status:  Not Registered")
        
    split_config = get_split_tunnel_config()
    items_count = len(split_config.get("split_items", []))
    exc_lan = split_config.get("exclude_lan", False)
    if items_count > 0 or exc_lan:
        print(f"[Split]  Status:  Active (LAN Excluded: {'Yes' if exc_lan else 'No'}, {items_count} items)")
    else:
        print("[Split]  Status:  Disabled")
    
    count = db.get_server_count()
    print(f"[Server] Cached:  {count} servers")
    print("============================")


def do_connect(server_name: str, awg_str: str):
    db = Database()
    vpn = ProtonVpnApi()
    
    routing_file = os.path.join(get_config_dir(), "routing_state.json")
    if os.path.exists(routing_file):
        print(f"{Colors.WARNING}-> Detecting previous crash state. Attempting auto-recovery before connecting...{Colors.ENDC}")
        do_disconnect(exit_on_success=False)
    
    server_json = vpn.get_server_by_name(server_name)
    if not server_json:
        print(f"[ERROR] Server '{server_name}' not found in local cache. Run 'fetch-servers' first.")
        sys.exit(1)

    # Extract real name (in case server_name was an ID from GUI)
    server_name = server_json.get("Name", server_name)

    servers_list = server_json.get("Servers", [])
    if not servers_list:
        print("[ERROR] No physical nodes found for this logical server.")
        sys.exit(1)
        
    node = servers_list[0]
    entry_ip = node.get("EntryIP")
    public_key = node.get("X25519PublicKey")
    
    if not entry_ip or not public_key:
        print("[ERROR] Missing IP or PublicKey in server response.")
        sys.exit(1)
        
    session = db.get_session()
    if not session:
        print("[ERROR] No active session. Run guest login.")
        sys.exit(1)
        
    private_key = session.get("wg_private_key")
    if not private_key:
        print("[ERROR] No WireGuard private key found. Run 'register-cert' first.")
        sys.exit(1)
        
    from pvpn_cli.awg import parse_awg_string
    
    final_awg_str = awg_str
    if not final_awg_str:
        mode = db.get_setting("active_awg_mode", "none")
        if mode == "custom":
            final_awg_str = db.get_setting("active_awg_custom_params", "")
        elif mode == "config":
            cfg_id = db.get_setting("active_awg_config_id")
            if cfg_id:
                cfg = db.get_awg_config(cfg_id)
                if cfg:
                    final_awg_str = cfg["params"]
                    print(f"-> Using AWG Config: {Colors.OKCYAN}{cfg['name']}{Colors.ENDC}")

    if final_awg_str and not awg_str:
        print(f"-> Using stored AWG parameters: {final_awg_str}")
    elif awg_str:
        print(f"-> Using manually provided AWG parameters: {awg_str}")

    awg_params = parse_awg_string(final_awg_str) if final_awg_str else {}
    
    config_lines = [
        "[Interface]",
        f"PrivateKey = {private_key}",
        "Address = 10.2.0.2/32"
    ]
    
    # DNS configuration
    active_dns_name = db.get_setting("active_dns_profile", "cloudflare")
    if active_dns_name == "proton":
        dns_ips = "10.2.0.1"
    else:
        predefined = {
            "cloudflare": "1.1.1.1, 1.0.0.1",
            "adguard": "94.140.14.14, 94.140.15.15",
            "google": "8.8.8.8, 8.8.4.4"
        }
        if active_dns_name in predefined:
            dns_ips = predefined[active_dns_name]
        else:
            import json
            try:
                custom_dns = json.loads(db.get_setting("custom_dns_profiles", "{}"))
                dns_ips = custom_dns.get(active_dns_name, "10.2.0.1")
            except:
                dns_ips = "10.2.0.1"
                
    config_lines.append(f"DNS = {dns_ips}")
    
    mtu = db.get_setting("mtu", "1280")
    if mtu:
        config_lines.append(f"MTU = {mtu}")
    
    for k, v in awg_params.items():
        config_lines.append(f"{k} = {v}")
        
    config_lines.extend([
        "",
        "[Peer]",
        f"PublicKey = {public_key}",
        f"Endpoint = {entry_ip}:51820",
        "AllowedIPs = 0.0.0.0/0",
        "PersistentKeepalive = 25"
    ])
    
    config = "\n".join(config_lines) + "\n"
    
    # Add delimiter to config for safety
    config += "---END---\n"
    
    config_dir = get_config_dir()
    config_path = os.path.join(config_dir, "connection.conf")
    with open(config_path, "w") as f:
        f.write(config)
    print(f"-> Saved AWG configuration to {config_path}")
    
    engine_path = get_engine_path()
    if not os.path.exists(engine_path):
        print(f"[ERROR] Engine binary not found at {engine_path}. Ensure it is compiled.")
        sys.exit(1)
        
    import shutil
    
    is_windows = platform.system() == "Windows"
    elevate_cmd = ""
    
    if is_windows:
        args = ["connect", server_name]
        if awg_str:
            args.append(f"awg={awg_str}")
        elevate_if_needed_windows(args)
    else:
        if os.geteuid() != 0:
            args = ["connect", server_name]
            if awg_str:
                args.append(f"awg={awg_str}")
            if elevate_command_linux(args):
                return

            # If elevate_command_linux failed to return but didn't exit,
            # we need to know what to use for routing commands
            elevate_cmd = "doas" if shutil.which("doas") else ("sudo" if shutil.which("sudo") else "")
        else:
            # We are already root (e.g. via run0 or manual sudo)
            elevate_cmd = ""

        if elevate_cmd:
            print(f"-> You may be prompted for your password by {elevate_cmd}.")
        
    print(f"-> Starting VPN connection to {server_name} ({entry_ip})...")
    
    log_path = os.path.join(config_dir, "awg.log")
    
    from pvpn_cli.routing import RoutingManager
    rm = RoutingManager(elevate_cmd)
        
    db.set_setting("active_server_name", server_name)
    db.set_setting("current_real_ip", "")
        
    try:
        rm.start_vpn(vpn_ip=entry_ip, engine_path=engine_path, config_path=config_path, log_path=log_path, dns_ips=dns_ips)
        if is_windows:
            try:
                from pvpn_cli.watchdog import Watchdog
                Watchdog().install_if_needed()
            except Exception as e:
                print(f"[WARNING] Failed to install watchdog: {e}")
    except KeyboardInterrupt:
        print("\nCancelled.")
        sys.exit(0)


def do_disconnect(exit_on_success=True):
    import subprocess
    import shutil

    is_windows = platform.system() == "Windows"
    elevate_cmd = ""
    
    if is_windows:
        elevate_if_needed_windows(["disconnect"], exit_on_success=exit_on_success)
    else:
        if os.geteuid() != 0:
            if elevate_command_linux(["disconnect"]):
                return
            elevate_cmd = "doas" if shutil.which("doas") else ("sudo" if shutil.which("sudo") else "")
        else:
            # Already root
            elevate_cmd = ""
    
    print("-> Disconnecting VPN...", flush=True)
    from pvpn_cli.routing import RoutingManager, get_config_dir
    print(f"-> Config dir is {get_config_dir()}", flush=True)
    uid = os.geteuid() if hasattr(os, 'geteuid') else 'N/A'
    print(f"-> UID is {uid}, SUDO_USER is {os.environ.get('SUDO_USER')}", flush=True)
    RoutingManager(elevate_cmd).teardown_routing()
    
    import socket
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(1.0)
        s.connect(("127.0.0.1", 34116))
        s.sendall(b"DISCONNECT\n")
        s.shutdown(socket.SHUT_WR)
        s.recv(1024)
        s.close()
        print("-> IPC disconnect sent.", flush=True)
    except Exception as e:
        print(f"-> IPC failed: {e}", flush=True)
        # Fallback if engine is unresponsive
        if is_windows:
            subprocess.run(["taskkill", "/F", "/IM", "pvpn-engine.exe"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=0x08000000)
        else:
            pkill_cmd = ["pkill", "-f", "pvpn-engine"]
            if elevate_cmd:
                pkill_cmd = [elevate_cmd] + pkill_cmd
            subprocess.run(pkill_cmd)
            
    print("-> VPN disconnected.", flush=True)
