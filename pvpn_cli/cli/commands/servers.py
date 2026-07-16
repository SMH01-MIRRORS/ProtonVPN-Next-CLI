"""Server list, load, and locale commands."""

import sys

from pvpn_cli.database import Database
from pvpn_cli.vpn import ProtonVpnApi


def do_fetch_servers():
    print("Fetching VPN servers from Proton API...")
    api = ProtonVpnApi()
    try:
        servers = api.fetch_servers()
        print(f"\n[SUCCESS] Successfully fetched and cached {len(servers)} servers.")
    except Exception as e:
        print(f"\n[ERROR] {str(e)}")
        sys.exit(1)


def do_fetch_loads():
    print("Updating server loads from Proton API...")
    api = ProtonVpnApi()
    try:
        loads = api.fetch_loads()
        print(f"\n[SUCCESS] Updated loads for {len(loads)} servers.")
    except Exception as e:
        print(f"\n[ERROR] {str(e)}")
        sys.exit(1)


def do_list_servers():
    db = Database()
    servers = db.get_all_servers()
    if not servers:
        print("No servers found. Run 'fetch-servers' first.")
        return

    api = ProtonVpnApi()
    try:
        max_tier = api.get_max_tier()
    except Exception:
        max_tier = 0

    try:
        from babel import Locale
        import locale

        lang = 'en'
        try:
            import warnings
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", DeprecationWarning)
                loc_tuple = locale.getdefaultlocale()
            if loc_tuple and loc_tuple[0]:
                lang = loc_tuple[0].split('_')[0]
            elif sys.platform == "win32":
                import ctypes
                win_lang = locale.windows_locale.get(ctypes.windll.kernel32.GetUserDefaultUILanguage())
                if win_lang:
                    lang = win_lang.split('_')[0]
        except Exception:
            pass

        sys_locale = Locale.parse(lang)
        territories = sys_locale.territories
    except ImportError:
        territories = {}

    tree = {}
    for s in servers:
        country_code = s['country'] or "Unknown"
        country = territories.get(country_code, country_code)
        city = s['city'] or "Unknown"
        name = s['name']
        tier = s['tier']

        if tier > max_tier:
            continue

        if country not in tree:
            tree[country] = {}
        if city not in tree[country]:
            tree[country][city] = []
        tree[country][city].append((name, tier))

    if not tree:
        print(f"No servers found for your current plan tier ({max_tier}).")
        return

    print(f"PVPN Servers (Filtered for Max Tier: {max_tier})")
    print("=================")
    countries = sorted(tree.keys())
    for i, country in enumerate(countries):
        is_last_country = i == len(countries) - 1
        c_prefix = "\u2514\u2500\u2500 " if is_last_country else "\u251c\u2500\u2500 "
        print(f"{c_prefix}{country}")

        cities = sorted(tree[country].keys())
        for j, city in enumerate(cities):
            is_last_city = j == len(cities) - 1
            city_pipe = "    " if is_last_country else "\u2502   "
            ci_prefix = "\u2514\u2500\u2500 " if is_last_city else "\u251c\u2500\u2500 "
            print(f"{city_pipe}{ci_prefix}{city}")

            srvs = tree[country][city]
            for k, (name, tier) in enumerate(srvs):
                is_last_srv = k == len(srvs) - 1
                srv_pipe = "    " if is_last_city else "\u2502   "
                s_prefix = "\u2514\u2500\u2500 " if is_last_srv else "\u251c\u2500\u2500 "
                tier_str = f" [Tier {tier}]" if tier > 0 else " [Free]"
                print(f"{city_pipe}{srv_pipe}{s_prefix}{name}{tier_str}")


def do_fetch_locale(locale: str):
    print(f"Fetching localized locations for '{locale}'...")
    api = ProtonVpnApi()
    try:
        response = api.fetch_locale(locale)
        cities_map = response.get("Cities", {})
        db = Database()
        db.update_localized_cities(cities_map)
        db.set_setting("locale", locale)
        print("[SUCCESS] Localized cities downloaded and saved to database.")
    except Exception as e:
        print(f"[ERROR] Failed to fetch localized locations: {e}")
        sys.exit(1)
