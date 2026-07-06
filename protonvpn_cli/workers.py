import time
import sys
import os
from .auth import ProtonAuthApi
from .api import ProtonVpnApi
from .database import Database

class BackgroundWorkers:
    def __init__(self):
        self.db = Database()
        self.auth = ProtonAuthApi()
        self.api = ProtonVpnApi()
        
        # In seconds
        self.SERVER_FETCH_INTERVAL = 120 * 60  # 2 hours
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
            servers = self.api.get_logical_servers()
            self.db.save_servers(servers)
            self._mark_run("last_server_fetch")
            print(f"[Daemon] Successfully fetched {len(servers)} servers.")
        except Exception as e:
            print(f"[Daemon] [ERROR] Server sync failed with error: {e}")

    def refresh_session(self):
        try:
            print("[Daemon] Starting session refresh...")
            self.auth.refresh_session()
            self._mark_run("last_session_refresh")
            print("[Daemon] Session successfully refreshed.")
        except Exception as e:
            print(f"[Daemon] [ERROR] Session update failed with error: {e}")

    def check_certificate(self):
        session = self.db.get_session()
        if not session:
            return
            
        cert_refresh_at = session.get("cert_refresh_at") or 0
        
        if cert_refresh_at > 0 and time.time() > cert_refresh_at:
            print("[Daemon] Certificate refresh threshold reached. Registering new certificate...")
            try:
                # To generate a new key we use wireguard logic
                # Since we don't have awg.py directly accessible without importing
                from .awg import create_wg_keys
                priv, pub = create_wg_keys()
                self.api.register_cert(pub, priv)
                print("[Daemon] Successfully registered new certificate.")
            except Exception as e:
                print(f"[Daemon] [ERROR] Certificate update failed with error: {e}")

    def start(self):
        print(f"[Daemon] Started background workers (PID: {os.getpid()})")
        while True:
            # Check servers
            if self._should_run("last_server_fetch", self.SERVER_FETCH_INTERVAL):
                self.sync_servers()
                
            # Check session
            if self._should_run("last_session_refresh", self.SESSION_REFRESH_INTERVAL):
                self.refresh_session()
                
            # Check certificate (dynamically based on DB value)
            self.check_certificate()
            
            time.sleep(self.LOOP_DELAY)
