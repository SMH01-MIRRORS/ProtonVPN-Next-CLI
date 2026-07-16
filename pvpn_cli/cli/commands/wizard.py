"""Setup wizard and privilege management commands."""

import os
import platform
import subprocess
import sys

from pvpn_cli.cli.commands.auth import do_guest_login, do_login, do_register_cert
from pvpn_cli.cli.commands.servers import do_fetch_locale, do_fetch_servers
from pvpn_cli.cli.commands.settings import do_set_bypass


def do_grant_privileges():
    import shutil
    if platform.system() != "Linux":
        print("[ERROR] This command is only supported on Linux.")
        return
        
    if getattr(sys, 'frozen', False):
        binary_path = os.path.abspath(sys.executable)
    else:
        # In dev mode, we would need to grant privileges to python3, which is dangerous!
        # It's better to just use the script path for the text output as a placeholder.
        print("[WARNING] You are running from source code. Granting privileges to python3 is a massive security risk.")
        print("Please compile the binary first (make build-linux-bin) and run grant-privileges from the compiled executable.")
        binary_path = os.path.abspath(sys.argv[0])
        
    user = os.environ.get("SUDO_USER", os.environ.get("USER"))
    
    has_doas = shutil.which("doas")
    has_sudo = shutil.which("sudo")
    elevate_cmd = "doas" if has_doas else "sudo"
    
    if has_doas:
        rule = f"permit nopass {user} as root cmd {binary_path}\n"
        print("\n=== For doas users (e.g. NixOS, Alpine, OpenBSD) ===")
        print("Add the following line to your /etc/doas.conf:")
        print(f"\n    permit nopass {user} as root cmd {binary_path}")
        print("\nNote: On NixOS, you should add this to your configuration.nix instead:")
        print(f"  security.doas.extraRules = [")
        print(f"    {{ groups = [ \"wheel\" ]; noPass = true; cmd = \"{binary_path}\"; }}")
        print(f"  ];")

    if has_sudo:
        rule = f"{user} ALL=(ALL) NOPASSWD: {binary_path}\n"
        print("\n=== For sudo users (e.g. Ubuntu, Debian, Arch) ===")
        print("We can automatically create a rule in /etc/sudoers.d/pvpn-next")
        try:
            subprocess.run([elevate_cmd, "sh", "-c", f"echo '{rule}' > /etc/sudoers.d/pvpn-next"], check=True)
            print("-> Successfully added sudoers rule!")
        except Exception:
            print(f"Please run this manually:\n    echo '{rule}' | sudo tee /etc/sudoers.d/pvpn-next")
            
    print("\nAfter granting privileges, the client will automatically execute connection tasks without asking for your password!")

def do_autosetup(proxy_override=None):
    print("=== PVPN-Next Auto-Setup ===\n")
    
    if proxy_override:
        print(f"-> Using provided API bypass strategy: {proxy_override}")
        do_set_bypass(proxy_override)
    else:
        print("Is access to Proton API blocked in your network?")
        print("Select an API bypass strategy:")
        print("0. None (Direct Connection)")
        print("1. Cloudflare Proxy (--proxy-cf)")
        print("2. Netlify Proxy (--proxy-netlify)")
        print("3. Deno Proxy (--proxy-deno)")
        
        while True:
            choice = input("\nEnter your choice (0-3) [0]: ").strip()
            if not choice:
                choice = "0"
            if choice in ["0", "1", "2", "3"]:
                do_set_bypass(choice)
                break
            print("Invalid choice. Please enter a number between 0 and 3.")

    print("\nSelect Login Method:")
    print("1. Guest Login (Anonymous)")
    print("2. User Login (Proton Account)")
    while True:
        login_choice = input("Enter your choice (1-2) [1]: ").strip()
        if not login_choice:
            login_choice = "1"
        if login_choice in ["1", "2"]:
            break
        print("Invalid choice. Please enter 1 or 2.")

    print(f"\n-> [1/4] Executing {'guest' if login_choice == '1' else 'user'} login...")
    try:
        if login_choice == "1":
            do_guest_login()
        else:
            do_login()
    except SystemExit as e:
        if e.code != 0:
            return

    print("\n-> [2/4] Registering VPN certificate...")
    try:
        do_register_cert()
    except SystemExit as e:
        if e.code != 0:
            return

    print("\n-> [3/4] Fetching servers list...")
    try:
        do_fetch_servers()
    except SystemExit as e:
        if e.code != 0:
            return

    print("\n-> [4/4] Fetching localized data...")
    import locale
    lang = "en"
    try:
        import sys
        if sys.platform == 'win32':
            import ctypes
            win_lang = locale.windows_locale.get(ctypes.windll.kernel32.GetUserDefaultUILanguage())
            if win_lang:
                lang = win_lang.split('_')[0]
        else:
            loc = locale.getlocale()[0]
            if loc:
                lang = loc.split('_')[0]
    except Exception:
        pass
    
    try:
        do_fetch_locale(lang)
    except Exception:
        pass
        
    print("\n=== Auto-Setup Complete ===")
    print("You can now connect using: connect <server_name>")
