"""PVPN Next CLI application: argument parser, command dispatch, and entry point."""

import argparse
import atexit
import os
import platform
import subprocess
import sys

from pvpn_cli.database import Database

from pvpn_cli.cli.config import fix_config_permissions, get_config_dir
from pvpn_cli.cli.commands.auth import (
    do_guest_login,
    do_login,
    do_register_cert,
    do_register_extended_cert,
    do_trigger_captcha,
)
from pvpn_cli.cli.commands.connection import do_connect, do_disconnect, do_status
from pvpn_cli.cli.commands.daemon import (
    do_daemon,
    do_manage_daemon,
    do_update_session,
    ensure_daemon_running,
)
from pvpn_cli.cli.commands.logs import (
    do_awg_logs,
    do_client_logs,
    do_daemon_logs,
    do_service_logs,
)
from pvpn_cli.cli.commands.servers import (
    do_fetch_loads,
    do_fetch_locale,
    do_fetch_servers,
    do_list_servers,
)
from pvpn_cli.cli.commands.settings import (
    do_awg_config,
    do_dns_config,
    do_gui_theme,
    do_port,
    do_set_awg,
    do_set_bypass,
    do_set_default_connect,
    do_set_mtu,
)
from pvpn_cli.cli.commands.split_tunnel import (
    do_exclude_lan,
    do_novpn,
    do_pid_scanner,
    do_split_tunneling,
)
from pvpn_cli.cli.commands.wizard import do_autosetup, do_grant_privileges


def do_version():
    print("1.0.0")


def run_cli(args_list=None):
    fix_config_permissions()
    parser = argparse.ArgumentParser(description="PVPN Next CLI")
    parser.add_argument("--config-dir", type=str, help="Override default configuration directory")
    parser.add_argument("--redirect-logs", type=str, help=argparse.SUPPRESS)
    parser.add_argument("--gui-mode", action="store_true", help=argparse.SUPPRESS)
    subparsers = parser.add_subparsers(dest="command", help="Available commands")
    
    # guest command
    subparsers.add_parser("guest", help="Log in via guest access (Android emulation)")
    
    # login command
    subparsers.add_parser("login", help="Log in with username and password (SRP & 2FA)")
    
    # fetch-servers command
    subparsers.add_parser("fetch-servers", help="Fetch and cache VPN servers list")

    # fetch-loads command
    subparsers.add_parser("fetch-loads", help="Update cached server loads")

    # list-servers command
    subparsers.add_parser("list-servers", help="List cached VPN servers as a tree")

    # fetch-locale command
    locale_parser = subparsers.add_parser("fetch-locale", help="Fetch translated locations")
    locale_parser.add_argument("locale", help="Locale code (e.g. ru, fr, es)")

    # connect command
    connect_parser = subparsers.add_parser("connect", help="Connect to a specific server")
    connect_parser.add_argument("server", help="Server name (e.g. NL-FREE#2)")
    connect_parser.add_argument("awg", nargs="?", help="AmneziaWG parameters (e.g. awg=\"vpn-next-default\")")
    connect_parser.add_argument("--port", type=int, default=None, help="Endpoint port override (0 uses the protocol default)")

    # set-awg command
    set_awg_parser = subparsers.add_parser("set-awg", help="Set custom AmneziaWG parameters to use on connect")
    set_awg_parser.add_argument("params", help="AmneziaWG parameters (e.g. Jc=1, Jmin=50)")

    # awg-config command
    awg_config_parser = subparsers.add_parser("awg-config", help="Manage saved AmneziaWG configurations")
    awg_config_parser.add_argument("action", choices=["list", "create", "delete", "set", "unset"], help="Action to perform")
    awg_config_parser.add_argument("config_args", nargs=argparse.REMAINDER, help="Parameters and/or name (depends on action)")

    # set-bypass command
    bypass_parser = subparsers.add_parser("set-bypass", help="Set API Block Bypass strategy",
        epilog="Available options:\n  0 or none       - Direct connection\n  1 or cloudflare - Cloudflare proxy\n  2 or netlify    - Netlify proxy\n  3 or deno       - Deno proxy",
        formatter_class=argparse.RawDescriptionHelpFormatter)
    bypass_parser.add_argument("bypass", help="Bypass strategy (name or number)")

    # set-mtu command
    mtu_parser = subparsers.add_parser("set-mtu", help="Set MTU size for VPN interface (default: 1280 for Wi-Fi compatibility)")
    mtu_parser.add_argument("mtu", help="MTU value (e.g. 1280, 1360, 1420)")

    # dns command
    dns_parser = subparsers.add_parser("dns", help="Manage custom DNS configurations")
    dns_parser.add_argument("action", choices=["list", "set", "add", "unset"], help="DNS action to perform")
    dns_parser.add_argument("dns_args", nargs=argparse.REMAINDER, help="DNS profile name and/or IP addresses")

    # status command
    subparsers.add_parser("status", help="Show current login and server cache status")
    
    # disconnect command
    subparsers.add_parser("disconnect", help="Disconnect the active VPN tunnel")
    
    # awg-logs command
    subparsers.add_parser("awg-logs", help="View AmneziaWG engine logs (Handshake, Keepalive, etc.)")
    
    # client-logs command
    subparsers.add_parser("client-logs", help="View Client logs (Engine setup, Python CLI, Daemon errors)")
    
    # daemon-logs command
    subparsers.add_parser("daemon-logs", help="View background update daemon logs")
    
    # service logs command
    subparsers.add_parser("service-logs", help="Show the Watchdog Microservice logs (Windows)")
    
    # exclude-lan command
    parser_exclude_lan = subparsers.add_parser("exclude-lan", help="Exclude Local Area Network (LAN) from VPN tunnel")
    parser_exclude_lan.add_argument("state", choices=["on", "off"], help="State (on/off)")
    
    # split-tunneling command
    parser_split = subparsers.add_parser("split-tunneling", help="Manage split tunneling for apps, domains, and IPs")
    parser_split.add_argument("action", choices=["list", "add", "remove"], help="Action to perform")
    parser_split.add_argument("value", nargs="?", help="Domain, IP, or Absolute Path to app binary (for add/remove)")
    
    # novpn wrapper
    parser_novpn = subparsers.add_parser("novpn", help="Run a command bypassing the VPN (Linux only)")
    parser_novpn.add_argument("cmd", nargs=argparse.REMAINDER, help="Command to run")
    
    # pid scanner (hidden internal command)
    parser_scanner = subparsers.add_parser("_pid-scanner", help=argparse.SUPPRESS)
    
    # daemon (hidden internal command)
    parser_daemon = subparsers.add_parser("_daemon", help=argparse.SUPPRESS)
    parser_watchdog = subparsers.add_parser("_watchdog", help=argparse.SUPPRESS)
    parser_install_watchdog = subparsers.add_parser("_install-watchdog", help=argparse.SUPPRESS)
    
    # update-session command
    subparsers.add_parser("update-session", help="Manually refresh API session tokens")
    
    # daemon management command
    parser_manage = subparsers.add_parser("daemon", help="Manage background update daemon")
    parser_manage.add_argument("action", choices=["start", "stop", "on", "off", "test"], help="Action")
    
    # version command
    subparsers.add_parser("version", help="Show CLI version")

    # register-cert command
    subparsers.add_parser("register-cert", help="Generate WG keys and register with API")
    
    # register-extended-cert command
    subparsers.add_parser("register-extended-cert", help="Generate WG keys and register with API as a persistent certificate")
    
    # trigger-captcha command
    subparsers.add_parser("trigger-captcha", help="Trigger a Captcha by spoofing an Android Emulator")
    
    # grant-privileges command
    subparsers.add_parser("grant-privileges", help="[Linux] Setup sudo/doas to run VPN without password prompts")
    
    # autosetup command
    parser_autosetup = subparsers.add_parser("autosetup", help="Interactive auto-setup wizard (login, cert, servers, locale)")
    parser_autosetup.add_argument("--proxy-cf", action="store_true", help="Auto-select Cloudflare proxy")
    parser_autosetup.add_argument("--proxy-netlify", action="store_true", help="Auto-select Netlify proxy")
    parser_autosetup.add_argument("--proxy-deno", action="store_true", help="Auto-select Deno proxy")
    parser_autosetup.add_argument("--no-proxy", action="store_true", help="Auto-select Direct connection (No proxy)")

    # api-server command (for GUI mode)
    parser_api = subparsers.add_parser("api-server", help="Start the local REST API server for GUI clients")
    parser_api.add_argument("--port", type=int, default=34115, help="Port to listen on (default: 34115)")
    parser_api.add_argument("--debug", action="store_true", help="Enable verbose API logging")
    parser_api.add_argument("--gui", action="store_true", help="Run in GUI mode (hide console on Windows)")
    parser_api.add_argument("--api-token", type=str, default=None, help="Secret token required for API requests")

    # Port management
    port_parser = subparsers.add_parser("port", help="VPN Port management")
    port_sub = port_parser.add_subparsers(dest="port_cmd")
    port_sub.add_parser("list", help="List available ports")
    set_port_parser = port_sub.add_parser("set", help="Set VPN port")
    set_port_parser.add_argument("value", help="Port number (0 for Auto)")

    # GUI Theme management
    theme_parser = subparsers.add_parser("gui-theme", help="GUI Theme management")
    theme_parser.add_argument("theme", nargs="?", help="Set GUI theme (system, light, dark, etc.)")

    # set-default-connect command
    default_conn_parser = subparsers.add_parser("set-default-connect", help="Set default connection behavior for Quick Connect")
    default_conn_parser.add_argument("strategy", choices=["best", "recent", "custom"], help="Strategy: best (lowest load), recent (last used), custom (specific server)")
    default_conn_parser.add_argument("server", nargs="?", help="Server name or ID (required only for 'custom' strategy)")

    args = parser.parse_args(args_list)
    
    if getattr(args, 'gui_mode', False):
        os.environ["PVPN_GUI_MODE"] = "1"
    
    if hasattr(args, 'redirect_logs') and args.redirect_logs:
        class LogWriter:
            def __init__(self, filename):
                self.filename = filename
            def write(self, msg):
                try:
                    with open(self.filename, "a", encoding="utf-8") as f:
                        f.write(msg)
                except Exception:
                    pass
            def flush(self):
                pass
        sys.stdout = LogWriter(args.redirect_logs)
        sys.stderr = sys.stdout
        def write_eof():
            try:
                with open(args.redirect_logs, "a", encoding="utf-8") as f:
                    f.write("\n===EOF===\n")
            except Exception:
                pass
        atexit.register(write_eof)
    
    if hasattr(args, 'config_dir') and args.config_dir:
        os.environ["PVPN_CONFIG_DIR"] = args.config_dir
        
    if not args.command:
        parser.print_help()
        sys.exit(1)
    
    if args.command == "guest":
        do_guest_login()
    elif args.command == "login":
        do_login()
    elif args.command == "fetch-servers":
        do_fetch_servers()
    elif args.command == "fetch-loads":
        do_fetch_loads()
    elif args.command == "list-servers":
        do_list_servers()
    elif args.command == "set-awg":
        do_set_awg(args.params)
    elif args.command == "set-mtu":
        do_set_mtu(args.mtu)
    elif args.command == "dns":
        do_dns_config(args.action, args.dns_args)
    elif args.command == "awg-config":
        do_awg_config(args.action, args.config_args)
    elif args.command == "fetch-locale":
        do_fetch_locale(args.locale)
    elif args.command == "connect":
        # Extract awg parameter if provided as awg=...
        awg_param = args.awg
        if awg_param and awg_param.startswith("awg="):
            awg_param = awg_param[4:].strip('"\'')
        do_connect(args.server, awg_param, args.port)
    elif args.command == "set-bypass":
        do_set_bypass(args.bypass)
    elif args.command == "status":
        do_status()
    elif args.command == "disconnect":
        do_disconnect()
    elif args.command == "awg-logs":
        do_awg_logs()
    elif args.command == "client-logs":
        do_client_logs()
    elif args.command == "daemon-logs":
        do_daemon_logs()
    elif args.command == "service-logs":
        do_service_logs()
    elif args.command == "_watchdog":
        from pvpn_cli.watchdog import Watchdog
        Watchdog().run()
    elif args.command == "_install-watchdog":
        from pvpn_cli.watchdog import Watchdog
        Watchdog().install()
    elif args.command == "exclude-lan":
        do_exclude_lan(args.state)
    elif args.command == "split-tunneling":
        do_split_tunneling(args.action, args.value)
    elif args.command == "autosetup":
        proxy = None
        if args.proxy_cf:
            proxy = "1"
        elif args.proxy_netlify:
            proxy = "2"
        elif args.proxy_deno:
            proxy = "3"
        elif args.no_proxy:
            proxy = "0"
        do_autosetup(proxy)
    elif args.command == "novpn":
        if not args.cmd:
            print("[ERROR] Command required for novpn")
            sys.exit(1)
        do_novpn(args.cmd)
    elif args.command == "_pid-scanner":
        do_pid_scanner()
    elif args.command == "_daemon":
        do_daemon()
    elif args.command == "update-session":
        do_update_session()
    elif args.command == "daemon":
        do_manage_daemon(args.action)
    elif args.command == "version":
        do_version()
    elif args.command == "register-cert":
        do_register_cert()
    elif args.command == "register-extended-cert":
        do_register_extended_cert()
    elif args.command == "port":
        do_port(args.port_cmd, getattr(args, "value", None))
    elif args.command == "gui-theme":
        do_gui_theme(args.theme)
    elif args.command == "set-default-connect":
        do_set_default_connect(args.strategy, args.server)
    elif args.command == "api-server":
        from pvpn_cli.api import run_api_server
        port = getattr(args, 'port', 34115)
        debug = getattr(args, 'debug', False)
        api_token = getattr(args, 'api_token', None)
        if getattr(args, 'gui', False):
            os.environ["PVPN_GUI_MODE"] = "1"
        run_api_server(port=port, debug=debug, api_token=api_token)
    elif args.command == "trigger-captcha":
        do_trigger_captcha()
    elif args.command == "grant-privileges":
        do_grant_privileges()
    else:
        parser.print_help()
    
    if args.command not in ("_daemon", "connect", "disconnect", "api-server", "_watchdog", "_install-watchdog") and os.environ.get("PVPN_GUI_MODE") != "1":
        ensure_daemon_running()


def main():
    if sys.platform == "win32":
        hide_window = any(arg == "_watchdog" or arg == "_daemon" or arg == "--gui" or arg.startswith("--redirect-logs") for arg in sys.argv)
        if hide_window:
            import ctypes
            hwnd = ctypes.windll.kernel32.GetConsoleWindow()
            if hwnd:
                ctypes.windll.user32.ShowWindow(hwnd, 0)
                
    if not sys.stdin.isatty() and sys.platform != "win32" and "_daemon" not in sys.argv and "--gui" not in sys.argv and os.environ.get("PVPN_GUI_MODE") != "1":
        import shutil
        terminals = [
            ("gnome-terminal", ["--"]),
            ("konsole", ["-e"]),
            ("xfce4-terminal", ["-e"]),
            ("xterm", ["-e"]),
            ("alacritty", ["-e"]),
            ("kitty", ["-e"])
        ]
        for term, args in terminals:
            if shutil.which(term):
                subprocess.Popen([term] + args + [sys.executable] + sys.argv[1:])
                sys.exit(0)
                
    if sys.platform == "win32":
        try:
            # Clean up stale NRPT rules from previous crashes/power loss so internet is not permanently broken
            clean_cmd = "Get-DnsClientNrptRule | Where-Object { $_.Comment -eq 'PVPN-Next' } | Remove-DnsClientNrptRule -ErrorAction SilentlyContinue -Force"
            subprocess.run(["powershell", "-NoProfile", "-Command", clean_cmd], creationflags=0x08000000, capture_output=True)
        except:
            pass

    if len(sys.argv) == 1:
        print("========================================")
        print("        Welcome to VPN-Next CLI")
        print("========================================")
        
        routing_file = os.path.join(get_config_dir(), "routing_state.json")
        if os.path.exists(routing_file):
            engine_name = "pvpn-engine.exe" if platform.system() == "Windows" else "pvpn-engine"
            vpn_running = False
            try:
                if platform.system() == "Windows":
                    import psutil
                    vpn_running = any(proc.info['name'] and engine_name.lower() in proc.info['name'].lower() 
                                      for proc in psutil.process_iter(['name']))
                else:
                    vpn_running = subprocess.run(["pgrep", "-f", engine_name], capture_output=True).returncode == 0
            except:
                pass
                
            if not vpn_running:
                # We just gracefully delete the state file because routing is already cleaned up by the OS
                try:
                    os.remove(routing_file)
                except:
                    pass
        
        print("Interactive shell mode activated.")
        print("For example, type 'list-servers' or 'autosetup'.")
        print("Type 'help' to see available commands, or 'exit' to quit.\n")
        
        import shlex
        try:
            import readline
        except ImportError:
            pass
            
        while True:
            try:
                cmd = input("VPN-Next> ").strip()
                if not cmd:
                    continue
                if cmd.lower() in ("exit", "quit"):
                    break
                if cmd.lower() == "help":
                    run_cli(["-h"])
                    continue
                if cmd.lower().startswith("debug-network"):
                    parts = cmd.lower().split()
                    if len(parts) > 1 and parts[1] == "on":
                        os.environ["PVPN_DEBUG_NETWORK"] = "1"
                        print("[INFO] Network debug logs ENABLED.")
                    elif len(parts) > 1 and parts[1] == "off":
                        os.environ["PVPN_DEBUG_NETWORK"] = "0"
                        print("[INFO] Network debug logs DISABLED.")
                    else:
                        print("Usage: debug-network on|off")
                    continue
                
                run_cli(shlex.split(cmd))
            except SystemExit:
                # Prevent sys.exit from killing the interactive shell
                pass
            except KeyboardInterrupt:
                print()
            except EOFError:
                break
            except Exception as e:
                print(f"[ERROR] {e}")
    else:
        try:
            run_cli(sys.argv[1:])
        except SystemExit:
            pass
        except KeyboardInterrupt:
            pass
        except BaseException:
            import traceback
            traceback.print_exc()
            sys.exit(1)
