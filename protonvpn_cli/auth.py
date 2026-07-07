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
        self.debug = False

    def _post(self, url: str, headers: Dict[str, str], payload: Dict[str, Any]) -> Dict[str, Any]:
        data = json.dumps(payload).encode('utf-8')
        req = urllib.request.Request(url, data=data, headers=headers, method='POST')
        
        if getattr(self, 'debug', False):
            print(f"\n[DEBUG] --- HTTP POST ---")
            print(f"[DEBUG] URL: {url}")
            print(f"[DEBUG] Headers: {json.dumps(headers, indent=2)}")
            print(f"[DEBUG] Payload: {json.dumps(payload, indent=2)}")
            
        try:
            with urllib.request.urlopen(req) as response:
                resp_data = json.loads(response.read().decode('utf-8'))
                if getattr(self, 'debug', False):
                    print(f"[DEBUG] Response Status: {response.status}")
                    print(f"[DEBUG] Response Body: {json.dumps(resp_data, indent=2)}")
                return resp_data
        except urllib.error.HTTPError as e:
            error_body = e.read().decode('utf-8')
            if getattr(self, 'debug', False):
                print(f"[DEBUG] Error Status: {e.code}")
                print(f"[DEBUG] Error Body: {error_body}")
            try:
                error_data = json.loads(error_body)
                if "Code" in error_data:
                    return error_data
            except Exception:
                pass
            raise Exception(f"HTTP {e.code}: {error_body}")
        except urllib.error.URLError as e:
            raise Exception(f"Network error: {e.reason}")

    def create_anonymous_session(self, challenge_payload: Dict[str, Any], captcha_token: str = None) -> Tuple[str, str]:
        """
        Phase 0: Request anonymous session.
        Returns: (access_token, session_id)
        """
        url = f"{self.BASE_URL}/auth/v4/sessions"
        
        headers = self.headers.copy()
        if captcha_token:
            headers["x-pm-human-verification-token"] = captcha_token
            headers["x-pm-human-verification-token-type"] = "captcha"
            
        data = self._post(url, headers, challenge_payload)
        
        if data.get("Code") != 1000:
            if data.get("Code") in (9001, 12087):
                return None, data
            raise Exception(f"Failed to create anonymous session. Code: {data.get('Code')} Error: {data}")
            
        access_token = data.get("AccessToken")
        session_id = data.get("UID")
        return access_token, session_id

    def perform_login_less(self, access_token: str, session_id: str, challenge_payload: Dict[str, Any], captcha_token: str = None) -> Dict[str, Any]:
        """
        Phase 1: Upgrade to guest session using credential-less endpoint.
        """
        url = f"{self.BASE_URL}/auth/v4/credentialless"
        headers = self.headers.copy()
        headers["Authorization"] = f"Bearer {access_token}"
        headers["x-pm-uid"] = session_id
        if captcha_token:
            headers["x-pm-human-verification-token"] = captcha_token
            headers["x-pm-human-verification-token-type"] = "captcha"
        
        data = self._post(url, headers, challenge_payload)
        
        if data.get("Code") != 1000:
            if data.get("Code") in (9001, 12087):
                return data
            raise Exception(f"Guest login rejected. Code: {data.get('Code')} Error: {data}")
            
        return data

    def _handle_captcha(self, error_data: Dict[str, Any], session_id: str = None) -> str:
        code = error_data.get("Code")
        if code == 12087:
            raise Exception("Captcha session expired. Please try again.")
            
        details = error_data.get("Details", {})
        web_url = details.get("WebUrl")
        if not web_url:
            raise Exception(f"Captcha required but no WebUrl provided: {error_data}")
            
        from .captcha import CaptchaProxyServer
        # For simplicity, we use netlify proxy to circumvent DPI
        proxy = CaptchaProxyServer("https://shimmering-stroopwafel-51675e.netlify.app", session_id)
        token = proxy.start_and_wait(web_url)
        return token

    def login_guest(self) -> Dict[str, Any]:
        """
        Performs the complete guest login flow.
        """
        payload = self.device_info.build_challenge_payload()
        captcha_token = None
        anon_token, anon_uid = None, None
        used_token_in_phase_0 = False
        
        while True:
            print("-> Requesting anonymous session (Phase 0)...")
            res_token, res_uid_or_err = self.create_anonymous_session(payload, captcha_token)
            if res_token is None:
                # It's an error
                err = res_uid_or_err
                if err.get("Code") in (9001, 12087):
                    # We don't have a session ID yet, so we pass None
                    captcha_token = self._handle_captcha(err, None)
                    continue
                else:
                    raise Exception(f"Failed Phase 0: {err}")
            else:
                anon_token = res_token
                anon_uid = res_uid_or_err
                if captcha_token:
                    used_token_in_phase_0 = True
                break
                
        print(f"-> Anonymous session obtained. UID: {anon_uid}")
        
        while True:
            print("-> Upgrading to guest session (Phase 1)...")
            phase1_token = None if used_token_in_phase_0 else captcha_token
            guest_response = self.perform_login_less(anon_token, anon_uid, payload, phase1_token)
            
            if guest_response.get("Code") != 1000:
                if guest_response.get("Code") in (9001, 12087):
                    captcha_token = self._handle_captcha(guest_response, anon_uid)
                    used_token_in_phase_0 = False
                    continue
                else:
                    raise Exception(f"Failed Phase 1: {guest_response}")
            else:
                break
        
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

    def refresh_session(self) -> Dict[str, Any]:
        """
        Refresh the session token using the refresh token from DB.
        Returns the new session data.
        """
        session = self.db.get_session()
        if not session or not session.get('refresh_token'):
            raise Exception("No active session or refresh token found.")
            
        url = f"{self.BASE_URL}/auth/v4/refresh"
        payload = {
            "ResponseType": "token",
            "GrantType": "refresh_token",
            "RefreshToken": session['refresh_token'],
            "RedirectURI": "https://protonvpn.com"
        }
        
        headers = self.headers.copy()
        headers["x-pm-uid"] = session['uid']
        
        data = self._post(url, headers, payload)
        
        if data.get("Code") != 1000:
            raise Exception(f"Failed to refresh session. Code: {data.get('Code')} Error: {data}")
            
        new_access_token = data.get("AccessToken")
        new_refresh_token = data.get("RefreshToken", session['refresh_token'])
        
        self.db.save_session(
            access_token=new_access_token,
            refresh_token=new_refresh_token,
            uid=session['uid'],
            user_id=session['user_id']
        )
        
        return data
