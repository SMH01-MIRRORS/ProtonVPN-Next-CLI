"""Background daemon commands: run, manage, session refresh, autostart."""

import os
import platform
import subprocess
import sys

from pvpn_cli.auth import ProtonAuthApi
from pvpn_cli.database import Database

from pvpn_cli.cli.config import get_config_dir


def do_daemon():
    from pvpn_cli.workers import BackgroundWorkers
    daemon = BackgroundWorkers()
    daemon.start()

def do_update_session():
    import time
    print("Updating session manually...")
    api = ProtonAuthApi()
    try:
        api.refresh_session()
        db = Database()
        db.set_setting("last_session_refresh", str(time.time()))
        print("[SUCCESS] Session updated.")
    except Exception as e:
        print(f"[ERROR] Session update failed: {e}")
        sys.exit(1)

def do_manage_daemon(action: str):
    db = Database()
    daemon_pid_path = os.path.join(get_config_dir(), "daemon.pid")
    
    if action in ("stop", "off"):
        db.set_setting("daemon_enabled", "0")
        if os.path.exists(daemon_pid_path):
            try:
                with open(daemon_pid_path, "r") as f:
                    pid = int(f.read().strip())
                if platform.system() == "Windows":
                    subprocess.run(["taskkill", "/F", "/PID", str(pid)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=0x08000000)
                else:
                    subprocess.run(["kill", "-9", str(pid)])
                os.remove(daemon_pid_path)
            except Exception:
                pass
        print("-> Background daemon is now STOPPED and DISABLED.")
        
    elif action in ("start", "on"):
        db.set_setting("daemon_enabled", "1")
        print("-> Background daemon is now ENABLED.")
        # It will be auto-started by ensure_daemon_running at the end of the command

    elif action == "test":
        print("=== Running Daemon Test ===")
        from pvpn_cli.workers import BackgroundWorkers
        worker = BackgroundWorkers()
        worker.sync_servers()
        worker.refresh_session()
        worker.check_certificate(force=True)
        print("=== Daemon Test Complete ===")

def ensure_daemon_running():
    db = Database()
    if db.get_setting("daemon_enabled", "1") == "0":
        return
        
    if platform.system() == "Windows":
        try:
            import psutil
            watchdog_running = False
            for proc in psutil.process_iter(['name', 'cmdline']):
                try:
                    if proc.info['name'] and 'pvpn-watchdog' in proc.info['name'].lower():
                        watchdog_running = True
                        break
                    cmdline = proc.info.get('cmdline') or []
                    if any('_watchdog' in arg for arg in cmdline):
                        watchdog_running = True
                        break
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    pass
            if watchdog_running:
                return  # Watchdog is running and handles daemon tasks! No need to spawn _daemon!
        except Exception:
            pass

    daemon_pid_path = os.path.join(get_config_dir(), "daemon.pid")
    is_running = False
    
    if os.path.exists(daemon_pid_path):
        try:
            with open(daemon_pid_path, "r") as f:
                pid = int(f.read().strip())
            try:
                if platform.system() == "Windows":
                    import psutil
                    try:
                        p = psutil.Process(pid)
                        if p.is_running() and any(x in p.name().lower() for x in ("pvpn", "python")):
                            is_running = True
                    except (psutil.NoSuchProcess, psutil.AccessDenied):
                        pass
                else:
                    os.kill(pid, 0)
                    is_running = True
            except Exception:
                pass
        except Exception:
            pass

    daemon_log_path = os.path.join(get_config_dir(), "daemon.log")
    daemon_log_file = open(daemon_log_path, "a")

    if not is_running:
        if getattr(sys, 'frozen', False):
            daemon_cmd = [sys.executable, "_daemon"]
        else:
            daemon_cmd = [sys.executable, os.path.abspath(sys.argv[0]), "_daemon"]
        
        env = os.environ.copy()
        for k in ["_MEIPASS", "_MEIPASS1", "_MEIPASS2", "_MEIPASS3"]:
            env.pop(k, None)

        if platform.system() == "Windows":
            CREATE_NO_WINDOW = 0x08000000
            daemon_proc = subprocess.Popen(
                daemon_cmd, 
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=daemon_log_file, 
                stderr=daemon_log_file, 
                close_fds=True,
                creationflags=CREATE_NO_WINDOW
            )
        else:
            daemon_proc = subprocess.Popen(
                daemon_cmd, 
                stdin=subprocess.DEVNULL,
                stdout=daemon_log_file, 
                stderr=daemon_log_file, 
                close_fds=True,
                start_new_session=True
            )
            
        with open(daemon_pid_path, "w") as f:
            f.write(str(daemon_proc.pid))
