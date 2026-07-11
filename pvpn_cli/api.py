import os
import sys
import locale
import threading
import json
from flask import Flask, request, jsonify
from flask_cors import CORS
from pvpn_cli.database import Database
from pvpn_cli.auth import ProtonAuthApi
from pvpn_cli.vpn import ProtonVpnApi

app = Flask(__name__)
CORS(app)

# Global state for login flow
login_state = {
    "thread": None,
    "status": "idle",  # idle, running, 2fa_required, success, error
    "error_message": "",
    "event": threading.Event(),
    "2fa_code": "",
    "uid": ""
}

@app.route("/api/status", methods=["GET"])
def get_status():
    db = Database()
    session = db.get_session()
    logged_in = session is not None and "access_token" in session

    max_tier = 0
    if logged_in:
        try:
            # We cache max_tier in database to avoid frequent network calls
            max_tier_str = db.get_setting("max_tier", "0")
            if max_tier_str == "0":
                api = ProtonVpnApi()
                max_tier = api.get_max_tier()
                db.set_setting("max_tier", str(max_tier))
            else:
                max_tier = int(max_tier_str)
        except Exception:
            max_tier = 0

    status = {
        "logged_in": logged_in,
        "bypass": db.get_setting("api_bypass", "0"),
        "active_server": db.get_setting("active_server_name", ""),
        "real_ip": db.get_setting("current_real_ip", ""),
        "max_tier": max_tier
    }
    return jsonify(status)

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
    try:
        servers = api.fetch_servers()
        if loc:
            try:
                city_names = api.fetch_locale(loc)
                if city_names and city_names.get("Code") == 1000:
                    db = Database()
                    db.update_localized_cities(city_names.get("Cities", {}))
            except Exception as le:
                print(f"[WARNING] Failed to fetch localized city names: {le}")
        return jsonify({"success": True, "count": len(servers)})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 400

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
        "vpn_port": db.get_setting("vpn_port", "0")
    }
    return jsonify({"success": True, "settings": settings})

@app.route("/api/settings", methods=["POST"])
def update_settings():
    db = Database()
    data = request.json or {}
    messages = []
    for key, value in data.items():
        if key in ["protocol", "obfuscation_enabled", "obfuscation_config", "split_tunneling", "custom_dns", "kill_switch", "auto_connect", "spoof_country", "allow_lan", "vpn_port"]:
            db.set_setting(key, str(value).lower() if isinstance(value, bool) else str(value))

            # Mimic CLI logging style
            msg = f"-> Setting changed: {key} = {value}"
            if key == "custom_dns":
                msg = f"-> DNS Configuration updated to: {value or 'Default'}"
            elif key == "protocol":
                msg = f"-> VPN Protocol set to: {value.upper()}"

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
    if not name or not params:
        return jsonify({"success": False, "error": "name and params required"}), 400
    db.add_awg_config(name, params)
    return jsonify({"success": True})

@app.route("/api/vpn/connect", methods=["POST"])
def vpn_connect():
    """Trigger VPN connect via the CLI connect logic."""
    data = request.json or {}
    server = data.get("server")
    if not server:
        return jsonify({"success": False, "error": "server required"}), 400
    try:
        # Import and call the same do_connect used by CLI
        import subprocess, sys
        cli_path = sys.executable if not getattr(sys, 'frozen', False) else sys.argv[0]

        print(f"-> GUI Connection request: {server}", flush=True)

        # Run as background but INHERIT stdout/stderr so we see logs in terminal
        subprocess.Popen([cli_path, "connect", server],
                         stdout=None, stderr=None)

        return jsonify({"success": True, "message": f"Connection to {server} initiated."})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 400

@app.route("/api/vpn/disconnect", methods=["POST"])
def vpn_disconnect():
    """Trigger VPN disconnect via the CLI disconnect logic."""
    try:
        import subprocess, sys
        cli_path = sys.executable if not getattr(sys, 'frozen', False) else sys.argv[0]

        print(f"-> GUI Disconnect request", flush=True)

        subprocess.Popen([cli_path, "disconnect"],
                         stdout=None, stderr=None)

        return jsonify({"success": True, "message": "Disconnection initiated."})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 400

def run_api_server(port=34115, debug=False):
    import logging
    log = logging.getLogger('werkzeug')
    if debug:
        log.setLevel(logging.INFO)
    else:
        log.setLevel(logging.ERROR)

    print(f"Starting API Daemon on port {port} (Debug: {debug})...", flush=True)
    app.run(host="127.0.0.1", port=port, debug=False, use_reloader=False)
