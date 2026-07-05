import uuid
from typing import Dict, Any

class DeviceInfoProvider:
    SPOOFED_APP_VERSION = "5.18.75.1"
    
    def __init__(self):
        # Generate some realistic Android device fingerprinting data
        self.android_version = "12"
        self.manufacturer = "Google"
        self.model = "Pixel 6"
        self.language = "en"
        self.region_code = "US"
        self.timezone = "America/New_York"
        # 240 minutes offset is for UTC-4 (EDT)
        self.timezone_offset = 240 
        
        # Consistent hash based on host UUID to act as Android ID
        machine_id = str(uuid.getnode())
        self.device_hash = self._java_string_hashcode(machine_id)

    def _java_string_hashcode(self, s: str) -> int:
        h = 0
        for c in s:
            h = (31 * h + ord(c)) & 0xFFFFFFFF
        return ((h + 0x80000000) & 0xFFFFFFFF) - 0x80000000

    def get_spoofed_user_agent(self) -> str:
        # Matches logic in Kotlin DeviceInfoProvider
        cap_manufacturer = self.manufacturer.capitalize()
        device_name = f"{cap_manufacturer} {self.model}"
        return f"ProtonVPN/{self.SPOOFED_APP_VERSION} (Android {self.android_version}; {device_name})"

    def build_challenge_payload(self) -> Dict[str, Any]:
        """
        Builds the JSON payload required for the `vpn-android-v4-challenge-0`
        """
        return {
            "Payload": {
                "vpn-android-v4-challenge-0": {
                    "type": "me.proton.core.challenge.data.frame.ChallengeFrame.Device",
                    "v": self.SPOOFED_APP_VERSION,
                    "appLang": self.language,
                    "timezone": self.timezone,
                    "deviceName": self.device_hash,
                    "regionCode": self.region_code,
                    "timezoneOffset": self.timezone_offset,
                    "isJailbreak": False,
                    "preferredContentSize": "1.0",
                    "storageCapacity": 128.0,
                    "isDarkmodeOn": False,
                    "keyboards": ["com.google.android.inputmethod.latin"]
                }
            }
        }
