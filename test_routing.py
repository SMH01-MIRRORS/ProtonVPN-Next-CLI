import os
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from pvpn_cli.routing import RoutingManager, launch_linux_engine, open_regular_no_follow, stage_frozen_engine


class LinuxEngineLaunchTest(unittest.TestCase):
    def test_engine_launch_handles_spaces_without_a_shell(self):
        with tempfile.TemporaryDirectory(prefix="pvpn project ") as root:
            engine = os.path.join(root, "engine with spaces")
            config = os.path.join(root, "connection config")
            log = os.path.join(root, "awg output")
            client_log = os.path.join(root, "client output")
            with open(engine, "w", encoding="utf-8") as script:
                script.write('#!/bin/sh\ncat >/dev/null\nprintf "engine:%s" "$2"\n')
            os.chmod(engine, 0o700)
            with open(config, "w", encoding="utf-8") as source:
                source.write("config")

            process = launch_linux_engine(
                engine, "1.1.1.1, 1.0.0.1", config, log, client_log
            )
            self.assertEqual(0, process.wait(timeout=2))
            with open(log, encoding="utf-8") as output:
                self.assertEqual("engine:1.1.1.1, 1.0.0.1", output.read())
            with open(client_log, encoding="utf-8") as errors:
                self.assertEqual("", errors.read())

    def test_sensitive_output_symlinks_are_rejected(self):
        with tempfile.TemporaryDirectory() as root:
            target = os.path.join(root, "target")
            link = os.path.join(root, "link")
            with open(target, "w", encoding="utf-8") as output:
                output.write("do not overwrite")
            os.symlink(target, link)
            with self.assertRaises(OSError):
                open_regular_no_follow(
                    link, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, "w"
                )
            with open(target, encoding="utf-8") as output:
                self.assertEqual("do not overwrite", output.read())


class RootInputValidationTest(unittest.TestCase):
    def test_split_network_rejects_shell_injection(self):
        with tempfile.TemporaryDirectory() as root, mock.patch.dict(
            os.environ, {"PVPN_CONFIG_DIR": root}
        ):
            manager = RoutingManager("")
            ips, _apps = manager._resolve_ips([
                {"type": "ip", "value": "10.0.0.0/8; touch /root/pwned"},
                {"type": "ip", "value": "192.168.1.1/24"},
            ])
        self.assertEqual(["192.168.1.0/24"], ips)

    def test_invalid_vpn_address_is_rejected_before_root_commands(self):
        with tempfile.TemporaryDirectory() as root, mock.patch.dict(
            os.environ, {"PVPN_CONFIG_DIR": root}
        ):
            manager = RoutingManager("")
            with self.assertRaisesRegex(RuntimeError, "Invalid VPN address"):
                manager.start_vpn(
                    "1.2.3.4; id", "/engine", "/config", "/log"
                )


class FrozenEngineStagingTest(unittest.TestCase):
    def test_staged_engine_survives_extraction_directory_cleanup(self):
        with tempfile.TemporaryDirectory() as root:
            extraction_dir = os.path.join(root, "_MEI-test")
            runtime_dir = os.path.join(root, "run", "pvpn-next")
            os.makedirs(extraction_dir)
            bundled_engine = os.path.join(extraction_dir, "pvpn-engine")
            with open(bundled_engine, "w", encoding="utf-8") as engine:
                engine.write("#!/bin/sh\nprintf staged-engine")
            os.chmod(bundled_engine, 0o700)

            with mock.patch.object(sys, "frozen", True, create=True):
                staged_engine = stage_frozen_engine(bundled_engine, runtime_dir)
                self.assertEqual(staged_engine, stage_frozen_engine(bundled_engine, runtime_dir))

            os.remove(bundled_engine)
            os.rmdir(extraction_dir)

            self.assertTrue(stat.S_IMODE(os.stat(staged_engine).st_mode) & stat.S_IXUSR)
            output = subprocess.run(
                [staged_engine], check=True, capture_output=True, text=True
            ).stdout
            self.assertEqual("staged-engine", output)

    def test_non_frozen_engine_is_not_copied(self):
        with mock.patch.object(sys, "frozen", False, create=True):
            self.assertEqual(
                "/project/engine/pvpn-engine",
                stage_frozen_engine("/project/engine/pvpn-engine"),
            )

    def test_rejects_symlink_runtime_directory(self):
        with tempfile.TemporaryDirectory() as root:
            source = os.path.join(root, "pvpn-engine")
            target_dir = os.path.join(root, "real-runtime")
            runtime_link = os.path.join(root, "runtime-link")
            with open(source, "wb") as engine:
                engine.write(b"engine")
            os.mkdir(target_dir)
            os.symlink(target_dir, runtime_link)

            with mock.patch.object(sys, "frozen", True, create=True):
                with self.assertRaises(RuntimeError):
                    stage_frozen_engine(source, runtime_link)


class WindowsStatelessDnsTest(unittest.TestCase):
    def _record(self, action):
        commands = []
        with tempfile.TemporaryDirectory() as root, mock.patch.dict(
            os.environ, {"PVPN_CONFIG_DIR": root}
        ):
            manager = RoutingManager("")
            with mock.patch.object(
                manager,
                "_run_cmd",
                side_effect=lambda cmd, silent=False: commands.append(cmd) or "",
            ), mock.patch("pvpn_cli.routing.subprocess.run") as run:
                action(manager)
                return commands, run

    def test_tunnel_dns_is_never_written_to_the_persistent_store(self):
        commands, _run = self._record(
            lambda manager: manager._windows_apply_dns("awg0", ["10.2.0.1", "10.2.0.2"])
        )
        configured = [c for c in commands if "source=static" in c]
        self.assertEqual(1, len(configured))
        self.assertIn("address=10.2.0.1", configured[0])
        self.assertIn("register=none", configured[0])
        self.assertEqual(
            [
                "netsh", "interface", "ipv4", "add", "dnsservers",
                "name=awg0", "address=10.2.0.2", "index=2", "validate=no",
            ],
            [c for c in commands if "add" in c and "dnsservers" in c][0],
        )
        self.assertEqual([], [c for c in commands if "add" in c and "store=persistent" in c])

    def test_ipv6_servers_are_configured_on_the_ipv6_stack(self):
        commands, _run = self._record(
            lambda manager: manager._windows_apply_dns(
                "awg0", ["10.2.0.1", "2606:4700:4700::1111"]
            )
        )
        configured = [c for c in commands if "source=static" in c]
        self.assertEqual(2, len(configured))
        self.assertIn("address=10.2.0.1", [c for c in configured if "ipv4" in c][0])
        self.assertIn(
            "address=2606:4700:4700::1111", [c for c in configured if "ipv6" in c][0]
        )

    def test_purge_clears_state_a_crash_could_have_left_behind(self):
        commands, run = self._record(lambda manager: manager._windows_purge_dns_state())
        powershell = " ".join(run.call_args[0][0])
        self.assertIn("Remove-DnsClientNrptRule", powershell)
        self.assertNotIn("Add-DnsClientNrptRule", powershell)
        for family in ("ipv4", "ipv6"):
            self.assertIn(
                ["netsh", "interface", family, "set", "dnsservers", "name=awg0", "source=dhcp"],
                commands,
            )
        self.assertIn(
            ["netsh", "interface", "ipv6", "delete", "route", "::/0", "awg0", "store=persistent"],
            commands,
        )


if __name__ == "__main__":
    unittest.main()
