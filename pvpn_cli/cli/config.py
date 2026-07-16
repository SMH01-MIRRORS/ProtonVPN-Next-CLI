"""Configuration directory and bundled-resource path helpers shared across the CLI."""

import atexit
import os
import platform
import subprocess
import sys


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


def fix_config_permissions():
    if platform.system() == "Windows":
        return
    if os.geteuid() != 0:
        return
    orig_user = os.environ.get("SUDO_USER") or os.environ.get("DOAS_USER")
    if not orig_user or orig_user == "root":
        return
    try:
        import pwd
        pw = pwd.getpwnam(orig_user)
        uid = pw.pw_uid
        gid = pw.pw_gid
    except Exception:
        return

    config_dir = get_config_dir()
    try:
        os.chown(config_dir, uid, gid)
        for root, dirs, files in os.walk(config_dir):
            for d in dirs:
                os.chown(os.path.join(root, d), uid, gid)
            for f in files:
                os.chown(os.path.join(root, f), uid, gid)
    except Exception:
        pass


# Config files may be created while running as root (sudo/doas); make sure
# ownership is returned to the original user when the process exits.
atexit.register(fix_config_permissions)


def get_base_dir() -> str:
    """Directory that contains bundled resources (e.g. the engine binary)."""
    if getattr(sys, 'frozen', False):
        return sys._MEIPASS
    # Project root in dev mode: the directory that contains the pvpn_cli package.
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def get_engine_path() -> str:
    engine_name = "pvpn-engine.exe" if platform.system() == "Windows" else "pvpn-engine"
    return os.path.abspath(os.path.join(get_base_dir(), "engine", engine_name))
