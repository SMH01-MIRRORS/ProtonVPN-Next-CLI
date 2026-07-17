# Project Overview: PVPN-Next-CLI

This document provides technical context and strict operational rules for AI assistants working on the PVPN-Next-CLI project.

## Core Identity
- **Project Name**: PVPN-Next-CLI
- **Goal**: A command-line interface for PVPN-Next, providing a fast, secure, and native VPN experience for Linux and Windows.

## Technical Stack
- **Language**: Python 3 (CLI logic) and Go (VPN engine)
- **Packaging**: PyInstaller (Standalone executables)
- **VPN Core**: Go-based VPN engine (`engine/`) handling the AmneziaWG/AmneziaWG connections.
- **Build System**: `make` and Docker (for Windows cross-compilation).

## Project Structure
- `pvpn-next`: The main Python entry point script.
- `pvpn_cli/`: The Python package containing core logic:
  - `auth.py`: Authentication flow (SRP, 2FA, Captcha proxy handling).
  - `vpn.py`: Interacting with Proton API for server fetching.
  - `database.py`: SQLite wrapper for storing sessions, servers, and settings.
  - `routing.py`: Network routing and firewall manipulation.
- `engine/`: Source code for the Go-based backend (`pvpn-engine`).
- `dist/`: Directory where the compiled binaries are outputted.

## Build Commands
The project uses a `Makefile` to orchestrate builds. The main commands are:
- `make build-linux-bin`: Compiles the Go engine and packages the Python CLI for Linux using PyInstaller.
- `make build-windows-docker`: Cross-compiles the Go engine and packages the Python CLI into a Windows `.exe` using a Docker container (`pywine`).

## Guidelines for AI
1. **Always Build After Changes**: Immediately after modifying the source code, you MUST compile the binaries for both Linux and Windows by running:
   ```bash
   make build-linux-bin && make build-windows-docker
   ```
2. **Commit After Building**: Once the build completes successfully, you MUST commit your changes to version control along with a clear and descriptive commit message.
3. **Database Schema**: Do not alter the SQLite database schema without providing an appropriate migration strategy.
4. **Privilege Elevation**: Be mindful of commands requiring root/admin privileges (`sudo`, `doas`, or Windows UAC) and ensure proper elevation mechanisms are utilized.
5. **Temporary Test Scripts**: If you write any scripts for testing purposes that are not part of the main project, you MUST remove them from git (if tracked) and add them to `.gitignore`. Do not leave unnecessary scratch scripts in the repository.
