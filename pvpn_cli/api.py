import os
import sys
import locale
import threading
import json
import shutil
import subprocess
import time
from flask import Flask, request, jsonify, Response
from flask_cors import CORS
from pvpn_cli.database import Database
from pvpn_cli.auth import ProtonAuthApi
from pvpn_cli.vpn import ProtonVpnApi

app = Flask(__name__)
CORS(app)

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

def run_cli_elevated(args):
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

    # In NixOS, Python scripts are often wrapped in bash scripts to inject PYTHONPATH.
    # Running them via sys.executable strips these vars. If it's executable, run it directly!
    if getattr(sys, 'frozen', False) or os.access(binary_path, os.X_OK):
        base_cmd = [binary_path]
    else:
        base_cmd = [sys.executable, binary_path]

    from pvpn_cli.routing import get_config_dir
    config_arg = f"--config-dir={get_config_dir()}"
    full_cmd = base_cmd + [config_arg] + args

    if sys.platform == "linux":
        # Force /run/wrappers/bin into PATH for NixOS to ensure SUID binaries are found
        env = os.environ.copy()
        env["PATH"] = f"/run/wrappers/bin:{env.get('PATH', '')}"

        # CHECK FOR LINUX SANDBOX (NO_NEW_PRIVS)
        def is_sandboxed():
            try:
                with open("/proc/self/status", "r") as f:
                    for line in f:
                        if line.startswith("NoNewPrivs:") and "1" in line:
                            return True
            except:
                pass
            return False

        sandboxed = is_sandboxed()
        if sandboxed:
            print("-> Sandbox (NO_NEW_PRIVS) detected. Will use systemd escape for terminals.", flush=True)

        systemd_run = shutil.which("systemd-run")

        def get_best_path(cmd):
            # NixOS prioritizes /run/wrappers/bin/ for SUID binaries
            nix_wrapper = f"/run/wrappers/bin/{cmd}"
            if os.path.exists(nix_wrapper):
                return nix_wrapper
            return shutil.which(cmd)

        # 1. Try GUI tools first (Standard for most distros)
        # We do NOT use systemd-run for GUI tools. Polkit needs the current session context!
        is_kde = os.environ.get("KDE_FULL_SESSION") == "true" or "plasma" in os.environ.get("XDG_CURRENT_DESKTOP", "").lower()
        gui_preference = ["kdesu", "pkexec"] if is_kde else ["pkexec", "kdesu"]

        for tool in gui_preference:
            path = get_best_path(tool)
            if path:
                try:
                    print(f"-> Trying GUI elevation: {tool}", flush=True)
                    proc = subprocess.Popen([path] + full_cmd, env=env)

                    # Wait briefly to see if it crashes immediately
                    try:
                        proc.wait(timeout=0.5)
                        if proc.returncode != 0:
                            print(f"[WARNING] {tool} exited immediately with code {proc.returncode}. Trying next...", flush=True)
                            continue # Try the next GUI tool or fallback
                        return # Successfully executed
                    except subprocess.TimeoutExpired:
                        return
                except Exception as e:
                    print(f"[WARNING] Failed to run {tool}: {e}", flush=True)
                    pass

        # 2. Terminal Fallback (Reliable for both sudo and doas)
        print("-> GUI elevation failed or missing, falling back to Terminal...", flush=True)
        terminals = [
            ("konsole", ["-e"]),
            ("kitty", ["--"]),
            ("gnome-terminal", ["--"]),
            ("xfce4-terminal", ["-e"]),
            ("mate-terminal", ["--"]),
            ("alacritty", ["-e"]),
            ("xterm", ["-e"])
        ]

        elevate_bin = get_best_path("sudo") or "sudo"

        for term, term_args in terminals:
            term_path = shutil.which(term)
            if term_path:
                try:
                    # Construct command string
                    safe_args = " ".join([f"'{c}'" for c in [elevate_bin] + full_cmd])

                    # Add a pause if the command fails, so the terminal stays open and you can read the error!
                    pause_cmd = f"{safe_args}; EXIT_CODE=$?; if [ $EXIT_CODE -ne 0 ]; then echo ''; echo '=== ERROR ==='; echo 'Command failed with exit code '$EXIT_CODE; echo 'Press ENTER to close this window...'; read dummy; fi"

                    launch_cmd = [term_path] + term_args + ["sh", "-c", pause_cmd]

                    # Apply Jailbreak ONLY for the terminal
                    if sandboxed and systemd_run:
                        prefix = [systemd_run, "--user", "--quiet"]
                        # Forward essential variables so the terminal can talk to Wayland/X11
                        for env_var in ["DISPLAY", "WAYLAND_DISPLAY", "XDG_RUNTIME_DIR", "DBUS_SESSION_BUS_ADDRESS", "XAUTHORITY", "KDE_FULL_SESSION", "XDG_CURRENT_DESKTOP"]:
                            if env_var in os.environ:
                                prefix.append(f"--setenv={env_var}={os.environ[env_var]}")
                        # Inject PATH so the systemd service sees our NixOS wrapper path
                        prefix.append(f"--setenv=PATH={env['PATH']}")
                        launch_cmd = prefix + launch_cmd

                    print(f"-> Spawning terminal: {term}", flush=True)
                    subprocess.Popen(launch_cmd, env=env)
                    return
                except Exception as e:
                    print(f"[WARNING] Failed to spawn terminal {term}: {e}", flush=True)
                    pass

        # 3. Last resort: direct run
        try:
            subprocess.Popen([elevate_bin] + full_cmd, env=env)
            return
        except:
            pass

    # Windows (UAC)
    subprocess.Popen(full_cmd)

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

                        # Save to historical DB via the daemon's logic or here
                        # For simplicity, we can do it here every 10 seconds
                        if int(time.time()) % 10 == 0:
                             db.update_traffic_stats(delta_rx, delta_tx)
                        else:
                             db.update_traffic_stats(delta_rx, delta_tx)

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
    db = Database()
    session = db.get_session()
    logged_in = session is not None and "access_token" in session

    max_tier = 0
    if logged_in:
        try:
            max_tier_str = db.get_setting("max_tier", "0")
            if max_tier_str == "0":
                api = ProtonVpnApi()
                max_tier = api.get_max_tier()
                db.set_setting("max_tier", str(max_tier))
            else:
                max_tier = int(max_tier_str)
        except Exception:
            max_tier = 0

    routing_file = os.path.join(db.db_path.replace("protonvpn.db", "routing_state.json"))
    vpn_active = os.path.exists(routing_file)

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

    return {
        "logged_in": logged_in,
        "bypass": db.get_setting("api_bypass", "0"),
        "active_server": db.get_setting("active_server_name", ""),
        "real_ip": db.get_setting("current_real_ip", ""),
        "max_tier": max_tier,
        "uid": session.get("uid", "N/A") if logged_in else "N/A",
        "vpn_state": current_state,
        "server_count": db.get_server_count(),
        "last_refresh": db.get_setting("last_server_fetch", "Never"),
        "locale": (locale.getdefaultlocale()[0] or "en_US") if hasattr(locale, 'getdefaultlocale') else "en_US",
        "traffic": traffic,
        "stats": historical,
        "traffic_stats_enabled": db.get_setting("traffic_stats_enabled", "true") == "true",
        "default_connect_strategy": db.get_setting("default_connect_strategy", "best"),
        "default_connect_server": db.get_setting("default_connect_server", "")
    }

@app.route("/api/status", methods=["GET"])
def get_status():
    return jsonify(get_current_status_dict())

@app.route("/api/login/guest", methods=["POST"])
def login_guest():
    api = ProtonAuthApi()
    try:
        response = api.login_guest()
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
            login_state["status"] = "success"
            login_state["uid"] = response.get('UID')
        except Exception as e:
            login_state["status"] = "error"
            login_state["error_message"] = str(e)

    login_state["thread"] = threading.Thread(target=login_worker)
    login_state["thread"].daemon = True
    login_state["thread"].start()

    return jsonify({"success": True, "status": login_state["status"]})

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
    data = request.json or {}
    loc = data.get("locale")
    print(f"-> Fetching full server list (Locale: {loc})...", flush=True)
    try:
        servers = api.fetch_servers()
        if loc:
            try:
                city_names = api.fetch_locale(loc)
                if city_names and city_names.get("Code") == 1000:
                    db = Database()
                    db.update_localized_cities(city_names.get("Cities", {}))
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

    if not public_key:
        from pvpn_cli.crypto import ProtonCrypto
        wg_priv, pem_pub = ProtonCrypto.generate_vpn_keys()
        public_key = pem_pub
        db = Database()
        db.update_certificate(wg_priv, public_key, 0, 0)

    try:
        response = api.register_cert(public_key)
        return jsonify({"success": True, "data": response})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 400

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
        "default_connect_server": db.get_setting("default_connect_server", "")
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
        "traffic_stats_enabled", "default_connect_strategy", "default_connect_server"
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
        db.add_recent_connection(server)
        print(f"-> GUI Connection request: {server}", flush=True)

        status_state["vpn_state"] = "CONNECTING"
        notify_status_change()

        run_cli_elevated(["connect", server])
        return jsonify({"success": True, "message": f"Connection to {server} initiated."})
    except Exception as e:
        status_state["vpn_state"] = "DISCONNECTED"
        notify_status_change()
        return jsonify({"success": False, "error": str(e)}), 400

@app.route("/api/vpn/disconnect", methods=["POST"])
def vpn_disconnect():
    """Trigger VPN disconnect via the CLI disconnect logic."""
    try:
        print(f"-> GUI Disconnect request", flush=True)

        status_state["vpn_state"] = "DISCONNECTING"
        notify_status_change()

        run_cli_elevated(["disconnect"])
        return jsonify({"success": True, "message": "Disconnection initiated."})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 400

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

    print(f"Starting API Daemon on port {port} (Debug: {debug})...", flush=True)
    app.run(host="127.0.0.1", port=port, debug=False, use_reloader=False)
