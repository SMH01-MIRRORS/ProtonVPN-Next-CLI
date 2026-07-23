# PVPN-Next CLI

> [!IMPORTANT]
> **Project Transferred to GitLab / Проект перенесен на GitLab**
>
> **English:**
> This project has moved from Codeberg to [GitLab](https://gitlab.com/SMH01/pvpn-next-cli).
>
> **Reason:** We are migrating due to Codeberg's new restrictive policies regarding AI-generated content. As PVPN-Next-CLI is a project 100% created and maintained by AI agents, it falls under the "Projects that are created by LLM agents in autonomous ways" and "Projects written and maintained with heavy use of LLMs" categories, which are now discouraged or prohibited on Codeberg.
>
> **GitLab URL:** [https://gitlab.com/SMH01/pvpn-next-cli](https://gitlab.com/SMH01/pvpn-next-cli)
>
> ---
>
> **Русский:**
> Этот проект переехал с Codeberg на [GitLab](https://gitlab.com/SMH01/pvpn-next-cli).
>
> **Причина:** Переезд связан с новой политикой Codeberg в отношении контента, созданного ИИ. Поскольку PVPN-Next-CLI на 100% создан и поддерживается ИИ-агентами, он попадает под категории «Проекты, созданные LLM-агентами автономно» и «Проекты, написанные и поддерживаемые с активным использованием LLM», которые теперь ограничены на Codeberg.
>
> **Ссылка на GitLab:** [https://gitlab.com/SMH01/pvpn-next-cli](https://gitlab.com/SMH01/pvpn-next-cli)


PVPN-Next CLI is a powerful, unofficial cross-platform command-line client for PVPN. It provides seamless integration with Proton's API and utilizes a robust custom Go-based engine to establish fast, secure AmneziaWG connections.

## Features

- **Cross-Platform Support**: Fully supports both **Linux** and **Windows**.
- **Guest Login**: Emulates the official Android client to acquire guest accounts automatically.
- **Custom Go Engine**: A lightweight backend engine written in Go (`pvpn-engine`) handles the Wintun/Netlink adapters and AmneziaWG protocol parsing.
- **Advanced Split Tunneling**:
  - **IP/Domain Exclusion**: Exclude specific IP addresses or domains from the VPN tunnel.
  - **App-Based Split Tunneling**: Route specific applications outside the VPN using Linux cgroups v2 (`novpn` command).
  - **LAN Exclusion**: Option to bypass the VPN for local network traffic (e.g., `192.168.0.0/16`).
- **Background Automatic Updates**: A hidden daemon runs automatically in the background to:
  - Synchronize the server list every 2 hours.
  - Keep the session alive and refresh API tokens every 12 hours.
  - Dynamically renew AmneziaWG certificates when required by the Proton API.
- **Device Spoofing**: Masks API calls with Android device fingerprints to blend in with official app traffic.
- **Mandatory DNS-over-HTTPS**: Public hostnames are always resolved through encrypted DoH using direct bootstrap IPs. Plain ISP DNS fallback and a disable option are intentionally not provided.
- **API Bypass Routing**: Multiple fallback endpoints (Cloudflare, Netlify, Deno) to circumvent API blocks in censored regions.

## Usage

The main executable is `pvpn-next`.

### Authentication
```bash
# Log in using a guest account
pvpn-next guest

# Check the current authentication and caching status
pvpn-next status
```

### Server Management
```bash
# Fetch and cache the latest logical servers list
pvpn-next fetch-servers

# List all available servers in a hierarchical tree view
pvpn-next list-servers

# Download translated country/city names
pvpn-next fetch-locale ru
```

### Connection
```bash
# Register an AmneziaWG key pair with the API (done automatically by the daemon, but required on first run)
pvpn-next register-cert

# Connect to a specific server
pvpn-next connect "NL-FREE#2"

# Disconnect the active VPN tunnel
pvpn-next disconnect
```

### Split Tunneling
```bash
# Exclude LAN traffic from the VPN
pvpn-next exclude-lan on

# Manage split tunneling (domains, IPs, or executable paths)
pvpn-next split-tunneling add "google.com"
pvpn-next split-tunneling list
pvpn-next split-tunneling remove 0

# Run a specific command bypassing the VPN (Linux only)
pvpn-next novpn curl -s https://ipinfo.io/ip
```

### Advanced
```bash
# Change the API block bypass strategy
pvpn-next set-bypass netlify

# Manually trigger a session token refresh
pvpn-next update-session

# View logs from the Go AmneziaWG engine
pvpn-next awg-logs
```

## How It Works

1. **Python Frontend**: Handles CLI argument parsing, API requests (with Android fingerprint spoofing), configuration generation, and routing/split-tunneling setups.
2. **Go Backend (`pvpn-engine`)**: Once the Python CLI retrieves the VPN credentials and node IP, it launches the Go engine in the background. The engine configures the local OS interfaces (`netlink` on Linux, `Wintun` + `netsh` on Windows) and manages the actual encrypted AmneziaWG data transfer.
3. **Daemon**: When connected, a lightweight Python background process monitors and automatically refreshes your session and server cache without manual intervention.
