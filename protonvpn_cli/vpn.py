import json
import urllib.request
import urllib.error
from typing import Dict, Any, List
from .database import Database
from .device_info import DeviceInfoProvider

class ProtonVpnApi:
    BASE_URL = "https://vpn-api.proton.me"

    def __init__(self):
        self.db = Database()
        self.device_info = DeviceInfoProvider()

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
