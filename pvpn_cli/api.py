import os
import sys
import locale
import threading
import json
import shutil
import subprocess
import time
import urllib.request
from flask import Flask, request, jsonify, Response
from flask_cors import CORS
from pvpn_cli.database import Database
from pvpn_cli.auth import ProtonAuthApi
from pvpn_cli.vpn import ProtonVpnApi
from pvpn_cli.sentry import init_sentry

# Initialize Sentry
init_sentry()

app = Flask(__name__)
CORS(app)

# Global location state
location_state = {
    "country_code": "",
    "real_ip": "",
    "last_check": 0,
    "lock": threading.Lock()
}

# Global state for status updates
status_state = {
    "vpn_state": "UNKNOWN",
    "subscribers": [],
    "lock": threading.Lock()
}

# Global traffic state
traffic_state = {
    "speed_rx": 0,
    "speed_tx": 0,
    "session_rx": 0,
    "session_tx": 0,
    "last_abs_rx": 0,
    "last_abs_tx": 0,
    "is_active": False
}

# Global state for login flow
login_state = {
    "thread": None,
    "status": "idle",
    "error_message": "",
    "event": threading.Event(),
    "2fa_code": "",
    "uid": ""
}

# Cache for last known status to handle DB locks
last_status_cache = {
    "data": None,
    "lock": threading.Lock()
}

def get_country_from_locale():
    """Extracts country code from system locale."""
    try:
        import locale
        loc, _ = locale.getdefaultlocale()
        if loc:
            # Handle formats like 'ru_RU', 'en_US.UTF-8', 'ru'
            parts = loc.replace(".", "_").split("_")
            for p in parts:
                if len(p) == 2 and p.isupper():
                    return p.upper()
            # If only language is given (e.g. 'ru'), we can't be 100% sure but 'RU' is a good guess
            if len(parts[0]) == 2:
                lang_to_country = {"ru": "RU", "uk": "UA", "be": "BY", "kk": "KZ", "en": "US"}
                return lang_to_country.get(parts[0].lower())
    except:
        pass
    return None

def get_country_from_timezone():
    """
    Tries to guess the country code from the system timezone.
    Returns ISO country code (e.g. 'US', 'RU') or 'US' as fallback.
    """
    try:
        # 1. Try to read from /etc/timezone (Linux)
        tz = ""
        if os.path.exists("/etc/timezone"):
            with open("/etc/timezone", "r") as f:
                tz = f.read().strip()
        # 2. Try to read from symlink /etc/localtime
        elif os.path.islink("/etc/localtime"):
            tz = os.path.realpath("/etc/localtime").split("zoneinfo/")[-1]

        if not tz:
            # Fallback for other systems or if detection fails
            import time
            tz = time.tzname[0]

        # Mapping common timezones to country codes
        # Expanded to include many more regions
        mapping = {
            # Russia
            "Europe/Moscow": "RU", "Europe/Kaliningrad": "RU", "Europe/Samara": "RU",
            "Europe/Volgograd": "RU", "Europe/Saratov": "RU", "Europe/Ulyanovsk": "RU",
            "Europe/Astrakhan": "RU", "Europe/Kirov": "RU", "Asia/Yekaterinburg": "RU",
            "Asia/Omsk": "RU", "Asia/Novosibirsk": "RU", "Asia/Barnaul": "RU",
            "Asia/Tomsk": "RU", "Asia/Novokuznetsk": "RU", "Asia/Krasnoyarsk": "RU",
            "Asia/Irkutsk": "RU", "Asia/Chita": "RU", "Asia/Yakutsk": "RU",
            "Asia/Khandyga": "RU", "Asia/Vladivostok": "RU", "Asia/Ust-Nera": "RU",
            "Asia/Magadan": "RU", "Asia/Sakhalin": "RU", "Asia/Srednekolymsk": "RU",
            "Asia/Anadyr": "RU", "Asia/Kamchatka": "RU", "Moscow": "RU", "MSK": "RU",

            # Europe
            "Europe/London": "GB", "Europe/Dublin": "IE", "Europe/Paris": "FR",
            "Europe/Berlin": "DE", "Europe/Rome": "IT", "Europe/Madrid": "ES",
            "Europe/Amsterdam": "NL", "Europe/Brussels": "BE", "Europe/Zurich": "CH",
            "Europe/Vienna": "AT", "Europe/Warsaw": "PL", "Europe/Prague": "CZ",
            "Europe/Budapest": "HU", "Europe/Stockholm": "SE", "Europe/Oslo": "NO",
            "Europe/Copenhagen": "DK", "Europe/Helsinki": "FI", "Europe/Athens": "GR",
            "Europe/Istanbul": "TR", "Europe/Kiev": "UA", "Europe/Bucharest": "RO",
            "Europe/Sofia": "BG", "Europe/Belgrade": "RS", "Europe/Bratislava": "SK",
            "Europe/Ljubljana": "SI", "Europe/Zagreb": "HR", "Europe/Tallinn": "EE",
            "Europe/Riga": "LV", "Europe/Vilnius": "LT", "Europe/Minsk": "BY",
            "Europe/Chisinau": "MD", "GMT": "GB", "BST": "GB", "CET": "DE", "EET": "FI",

            # North America
            "America/New_York": "US", "America/Chicago": "US", "America/Denver": "US",
            "America/Los_Angeles": "US", "America/Phoenix": "US", "America/Anchorage": "US",
            "America/Honolulu": "US", "America/Toronto": "CA", "America/Vancouver": "CA",
            "America/Mexico_City": "MX", "EST": "US", "EDT": "US", "CST": "US", "CDT": "US",
            "MST": "US", "MDT": "US", "PST": "US", "PDT": "US",

            # Asia
            "Asia/Tokyo": "JP", "Asia/Seoul": "KR", "Asia/Shanghai": "CN",
            "Asia/Hong_Kong": "HK", "Asia/Singapore": "SG", "Asia/Dubai": "AE",
            "Asia/Bangkok": "TH", "Asia/Jakarta": "ID", "Asia/Ho_Chi_Minh": "VN",
            "Asia/Manila": "PH", "Asia/Kuala_Lumpur": "MY", "Asia/Taipei": "TW",
            "Asia/Jerusalem": "IL", "Asia/Riyadh": "SA", "Asia/Tehran": "IR",

            # Australia & Oceania
            "Australia/Sydney": "AU", "Australia/Melbourne": "AU", "Australia/Perth": "AU",
            "Australia/Brisbane": "AU", "Australia/Adelaide": "AU", "Pacific/Auckland": "NZ",

            # South America
            "America/Sao_Paulo": "BR", "America/Buenos_Aires": "AR", "America/Santiago": "CL",
            "America/Bogota": "CO", "America/Lima": "PE"
        }

        country = mapping.get(tz)
        if not country:
            # Try partial match (e.g. if tz is "Europe/Volgograd (MSK+0)")
            for key in mapping:
                if key in tz:
                    return mapping[key]

        return country or get_country_from_locale() or "US"
    except:
        return get_country_from_locale() or "US"

def location_tracker():
    """Background thread to fetch real IP and country code."""
    last_vpn_state = None
    while True:
        try:
            db = Database()
            routing_file = os.path.join(db.db_path.replace("protonvpn.db", "routing_state.json"))
            vpn_active = os.path.exists(routing_file)

            # Force refresh if VPN state changed
            force = False
            if vpn_active != last_vpn_state:
                force = True
                last_vpn_state = vpn_active
                # Clear IP immediately on state change to show "..." in UI
                with location_state["lock"]:
                    location_state["real_ip"] = ""
                    location_state["country_code"] = ""
                notify_status_change()
                if vpn_active:
                    time.sleep(2) # Give VPN time to stabilize routing

            # Fetch from ip-api (free, no key required)
            try:
                with urllib.request.urlopen("http://ip-api.com/json", timeout=5) as response:
                    data = json.loads(response.read().decode())
                    if data.get("status") == "success":
                        new_ip = data.get("query", "")
                        new_cc = data.get("countryCode", "US")

                        with location_state["lock"]:
                            changed = location_state["real_ip"] != new_ip
                            location_state["country_code"] = new_cc
                            location_state["real_ip"] = new_ip
                            location_state["last_check"] = time.time()

                        if changed or force:
                            notify_status_change()
            except Exception as e:
                print(f"[WARNING] IP location fetch failed: {e}", flush=True)

            # Check every 5 minutes when idle, or more often if no data
            sleep_time = 300 if location_state["country_code"] else 30
            time.sleep(sleep_time)

        except Exception:
            time.sleep(60)

class SudoRequiredError(Exception): pass

def cleanup_stale_cli_processes():
    """Kill any orphaned pvpn-next.exe processes except api-server and watchdog."""
    if sys.platform != "win32":
        return
    try:
        import psutil
        my_pid = os.getpid()
        for proc in psutil.process_iter(['pid', 'name', 'cmdline']):
            try:
                if proc.info['name'] and 'pvpn-next' in proc.info['name'].lower():
                    if proc.info['pid'] == my_pid:
                        continue
                    cmdline = proc.info.get('cmdline') or []
                    cmdline_str = ' '.join(cmdline).lower()
                    # Keep api-server and watchdog alive
                    if 'api-server' in cmdline_str or '_watchdog' in cmdline_str:
                        continue
                    print(f"[CLEANUP] Killing stale CLI process PID={proc.info['pid']}", flush=True)
                    proc.kill()
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
    except Exception as e:
        print(f"[WARNING] Stale process cleanup failed: {e}", flush=True)

def run_cli_elevated(args, sudo_password=None):
    """
    Universal Linux Elevation Logic:
    Works on NixOS (via wrappers), Arch, Ubuntu, etc.
    """
    import sys, subprocess, os, shutil, time

    # Identify binary path safely
    binary_name = sys.argv[0]
    binary_path = binary_name

    if not os.path.isabs(binary_name) or not os.path.exists(binary_name):
        found_path = shutil.which(binary_name)
        if found_path:
            binary_path = found_path
        else:
            binary_path = os.path.abspath(binary_name)

    if getattr(sys, 'frozen', False) or os.access(binary_path, os.X_OK):
        base_cmd = [binary_path]
    else:
        base_cmd = [sys.executable, binary_path]

    from pvpn_cli.routing import get_config_dir
    config_arg = f"--config-dir={get_config_dir()}"
    full_cmd = base_cmd + [config_arg] + args

    if sys.platform == "linux":
        env = os.environ.copy()
        for k in ["_MEIPASS", "_MEIPASS1", "_MEIPASS2", "_MEIPASS3"]:
            env.pop(k, None)
        env["PVPN_GUI_MODE"] = "1"
        robust_path = "/run/wrappers/bin:/run/current-system/sw/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
        env["PATH"] = f"{robust_path}:{env.get('PATH', '')}"

        def is_sandboxed():
            try:
                with open("/proc/self/status", "r") as f:
                    for line in f:
                        if line.startswith("NoNewPrivs:") and "1" in line:
                            return True
            except: pass
            return False

        sandboxed = is_sandboxed()
        systemd_run = shutil.which("systemd-run")

        def get_best_path(cmd):
            nix_wrapper = f"/run/wrappers/bin/{cmd}"
            if os.path.exists(nix_wrapper): return nix_wrapper
            return shutil.which(cmd)

        elevate_bin = get_best_path("doas") if shutil.which("doas") else (get_best_path("sudo") or "sudo")

        # 1. Try passwordless execution first (Silent check)
        try:
            check_cmd = [elevate_bin, "-n", "true"]
            if subprocess.run(check_cmd, env=env, capture_output=True).returncode == 0:
                subprocess.Popen([elevate_bin, "-n"] + full_cmd, env=env)
                return
        except Exception: pass

        # Password is required
        if not sudo_password:
            raise SudoRequiredError("Sudo password is required")

        # 2. Sudo with password provided via UI
        sudo_bin = get_best_path("sudo") or "sudo"
        launch_cmd = [sudo_bin, "-S", "env", "PVPN_GUI_MODE=1"] + full_cmd
        
        if sandboxed and systemd_run:
            prefix = [systemd_run, "--user", "--wait", "--quiet", "--pipe", "--property=KillMode=process"]
            for env_var in ["DISPLAY", "WAYLAND_DISPLAY", "XDG_RUNTIME_DIR", "DBUS_SESSION_BUS_ADDRESS", "XAUTHORITY"]:
                if env_var in os.environ: prefix.append(f"--setenv={env_var}={os.environ[env_var]}")
            prefix.append(f"--setenv=PATH={env['PATH']}")
            launch_cmd = prefix + launch_cmd

        try:
            with open("/tmp/pvpn-next-sudo.log", "a") as f:
                f.write(f"\n--- RUN CLI ELEVATED ---\nCMD: {launch_cmd}\n")
            
            import tempfile
            with tempfile.TemporaryFile() as out_f, tempfile.TemporaryFile() as err_f:
                proc = subprocess.Popen(launch_cmd, env=env, stdin=subprocess.PIPE, stdout=out_f, stderr=err_f)
                proc.stdin.write(f"{sudo_password}\n".encode())
                proc.stdin.close()
                proc.wait()
                out_f.seek(0)
                err_f.seek(0)
                outs = out_f.read()
                errs = err_f.read()
            
            out_str = outs.decode() if outs else ""
            err_str = errs.decode() if errs else ""

            with open("/tmp/pvpn-next-sudo.log", "a") as f:
                f.write(f"Return code: {proc.returncode}\nOUT: {out_str}\nERR: {err_str}\n")

            if proc.returncode != 0:
                err_lower = err_str.lower()
                if "incorrect password" in err_lower or "sorry, try again" in err_lower or "authentication failure" in err_lower:
                    raise Exception("Incorrect password")
                raise Exception(f"Elevation failed. Exit {proc.returncode}\nOUT:\n{out_str}\nERR:\n{err_str}")
            
            return out_str + "\n" + err_str
        except Exception as e:
            raise e

    # Windows (UAC) — track the process and wait for it to complete
    proc = subprocess.Popen(full_cmd, creationflags=0x08000000,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        stdout, stderr = proc.communicate(timeout=120)
        return (stdout.decode(errors='ignore') + "\n" + stderr.decode(errors='ignore')).strip()
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.communicate()
        return "[ERROR] Elevated process timed out after 120s"

@app.route("/api/events")
def events():
    def stream():
        q = threading.Event()
        with status_state["lock"]:
            status_state["subscribers"].append(q)

        try:
            # Send initial status
            yield f"data: {json.dumps(get_current_status_dict())}\n\n"

            while True:
                # Wait for update
                if q.wait(timeout=30):
                    q.clear()
                    yield f"data: {json.dumps(get_current_status_dict())}\n\n"
                else:
                    # Keep-alive
                    yield ": keep-alive\n\n"
        finally:
            with status_state["lock"]:
                if q in status_state["subscribers"]:
                    status_state["subscribers"].remove(q)

    return Response(stream(), mimetype="text/event-stream")

def notify_status_change():
    with status_state["lock"]:
        for q in status_state["subscribers"]:
            q.set()

def traffic_tracker():
    """Background thread in API process to track real-time traffic."""
    import psutil
    from .routing import get_config_dir
    db = Database()
    routing_file = os.path.join(db.db_path.replace("protonvpn.db", "routing_state.json"))
    iface = "awg0"

    while True:
        try:
            # Check if stats are enabled
            stats_enabled = db.get_setting("traffic_stats_enabled", "true") == "true"

            vpn_active = os.path.exists(routing_file)
            if vpn_active and stats_enabled:
                stats = psutil.net_io_counters(pernic=True)
                if iface in stats:
                    current = stats[iface]
                    rx = current.bytes_recv
                    tx = current.bytes_sent

                    if traffic_state["last_abs_rx"] > 0:
                        delta_rx = rx - traffic_state["last_abs_rx"]
                        delta_tx = tx - traffic_state["last_abs_tx"]

                        if delta_rx < 0: delta_rx = 0
                        if delta_tx < 0: delta_tx = 0

                        traffic_state["speed_rx"] = delta_rx
                        traffic_state["speed_tx"] = delta_tx
                        traffic_state["session_rx"] += delta_rx
                        traffic_state["session_tx"] += delta_tx

                        # Update historical DB
                        db.update_traffic_stats(delta_rx, delta_tx, 1)

                    traffic_state["last_abs_rx"] = rx
                    traffic_state["last_abs_tx"] = tx
                    traffic_state["is_active"] = True

                    # Force SSE update every second for live speed
                    notify_status_change()
                else:
                    traffic_state["speed_rx"] = 0
                    traffic_state["speed_tx"] = 0
            else:
                # Reset if VPN disconnected
                if traffic_state["is_active"]:
                    traffic_state["speed_rx"] = 0
                    traffic_state["speed_tx"] = 0
                    traffic_state["session_rx"] = 0
                    traffic_state["session_tx"] = 0
                    traffic_state["last_abs_rx"] = 0
                    traffic_state["last_abs_tx"] = 0
                    traffic_state["is_active"] = False
                    notify_status_change()

        except Exception:
            pass
        time.sleep(1)

def get_current_status_dict():
    try:
        db = Database()
        session = db.get_session()
        logged_in = session is not None and "access_token" in session

        max_tier = 0
        if logged_in:
            try:
                max_tier = int(db.get_setting("max_tier", "0"))
            except Exception:
                max_tier = 0

        routing_file = os.path.join(db.db_path.replace("protonvpn.db", "routing_state.json"))
        vpn_active = os.path.exists(routing_file)

        if vpn_active:
            import psutil
            import sys
            engine_running = False
            engine_name = "pvpn-engine.exe" if sys.platform == "win32" else "pvpn-engine"
            for proc in psutil.process_iter(['name']):
                try:
                    if proc.info['name'] and engine_name in proc.info['name'].lower():
                        engine_running = True
                        break
                except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                    pass
            if not engine_running:
                vpn_active = False

        # Logic for current state
        current_state = status_state["vpn_state"]
        if current_state not in ["CONNECTING", "DISCONNECTING"]:
            current_state = "CONNECTED" if vpn_active else "DISCONNECTED"

        # Fetch traffic stats
        traffic = {
            "speed_rx": traffic_state["speed_rx"],
            "speed_tx": traffic_state["speed_tx"],
            "session_rx": traffic_state["session_rx"],
            "session_tx": traffic_state["session_tx"]
        }

        historical = db.get_historical_stats()

        # Fetch location data
        with location_state["lock"]:
            country_code = location_state["country_code"]
            real_ip = location_state["real_ip"] or db.get_setting("current_real_ip", "")

        status_data = {
            "logged_in": logged_in,
            "bypass": db.get_setting("api_bypass", "0"),
            "active_server": db.get_setting("active_server_name", ""),
            "real_ip": real_ip,
            "country_code": country_code,
            "timezone_country_code": get_country_from_timezone(),
            "max_tier": max_tier,
            "uid": session.get("uid", "N/A") if logged_in else "N/A",
            "has_certificate": session is not None and bool(session.get("wg_private_key")),
            "vpn_state": current_state,
            "server_count": db.get_server_count(),
            "last_refresh": db.get_setting("last_server_fetch", "Never"),
            "locale": (locale.getdefaultlocale()[0] or "en_US") if hasattr(locale, 'getdefaultlocale') else "en_US",
            "traffic": traffic,
            "stats": historical,
            "traffic_stats_enabled": db.get_setting("traffic_stats_enabled", "true") == "true",
            "default_connect_strategy": db.get_setting("default_connect_strategy", "best"),
            "default_connect_server": db.get_setting("default_connect_server", ""),
            "os": sys.platform
        }

        with last_status_cache["lock"]:
            last_status_cache["data"] = status_data

        return status_data
    except Exception as e:
        print(f"[WARNING] get_current_status_dict failed (likely DB lock): {e}", flush=True)
        with last_status_cache["lock"]:
            if last_status_cache["data"]:
                return last_status_cache["data"]
        # Fallback if no cache
        return {
            "logged_in": False,
            "vpn_state": "UNKNOWN",
            "error": "Database is temporarily busy"
        }

@app.route("/api/status", methods=["GET"])
def get_status():
    return jsonify(get_current_status_dict())

@app.route("/api/login/guest", methods=["POST"])
def login_guest():
    api = ProtonAuthApi()
    try:
        response = api.login_guest()

        # Auto-initialize session (Register cert + Fetch servers) to match CLI behavior
        try:
            db = Database()
            api_vpn = ProtonVpnApi()
            session = db.get_session()

            if session and not session.get("wg_private_key"):
                from pvpn_cli.crypto import ProtonCrypto
                wg_priv, pem_pub = ProtonCrypto.generate_vpn_keys()
                db.update_certificate(wg_priv, pem_pub, 0, 0)
                response = api_vpn.register_cert(pem_pub)
                cert_data = response.get('Certificate', pem_pub)
                exp = response.get('ExpirationTime', 0)
                ref = response.get('RefreshTime', 0)
                db.update_certificate(wg_priv, cert_data, exp, ref)
                print("-> Guest Login: Certificate registered automatically.", flush=True)

            if db.get_server_count() == 0:
                api_vpn.fetch_servers()
                api_vpn.fetch_loads()
                print("-> Guest Login: Server list fetched automatically.", flush=True)
        except Exception as init_err:
            print(f"[WARNING] Guest login post-init failed: {init_err}", flush=True)

        return jsonify({"success": True, "uid": response.get('UID')})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 400

@app.route("/api/login/anonymous", methods=["POST"])
def login_anonymous():
    return login_guest()

@app.route("/api/login", methods=["POST"])
def login_user_endpoint():
    data = request.json or {}
    username = data.get("username")
    password = data.get("password")

    if not username or not password:
        return jsonify({"success": False, "error": "Username and password required"}), 400

    global login_state
    if login_state["status"] == "running":
        return jsonify({"success": False, "error": "Login already in progress"}), 400

    login_state["status"] = "running"
    login_state["error_message"] = ""
    login_state["event"].clear()
    login_state["2fa_code"] = ""
    login_state["uid"] = ""

    def two_factor_callback():
        login_state["status"] = "2fa_required"
        login_state["event"].wait()
        return login_state["2fa_code"]

    def login_worker():
        global login_state
        api = ProtonAuthApi()
        try:
            response = api.login_user(username, password, two_factor_callback)

            # Auto-initialize session
            try:
                db = Database()
                api_vpn = ProtonVpnApi()
                session = db.get_session()
                if session and not session.get("wg_private_key"):
                    from pvpn_cli.crypto import ProtonCrypto
                    wg_priv, pem_pub = ProtonCrypto.generate_vpn_keys()
                    db.update_certificate(wg_priv, pem_pub, 0, 0)
                    response = api_vpn.register_cert(pem_pub)
                    cert_data = response.get('Certificate', pem_pub)
                    exp = response.get('ExpirationTime', 0)
                    ref = response.get('RefreshTime', 0)
                    db.update_certificate(wg_priv, cert_data, exp, ref)
                if db.get_server_count() == 0:
                    api_vpn.fetch_servers()
                    api_vpn.fetch_loads()
            except Exception:
                pass

            login_state["status"] = "success"
            login_state["uid"] = response.get('UID')
        except Exception as e:
            login_state["status"] = "error"
            login_state["error_message"] = str(e)

    login_state["thread"] = threading.Thread(target=login_worker)
    login_state["thread"].daemon = True
    login_state["thread"].start()

    return jsonify({"success": True, "status": login_state["status"]})

@app.route("/api/trigger-captcha", methods=["POST"])
def trigger_captcha():
    # Spawns trigger-captcha without --gui so it opens a browser
    import subprocess, sys
    try:
        # We need to run the CLI script directly
        if getattr(sys, 'frozen', False):
            # PyInstaller binary
            cmd = [sys.executable, "trigger-captcha"]
        else:
            # Source script
            cmd = [sys.executable, sys.argv[0], "trigger-captcha"]
        
        # We use Popen so it runs in background and doesn't block
        subprocess.Popen(cmd)
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)})

@app.route("/api/login/status", methods=["GET"])
def get_login_status():
    global login_state
    return jsonify({
        "status": login_state["status"],
        "error": login_state["error_message"],
        "uid": login_state["uid"]
    })

@app.route("/api/login/2fa", methods=["POST"])
def submit_2fa():
    global login_state
    data = request.json or {}
    code = data.get("code")

    if login_state["status"] != "2fa_required":
        return jsonify({"success": False, "error": "Not waiting for 2FA"}), 400

    login_state["2fa_code"] = code
    login_state["status"] = "running"
    login_state["event"].set()

    return jsonify({"success": True})

@app.route("/api/servers/fetch", methods=["POST"])
def fetch_servers():
    api = ProtonVpnApi()
    data = request.get_json(silent=True) or {}

    # Priority: 1. Body param, 2. Accept-Language header, 3. System Locale
    loc = data.get("locale")
    if not loc:
        accept_lang = request.headers.get("Accept-Language", "")
        if accept_lang:
            loc = accept_lang.split(",")[0].replace("-", "_")
    if not loc:
        try:
            import locale
            loc, _ = locale.getdefaultlocale()
        except:
            loc = "en_US"

    print(f"-> Fetching full server list (Locale: {loc})...", flush=True)
    try:
        servers = api.fetch_servers()
        if loc:
            try:
                city_names = api.fetch_locale(loc)
                if city_names and city_names.get("Code") == 1000:
                    db = Database()
                    db.update_localized_cities(city_names.get("Cities", {}))
                    db.set_setting("locale", loc)
                    print(f"-> Localized city names updated for: {loc}", flush=True)
            except Exception as le:
                print(f"[WARNING] Failed to fetch localized city names: {le}", flush=True)

        msg = f"[SUCCESS] Fetched {len(servers)} servers."
        print(msg, flush=True)
        return jsonify({"success": True, "count": len(servers), "message": msg})
    except Exception as e:
        err_msg = f"[ERROR] Failed to fetch servers: {str(e)}"
        print(err_msg, flush=True)
        return jsonify({"success": False, "error": str(e), "message": err_msg}), 400

@app.route("/api/servers/loads", methods=["POST"])
def fetch_loads():
    print("-> Updating server loads from Proton API...", flush=True)
    api = ProtonVpnApi()
    # Ensure we don't 400 on empty body
    _ = request.get_json(silent=True)
    try:
        loads = api.fetch_loads()
        msg = f"[SUCCESS] Updated loads for {len(loads)} servers."
        print(msg, flush=True)
        return jsonify({"success": True, "count": len(loads), "message": msg})
    except Exception as e:
        err_msg = f"[ERROR] Failed to fetch loads: {str(e)}"
        print(err_msg, flush=True)
        return jsonify({"success": False, "error": str(e), "message": err_msg}), 400

@app.route("/api/servers", methods=["GET"])
def get_servers():
    db = Database()
    servers = db.get_all_servers()
    return jsonify({"success": True, "servers": servers})

@app.route("/api/cert/register", methods=["POST"])
def register_cert():
    api = ProtonVpnApi()
    data = request.json or {}
    public_key = data.get("public_key")
    db = Database()

    extended_cert = db.get_setting("extended_cert", "false") == "true"
    mode = "persistent" if extended_cert else None

    if not public_key:
        from pvpn_cli.crypto import ProtonCrypto
        wg_priv, pem_pub = ProtonCrypto.generate_vpn_keys()
        public_key = pem_pub
        db = Database()
        db.update_certificate(wg_priv, public_key, 0, 0)

    try:
        response = api.register_cert(public_key, mode=mode)
        if 'wg_priv' in locals():
            cert_data = response.get('Certificate', public_key)
            exp = response.get('ExpirationTime', 0)
            ref = response.get('RefreshTime', 0)
            db.update_certificate(wg_priv, cert_data, exp, ref)
        return jsonify({"success": True, "data": response})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 400

@app.route("/api/cert/info", methods=["GET"])
def get_cert_info():
    db = Database()
    session = db.get_session()
    if not session or not session.get("wg_private_key"):
        return jsonify({"success": False, "error": "No certificate found"})
        
    import hashlib
    # Compute a short ID from the public key or private key just for display
    priv_key = session.get("wg_private_key")
    cert_id = hashlib.sha256(priv_key.encode()).hexdigest()[:12].upper()
    
    expires_at = session.get("cert_expires_at", 0)
    updated_at = session.get("updated_at", "")
    from datetime import datetime
    
    if expires_at > 0:
        expires_str = datetime.fromtimestamp(expires_at).strftime('%Y-%m-%d %H:%M')
        issued_str = updated_at.split(" ")[0] if " " in updated_at else updated_at
        if not issued_str:
            issued_str = datetime.now().strftime('%Y-%m-%d')
    else:
        expires_str = "Unknown"
        issued_str = "Unknown"
        
    return jsonify({
        "success": True,
        "cert": {
            "id": cert_id,
            "issued": issued_str,
            "expires": expires_str
        }
    })


@app.route("/api/locale", methods=["GET"])
def get_locale():
    try:
        loc, _ = locale.getdefaultlocale()
        return jsonify({"locale": loc or "en_US"})
    except Exception:
        return jsonify({"locale": "en_US"})

@app.route("/api/settings/bypass", methods=["POST"])
def set_bypass():
    data = request.json or {}
    strategy = data.get("strategy")
    if strategy not in ["0", "1", "2", "3"]:
        return jsonify({"success": False, "error": "Invalid strategy"}), 400

    db = Database()
    db.set_setting("api_bypass", strategy)

    strategy_names = {
        "0": "Direct Connection",
        "1": "Cloudflare Proxy",
        "2": "Netlify Proxy",
        "3": "Deno Proxy"
    }
    print(f"-> API Bypass strategy changed to: {strategy_names.get(strategy, strategy)}", flush=True)

    return jsonify({"success": True, "strategy": strategy})

@app.route("/api/settings", methods=["GET"])
def get_settings():
    db = Database()
    settings = {
        "protocol": db.get_setting("protocol", "wireguard"),
        "obfuscation_enabled": db.get_setting("obfuscation_enabled", "false"),
        "obfuscation_config": db.get_setting("obfuscation_config", "vpn-next-default"),
        "split_tunneling": db.get_setting("split_tunneling", "false"),
        "custom_dns": db.get_setting("custom_dns", ""),
        "kill_switch": db.get_setting("kill_switch", "false"),
        "auto_connect": db.get_setting("auto_connect", "false"),
        "spoof_country": db.get_setting("spoof_country", "false"),
        "allow_lan": db.get_setting("allow_lan", "false"),
        "vpn_port": db.get_setting("vpn_port", "0"),
        "gui_theme": db.get_setting("gui_theme", "system"),
        "traffic_stats_enabled": db.get_setting("traffic_stats_enabled", "true"),
        "default_connect_strategy": db.get_setting("default_connect_strategy", "best"),
        "default_connect_server": db.get_setting("default_connect_server", ""),
        "extended_cert": db.get_setting("extended_cert", "false")
    }
    return jsonify({"success": True, "settings": settings})

@app.route("/api/settings", methods=["POST"])
def update_settings():
    db = Database()
    data = request.json or {}
    messages = []
    allowed_keys = [
        "protocol", "obfuscation_enabled", "obfuscation_config",
        "split_tunneling", "custom_dns", "kill_switch", "auto_connect",
        "spoof_country", "allow_lan", "vpn_port", "gui_theme",
        "traffic_stats_enabled", "default_connect_strategy", "default_connect_server",
        "extended_cert"
    ]
    for key, value in data.items():
        if key in allowed_keys:
            db.set_setting(key, str(value).lower() if isinstance(value, bool) else str(value))

            # Mimic CLI logging style
            msg = f"-> Setting changed: {key} = {value}"
            if key == "custom_dns":
                msg = f"-> DNS Configuration updated to: {value or 'Default'}"
            elif key == "protocol":
                msg = f"-> VPN Protocol set to: {value.upper()}"
            elif key == "vpn_port":
                msg = f"-> VPN Port set to: {value if value != '0' else 'Auto'}"
            elif key == "gui_theme":
                msg = f"-> GUI Theme saved to database: {value}"

            print(msg, flush=True)
            messages.append(msg)

    return jsonify({"success": True, "messages": messages})

@app.route("/api/settings/split", methods=["GET"])
def get_split_settings():
    from pvpn_cli.routing import get_config_dir
    config_path = os.path.join(get_config_dir(), "split_tunnel.json")
    if os.path.exists(config_path):
        try:
            with open(config_path, "r") as f:
                return jsonify(json.load(f))
        except:
            pass
    return jsonify({"exclude_lan": False, "split_items": []})

@app.route("/api/settings/split", methods=["POST"])
def update_split_settings():
    from pvpn_cli.routing import get_config_dir
    data = request.json or {}
    config_path = os.path.join(get_config_dir(), "split_tunnel.json")
    try:
        with open(config_path, "w") as f:
            json.dump(data, f)
        msg = f"-> Split Tunneling configuration updated ({len(data.get('split_items', []))} items)"
        print(msg, flush=True)
        return jsonify({"success": True, "message": msg})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 400

@app.route("/api/awg", methods=["GET"])
def get_awg_configs():
    db = Database()
    configs = db.get_awg_configs()
    return jsonify({"success": True, "configs": configs})

@app.route("/api/awg", methods=["POST"])
def add_awg_config():
    db = Database()
    data = request.json or {}
    name = data.get("name")
    params = data.get("params")
    junk_level = data.get("junk_level", 3)
    if not name or not params:
        return jsonify({"success": False, "error": "name and params required"}), 400
    db.add_awg_config(name, params, junk_level)
    return jsonify({"success": True})

@app.route("/api/awg/generate-i1", methods=["POST"])
def generate_i1():
    data = request.json or {}
    domain = data.get("domain")
    from pvpn_cli.crypto import QuicI1Generator
    import secrets

    if domain:
        i1 = QuicI1Generator.generate_i1(domain)
    else:
        i1 = QuicI1Generator.generate_i1("google.com")

    return jsonify({"success": True, "i1": i1})

@app.route("/api/awg", methods=["DELETE"])
def delete_awg_config():
    db = Database()
    data = request.json or {}
    name = data.get("name")
    if not name:
        return jsonify({"success": False, "error": "name required"}), 400

    if name.startswith("preset-") or name == "vpn-next-default":
        return jsonify({"success": False, "error": "Cannot delete built-in config"}), 400

    db.delete_awg_config(name)
    return jsonify({"success": True})

@app.route("/api/vpn/recents", methods=["GET"])
def get_recent_connections():
    db = Database()
    recents = db.get_recent_connections()
    return jsonify({"success": True, "recents": recents})

@app.route("/api/vpn/connect", methods=["POST"])
def vpn_connect():
    """Trigger VPN connect via the CLI connect logic."""
    with status_state["lock"]:
        if status_state["vpn_state"] in ["CONNECTING", "DISCONNECTING"]:
            return jsonify({"success": False, "error": "Connection action already in progress"}), 400

    data = request.json or {}
    server = data.get("server")
    if not server:
        return jsonify({"success": False, "error": "server required"}), 400

    db = Database()
    vpn = ProtonVpnApi()

    # Handle "default" or "fastest" server aliases
    if server in ["default", "fastest"]:
        strategy = db.get_setting("default_connect_strategy", "best")
        if server == "fastest": strategy = "best" # UI Quick Connect always uses "best"

        if strategy == "best":
            best = vpn.get_best_server()
            if best:
                server = best.get("ID")
            else:
                return jsonify({"success": False, "error": "No servers available for your tier."}), 400
        elif strategy == "recent":
            recents = db.get_recent_connections(limit=1)
            if recents:
                server = recents[0].get("id")
            else:
                # Fallback to best if no recents
                best = vpn.get_best_server()
                server = best.get("ID") if best else None
        elif strategy == "custom":
            custom_id = db.get_setting("default_connect_server")
            if custom_id:
                server = custom_id
            else:
                best = vpn.get_best_server()
                server = best.get("ID") if best else None

    if not server:
         return jsonify({"success": False, "error": "Could not determine server to connect."}), 400

    try:
        # Kill any stale CLI processes from previous operations
        cleanup_stale_cli_processes()

        db.add_recent_connection(server)
        print(f"-> GUI Connection request: {server}", flush=True)

        with status_state["lock"]:
            was_connected = status_state["vpn_state"] == "CONNECTED"
            if was_connected:
                status_state["vpn_state"] = "DISCONNECTING"
        
        if was_connected:
            notify_status_change()
            print(f"-> Active connection found, disconnecting first...", flush=True)
            try:
                run_cli_elevated(["disconnect"], sudo_password=data.get("sudo_password"))
            except Exception as de:
                print(f"[WARNING] Disconnect before reconnect failed: {de}", flush=True)

        with status_state["lock"]:
            status_state["vpn_state"] = "CONNECTING"
            
        notify_status_change()

        output = run_cli_elevated(["connect", server], sudo_password=data.get("sudo_password"))
        msgs = output.splitlines() if output else []
        return jsonify({"success": True, "message": f"Connection to {server} initiated.", "messages": msgs})
    except SudoRequiredError:
        status_state["vpn_state"] = "DISCONNECTED"
        notify_status_change()
        return jsonify({"success": False, "requires_password": True}), 401
    except Exception as e:
        status_state["vpn_state"] = "DISCONNECTED"
        notify_status_change()
        return jsonify({"success": False, "error": str(e)}), 400

@app.route("/api/vpn/disconnect", methods=["POST"])
def vpn_disconnect():
    """Trigger VPN disconnect via the CLI disconnect logic."""
    with status_state["lock"]:
        if status_state["vpn_state"] in ["CONNECTING", "DISCONNECTING"]:
            return jsonify({"success": False, "error": "Connection action already in progress"}), 400

    try:
        # Kill any stale CLI processes from previous operations
        cleanup_stale_cli_processes()

        print(f"-> GUI Disconnect request", flush=True)

        status_state["vpn_state"] = "DISCONNECTING"
        notify_status_change()

        data = request.json or {}
        output = run_cli_elevated(["disconnect"], sudo_password=data.get("sudo_password"))
        msgs = output.splitlines() if output else []
        return jsonify({"success": True, "message": "Disconnection initiated.", "messages": msgs})
    except SudoRequiredError:
        status_state["vpn_state"] = "CONNECTED"  # rollback state
        notify_status_change()
        return jsonify({"success": False, "requires_password": True}), 401
    except Exception as e:
        status_state["vpn_state"] = "UNKNOWN"
        notify_status_change()
        return jsonify({"success": False, "error": str(e)}), 400

@app.route("/api/logs", methods=["GET"])
def get_logs():
    from .routing import get_config_dir
    config_dir = get_config_dir()
    log_files = ["client.log", "daemon.log", "awg.log", "service.log"]
    logs = {}
    for filename in log_files:
        path = os.path.join(config_dir, filename)
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    # Return last 1000 lines for efficiency
                    lines = f.readlines()
                    logs[filename] = "".join(lines[-1000:])
            except Exception as e:
                logs[filename] = f"Error reading log: {str(e)}"
        else:
            logs[filename] = "Log file not found."
    return jsonify(logs)

def status_watcher():
    """Background thread to watch for routing_state.json changes."""
    db = Database()
    routing_file = os.path.join(db.db_path.replace("protonvpn.db", "routing_state.json"))
    last_exists = os.path.exists(routing_file)

    while True:
        time.sleep(0.5)
        exists = os.path.exists(routing_file)

        # If state was CONNECTING/DISCONNECTING, we check if it finished
        current_state = status_state["vpn_state"]

        changed = False
        if exists != last_exists:
            changed = True
        elif current_state == "CONNECTING" and exists:
            changed = True
        elif current_state == "DISCONNECTING" and not exists:
            changed = True

        if changed:
            status_state["vpn_state"] = "CONNECTED" if exists else "DISCONNECTED"
            last_exists = exists
            notify_status_change()

def run_api_server(port=34115, debug=False):
    import logging
    log = logging.getLogger('werkzeug')
    if debug:
        log.setLevel(logging.INFO)
    else:
        log.setLevel(logging.ERROR)

    # Start status watcher
    watcher = threading.Thread(target=status_watcher, daemon=True)
    watcher.start()

    # Start traffic tracker
    tracker = threading.Thread(target=traffic_tracker, daemon=True)
    tracker.start()

    # Start location tracker
    locator = threading.Thread(target=location_tracker, daemon=True)
    locator.start()

    print(f"Starting API Daemon on port {port} (Debug: {debug})...", flush=True)
    app.run(host="127.0.0.1", port=port, debug=False, use_reloader=False)