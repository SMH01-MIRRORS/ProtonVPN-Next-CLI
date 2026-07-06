import json
import urllib.request
import urllib.error
from typing import Dict, Any, Tuple
from .device_info import DeviceInfoProvider
from .database import Database

class ProtonAuthApi:
    def __init__(self):
        self.device_info = DeviceInfoProvider()
        self.db = Database()
        
        bypass = self.db.get_setting("api_bypass", "0")
        if bypass in ("1", "cloudflare"):
            self.BASE_URL = "https://api.protonnext.qzz.io"
        elif bypass in ("2", "netlify"):
            self.BASE_URL = "https://shimmering-stroopwafel-51675e.netlify.app"
        elif bypass in ("3", "deno"):
            self.BASE_URL = "https://quick-bluejay-8760.smh01-mirrors.deno.net"
        else:
            self.BASE_URL = "https://vpn-api.proton.me"
        
        # Setup common headers according to NetworkModule.kt
        self.headers = {
            "User-Agent": self.device_info.get_spoofed_user_agent(),
            "x-pm-appversion": f"android-vpn@{DeviceInfoProvider.SPOOFED_APP_VERSION}-dev+play",
            "x-pm-apiversion": "4",
            "Accept": "application/vnd.protonmail.v1+json",
            "Content-Type": "application/json"
        }

    def _post(self, url: str, headers: Dict[str, str], payload: Dict[str, Any]) -> Dict[str, Any]:
        data = json.dumps(payload).encode('utf-8')
        req = urllib.request.Request(url, data=data, headers=headers, method='POST')
        try:
            with urllib.request.urlopen(req) as response:
                return json.loads(response.read().decode('utf-8'))
        except urllib.error.HTTPError as e:
            error_body = e.read().decode('utf-8')
            raise Exception(f"HTTP {e.code}: {error_body}")
        except urllib.error.URLError as e:
            raise Exception(f"Network error: {e.reason}")

    def create_anonymous_session(self, challenge_payload: Dict[str, Any]) -> Tuple[str, str]:
        """
        Phase 0: Request anonymous session.
        Returns: (access_token, session_id)
        """
        url = f"{self.BASE_URL}/auth/v4/sessions"
        
        data = self._post(url, self.headers, challenge_payload)
        
        if data.get("Code") != 1000:
            raise Exception(f"Failed to create anonymous session. Code: {data.get('Code')} Error: {data}")
            
        access_token = data.get("AccessToken")
        session_id = data.get("UID")
        return access_token, session_id

    def perform_login_less(self, access_token: str, session_id: str, challenge_payload: Dict[str, Any]) -> Dict[str, Any]:
        """
        Phase 1: Upgrade to guest session using credential-less endpoint.
        """
        url = f"{self.BASE_URL}/auth/v4/credentialless"
        headers = self.headers.copy()
        headers["Authorization"] = f"Bearer {access_token}"
        headers["x-pm-uid"] = session_id
        
        data = self._post(url, headers, challenge_payload)
        
        if data.get("Code") != 1000:
            raise Exception(f"Guest login rejected. Code: {data.get('Code')} Error: {data}")
            
        return data

    def login_guest(self) -> Dict[str, Any]:
        """
        Performs the complete guest login flow.
        """
        payload = self.device_info.build_challenge_payload()
        
        print("-> Requesting anonymous session (Phase 0)...")
        anon_token, anon_uid = self.create_anonymous_session(payload)
        print(f"-> Anonymous session obtained. UID: {anon_uid}")
        
        print("-> Upgrading to guest session (Phase 1)...")
        guest_response = self.perform_login_less(anon_token, anon_uid, payload)
        
        # If successful, the response contains the final access token.
        # If missing in response, fallback to the anonymous one.
        final_token = guest_response.get("AccessToken", anon_token)
        final_uid = guest_response.get("UID", anon_uid)
        
        guest_response["AccessToken"] = final_token
        guest_response["UID"] = final_uid
        
        db = Database()
        db.save_session(
            access_token=final_token,
            refresh_token=guest_response.get("RefreshToken", ""),
            uid=final_uid,
            user_id=guest_response.get("UserID", "")
        )
        
        return guest_response
