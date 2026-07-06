# Build Instructions

The ProtonVPN-Next CLI consists of two main components:
1. **The Go Engine (`protonvpn-engine`)**: A compiled Go binary that manages the low-level WireGuard connections and OS network interfaces.
2. **The Python CLI (`protonvpn-next`)**: A Python script that handles API requests, configuration, and user interaction.

The project utilizes `PyInstaller` to bundle the Python CLI and the compiled Go engine into a single, standalone executable for ease of distribution.

## Prerequisites

- **Go**: Version 1.26 or higher (required by `go.mod`).
- **Python**: Version 3.11 or higher.
- **Make**: For running build automation.
- **Git**: For version control.

## Building for Linux

To build a standalone Linux executable, simply run:

```bash
make build
```

**What this does**:
1. Compiles the Go engine (`engine/setup_linux.go`, `engine/helper.go`) into an ELF binary `protonvpn-engine`.
2. Creates a Python virtual environment (`.venv`).
3. Installs `pyinstaller` inside the virtual environment.
4. Bundles the Python scripts and the `protonvpn-engine` binary into a single standalone executable using `pyinstaller --onefile`.
5. The final output is placed in the `dist/` directory as `protonvpn-next`.

You can also install it to your system (`/usr/local/bin` and `/usr/local/share/protonvpn-next`):
```bash
sudo make install
```

## Building for Windows

> [!WARNING]
> PyInstaller **cannot** cross-compile Python executables. If you run the Windows build command on a Linux machine, it will generate a Linux executable containing the Windows `.exe` engine, which is not functional on Windows.
> 
> To generate a valid `.exe` file for the Python CLI, you **must** run the build command on a Windows machine, or use a CI pipeline (like Woodpecker CI) that runs a Windows/Wine Docker container.

To build the standalone Windows `.exe` on a Windows machine (or in a compatible CI environment):

```bash
make build-windows
```

**What this does**:
1. Cross-compiles the Go engine for Windows (`GOOS=windows GOARCH=amd64`) into `protonvpn-engine.exe`. Note: Go natively supports cross-compilation, so this step works flawlessly on Linux.
2. Uses `pyinstaller` to bundle the Python scripts and the `.exe` engine into a single `protonvpn-next.exe` executable.

## Continuous Integration (Woodpecker CI)

The repository includes a `.woodpecker.yml` file designed for Codeberg / Woodpecker CI. 
Because Woodpecker primarily utilizes Linux runners, the pipeline is configured as follows:

1. **Linux Engine**: Builds the Go engine natively using the `golang:latest` image.
2. **Windows Engine**: Cross-compiles the Go engine for Windows using the `golang:latest` image.
3. **Linux CLI**: Bundles the Linux release using the `python:3.11` image.
4. **Windows CLI**: Uses the `tobix/pywine:3.11` Docker image (which includes a pre-configured Wine emulator and Windows Python) to successfully build the final `protonvpn-next-windows.exe` without requiring a native Windows runner.

## Linux Packaging (Nix & Arch Linux)

The repository also includes configuration files for native Linux package managers:

- **NixOS**: Use `flake.nix` to build or run the package within a reproducible Nix environment.
- **Arch Linux**: A `PKGBUILD` is provided to build and install the application using `makepkg`.
