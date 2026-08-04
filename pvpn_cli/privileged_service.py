"""Minimal root broker for Linux VPN networking operations.

The service intentionally exposes only connect and disconnect over a protected
Unix socket.  It never accepts shell commands, executable paths, environment
variables, or arbitrary configuration directories from clients.
"""

from __future__ import annotations

import json
import os
import shutil
import socket
import struct
import subprocess
import sys
from pathlib import Path
from typing import Any

# The broker itself only runs as root on Linux, but Windows still imports this
# module for its client-side helpers, and grp/pwd do not exist there.
if sys.platform == "linux":
    import grp
    import pwd

DEFAULT_SOCKET_PATH = "/run/pvpn-next-service/control.sock"
SERVICE_GROUP = "pvpn-next"
MAX_REQUEST_BYTES = 64 * 1024


class PrivilegedServiceUnavailable(RuntimeError):
    """The optional system service is not installed or not reachable."""


class PrivilegedServiceError(RuntimeError):
    """The system service received the request but rejected or failed it."""


def _read_message(conn: socket.socket) -> dict[str, Any]:
    data = bytearray()
    while len(data) <= MAX_REQUEST_BYTES:
        chunk = conn.recv(min(4096, MAX_REQUEST_BYTES + 1 - len(data)))
        if not chunk:
            break
        data.extend(chunk)
        if b"\n" in chunk:
            break
    if len(data) > MAX_REQUEST_BYTES:
        raise ValueError("Request is too large")
    line = bytes(data).split(b"\n", 1)[0]
    if not line:
        raise ValueError("Empty request")
    request = json.loads(line.decode("utf-8"))
    if not isinstance(request, dict):
        raise ValueError("Request must be an object")
    return request


def _write_message(conn: socket.socket, response: dict[str, Any]) -> None:
    payload = json.dumps(response, ensure_ascii=False, separators=(",", ":"))
    conn.sendall(payload.encode("utf-8") + b"\n")


def _peer_identity(conn: socket.socket) -> tuple[int, int, int]:
    if not hasattr(socket, "SO_PEERCRED"):
        raise RuntimeError("SO_PEERCRED is unavailable")
    raw = conn.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i"))
    pid, uid, gid = struct.unpack("3i", raw)
    if uid == 0:
        raise PermissionError("Root clients must invoke the CLI directly")
    return pid, uid, gid


def _validate_config_dir(value: Any, uid: int) -> tuple[str, str]:
    if not isinstance(value, str) or not value:
        raise ValueError("config_dir is required")
    account = pwd.getpwuid(uid)
    expected = os.path.abspath(os.path.join(account.pw_dir, ".config", "pvpn-next"))
    requested = os.path.abspath(value)
    if requested != expected or os.path.realpath(requested) != expected:
        raise PermissionError("Only a non-symlink caller ~/.config/pvpn-next directory is allowed")
    if os.path.exists(expected):
        info = os.lstat(expected)
        if not os.path.isdir(expected) or info.st_uid != uid:
            raise PermissionError("The caller must own ~/.config/pvpn-next")
    return expected, account.pw_name


def _validate_action_args(action: Any, args: Any) -> list[str]:
    if action not in ("connect", "disconnect"):
        raise ValueError("Unsupported privileged action")
    if not isinstance(args, list) or not all(isinstance(item, str) for item in args):
        raise ValueError("args must be a list of strings")
    if any("\x00" in item or "\n" in item or "\r" in item for item in args):
        raise ValueError("Control characters are not allowed")

    if action == "disconnect":
        if args != ["disconnect"]:
            raise ValueError("disconnect accepts no arguments")
        return args

    if not (2 <= len(args) <= 4) or args[0] != "connect":
        raise ValueError("Invalid connect arguments")
    if not args[1] or len(args[1]) > 1024:
        raise ValueError("Invalid server identifier")
    for item in args[2:]:
        if item.startswith("awg="):
            if len(item) > 16384:
                raise ValueError("AWG parameters are too large")
        elif item.startswith("--port="):
            try:
                port = int(item.split("=", 1)[1])
            except ValueError as exc:
                raise ValueError("Invalid port") from exc
            if not 0 <= port <= 65535:
                raise ValueError("Invalid port")
        else:
            raise ValueError("Unsupported connect argument")
    return args


def _cli_command() -> list[str]:
    if getattr(sys, "frozen", False):
        return [os.path.realpath(sys.executable)]
    entrypoint = os.path.realpath(sys.argv[0])
    if not os.path.isfile(entrypoint):
        raise RuntimeError("Cannot locate the pvpn-next entrypoint")
    return [sys.executable, entrypoint]


def _run_action(uid: int, request: dict[str, Any]) -> dict[str, Any]:
    config_dir, username = _validate_config_dir(request.get("config_dir"), uid)
    action = request.get("action")
    args = _validate_action_args(action, request.get("args"))

    os.makedirs(config_dir, mode=0o700, exist_ok=True)
    account = pwd.getpwuid(uid)
    os.chown(config_dir, uid, account.pw_gid)

    command = _cli_command() + [f"--config-dir={config_dir}", "--gui-mode"] + args
    env = os.environ.copy()
    for key in ("_MEIPASS", "_MEIPASS1", "_MEIPASS2", "_MEIPASS3"):
        env.pop(key, None)
    if getattr(sys, "frozen", False):
        env["PYINSTALLER_RESET_ENVIRONMENT"] = "1"
    env.update({
        "PVPN_GUI_MODE": "1",
        "PVPN_SERVICE_CHILD": "1",
        "SUDO_USER": username,
        "USER": username,
        "HOME": account.pw_dir,
        "PATH": "/run/wrappers/bin:/run/current-system/sw/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
    })

    print(f"[broker] uid={uid} action={action}", flush=True)
    try:
        result = subprocess.run(
            command,
            env=env,
            capture_output=True,
            text=True,
            errors="replace",
            timeout=120,
        )
    except subprocess.TimeoutExpired as exc:
        return {
            "ok": False,
            "error": "Privileged VPN operation timed out",
            "stdout": exc.stdout or "",
            "stderr": exc.stderr or "",
        }

    return {
        "ok": result.returncode == 0,
        "returncode": result.returncode,
        "stdout": result.stdout,
        "stderr": result.stderr,
        "error": None if result.returncode == 0 else f"VPN operation exited with code {result.returncode}",
    }


def _handle_client(conn: socket.socket) -> None:
    try:
        _pid, uid, _gid = _peer_identity(conn)
        response = _run_action(uid, _read_message(conn))
    except Exception as exc:
        response = {"ok": False, "error": str(exc), "stdout": "", "stderr": ""}
    _write_message(conn, response)


def run_privileged_service(socket_path: str = DEFAULT_SOCKET_PATH) -> None:
    """Run the root broker. Intended to be started only by systemd."""
    if sys.platform != "linux" or os.geteuid() != 0:
        raise RuntimeError("The privileged service must run as root on Linux")

    path = Path(socket_path)
    path.parent.mkdir(mode=0o750, parents=True, exist_ok=True)
    try:
        group_gid = grp.getgrnam(SERVICE_GROUP).gr_gid
    except KeyError as exc:
        raise RuntimeError(f"Required group '{SERVICE_GROUP}' does not exist") from exc
    os.chown(path.parent, 0, group_gid)
    os.chmod(path.parent, 0o750)
    try:
        path.unlink()
    except FileNotFoundError:
        pass

    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as server:
        server.bind(str(path))
        os.chown(path, 0, group_gid)
        os.chmod(path, 0o660)
        server.listen(8)
        print(f"PVPN privileged service listening on {path}", flush=True)
        while True:
            conn, _ = server.accept()
            with conn:
                _handle_client(conn)


def call_privileged_service(
    args: list[str], config_dir: str, socket_path: str = DEFAULT_SOCKET_PATH, timeout: float = 125
) -> str:
    """Execute a whitelisted action through the optional system service."""
    action = args[0] if args else ""
    request = {"action": action, "args": args, "config_dir": config_dir}
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
            client.settimeout(timeout)
            client.connect(socket_path)
            _write_message(client, request)
            response = _read_message(client)
    except (FileNotFoundError, ConnectionRefusedError, PermissionError, socket.timeout, OSError) as exc:
        raise PrivilegedServiceUnavailable(str(exc)) from exc

    if not response.get("ok"):
        details = "\n".join(
            part for part in (response.get("error"), response.get("stdout"), response.get("stderr")) if part
        )
        raise PrivilegedServiceError(details or "Privileged VPN operation failed")
    return "\n".join(part for part in (response.get("stdout"), response.get("stderr")) if part)


def install_privileged_service(user: str | None = None) -> None:
    """Install a frozen CLI as a root-owned systemd broker (one-time elevation)."""
    if sys.platform != "linux":
        raise RuntimeError("The system service is supported only on Linux")
    if os.geteuid() != 0:
        raise RuntimeError("Run this one-time installer through sudo, doas, or run0")
    if not getattr(sys, "frozen", False):
        raise RuntimeError("Build the standalone Linux binary before installing the service")

    source = os.path.realpath(sys.executable)
    target_dir = Path("/usr/local/lib/pvpn-next")
    target = target_dir / "pvpn-next"
    target_dir.mkdir(mode=0o755, parents=True, exist_ok=True)
    temporary = target.with_suffix(".new")
    shutil.copyfile(source, temporary)
    os.chown(temporary, 0, 0)
    os.chmod(temporary, 0o755)
    os.replace(temporary, target)

    try:
        grp.getgrnam(SERVICE_GROUP)
    except KeyError:
        subprocess.run(["groupadd", "--system", SERVICE_GROUP], check=True)
    if user:
        pwd.getpwnam(user)
        subprocess.run(["usermod", "-a", "-G", SERVICE_GROUP, user], check=True)

    unit = f'''[Unit]
Description=PVPN Next privileged networking broker
After=network.target

[Service]
Type=simple
ExecStart={target} privileged-service
User=root
Group={SERVICE_GROUP}
UMask=0077
RuntimeDirectory=pvpn-next-service pvpn-next
RuntimeDirectoryMode=0750
Restart=on-failure
RestartSec=2
NoNewPrivileges=yes
PrivateTmp=yes
ProtectSystem=strict
ProtectHome=false
ReadWritePaths=/home /run/pvpn-next /run/pvpn-next-service
ProtectKernelTunables=yes
ProtectKernelModules=yes
ProtectControlGroups=yes
RestrictNamespaces=yes
RestrictRealtime=yes
LockPersonality=yes
RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6 AF_NETLINK
CapabilityBoundingSet=CAP_NET_ADMIN CAP_NET_RAW CAP_DAC_OVERRIDE CAP_CHOWN CAP_FOWNER CAP_KILL CAP_SETUID CAP_SETGID

[Install]
WantedBy=multi-user.target
'''
    unit_path = Path("/etc/systemd/system/pvpn-next-privileged.service")
    unit_path.write_text(unit, encoding="utf-8")
    os.chown(unit_path, 0, 0)
    os.chmod(unit_path, 0o644)
    subprocess.run(["systemctl", "daemon-reload"], check=True)
    subprocess.run(["systemctl", "enable", "--now", "pvpn-next-privileged.service"], check=True)
    print("PVPN Next privileged service installed.")
    if user:
        print(f"User {user} was added to {SERVICE_GROUP}; log out and back in once.")
