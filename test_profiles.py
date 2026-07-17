import os
import tempfile
import unittest

from pvpn_cli.database import Database
from pvpn_cli.profiles import ProfileService, ProfileValidationError


class ProfileServiceTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.previous_config = os.environ.get("PVPN_CONFIG_DIR")
        os.environ["PVPN_CONFIG_DIR"] = self.temp_dir.name
        self.db = Database()
        self.db.set_setting("max_tier", "0")
        self.db.save_servers([
            {"ID": "nl-1", "Name": "NL-FREE#1", "EntryCountry": "NL", "City": "Amsterdam", "Tier": 0, "Load": 35, "Servers": []},
            {"ID": "nl-2", "Name": "NL-FREE#2", "EntryCountry": "NL", "City": "Amsterdam", "Tier": 0, "Load": 12, "Servers": []},
            {"ID": "us-1", "Name": "US-FREE#1", "EntryCountry": "US", "City": "New York", "Tier": 0, "Load": 7, "Servers": []},
            {"ID": "plus-1", "Name": "CH-PLUS#1", "EntryCountry": "CH", "City": "Zurich", "Tier": 2, "Load": 1, "Servers": []},
        ])
        self.service = ProfileService(self.db)

    def tearDown(self):
        if self.previous_config is None:
            os.environ.pop("PVPN_CONFIG_DIR", None)
        else:
            os.environ["PVPN_CONFIG_DIR"] = self.previous_config
        self.temp_dir.cleanup()

    def test_crud_and_boolean_serialization(self):
        created = self.service.create_profile({
            "name": "Amsterdam",
            "target_type": "city",
            "country": "NL",
            "city": "Amsterdam",
            "protocol": "amneziawg",
            "port": 443,
            "obfuscation_enabled": True,
            "awg_config": "preset-medium",
            "auto_open_url": "https://example.com/ready",
        })
        self.assertTrue(created["obfuscation_enabled"])
        self.assertEqual(443, created["port"])
        self.assertEqual("nl-2", self.service.resolve_profile(created["id"])["server_id"])

        updated = self.service.update_profile(created["id"], {"name": "Work", "target_type": "fastest"})
        self.assertEqual("Work", updated["name"])
        self.assertIsNone(updated["country"])
        self.assertEqual("us-1", self.service.resolve_profile(created["id"])["server_id"])
        self.assertTrue(self.service.delete_profile(created["id"]))
        self.assertIsNone(self.service.get_profile(created["id"]))

    def test_exact_server_wins_even_when_not_fastest(self):
        created = self.service.create_profile({
            "name": "Pinned",
            "target_type": "server",
            "server_id": "nl-1",
            "obfuscation_enabled": False,
        })
        resolved = self.service.resolve_profile(created["id"])
        self.assertEqual("nl-1", resolved["server_id"])
        self.assertEqual("off", resolved["awg_params"])
        self.assertFalse(created["obfuscation_enabled"])

    def test_rejects_unavailable_or_unsafe_profiles(self):
        with self.assertRaises(ProfileValidationError):
            self.service.create_profile({"name": "Missing", "target_type": "country", "country": "DE"})
        with self.assertRaises(ProfileValidationError):
            self.service.create_profile({"name": "Bad URL", "auto_open_url": "file:///tmp/secret"})
        with self.assertRaises(ProfileValidationError):
            self.service.create_profile({"name": "Bad port", "port": 70000})
        with self.assertRaises(ProfileValidationError):
            self.service.create_profile({"name": "Legacy protocol", "protocol": "legacy"})


if __name__ == "__main__":
    unittest.main()
