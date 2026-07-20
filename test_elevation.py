import os
import subprocess
import sys
import unittest
from unittest import mock

from pvpn_cli.cli.elevation import elevate_command_linux


class LinuxElevationTest(unittest.TestCase):
    @mock.patch("pvpn_cli.cli.elevation.get_config_dir", return_value="/tmp/pvpn config")
    @mock.patch("shutil.which", side_effect=lambda name: "/usr/bin/sudo" if name == "sudo" else None)
    @mock.patch("os.geteuid", return_value=1000)
    @mock.patch("subprocess.run")
    def test_frozen_relaunch_resets_pyinstaller_environment(
        self, run, _geteuid, _which, _config_dir
    ):
        with mock.patch.object(sys, "frozen", True, create=True), mock.patch.object(
            sys, "executable", "/opt/pvpn-next"
        ):
            self.assertTrue(elevate_command_linux(["connect", "NL-FREE#156"]))

        run.assert_called_once_with(
            [
                "sudo",
                "env",
                "PYINSTALLER_RESET_ENVIRONMENT=1",
                "/opt/pvpn-next",
                "--config-dir=/tmp/pvpn config",
                "connect",
                "NL-FREE#156",
            ],
            check=True,
        )

    @mock.patch("pvpn_cli.cli.elevation.get_config_dir", return_value="/tmp/pvpn")
    @mock.patch("shutil.which", side_effect=lambda name: "/usr/bin/doas" if name == "doas" else None)
    @mock.patch("os.geteuid", return_value=1000)
    @mock.patch("subprocess.run")
    def test_source_relaunch_keeps_python_entrypoint(
        self, run, _geteuid, _which, _config_dir
    ):
        with mock.patch.object(sys, "frozen", False, create=True), mock.patch.object(
            sys, "executable", "/usr/bin/python3"
        ), mock.patch.object(sys, "argv", ["relative/pvpn-next"]):
            self.assertTrue(elevate_command_linux(["disconnect"]))

        run.assert_called_once_with(
            [
                "doas",
                "/usr/bin/python3",
                os.path.abspath("relative/pvpn-next"),
                "--config-dir=/tmp/pvpn",
                "disconnect",
            ],
            check=True,
        )

    @mock.patch("shutil.which", return_value="/usr/bin/sudo")
    @mock.patch("os.geteuid", return_value=1000)
    @mock.patch("subprocess.run", side_effect=subprocess.CalledProcessError(7, "sudo"))
    def test_elevated_failure_preserves_exit_code(self, _run, _geteuid, _which):
        with self.assertRaises(SystemExit) as raised:
            elevate_command_linux(["connect", "NL-FREE#156"])
        self.assertEqual(7, raised.exception.code)


if __name__ == "__main__":
    unittest.main()
