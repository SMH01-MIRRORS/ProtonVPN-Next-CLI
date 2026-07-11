import time
import sys
import os
from .auth import ProtonAuthApi
from .vpn import ProtonVpnApi
from .database import Database

class BackgroundWorkers:
    def __init__(self):
        self.db = Database()
        self.auth = ProtonAuthApi()
        self.api = ProtonVpnApi()
        
        # In seconds
        self.SERVER_FETCH_INTERVAL = 120 * 60  # 2 hours
        self.LOAD_FETCH_INTERVAL = 15 * 60    # 15 minutes
        self.SESSION_REFRESH_INTERVAL = 12 * 60 * 60  # 12 hours
        self.LOOP_DELAY = 60  # Check every minute

    def _should_run(self, key: str, interval: int) -> bool:
        last_run = self.db.get_setting(key)
        if not last_run:
            return True
        try:
            return time.time() - float(last_run) >= interval
        except ValueError:
            return True

    def _mark_run(self, key: str):
        self.db.set_setting(key, str(time.time()))

    def sync_servers(self):
        try:
            print("[Daemon] Starting server sync...")
            servers = self.api.fetch_servers()
            self._mark_run("last_server_fetch")
            print(f"[Daemon] Successfully fetched {len(servers)} servers.")
        except Exception as e:
            print(f"[Daemon] [ERROR] Server sync failed with error: {e}")

    def sync_loads(self):
        try:
            print("[Daemon] Starting load sync...")
            loads = self.api.fetch_loads()
            self._mark_run("last_load_fetch")
            print(f"[Daemon] Successfully updated loads for {len(loads)} servers.")
        except Exception as e:
            print(f"[Daemon] [ERROR] Load sync failed: {e}")

    def refresh_session(self):
        try:
            print("[Daemon] Starting session refresh...")
            self.auth.refresh_session()
            self._mark_run("last_session_refresh")
            print("[Daemon] Session successfully refreshed.")
        except Exception as e:
            print(f"[Daemon] [ERROR] Session update failed with error: {e}")

    def check_certificate(self, force=False):
        session = self.db.get_session()
        if not session:
            return
            
        cert_refresh_at = session.get("cert_refresh_at") or 0
        
        if force or (cert_refresh_at > 0 and time.time() > cert_refresh_at):
            print("[Daemon] Certificate refresh threshold reached (or forced). Registering new certificate...")
            try:
                from .crypto import ProtonCrypto
                priv_key, pub_key_pem = ProtonCrypto.generate_vpn_keys()
                response = self.api.register_cert(pub_key_pem)
                cert_data = response.get('Certificate', '')
                expires_at = response.get('ExpirationTime', 0)
                refresh_at = response.get('RefreshTime', 0)
                self.db.update_certificate(priv_key, cert_data, expires_at, refresh_at)
                print("[Daemon] Successfully registered new certificate.")
            except Exception as e:
                print(f"[Daemon] [ERROR] Certificate update failed with error: {e}")

    def _ip_checker_loop(self):
        import urllib.request
        from .routing import get_config_dir
        routing_file = os.path.join(get_config_dir(), "routing_state.json")
        
        while True:
            try:
                if os.path.exists(routing_file):
                    current_ip = self.db.get_setting("current_real_ip", "")
                    if current_ip:
                        time.sleep(5)
                        continue
                        
                    try:
                        req = urllib.request.Request("https://1.1.1.1/cdn-cgi/trace", headers={'User-Agent': 'Mozilla/5.0'})
                        resp = urllib.request.urlopen(req, timeout=3).read().decode('utf-8')
                        for line in resp.split('\n'):
                            if line.startswith('ip='):
                                real_ip = line.split('=')[1].strip()
                                self.db.set_setting("current_real_ip", real_ip)
                                break
                    except Exception:
                        pass
                    time.sleep(1)
                else:
                    self.db.set_setting("current_real_ip", "")
                    time.sleep(2)
            except Exception as e:
                print(f"[Daemon] [ERROR] IP Checker exception: {e}")
                time.sleep(2)

    def start(self):
        print(f"[Daemon] Started background workers (PID: {os.getpid()})")
        
        import sys
        try:
            sys.stdout.reconfigure(line_buffering=True)
            sys.stderr.reconfigure(line_buffering=True)
        except Exception:
            pass
        
        import threading
        ip_thread = threading.Thread(target=self._ip_checker_loop, daemon=True)
        ip_thread.start()
        
        while True:
            try:
                # Check servers
                if self._should_run("last_server_fetch", self.SERVER_FETCH_INTERVAL):
                    self.sync_servers()
                    
                # Check loads
                if self._should_run("last_load_fetch", self.LOAD_FETCH_INTERVAL):
                    self.sync_loads()

                # Check session
                if self._should_run("last_session_refresh", self.SESSION_REFRESH_INTERVAL):
                    self.refresh_session()
                    
                # Check certificate (dynamically based on DB value)
                self.check_certificate()
            except Exception as e:
                print(f"[Daemon] [ERROR] Unexpected exception in main loop: {e}")
                
            time.sleep(self.LOOP_DELAY)
