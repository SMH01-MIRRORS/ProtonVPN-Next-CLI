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


if __name__ == "__main__":
    unittest.main()
