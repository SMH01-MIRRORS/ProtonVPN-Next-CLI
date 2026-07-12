import json
import urllib.request
import urllib.error
from typing import Dict, Any, List, Optional
import sqlite3
from .database import Database
from .device_info import DeviceInfoProvider

class ProtonVpnApi:
    def __init__(self):
        self.db = Database()
        self.device_info = DeviceInfoProvider()
        
        bypass = self.db.get_setting("api_bypass", "0")
        if bypass in ("1", "cloudflare"):
            self.BASE_URL = "https://api.protonnext.qzz.io"
        elif bypass in ("2", "netlify"):
            self.BASE_URL = "https://shimmering-stroopwafel-51675e.netlify.app"
        elif bypass in ("3", "deno"):
            self.BASE_URL = "https://quick-bluejay-8760.smh01-mirrors.deno.net"
        else:
            self.BASE_URL = "https://vpn-api.proton.me"

    def fetch_servers(self) -> List[Dict[str, Any]]:
        session = self.db.get_session()
        if not session or not session.get("access_token"):
            raise Exception("No active session. Please run 'guest' login first.")

        # API: /vpn/v2/logicals?WithEntriesForProtocols=wireguard&WithState=true
        url = f"{self.BASE_URL}/vpn/v2/logicals?WithEntriesForProtocols=wireguard&WithState=true"
        
        headers = {
            "Authorization": f"Bearer {session['access_token']}",
            "x-pm-uid": session['uid'],
            "User-Agent": self.device_info.get_spoofed_user_agent(),
            "x-pm-appversion": f"android-vpn@{DeviceInfoProvider.SPOOFED_APP_VERSION}-dev+play",
            "x-pm-apiversion": "4",
            "Accept": "application/vnd.protonmail.v1+json",
        }
        
        req = urllib.request.Request(url, headers=headers, method='GET')
        try:
            with urllib.request.urlopen(req) as response:
                data = json.loads(response.read().decode('utf-8'))
                
                if data.get("Code") != 1000:
                    raise Exception(f"Failed to fetch servers. API Code: {data.get('Code')} Error: {data}")
                
                servers = data.get("LogicalServers", [])
                self.db.save_servers(servers)
                return servers
                
        except urllib.error.HTTPError as e:
            error_body = e.read().decode('utf-8')
            raise Exception(f"HTTP {e.code}: {error_body}")
        except urllib.error.URLError as e:
            raise Exception(f"Network error: {e.reason}")

    def fetch_loads(self) -> List[Dict[str, Any]]:
        session = self.db.get_session()
        if not session or not session.get("access_token"):
            raise Exception("No active session.")

        url = f"{self.BASE_URL}/vpn/v1/loads"
        headers = {
            "Authorization": f"Bearer {session['access_token']}",
            "x-pm-uid": session['uid'],
            "User-Agent": self.device_info.get_spoofed_user_agent(),
            "x-pm-appversion": f"android-vpn@{DeviceInfoProvider.SPOOFED_APP_VERSION}-dev+play",
            "x-pm-apiversion": "4",
            "Accept": "application/vnd.protonmail.v1+json",
        }

        req = urllib.request.Request(url, headers=headers, method='GET')
        try:
            with urllib.request.urlopen(req) as response:
                data = json.loads(response.read().decode('utf-8'))
                if data.get("Code") != 1000:
                    raise Exception(f"Failed to fetch loads. Code: {data.get('Code')}")

                loads = data.get("LogicalServers", [])
                self.db.update_server_loads(loads)
                return loads
        except Exception as e:
            raise Exception(f"Failed to fetch loads: {e}")

    def get_server_by_name(self, name: str) -> Optional[Dict[str, Any]]:
        with sqlite3.connect(self.db.db_path) as conn:
            cursor = conn.cursor()
            # Try by name first, then by ID
            cursor.execute("SELECT raw_json FROM servers WHERE name = ? OR id = ? LIMIT 1", (name, name))
            row = cursor.fetchone()
            if row:
                return json.loads(row[0])
            return None

    def get_best_server(self) -> Optional[Dict[str, Any]]:
        """Get the server with the lowest load available for the current account tier."""
        max_tier = self.get_max_tier()
        with sqlite3.connect(self.db.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            # Find logical server with lowest load and tier <= max_tier
            cursor.execute("""
                SELECT raw_json FROM servers
                WHERE tier <= ?
                ORDER BY load ASC, tier DESC, name ASC
                LIMIT 1
            """, (max_tier,))
            row = cursor.fetchone()
            if row:
                return json.loads(row[0])
            return None

    def get_max_tier(self) -> int:
        session = self.db.get_session()
        if not session or not session.get("access_token"):
            return 0

        url = f"{self.BASE_URL}/vpn/v2"
        headers = {
            "Authorization": f"Bearer {session['access_token']}",
            "x-pm-uid": session['uid'],
            "User-Agent": self.device_info.get_spoofed_user_agent(),
            "x-pm-appversion": f"android-vpn@{DeviceInfoProvider.SPOOFED_APP_VERSION}-dev+play",
            "x-pm-apiversion": "4",
            "Accept": "application/vnd.protonmail.v1+json",
        }
        req = urllib.request.Request(url, headers=headers, method='GET')
        try:
            with urllib.request.urlopen(req, timeout=3) as response:
                data = json.loads(response.read().decode('utf-8'))
                return data.get("VPN", {}).get("MaxTier", 0)
        except Exception:
            return 0

    def fetch_locale(self, locale: str) -> Dict[str, Any]:
        session = self.db.get_session()
        if not session or not session.get("access_token"):
            raise Exception("No active session.")

        url = f"{self.BASE_URL}/vpn/v1/cities/names"
        headers = {
            "Authorization": f"Bearer {session['access_token']}",
            "x-pm-uid": session['uid'],
            "User-Agent": self.device_info.get_spoofed_user_agent(),
            "x-pm-appversion": f"android-vpn@{DeviceInfoProvider.SPOOFED_APP_VERSION}-dev+play",
            "x-pm-apiversion": "4",
            "x-pm-locale": locale,
            "Accept": "application/vnd.protonmail.v1+json",
        }
        req = urllib.request.Request(url, headers=headers, method='GET')
        try:
            with urllib.request.urlopen(req) as response:
                data = json.loads(response.read().decode('utf-8'))
                return data
        except urllib.error.HTTPError as e:
            error_body = e.read().decode('utf-8')
            raise Exception(f"HTTP {e.code}: {error_body}")
        except urllib.error.URLError as e:
            raise Exception(f"Network error: {e.reason}")

    def register_cert(self, public_key: str, mode: str = None) -> Dict[str, Any]:
        session = self.db.get_session()
        if not session or not session.get("access_token"):
            raise Exception("No active session. Please run 'guest' login first.")

        url = f"{self.BASE_URL}/vpn/v1/certificate"
        
        headers = {
            "Authorization": f"Bearer {session['access_token']}",
            "x-pm-uid": session['uid'],
            "User-Agent": self.device_info.get_spoofed_user_agent(),
            "x-pm-appversion": f"android-vpn@{DeviceInfoProvider.SPOOFED_APP_VERSION}-dev+play",
            "x-pm-apiversion": "4",
            "Content-Type": "application/json",
            "Accept": "application/vnd.protonmail.v1+json",
        }
        
        payload = {"ClientPublicKey": public_key}
        if mode:
            payload["Mode"] = mode
            
        data = json.dumps(payload).encode('utf-8')
        
        req = urllib.request.Request(url, data=data, headers=headers, method='POST')
        try:
            with urllib.request.urlopen(req) as response:
                resp_data = json.loads(response.read().decode('utf-8'))
                if resp_data.get("Code") != 1000:
                    raise Exception(f"Failed to register cert. Code: {resp_data.get('Code')} Error: {resp_data}")
                return resp_data
        except urllib.error.HTTPError as e:
            error_body = e.read().decode('utf-8')
            raise Exception(f"HTTP {e.code}: {error_body}")
        except urllib.error.URLError as e:
            raise Exception(f"Network error: {e.reason}")
