# ProtonVPN-Next CLI

ProtonVPN-Next CLI is a powerful, unofficial cross-platform command-line client for ProtonVPN. It provides seamless integration with Proton's API and utilizes a robust custom Go-based engine to establish fast, secure AmneziaWG (WireGuard) connections.

## Features

- **Cross-Platform Support**: Fully supports both **Linux** and **Windows**.
- **Guest Login**: Emulates the official Android client to acquire guest accounts automatically.
- **Custom Go Engine**: A lightweight backend engine written in Go (`protonvpn-engine`) handles the Wintun/Netlink adapters and WireGuard protocol parsing.
- **Advanced Split Tunneling**:
  - **IP/Domain Exclusion**: Exclude specific IP addresses or domains from the VPN tunnel.
  - **App-Based Split Tunneling**: Route specific applications outside the VPN using Linux cgroups v2 (`novpn` command).
  - **LAN Exclusion**: Option to bypass the VPN for local network traffic (e.g., `192.168.0.0/16`).
- **Background Automatic Updates**: A hidden daemon runs automatically in the background to:
  - Synchronize the server list every 2 hours.
  - Keep the session alive and refresh API tokens every 12 hours.
  - Dynamically renew WireGuard certificates when required by the Proton API.
- **Device Spoofing**: Masks API calls with Android device fingerprints to blend in with official app traffic.
- **API Bypass Routing**: Multiple fallback endpoints (Cloudflare, Netlify, Deno) to circumvent API blocks in censored regions.

## Usage

The main executable is `protonvpn-next`.

### Authentication
```bash
# Log in using a guest account
protonvpn-next guest

# Check the current authentication and caching status
protonvpn-next status
```

### Server Management
```bash
# Fetch and cache the latest logical servers list
protonvpn-next fetch-servers

# List all available servers in a hierarchical tree view
protonvpn-next list-servers

# Download translated country/city names
protonvpn-next fetch-locale ru
```

### Connection
```bash
# Register a WireGuard key pair with the API (done automatically by the daemon, but required on first run)
protonvpn-next register-cert

# Connect to a specific server
protonvpn-next connect "NL-FREE#2"

# Disconnect the active VPN tunnel
protonvpn-next disconnect
```

### Split Tunneling
```bash
# Exclude LAN traffic from the VPN
protonvpn-next exclude-lan on

# Manage split tunneling (domains, IPs, or executable paths)
protonvpn-next split-tunneling add "google.com"
protonvpn-next split-tunneling list
protonvpn-next split-tunneling remove 0

# Run a specific command bypassing the VPN (Linux only)
protonvpn-next novpn curl -s https://ipinfo.io/ip
```

### Advanced
```bash
# Change the API block bypass strategy
protonvpn-next set-bypass netlify

# Manually trigger a session token refresh
protonvpn-next update-session

# View logs from the Go AmneziaWG engine
protonvpn-next awg-logs
```

## How It Works

1. **Python Frontend**: Handles CLI argument parsing, API requests (with Android fingerprint spoofing), configuration generation, and routing/split-tunneling setups.
2. **Go Backend (`protonvpn-engine`)**: Once the Python CLI retrieves the VPN credentials and node IP, it launches the Go engine in the background. The engine configures the local OS interfaces (`netlink` on Linux, `Wintun` + `netsh` on Windows) and manages the actual encrypted WireGuard data transfer.
3. **Daemon**: When connected, a lightweight Python background process monitors and automatically refreshes your session and server cache without manual intervention.
