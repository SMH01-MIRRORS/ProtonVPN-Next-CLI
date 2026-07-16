"""Log viewing commands: awg, client, daemon, and watchdog service logs."""

import os

from pvpn_cli.cli.config import get_config_dir


def do_awg_logs():
    
    log_path = os.path.join(get_config_dir(), "awg.log")
    if os.path.exists(log_path):
        with open(log_path, "r") as f:
            print(f.read())
    else:
        print(f"No logs found at {log_path}")

def do_client_logs():
    
    print("=== Client Logs (client.log) ===")
    client_path = os.path.join(get_config_dir(), "client.log")
    if os.path.exists(client_path):
        with open(client_path, "r") as f:
            print(f.read())
    else:
        print(f"No client logs found at {client_path}")
        
    print("\n=== Daemon Logs (daemon.log) ===")
    daemon_path = os.path.join(get_config_dir(), "daemon.log")
    if os.path.exists(daemon_path):
        with open(daemon_path, "r") as f:
            print(f.read())
    else:
        print(f"No daemon logs found at {daemon_path}")

def do_daemon_logs():
    print("=== Background Daemon Logs ===")
    daemon_path = os.path.join(get_config_dir(), "daemon.log")
    if os.path.exists(daemon_path):
        with open(daemon_path, "r") as f:
            print(f.read())
    else:
        print(f"No daemon logs found at {daemon_path}")

def do_service_logs():
    print("=== Watchdog Service Logs ===")
    service_path = os.path.join(get_config_dir(), "service.log")
    if os.path.exists(service_path):
        with open(service_path, "r") as f:
            print(f.read())
    else:
        print(f"No service logs found at {service_path}")
