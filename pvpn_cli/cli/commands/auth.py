"""Authentication, session, and certificate commands."""

import sys

from pvpn_cli.auth import ProtonAuthApi
from pvpn_cli.database import Database
from pvpn_cli.vpn import ProtonVpnApi


def do_guest_login():
    print("Initiating PVPN Guest Login...")
    api = ProtonAuthApi()
    try:
        response = api.login_guest()
        print("\n=== Guest Login Successful ===")
        print(f"User ID:      {response.get('UserID', 'N/A')}")
        print(f"Session UID:  {response.get('UID')}")
        print(f"Access Token: {response.get('AccessToken')[:20]}... (truncated)")
        print(f"Refresh Token:{response.get('RefreshToken', 'N/A')[:20]}...")
        print("==============================")
    except Exception as e:
        print(f"\n[ERROR] {str(e)}")
        sys.exit(1)


def do_login():
    import getpass
    print("Initiating PVPN User Login...")
    username = input("Username: ").strip()
    if not username:
        print("[ERROR] Username cannot be empty")
        sys.exit(1)

    password = getpass.getpass("Password: ")

    def prompt_2fa():
        return input("Two-Factor Authentication Code (TOTP): ").strip()

    api = ProtonAuthApi()
    try:
        response = api.login_user(username, password, prompt_2fa)
        print("\n=== Login Successful ===")
        print(f"User ID:      {response.get('UserID', 'N/A')}")
        print(f"Session UID:  {response.get('UID')}")
        print(f"Scopes:       {', '.join(response.get('Scopes', []))}")
        print("========================")
    except Exception as e:
        print(f"\n[ERROR] {str(e)}")
        sys.exit(1)


def do_register_cert():
    from pvpn_cli.crypto import ProtonCrypto
    print("Generating WireGuard key pair...")
    try:
        priv_key, pub_key_pem = ProtonCrypto.generate_vpn_keys()
    except Exception as e:
        print(f"[ERROR] Failed to generate keys: {e}")
        sys.exit(1)

    print("Registering public key with PVPN API...")
    api = ProtonVpnApi()
    try:
        response = api.register_cert(pub_key_pem)

        cert_data = response.get('Certificate', '')
        expires_at = response.get('ExpirationTime', 0)
        refresh_at = response.get('RefreshTime', 0)

        db = Database()
        db.update_certificate(priv_key, cert_data, expires_at, refresh_at)

        print("\n=== Certificate Registered ===")
        print(f"Status: Successfully saved to local database.")
        from datetime import datetime
        if expires_at:
            dt = datetime.fromtimestamp(expires_at).strftime('%Y-%m-%d %H:%M:%S')
            print(f"Expiration: {dt}")
        print(f"Certificate Length: {len(cert_data)} bytes")
        print("==============================")
    except Exception as e:
        print(f"\n[ERROR] {str(e)}")
        sys.exit(1)


def do_register_extended_cert():
    from pvpn_cli.crypto import ProtonCrypto
    print("Generating WireGuard key pair...")
    try:
        priv_key, pub_key_pem = ProtonCrypto.generate_vpn_keys()
    except Exception as e:
        print(f"[ERROR] Failed to generate keys: {e}")
        sys.exit(1)

    print("Registering public key with PVPN API (Mode: persistent)...")
    api = ProtonVpnApi()
    try:
        response = api.register_cert(pub_key_pem, mode="persistent")

        cert_data = response.get('Certificate', '')
        expires_at = response.get('ExpirationTime', 0)
        refresh_at = response.get('RefreshTime', 0)

        db = Database()
        db.update_certificate(priv_key, cert_data, expires_at, refresh_at)

        print("\n=== Extended Certificate Registered ===")
        print(f"Status: Successfully saved to local database.")
        print(f"Mode: {response.get('Mode', 'Unknown')}")
        from datetime import datetime
        if expires_at:
            dt = datetime.fromtimestamp(expires_at).strftime('%Y-%m-%d %H:%M:%S')
            print(f"Expiration: {dt}")
        print(f"Certificate Length: {len(cert_data)} bytes")
        print("==============================")
    except Exception as e:
        print(f"\n[ERROR] {str(e)}")
        sys.exit(1)


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


def do_trigger_captcha():
    from pvpn_cli.device_info import DeviceInfoProvider

    print("-> Triggering CAPTCHA by spoofing an Android Emulator (SDK 36, x86_64)...")

    class EmulatorInfoProvider(DeviceInfoProvider):
        def __init__(self):
            super().__init__()
            self.android_version = "16"
            self.manufacturer = "Google"
            self.model = "sdk_gphone64_x86_64"
            self.language = "en"
            self.region_code = "US"
            self.timezone = "Europe/Volgograd"
            self.timezone_offset = -180
            self.device_hash = -1631082355

        def build_challenge_payload(self):
            payload = super().build_challenge_payload()
            challenge = payload["Payload"]["vpn-android-v4-challenge-0"]
            challenge["isJailbreak"] = False
            challenge["isEmulator"] = True
            challenge["storageCapacity"] = 5.800384521484375
            challenge["isDarkmodeOn"] = False
            challenge["preferredContentSize"] = "1.0"
            challenge["keyboards"] = ["com.google.android.inputmethod.latin", "com.google.android.tts"]
            return payload

    api = ProtonAuthApi()
    api.device_info = EmulatorInfoProvider()
    api.headers["User-Agent"] = api.device_info.get_spoofed_user_agent()

    try:
        api.login_guest()
        print("[WARNING] The API accepted the emulator fingerprint without requiring a CAPTCHA.")
    except Exception as e:
        print(f"[SUCCESS/RESULT] {e}")
