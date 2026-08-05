"""Tests for the GUI -> engine settings bridge and the split tunneling switch.

The GUI used to write keys that no connection code ever read, so toggles looked
saved while `connect` kept using the old values.
"""

import json
import os
import shutil
import tempfile
import unittest

os.environ["PVPN_DISABLE_SENTRY"] = "1"

from pvpn_cli import api as api_module
from pvpn_cli.database import Database
from pvpn_cli.routing import RoutingManager, split_tunneling_enabled


class SettingsBridgeTest(unittest.TestCase):
    def setUp(self):
        self.config_dir = tempfile.mkdtemp(prefix="pvpn-settings-")
        os.environ["PVPN_CONFIG_DIR"] = self.config_dir
        self.db = Database()

    def tearDown(self):
        os.environ.pop("PVPN_CONFIG_DIR", None)
        shutil.rmtree(self.config_dir, ignore_errors=True)

    def write_split(self, config):
        path = os.path.join(self.config_dir, "split_tunnel.json")
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(config, handle, indent=4)

    def test_gui_toggle_selects_the_config_connect_reads(self):
        self.db.add_awg_config("stealth", json.dumps({"Jc": 4}), 3)
        expected_id = str(self.db.get_awg_config("stealth")["id"])
        self.db.set_setting("obfuscation_enabled", "true")
        self.db.set_setting("obfuscation_config", "stealth")

        api_module.sync_obfuscation_to_engine(self.db)

        self.assertEqual("config", self.db.get_setting("active_awg_mode"))
        self.assertEqual(expected_id, self.db.get_setting("active_awg_config_id"))

    def test_disabling_obfuscation_clears_the_engine_mode(self):
        self.db.set_setting("active_awg_mode", "config")
        self.db.set_setting("obfuscation_enabled", "false")

        api_module.sync_obfuscation_to_engine(self.db)

        self.assertEqual("none", self.db.get_setting("active_awg_mode"))

    def test_missing_config_never_enables_obfuscation(self):
        self.db.set_setting("obfuscation_enabled", "true")
        self.db.set_setting("obfuscation_config", "does-not-exist")

        message = api_module.sync_obfuscation_to_engine(self.db)

        self.assertEqual("none", self.db.get_setting("active_awg_mode"))
        self.assertIn("not found", message)

    def test_settings_report_obfuscation_enabled_from_the_cli(self):
        self.db.set_setting("active_awg_mode", "custom")
        self.db.set_setting("active_awg_custom_params", json.dumps({"Jc": 7}))

        enabled, _ = api_module.current_obfuscation(self.db)

        self.assertEqual("true", enabled)

    def test_custom_dns_becomes_a_profile(self):
        self.db.set_setting("custom_dns", "9.9.9.9, 149.112.112.112")

        api_module.sync_dns_to_engine(self.db)

        self.assertEqual(
            api_module.GUI_DNS_PROFILE, self.db.get_setting("active_dns_profile")
        )
        profiles = json.loads(self.db.get_setting("custom_dns_profiles"))
        self.assertEqual(
            "9.9.9.9, 149.112.112.112", profiles[api_module.GUI_DNS_PROFILE]
        )

    def test_known_servers_reuse_the_predefined_profile(self):
        self.db.set_setting("custom_dns", "1.1.1.1,1.0.0.1")

        api_module.sync_dns_to_engine(self.db)

        self.assertEqual("cloudflare", self.db.get_setting("active_dns_profile"))

    def test_empty_dns_falls_back_to_proton(self):
        self.db.set_setting("custom_dns", "")

        api_module.sync_dns_to_engine(self.db)

        self.assertEqual("proton", self.db.get_setting("active_dns_profile"))

    def test_items_are_ignored_while_the_switch_is_off(self):
        self.write_split(
            {"exclude_lan": True, "split_items": [{"type": "ip", "value": "10.0.0.5"}]}
        )
        self.db.set_setting("split_tunneling", "false")

        config = RoutingManager("")._get_split_config()

        self.assertEqual([], config["split_items"])
        self.assertTrue(config["exclude_lan"])

    def test_items_apply_while_the_switch_is_on(self):
        items = [{"type": "ip", "value": "10.0.0.5"}]
        self.write_split({"exclude_lan": False, "split_items": items})
        self.db.set_setting("split_tunneling", "true")

        self.assertEqual(items, RoutingManager("")._get_split_config()["split_items"])

    def test_lists_predating_the_switch_keep_working(self):
        self.write_split(
            {"exclude_lan": False, "split_items": [{"type": "ip", "value": "10.0.0.5"}]}
        )

        self.assertTrue(split_tunneling_enabled(db=self.db))

    def test_switch_defaults_to_off_without_items(self):
        self.write_split({"exclude_lan": False, "split_items": []})

        self.assertFalse(split_tunneling_enabled(db=self.db))

    def test_include_mode_keeps_traffic_in_the_tunnel(self):
        self.write_split(
            {
                "exclude_lan": False,
                "mode": "include",
                "split_items": [{"type": "app", "value": "/usr/bin/curl"}],
            }
        )
        self.db.set_setting("split_tunneling", "true")

        self.assertEqual([], RoutingManager("")._get_split_config()["split_items"])


class SettingsRouteTest(unittest.TestCase):
    """End-to-end checks through the HTTP layer the GUI actually calls."""

    def setUp(self):
        self.config_dir = tempfile.mkdtemp(prefix="pvpn-routes-")
        os.environ["PVPN_CONFIG_DIR"] = self.config_dir
        api_module.app.config["TESTING"] = True
        self.client = api_module.app.test_client()

    def tearDown(self):
        os.environ.pop("PVPN_CONFIG_DIR", None)
        shutil.rmtree(self.config_dir, ignore_errors=True)

    def test_posting_the_gui_toggle_reaches_the_connection_path(self):
        Database().add_awg_config("stealth", json.dumps({"Jc": 4}), 3)

        response = self.client.post(
            "/api/settings",
            json={"obfuscation_enabled": "true", "obfuscation_config": "stealth"},
        )

        self.assertEqual(200, response.status_code)
        self.assertEqual("config", Database().get_setting("active_awg_mode"))

    def test_posting_custom_dns_reaches_the_connection_path(self):
        response = self.client.post("/api/settings", json={"custom_dns": "8.8.8.8, 8.8.4.4"})

        self.assertEqual(200, response.status_code)
        self.assertEqual("google", Database().get_setting("active_dns_profile"))

    def test_saving_from_the_gui_keeps_exclude_lan(self):
        path = os.path.join(self.config_dir, "split_tunnel.json")
        with open(path, "w", encoding="utf-8") as handle:
            json.dump({"exclude_lan": True, "split_items": []}, handle, indent=4)

        response = self.client.post(
            "/api/settings/split",
            json={"mode": "exclude", "split_items": [{"type": "ip", "value": "10.0.0.5"}]},
        )

        self.assertEqual(200, response.status_code)
        with open(path, encoding="utf-8") as handle:
            saved = json.load(handle)
        self.assertTrue(saved["exclude_lan"])
        self.assertEqual(1, len(saved["split_items"]))


if __name__ == "__main__":
    unittest.main()
