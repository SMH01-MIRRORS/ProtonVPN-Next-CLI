import sys
import subprocess
import os
import json
import urllib.request
import platform
import socket
import hashlib
import shutil
import stat
import tempfile
import shlex
import ipaddress
import re
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


def open_regular_no_follow(path: str, flags: int, mode: str, permissions: int = 0o600):
    """Open one regular file without following symlinks or hard links."""
    safe_flags = flags | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(path, safe_flags, permissions)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise RuntimeError(f"Unsafe file rejected: {path}")
        return os.fdopen(fd, mode)
    except Exception:
        os.close(fd)
        raise


def launch_linux_engine(engine_path: str, dns_ips: str, config_path: str, log_path: str, client_log_path: str):
    """Launch the engine with argv and pre-opened safe files, never a root shell."""
    with open_regular_no_follow(config_path, os.O_RDONLY, "rb") as config_file, \
         open_regular_no_follow(log_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, "wb") as log_file, \
         open_regular_no_follow(client_log_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, "wb") as client_log:
        return subprocess.Popen(
            [engine_path, "-dns", dns_ips],
            stdin=config_file,
            stdout=log_file,
            stderr=client_log,
            close_fds=True,
            start_new_session=True,
        )


def stage_frozen_engine(engine_path: str, runtime_dir: str = "/run/pvpn-next") -> str:
    """Copy a bundled engine out of PyInstaller's disposable extraction tree."""
    if not getattr(sys, "frozen", False) or sys.platform == "win32":
        return engine_path

    os.makedirs(runtime_dir, mode=0o700, exist_ok=True)
    runtime_stat = os.lstat(runtime_dir)
    if not stat.S_ISDIR(runtime_stat.st_mode) or runtime_stat.st_uid != os.geteuid():
        raise RuntimeError(f"Unsafe engine runtime directory: {runtime_dir}")
    os.chmod(runtime_dir, 0o700)

    digest = hashlib.sha256()
    with open(engine_path, "rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    staged_path = os.path.join(runtime_dir, f"pvpn-engine-{digest.hexdigest()[:16]}")

    if not os.path.exists(staged_path):
        fd, temporary_path = tempfile.mkstemp(prefix=".pvpn-engine-", dir=runtime_dir)
        os.close(fd)
        try:
            shutil.copyfile(engine_path, temporary_path)
            os.chmod(temporary_path, 0o700)
            os.replace(temporary_path, staged_path)
        finally:
            try:
                os.remove(temporary_path)
            except FileNotFoundError:
                pass

    return staged_path


def load_split_config() -> dict:
    """Read split_tunnel.json, the file shared by the CLI and the GUI."""
    config_path = os.path.join(get_config_dir(), "split_tunnel.json")
    if os.path.exists(config_path):
        try:
            with open(config_path, "r") as f:
                loaded = json.load(f)
            if isinstance(loaded, dict):
                return loaded
        except (OSError, ValueError):
            pass
    return {"exclude_lan": False, "split_items": []}


def split_tunneling_enabled(config=None, db=None) -> bool:
    """The item list lives in split_tunnel.json, the master switch in the
    database. Lists created before the switch existed keep working."""
    from pvpn_cli.database import Database

    raw = (db or Database()).get_setting("split_tunneling")
    if raw is None:
        config = load_split_config() if config is None else config
        return bool(config.get("split_items"))
    return raw == "true"


class RoutingManager:
    def __init__(self, elevate_cmd: str):
        self.elevate_cmd = elevate_cmd
        self.state_file = os.path.join(get_config_dir(), "routing_state.json")
        self.is_windows = sys.platform == "win32"

    def _elevate(self, cmd: list) -> list:
        if self.elevate_cmd:
            return [self.elevate_cmd] + cmd
        return cmd

    def _run_cmd(self, cmd: list, silent: bool = False) -> str:
        try:
            kwargs = {"check": True, "capture_output": True, "text": True, "errors": "ignore"}
            if self.is_windows:
                kwargs["creationflags"] = 0x08000000
            result = subprocess.run(cmd, **kwargs)
            return result.stdout.strip()
        except subprocess.CalledProcessError as e:
            if not silent:
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
        config = load_split_config()
        if not split_tunneling_enabled(config):
            if config.get("split_items"):
                print("-> Split tunneling is switched off, so its items are ignored.")
            return dict(config, split_items=[])
        if config.get("mode") == "include":
            # Routing can only keep traffic out of the tunnel. Applying the list
            # here would route exactly the wrong side, so keep everything in.
            print("[WARNING] Split tunneling 'include' mode is not supported yet, so all traffic stays in the VPN.")
            return dict(config, split_items=[])
        return config

    def _resolve_ips(self, items):
        ips = []
        apps = []
        for item in items:
            if not isinstance(item, dict):
                continue
            item_type = item.get("type")
            value = item.get("value")
            if not isinstance(value, str):
                continue
            if item_type == "ip":
                try:
                    ips.append(str(ipaddress.ip_network(value, strict=False)))
                except ValueError:
                    print(f"[WARNING] Ignoring invalid split-tunnel network: {value}")
            elif item_type == "domain":
                try:
                    resolved = socket.gethostbyname_ex(value)[2]
                    ips.extend(str(ipaddress.ip_address(ip)) for ip in resolved)
                    print(f"-> Resolved domain {value} to {resolved}")
                except (socket.gaierror, ValueError):
                    print(f"[WARNING] Could not resolve domain: {value}")
            elif item_type == "app":
                apps.append(value)
        return ips, apps

    def start_vpn(self, vpn_ip: str, engine_path: str, config_path: str, log_path: str, awg_ip: str = "10.2.0.2", awg_iface: str = "awg0", dns_ips: str = "10.2.0.1", split_cfg=None, db_allow_lan=None):
        # The broker reads server/settings data from a user-owned database. Treat
        # every networking value as untrusted before it reaches the root shell.
        try:
            vpn_ip = str(ipaddress.ip_address(vpn_ip))
            awg_ip = str(ipaddress.ip_address(awg_ip))
        except ValueError as exc:
            raise RuntimeError("Invalid VPN address") from exc
        if not re.fullmatch(r"[A-Za-z0-9_.:-]{1,15}", awg_iface):
            raise RuntimeError("Invalid VPN interface name")
        print(f"-> Setting up traffic routing for {vpn_ip}...")
        
        if split_cfg is None:
            split_cfg = self._get_split_config()
        exclude_ips, exclude_apps = self._resolve_ips(split_cfg.get("split_items", []))
        if db_allow_lan is None:
            from pvpn_cli.database import Database
            db_allow_lan = Database().get_setting("allow_lan", "false") == "true"
        exclude_lan = split_cfg.get("exclude_lan", False) or db_allow_lan
        try:
            dns_list = [str(ipaddress.ip_address(ip.strip())) for ip in dns_ips.split(",") if ip.strip()]
        except ValueError as exc:
            raise RuntimeError("Invalid DNS address") from exc
        
        engine_running = False
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(0.5)
            s.connect(("127.0.0.1", 34116))
            with open(config_path, "r") as f:
                s.sendall(f.read().encode("utf-8"))
            s.shutdown(socket.SHUT_WR)
            if "OK" in s.recv(1024).decode():
                engine_running = True
            s.close()
            print("-> Dynamic engine update via IPC successful.")
        except Exception:
            pass
        
        state = {"vpn_ip": vpn_ip, "gw": None, "iface": None, "os": sys.platform, "ips": exclude_ips, "exclude_lan": exclude_lan, "cgroup_created": False, "dns_list": dns_list}
        
        if self.is_windows:
            gw, phys_idx, phys_ip = self._get_windows_default_gateway()
            if not gw:
                print("[ERROR] Could not detect default Windows gateway.")
                sys.exit(1)
                
            state["gw"] = gw
            with open_regular_no_follow(
                self.state_file, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, "w"
            ) as f:
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
                        # These rules stay in the registry until deleted and netsh appends
                        # a duplicate pair on every connect, so clear the old ones first.
                        self._run_cmd(["netsh", "advfirewall", "firewall", "delete", "rule", "name=pvpn-engine"], silent=True)
                        self._run_cmd(["netsh", "advfirewall", "firewall", "add", "rule", "name=pvpn-engine", "dir=in", "action=allow", f"program={engine_path}", "enable=yes"])
                        self._run_cmd(["netsh", "advfirewall", "firewall", "add", "rule", "name=pvpn-engine", "dir=out", "action=allow", f"program={engine_path}", "enable=yes"])
                    except:
                        pass
                
                if not engine_running:
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
                        # netsh defaults to store=persistent, which would leave a dead
                        # IPv6 default route in the registry after a crash or a BSOD.
                        self._run_cmd(["netsh", "interface", "ipv6", "add", "route", "::/0", awg_iface, "metric=1", "store=active"], silent=True)
                    except:
                        pass
                    
                    if dns_list:
                        with open(client_log_path, "a") as f:
                            self._windows_apply_dns(awg_iface, dns_list, f)
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
            try:
                gw = str(ipaddress.ip_address(gw))
            except ValueError as exc:
                raise RuntimeError("Invalid default gateway") from exc
            if not re.fullmatch(r"[A-Za-z0-9_.:-]{1,15}", iface):
                raise RuntimeError("Invalid physical interface name")
            if exclude_apps and os.environ.get("PVPN_SERVICE_CHILD") == "1":
                raise RuntimeError(
                    "App-based split tunneling is not available through the hardened service; "
                    "use LAN, IP or domain exclusions"
                )
                
            state["gw"] = gw
            state["iface"] = iface
            
            # Setup bash commands for excluded IPs and LAN
            split_cmds = []
            for ip in exclude_ips:
                split_cmds.append(
                    f"ip route add {shlex.quote(ip)} via {shlex.quote(gw)} dev {shlex.quote(iface)}"
                )
            if exclude_lan:
                split_cmds.append(f"ip route add 10.0.0.0/8 via {shlex.quote(gw)} dev {shlex.quote(iface)}")
                split_cmds.append(f"ip route add 172.16.0.0/12 via {shlex.quote(gw)} dev {shlex.quote(iface)}")
                split_cmds.append(f"ip route add 192.168.0.0/16 via {shlex.quote(gw)} dev {shlex.quote(iface)}")
                
            if exclude_apps:
                state["cgroup_created"] = True
                # Setup cgroup v2 hierarchy and routing
                split_cmds.extend([
                    "mkdir -p /sys/fs/cgroup/protonvpn_exclude",
                    # Enable fwmark matching for this cgroup
                    "iptables -t mangle -C OUTPUT -m cgroup --path protonvpn_exclude -j MARK --set-mark 51820 2>/dev/null || iptables -t mangle -A OUTPUT -m cgroup --path protonvpn_exclude -j MARK --set-mark 51820",
                    "ip rule add fwmark 51820 table 200",
                    f"ip route add default via {shlex.quote(gw)} dev {shlex.quote(iface)} table 200"
                ])
                
            split_cmds_str = "\n".join(split_cmds)
            
            # --- Stateless IPv6 and DNS ---
            # We don't save any state because routes attached to awg0 will automatically vanish when it dies.
            state["ipv6_disabled_originally"] = False
            state["dns_backup"] = False
            
            with open_regular_no_follow(
                self.state_file, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, "w"
            ) as f:
                json.dump(state, f)
            client_log_path = log_path.replace("awg.log", "client.log")
            
            dns_setup_script = ""
            if dns_list:
                dns_ips_space = " ".join(shlex.quote(ip) for ip in dns_list)
                dns_setup_script = f"""
if command -v resolvectl >/dev/null 2>&1; then
    resolvectl dns {shlex.quote(awg_iface)} {dns_ips_space}
    resolvectl domain {shlex.quote(awg_iface)} ~\\.
    PHYSICAL_DNS=$(resolvectl status {shlex.quote(iface)} 2>/dev/null | grep 'DNS Servers' | awk '{{print $3, $4, $5}}')
else
    PHYSICAL_DNS=$(grep -Eo '[0-9]+[.][0-9]+[.][0-9]+[.][0-9]+' /etc/resolv.conf 2>/dev/null)
fi

for ip in $PHYSICAL_DNS; do
    if [[ "$ip" != 127.* ]]; then
        ip route add "$ip/32" dev {shlex.quote(awg_iface)} 2>/dev/null || true
    fi
done
"""
                    
            if not engine_running:
                engine_path = stage_frozen_engine(engine_path)
                subprocess.run(
                    self._elevate(["ip", "link", "delete", awg_iface]), capture_output=True
                )
                engine_process = launch_linux_engine(
                    engine_path, dns_ips, config_path, log_path, client_log_path
                )
                import time
                time.sleep(0.2)
                if engine_process.poll() is not None:
                    try:
                        with open_regular_no_follow(client_log_path, os.O_RDONLY, "r") as errors:
                            details = errors.read()
                    except Exception:
                        details = ""
                    raise RuntimeError(
                        "VPN engine exited immediately after launch"
                        + (f": {details.strip()}" if details else "")
                    )
            script = f"""
# Wait for the userspace engine to create its TUN interface.
for i in $(seq 1 30); do
    ip link show {shlex.quote(awg_iface)} >/dev/null 2>&1 && break
    sleep 0.5
done
if ! ip link show {shlex.quote(awg_iface)} >/dev/null 2>&1; then
    cat {shlex.quote(client_log_path)} >&2
    exit 1
fi
ip -6 route add default dev {shlex.quote(awg_iface)} metric 1 2>/dev/null || true
{dns_setup_script}
ip route add {shlex.quote(vpn_ip)} via {shlex.quote(gw)} dev {shlex.quote(iface)}
{split_cmds_str}
ip route add 0.0.0.0/1 dev {shlex.quote(awg_iface)}
ip route add 128.0.0.0/1 dev {shlex.quote(awg_iface)}
echo "-> Routing configured successfully. All traffic is now secured."
echo "-> VPN is running in the background. Use 'disconnect' to stop."
"""
            result = subprocess.run(self._elevate(["sh", "-c", script]))
            if result.returncode != 0:
                try:
                    os.remove(self.state_file)
                except FileNotFoundError:
                    pass
                sys.exit(result.returncode)
            
            # Launch PID scanner in background if needed
            if exclude_apps:
                env = os.environ.copy()
                for k in ["_MEIPASS", "_MEIPASS1", "_MEIPASS2", "_MEIPASS3"]:
                    env.pop(k, None)
                scanner_cmd = [sys.executable, os.path.realpath(sys.argv[0]), "_pid-scanner"]
                subprocess.Popen(scanner_cmd, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def _windows_purge_dns_state(self, awg_iface: str = "awg0", log=None) -> None:
        """Remove every persistent DNS artefact this app can leave on Windows.

        Runs before the tunnel is configured and again on teardown, so a crash,
        a taskkill or a BSOD cannot leave the machine pointing at a tunnel DNS
        that no longer exists. NRPT rules and a persistent IPv6 default route
        are the dangerous ones, because both are registry policy that survives
        a reboot and older builds created them, so they are swept here even
        though the current code never writes either.
        """
        clean_cmd = (
            "Get-DnsClientNrptRule | Where-Object { $_.Comment -eq 'PVPN-Next' } "
            "| Remove-DnsClientNrptRule -ErrorAction SilentlyContinue -Force"
        )
        try:
            subprocess.run(
                ["powershell", "-NoProfile", "-Command", clean_cmd],
                creationflags=0x08000000,
                capture_output=True,
            )
            if log:
                log.write("Cleared any stale NRPT rules.\n")
        except Exception:
            pass

        for family in ("ipv4", "ipv6"):
            self._run_cmd(
                ["netsh", "interface", family, "set", "dnsservers", f"name={awg_iface}", "source=dhcp"],
                silent=True,
            )

        for store in ("active", "persistent"):
            self._run_cmd(
                ["netsh", "interface", "ipv6", "delete", "route", "::/0", awg_iface, f"store={store}"],
                silent=True,
            )

    def _windows_apply_dns(self, awg_iface: str, dns_list: list, log=None) -> None:
        """Point the tunnel adapter at the VPN DNS servers, and nothing else.

        Windows keeps per-interface DNS in the registry and netsh has no
        store=active for dnsservers, so this one write cannot be avoided. It is
        scoped to the Wintun adapter, which the engine destroys when it exits,
        so the setting dies with the tunnel instead of outliving it. Leak
        protection is the engine's WFP block, which runs in a dynamic session
        that the kernel tears down even if the engine crashes.
        """
        self._windows_purge_dns_state(awg_iface, log)

        for family, servers in (
            ("ipv4", [ip for ip in dns_list if ":" not in ip]),
            ("ipv6", [ip for ip in dns_list if ":" in ip]),
        ):
            if not servers:
                continue
            try:
                # register=none keeps the tunnel out of dynamic DNS updates and
                # validate=no stops netsh from stalling on a server that is not
                # reachable until routing is in place.
                self._run_cmd([
                    "netsh", "interface", family, "set", "dnsservers",
                    f"name={awg_iface}", "source=static", f"address={servers[0]}",
                    "register=none", "validate=no",
                ])
                if log:
                    log.write(f"Set tunnel {family} DNS to {servers[0]}.\n")
                for idx, dns_ip in enumerate(servers[1:], start=2):
                    self._run_cmd([
                        "netsh", "interface", family, "add", "dnsservers",
                        f"name={awg_iface}", f"address={dns_ip}", f"index={idx}",
                        "validate=no",
                    ])
                    if log:
                        log.write(f"Added secondary {family} DNS: {dns_ip}\n")
            except Exception as e:
                if log:
                    log.write(f"Exception during tunnel DNS assignment: {e}\n")

        if log:
            log.write("--- End DNS Setup ---\n")

    def teardown_routing(self):
        if not os.path.exists(self.state_file):
            return
            
        try:
            with open_regular_no_follow(self.state_file, os.O_RDONLY, "r") as f:
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
                
                # Drop the tunnel DNS, the IPv6 default route and any legacy NRPT
                # policy so nothing this app configured can outlive the tunnel.
                self._windows_purge_dns_state()
                self._run_cmd(["netsh", "advfirewall", "firewall", "delete", "rule", "name=pvpn-engine"], silent=True)
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
        except Exception as e:
            import traceback
            traceback.print_exc()
            print(f"-> Teardown error: {e}", flush=True)
