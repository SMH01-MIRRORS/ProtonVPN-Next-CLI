"""Privilege elevation helpers: Windows UAC and Linux sudo/doas."""

import os
import platform
import subprocess
import sys

from pvpn_cli.cli.config import get_config_dir


def elevate_if_needed_windows(cmd_args=None, exit_on_success=True):
    if platform.system() != "Windows":
        return
    import ctypes
    if not ctypes.windll.shell32.IsUserAnAdmin():
        print("-> Requesting administrator privileges via UAC...")

        args_to_use = cmd_args if cmd_args is not None else sys.argv[1:]
        config_arg = f'--config-dir="{get_config_dir()}"'

        import tempfile
        import uuid
        import time
        from ctypes import wintypes

        log_id = str(uuid.uuid4())
        log_file = os.path.join(tempfile.gettempdir(), f"pvpn_elevate_{log_id}.log")
        open(log_file, "w").close()

        if os.environ.get("PVPN_GUI_MODE") == "1":
            args_to_use = ['--gui-mode'] + args_to_use
        args_to_use = [config_arg, f'--redirect-logs="{log_file}"'] + args_to_use
        params = ' '.join([arg if arg.startswith('--') and '"' in arg else f'"{arg}"' for arg in args_to_use])

        class SHELLEXECUTEINFOW(ctypes.Structure):
            _fields_ = [
                ("cbSize", wintypes.DWORD),
                ("fMask", ctypes.c_ulong),
                ("hwnd", wintypes.HWND),
                ("lpVerb", ctypes.c_wchar_p),
                ("lpFile", ctypes.c_wchar_p),
                ("lpParameters", ctypes.c_wchar_p),
                ("lpDirectory", ctypes.c_wchar_p),
                ("nShow", ctypes.c_int),
                ("hInstApp", wintypes.HINSTANCE),
                ("lpIDList", ctypes.c_void_p),
                ("lpClass", ctypes.c_wchar_p),
                ("hkeyClass", wintypes.HKEY),
                ("dwHotKey", wintypes.DWORD),
                ("hIconOrMonitor", wintypes.HANDLE),
                ("hProcess", wintypes.HANDLE),
            ]

        SEE_MASK_NOCLOSEPROCESS = 0x00000040

        sei = SHELLEXECUTEINFOW()
        sei.cbSize = ctypes.sizeof(sei)
        sei.fMask = SEE_MASK_NOCLOSEPROCESS
        sei.hwnd = None
        sei.lpVerb = "runas"

        if getattr(sys, 'frozen', False):
            sei.lpFile = sys.executable
            sei.lpParameters = params
        else:
            sei.lpFile = sys.executable
            sei.lpParameters = f'"{os.path.abspath(sys.argv[0])}" {params}'

        sei.lpDirectory = None
        sei.nShow = 0  # SW_HIDE

        ret = ctypes.windll.shell32.ShellExecuteExW(ctypes.byref(sei))
        if not ret or not sei.hProcess:
            print("[ERROR] Failed to UAC elevate privileges. (Prompt denied or failed)")
            sys.exit(1)

        hProcess = sei.hProcess

        try:
            with open(log_file, "r", encoding="utf-8") as f:
                while True:
                    line = f.readline()
                    if line:
                        print(line, end="")
                        continue

                    # Check if process is still running
                    wait_ret = ctypes.windll.kernel32.WaitForSingleObject(hProcess, 50)
                    if wait_ret == 0:  # WAIT_OBJECT_0
                        # Final read
                        for remaining_line in f.readlines():
                            print(remaining_line, end="")
                        break
                    time.sleep(0.05)
        except KeyboardInterrupt:
            ctypes.windll.kernel32.TerminateProcess(hProcess, 1)
        finally:
            ctypes.windll.kernel32.CloseHandle(hProcess)
            try:
                os.remove(log_file)
            except:
                pass

        if exit_on_success:
            sys.exit(0)
        else:
            return True  # Returned to let caller know elevation happened and completed


def elevate_command_linux(cmd_args):
    import shutil
    if os.geteuid() == 0:
        return False

    elevate_cmd = "doas" if shutil.which("doas") else ("sudo" if shutil.which("sudo") else None)
    if elevate_cmd:
        print(f"-> Elevating privileges using {elevate_cmd}...")
        try:
            if getattr(sys, 'frozen', False):
                # A privileged one-file process must unpack into its own temporary
                # directory. Reusing the caller's PyInstaller environment leaves
                # sys._MEIPASS pointing at files that disappear when the caller exits.
                base_cmd = ["env", "PYINSTALLER_RESET_ENVIRONMENT=1", sys.executable]
            else:
                base_cmd = [sys.executable, os.path.abspath(sys.argv[0])]

            config_arg = f"--config-dir={get_config_dir()}"
            subprocess.run([elevate_cmd] + base_cmd + [config_arg] + cmd_args, check=True)
            return True
        except subprocess.CalledProcessError as e:
            sys.exit(e.returncode)
    else:
        print("[ERROR] Neither sudo nor doas found. Please run as root.")
        sys.exit(1)
