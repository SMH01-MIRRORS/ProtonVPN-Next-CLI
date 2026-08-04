import json
import os
import pwd
import socket
import tempfile
import threading
import unittest
from unittest import mock

from pvpn_cli.privileged_service import (
    PrivilegedServiceError,
    _validate_action_args,
    _validate_config_dir,
    call_privileged_service,
)


class RequestValidationTest(unittest.TestCase):
    def test_only_connect_and_disconnect_are_exposed(self):
        with self.assertRaises(ValueError):
            _validate_action_args("shell", ["rm", "-rf", "/"])
        with self.assertRaises(ValueError):
            _validate_action_args("connect", ["connect", "server", "--output=/root/file"])
        self.assertEqual(["disconnect"], _validate_action_args("disconnect", ["disconnect"]))

    def test_connect_accepts_only_awg_and_port(self):
        args = ["connect", "NL-FREE#1", "awg=Jc=3,Jmin=20", "--port=51820"]
        self.assertEqual(args, _validate_action_args("connect", args))
        with self.assertRaises(ValueError):
            _validate_action_args("connect", ["connect", "NL-FREE#1", "--port=70000"])

    def test_config_directory_is_pinned_to_peer_home(self):
        account = pwd.getpwuid(os.getuid())
        expected = os.path.join(account.pw_dir, ".config", "pvpn-next")
        self.assertEqual((os.path.realpath(expected), account.pw_name), _validate_config_dir(expected, os.getuid()))
        with self.assertRaises(PermissionError):
            _validate_config_dir("/etc", os.getuid())


class ClientProtocolTest(unittest.TestCase):
    def _serve_once(self, socket_path, response, captured):
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as server:
            server.bind(socket_path)
            server.listen(1)
            conn, _ = server.accept()
            with conn:
                payload = b""
                while b"\n" not in payload:
                    payload += conn.recv(4096)
                captured.update(json.loads(payload.split(b"\n", 1)[0]))
                conn.sendall(json.dumps(response).encode() + b"\n")

    def test_client_sends_structured_request_without_shell(self):
        with tempfile.TemporaryDirectory() as root:
            path = os.path.join(root, "control.sock")
            captured = {}
            thread = threading.Thread(
                target=self._serve_once,
                args=(path, {"ok": True, "stdout": "connected", "stderr": ""}, captured),
            )
            thread.start()
            # Wait until bind() completes without adding test-only sleeps to production.
            for _ in range(1000):
                if os.path.exists(path):
                    break
            output = call_privileged_service(
                ["connect", "NL-FREE#1"],
                os.path.join(pwd.getpwuid(os.getuid()).pw_dir, ".config", "pvpn-next"),
                socket_path=path,
            )
            thread.join()
            self.assertEqual("connected", output)
            self.assertEqual("connect", captured["action"])
            self.assertEqual(["connect", "NL-FREE#1"], captured["args"])

    def test_service_errors_are_not_silently_downgraded(self):
        with tempfile.TemporaryDirectory() as root:
            path = os.path.join(root, "control.sock")
            captured = {}
            thread = threading.Thread(
                target=self._serve_once,
                args=(path, {"ok": False, "error": "denied", "stdout": "", "stderr": ""}, captured),
            )
            thread.start()
            for _ in range(1000):
                if os.path.exists(path):
                    break
            with self.assertRaisesRegex(PrivilegedServiceError, "denied"):
                call_privileged_service(["disconnect"], "/tmp/ignored", socket_path=path)
            thread.join()


class ApiBrokerPreferenceTest(unittest.TestCase):
    @mock.patch("pvpn_cli.privileged_service.call_privileged_service", return_value="broker output")
    @mock.patch("pvpn_cli.routing.get_config_dir", return_value="/home/alice/.config/pvpn-next")
    def test_api_prefers_broker_before_sudo(self, _config_dir, broker):
        from pvpn_cli.api import run_cli_elevated

        with mock.patch("sys.platform", "linux"):
            self.assertEqual("broker output", run_cli_elevated(["disconnect"]))
        broker.assert_called_once_with(["disconnect"], "/home/alice/.config/pvpn-next")


if __name__ == "__main__":
    unittest.main()
