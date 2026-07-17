"""Persistent VPN profiles and server resolution for CLI/API clients."""

from __future__ import annotations

import threading
import time
import webbrowser
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

from .database import Database


class ProfileValidationError(ValueError):
    """Raised when a profile cannot be saved or connected."""


class ProfileService:
    TARGET_TYPES = {"fastest", "country", "city", "server"}

    def __init__(self, db: Optional[Database] = None):
        self.db = db or Database()

    def list_profiles(self) -> List[Dict[str, Any]]:
        return [self._public_profile(row) for row in self.db.get_profiles()]

    def get_profile(self, profile_id: str) -> Optional[Dict[str, Any]]:
        row = self.db.get_profile(profile_id)
        return self._public_profile(row) if row else None

    def create_profile(self, data: Dict[str, Any]) -> Dict[str, Any]:
        profile = self.validate(data)
        return self._public_profile(self.db.create_profile(profile))

    def update_profile(self, profile_id: str, data: Dict[str, Any]) -> Dict[str, Any]:
        current = self.db.get_profile(profile_id)
        if not current:
            raise KeyError("Profile not found")
        merged = {**current, **data}
        profile = self.validate(merged)
        updated = self.db.update_profile(profile_id, profile)
        if not updated:
            raise KeyError("Profile not found")
        return self._public_profile(updated)

    def delete_profile(self, profile_id: str) -> bool:
        return self.db.delete_profile(profile_id)

    def validate(self, data: Dict[str, Any]) -> Dict[str, Any]:
        name = str(data.get("name") or "").strip()
        if not name:
            raise ProfileValidationError("Profile name is required")
        if len(name) > 80:
            raise ProfileValidationError("Profile name must be 80 characters or fewer")

        target_type = str(data.get("target_type") or "fastest").lower()
        if target_type not in self.TARGET_TYPES:
            raise ProfileValidationError("Invalid profile target")

        country = self._optional_text(data.get("country"), 8)
        city = self._optional_text(data.get("city"), 120)
        server_id = self._optional_text(data.get("server_id"), 160)
        if target_type in {"country", "city"} and not country:
            raise ProfileValidationError("Country is required for this target")
        if target_type == "city" and not city:
            raise ProfileValidationError("City is required for this target")
        if target_type == "server" and not server_id:
            raise ProfileValidationError("Server is required for this target")

        requested_protocol = data.get("protocol")
        if requested_protocol and str(requested_protocol).lower() != "amneziawg":
            raise ProfileValidationError("Only AmneziaWG profiles are supported")
        protocol = "amneziawg"

        try:
            port = int(data.get("port", 0) or 0)
        except (TypeError, ValueError):
            raise ProfileValidationError("Port must be a number")
        if port < 0 or port > 65535:
            raise ProfileValidationError("Port must be between 1 and 65535, or 0 for Auto")

        obfuscation_enabled = self._as_bool(data.get("obfuscation_enabled", True))
        awg_config = self._optional_text(data.get("awg_config"), 120) or "vpn-next-default"
        if obfuscation_enabled and not self.db.get_awg_config(awg_config):
            raise ProfileValidationError("Selected obfuscation configuration no longer exists")

        auto_open_url = str(data.get("auto_open_url") or "").strip()
        if auto_open_url:
            parsed = urlparse(auto_open_url)
            if parsed.scheme not in {"http", "https"} or not parsed.netloc:
                raise ProfileValidationError("Connect & Go URL must use http:// or https://")
            if len(auto_open_url) > 2048:
                raise ProfileValidationError("Connect & Go URL is too long")

        profile = {
            "name": name,
            "target_type": target_type,
            "country": country if target_type in {"country", "city"} else None,
            "city": city if target_type == "city" else None,
            "server_id": server_id if target_type == "server" else None,
            "protocol": protocol,
            "port": port,
            "obfuscation_enabled": obfuscation_enabled,
            "awg_config": awg_config,
            "auto_open_url": auto_open_url or None,
        }

        # Validate the target against the current cache when servers are available.
        if self.db.get_server_count() and not self.resolve_server(profile):
            raise ProfileValidationError("No available server matches this profile")
        return profile

    def resolve_profile(self, profile_id: str) -> Dict[str, Any]:
        profile = self.get_profile(profile_id)
        if not profile:
            raise KeyError("Profile not found")
        server = self.resolve_server(profile)
        if not server:
            raise ProfileValidationError("No available server matches this profile")

        awg_params = "off"
        if profile["obfuscation_enabled"]:
            config = self.db.get_awg_config(profile["awg_config"])
            if not config:
                raise ProfileValidationError("Obfuscation configuration no longer exists")
            awg_params = config["params"]

        return {
            "profile": profile,
            "server": server,
            "server_id": server["id"],
            "port": profile["port"],
            "awg_params": awg_params,
            "auto_open_url": profile.get("auto_open_url"),
        }

    def resolve_server(self, profile: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        try:
            max_tier = int(self.db.get_setting("max_tier", "0"))
        except (TypeError, ValueError):
            max_tier = 0
        candidates = [server for server in self.db.get_all_servers() if int(server.get("tier") or 0) <= max_tier]
        target_type = profile.get("target_type", "fastest")
        if target_type == "server":
            wanted = profile.get("server_id")
            candidates = [s for s in candidates if s.get("id") == wanted or s.get("name") == wanted]
        elif target_type == "city":
            candidates = [s for s in candidates if s.get("country") == profile.get("country") and s.get("city") == profile.get("city")]
        elif target_type == "country":
            candidates = [s for s in candidates if s.get("country") == profile.get("country")]
        if not candidates:
            return None
        return min(candidates, key=lambda s: (int(s.get("load") or 0), -int(s.get("tier") or 0), s.get("name") or ""))

    @staticmethod
    def open_url_when_connected(url: Optional[str], routing_file: str, timeout: int = 60) -> None:
        if not url:
            return

        def worker() -> None:
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                import os
                if os.path.exists(routing_file):
                    webbrowser.open(url, new=2)
                    return
                time.sleep(0.5)

        threading.Thread(target=worker, name="profile-connect-and-go", daemon=True).start()

    @staticmethod
    def _public_profile(row: Dict[str, Any]) -> Dict[str, Any]:
        result = dict(row)
        result["obfuscation_enabled"] = bool(result.get("obfuscation_enabled"))
        result["port"] = int(result.get("port") or 0)
        return result

    @staticmethod
    def _as_bool(value: Any) -> bool:
        if isinstance(value, bool):
            return value
        return str(value).lower() in {"1", "true", "yes", "on"}

    @staticmethod
    def _optional_text(value: Any, max_length: int) -> Optional[str]:
        text = str(value or "").strip()
        if len(text) > max_length:
            raise ProfileValidationError("Profile field is too long")
        return text or None
