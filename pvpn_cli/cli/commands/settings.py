"""Settings commands: bypass, AWG, MTU, DNS, port, GUI theme, default connect."""

import sys

from pvpn_cli.database import Database
from pvpn_cli.vpn import ProtonVpnApi

from pvpn_cli.cli.colors import Colors


def do_set_bypass(bypass: str):
    db = Database()
    bypass_lower = bypass.lower()
    
    mapping = {
        "0": ("0", "Direct (vpn-api.proton.me)"),
        "none": ("0", "Direct (vpn-api.proton.me)"),
        "1": ("1", "Cloudflare Proxy (api.protonnext.qzz.io)"),
        "cloudflare": ("1", "Cloudflare Proxy (api.protonnext.qzz.io)"),
        "2": ("2", "Netlify Proxy (shimmering-stroopwafel-51675e.netlify.app)"),
        "netlify": ("2", "Netlify Proxy (shimmering-stroopwafel-51675e.netlify.app)"),
        "3": ("3", "Deno Proxy (quick-bluejay-8760.smh01-mirrors.deno.net)"),
        "deno": ("3", "Deno Proxy (quick-bluejay-8760.smh01-mirrors.deno.net)")
    }
    
    if bypass_lower not in mapping:
        print(f"Invalid bypass option: {bypass}")
        print("Available options: 0 (none), 1 (cloudflare), 2 (netlify), 3 (deno)")
        sys.exit(1)
        
    val, desc = mapping[bypass_lower]
    db.set_setting("api_bypass", val)
    print(f"API Block Bypass set to: {desc}")


def do_set_awg(params: str):
    db = Database()
    db.set_setting("active_awg_mode", "custom")
    db.set_setting("active_awg_custom_params", params)
    print(f"{Colors.OKGREEN}[SUCCESS]{Colors.ENDC} Custom AWG parameters set and activated.")


def do_set_mtu(mtu_str: str):
    db = Database()
    db.set_setting("mtu", mtu_str)
    print(f"{Colors.OKGREEN}[SUCCESS]{Colors.ENDC} MTU set to: {mtu_str}")


def do_dns_config(action: str, args):
    db = Database()
    
    # Predefined servers
    predefined = {
        "cloudflare": "1.1.1.1, 1.0.0.1",
        "adguard": "94.140.14.14, 94.140.15.15",
        "google": "8.8.8.8, 8.8.4.4",
        "proton": "10.2.0.1"
    }

    import json
    
    def get_custom_dns():
        custom_str = db.get_setting("custom_dns_profiles", "{}")
        try:
            return json.loads(custom_str)
        except:
            return {}
            
    def save_custom_dns(profiles):
        db.set_setting("custom_dns_profiles", json.dumps(profiles))

    if action == "list":
        active_dns = db.get_setting("active_dns_profile", "cloudflare")
        print("=== DNS Profiles ===")
        print("--- Built-in ---")
        for k, v in predefined.items():
            marker = f"{Colors.OKGREEN}*[ACTIVE]*{Colors.ENDC}" if k == active_dns else ""
            print(f"{k:<15} | {v:<30} {marker}")
        
        custom_dns = get_custom_dns()
        if custom_dns:
            print("\n--- Custom ---")
            for k, v in custom_dns.items():
                marker = f"{Colors.OKGREEN}*[ACTIVE]*{Colors.ENDC}" if k == active_dns else ""
                print(f"{k:<15} | {v:<30} {marker}")
                
    elif action == "add":
        if len(args) < 2:
            print(f"{Colors.FAIL}[ERROR]{Colors.ENDC} Usage: dns add <name> <ip1,ip2...>")
            return
        name = args[0].lower()
        if name in predefined:
            print(f"{Colors.FAIL}[ERROR]{Colors.ENDC} Cannot overwrite built-in profile: {name}")
            return
        ips = "".join(args[1:]) # Combine rest
        custom_dns = get_custom_dns()
        custom_dns[name] = ips
        save_custom_dns(custom_dns)
        print(f"{Colors.OKGREEN}[SUCCESS]{Colors.ENDC} Added custom DNS profile: {name} ({ips})")
        
    elif action == "set":
        if not args:
            print(f"{Colors.FAIL}[ERROR]{Colors.ENDC} Usage: dns set <name>")
            return
        name = args[0].lower()
        custom_dns = get_custom_dns()
        
        if name in predefined or name in custom_dns:
            db.set_setting("active_dns_profile", name)
            print(f"{Colors.OKGREEN}[SUCCESS]{Colors.ENDC} Active DNS set to: {name}")
        else:
            print(f"{Colors.FAIL}[ERROR]{Colors.ENDC} DNS profile '{name}' not found. Use 'dns list' to see available profiles.")
            
    elif action == "unset":
        db.set_setting("active_dns_profile", "proton")
        print(f"{Colors.OKGREEN}[SUCCESS]{Colors.ENDC} DNS unset. Reverting to default proton DNS (10.2.0.1).")
    else:
        print(f"{Colors.FAIL}[ERROR]{Colors.ENDC} Unknown DNS action.")


def do_awg_config(action: str, args):
    db = Database()
    if action == "create":
        if len(args) < 2:
            print(f"{Colors.FAIL}[ERROR]{Colors.ENDC} Usage: awg-config create <parameters> <name>")
            sys.exit(1)
        params = args[0]
        name = " ".join(args[1:])
        db.add_awg_config(name, params)
        print(f"{Colors.OKGREEN}[SUCCESS]{Colors.ENDC} AWG config '{Colors.OKCYAN}{name}{Colors.ENDC}' created.")
    elif action == "delete":
        if len(args) < 1:
            print(f"{Colors.FAIL}[ERROR]{Colors.ENDC} Usage: awg-config delete <name_or_number>")
            sys.exit(1)
        identifier = " ".join(args)
        
        # Check if they are trying to delete the default config
        cfg = db.get_awg_config(identifier)
        if cfg and cfg['name'] == 'vpn-next-default':
            print(f"{Colors.FAIL}[ERROR]{Colors.ENDC} Cannot delete the built-in 'vpn-next-default' configuration.")
            sys.exit(1)
            
        if db.delete_awg_config(identifier):
            print(f"{Colors.OKGREEN}[SUCCESS]{Colors.ENDC} AWG config deleted.")
            # If it was active, unset it
            if db.get_setting("active_awg_mode") == "config":
                active_id = db.get_setting("active_awg_config_id")
                if active_id == identifier or (identifier.isdigit() and active_id == identifier):
                    db.set_setting("active_awg_mode", "none")
        else:
            print(f"{Colors.FAIL}[ERROR]{Colors.ENDC} Config not found.")
    elif action == "set":
        if len(args) < 1:
            print(f"{Colors.FAIL}[ERROR]{Colors.ENDC} Usage: awg-config set <name_or_number>")
            sys.exit(1)
        identifier = " ".join(args)
        cfg = db.get_awg_config(identifier)
        if cfg:
            db.set_setting("active_awg_mode", "config")
            db.set_setting("active_awg_config_id", str(cfg["id"]))
            print(f"{Colors.OKGREEN}[SUCCESS]{Colors.ENDC} Active AWG config set to: {Colors.OKCYAN}{cfg['name']}{Colors.ENDC}")
        else:
            print(f"{Colors.FAIL}[ERROR]{Colors.ENDC} Config not found.")
    elif action == "unset":
        db.set_setting("active_awg_mode", "none")
        print(f"{Colors.OKGREEN}[SUCCESS]{Colors.ENDC} AmneziaWG obfuscation parameters disabled.")
    elif action == "list":
        configs = db.get_awg_configs()
        if not configs:
            print(f"{Colors.WARNING}No AWG configs found.{Colors.ENDC}")
            return
        
        active_mode = db.get_setting("active_awg_mode")
        active_id = db.get_setting("active_awg_config_id")
        
        print(f"\n{Colors.HEADER}=== AmneziaWG Configurations ==={Colors.ENDC}")
        print(f"{Colors.BOLD}{'ID':<4} | {'Name':<25} | {'Status':<10} | {'Parameters'}{Colors.ENDC}")
        print("-" * 80)
        for cfg in configs:
            is_active = (active_mode == "config" and str(cfg["id"]) == active_id)
            status = f"{Colors.OKGREEN}ACTIVE{Colors.ENDC}" if is_active else f"{Colors.ENDC}INACTIVE"
            name = cfg['name']
            if len(name) > 25:
                name = name[:22] + "..."
            
            # Highlight parts of parameters to make it beautiful
            params = cfg['params']
            if params == 'vpn-next-default':
                params_display = f"{Colors.OKCYAN}Built-in Standard Profile (Jc=3, Jmin=1...){Colors.ENDC}"
            else:
                params_display = params.replace('Jc=', f'{Colors.OKCYAN}Jc={Colors.ENDC}').replace('Jmin=', f'{Colors.OKCYAN}Jmin={Colors.ENDC}')
            
            print(f"{cfg['id']:<4} | {Colors.BOLD}{name:<25}{Colors.ENDC} | {status:<19} | {params_display}")
        print("-" * 80)
        
        if active_mode == "custom":
            print(f"\n{Colors.WARNING}Note:{Colors.ENDC} A custom AWG config is currently active (set via 'set-awg').")


def do_set_default_connect(strategy: str, server: str = None):
    db = Database()
    strategy = strategy.lower()
    if strategy not in ["best", "recent", "custom"]:
        print(f"{Colors.FAIL}[ERROR]{Colors.ENDC} Invalid strategy. Use: best, recent, or custom.")
        sys.exit(1)

    db.set_setting("default_connect_strategy", strategy)
    if strategy == "custom":
        if not server:
            print(f"{Colors.FAIL}[ERROR]{Colors.ENDC} Server name/ID required for 'custom' strategy.")
            sys.exit(1)

        # Verify server exists
        vpn = ProtonVpnApi()
        srv = vpn.get_server_by_name(server)
        if not srv:
            print(f"{Colors.FAIL}[ERROR]{Colors.ENDC} Server '{server}' not found.")
            sys.exit(1)

        db.set_setting("default_connect_server", str(srv.get("ID")))
        print(f"{Colors.OKGREEN}[SUCCESS]{Colors.ENDC} Default connection set to custom server: {srv.get('Name')}")
    else:
        print(f"{Colors.OKGREEN}[SUCCESS]{Colors.ENDC} Default connection strategy set to: {strategy}")


AVAILABLE_PORTS = ["0", "53", "80", "123", "443", "500", "51820"]


def do_port(port_cmd, value=None):
    db = Database()
    if port_cmd == "list":
        current = db.get_setting("vpn_port", "0")
        print("Available VPN Ports:")
        for p in AVAILABLE_PORTS:
            tag = "(Auto)" if p == "0" else ""
            active = "[*]" if p == current else "[ ]"
            print(f" {active} {p} {tag}")
    elif port_cmd == "set":
        if value not in AVAILABLE_PORTS:
            print(f"Error: Port {value} is not in the allowed list: {', '.join(AVAILABLE_PORTS)}")
            sys.exit(1)
        db.set_setting("vpn_port", value)
        print(f"VPN Port set to: {value if value != '0' else 'Auto'}")


def do_gui_theme(theme=None):
    db = Database()
    if theme:
        db.set_setting("gui_theme", theme)
        print(f"GUI Theme set to: {theme}")
    else:
        current = db.get_setting("gui_theme", "system")
        print(f"Current GUI Theme: {current}")
