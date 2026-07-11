import os
import sys
import locale
import threading
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
    status = {
        "logged_in": session is not None and "access_token" in session,
        "bypass": db.get_setting("api_bypass", "0"),
        "active_server": db.get_setting("active_server_name", ""),
        "real_ip": db.get_setting("current_real_ip", "")
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
    try:
        servers = api.fetch_servers()
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
        "obfuscation_config": db.get_setting("obfuscation_config", "vpn-next-default")
    }
    return jsonify({"success": True, "settings": settings})

@app.route("/api/settings", methods=["POST"])
def update_settings():
    db = Database()
    data = request.json or {}
    for key, value in data.items():
        if key in ["protocol", "obfuscation_enabled", "obfuscation_config"]:
            db.set_setting(key, str(value).lower())
    return jsonify({"success": True})

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
        # Run as background so the API call returns immediately
        subprocess.Popen([cli_path, "connect", server],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 400

@app.route("/api/vpn/disconnect", methods=["POST"])
def vpn_disconnect():
    """Trigger VPN disconnect via the CLI disconnect logic."""
    try:
        import subprocess, sys
        cli_path = sys.executable if not getattr(sys, 'frozen', False) else sys.argv[0]
        subprocess.Popen([cli_path, "disconnect"],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 400

def run_api_server(port=34115):
    import logging
    log = logging.getLogger('werkzeug')
    log.setLevel(logging.ERROR)

    print(f"Starting API Daemon on port {port}...")
    app.run(host="127.0.0.1", port=port, debug=False, use_reloader=False)
