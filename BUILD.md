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

To compile just the Go engine for Linux, run:
```bash
make build
```

To build a **standalone Linux executable** (which bundles the Python CLI and the compiled Go engine into a single binary), run:
```bash
make build-linux-bin
```

**What this does**:
1. Compiles the Go engine (`engine/setup_linux.go`, `engine/helper.go`) into an ELF binary `protonvpn-engine`.
2. Creates a Python virtual environment (`.venv`).
3. Installs `pyinstaller` inside the virtual environment.
4. Bundles the Python scripts and the `protonvpn-engine` binary into a single standalone executable using `pyinstaller --onefile`.
5. The final output is placed in the `dist/` directory as `protonvpn-next-linux`.

You can also install it to your system (`/usr/local/bin` and `/usr/local/share/protonvpn-next`):
```bash
sudo make install
```

## Building for Windows

> [!WARNING]
> PyInstaller **cannot** cross-compile Python executables. If you run the Windows build command on a Linux machine natively, it will generate a Linux executable containing the Windows `.exe` engine, which is not functional on Windows.
> 
> To generate a valid `.exe` file for the Python CLI, you **must** run the build command on a Windows machine, or use a Docker container (like `tobix/pywine:3.11`).

### Option 1: Native Windows
To build the standalone Windows `.exe` on a Windows machine:

```bash
make build-windows
```

### Option 2: Cross-Compiling from Linux using Docker
If you want to compile the Windows `.exe` directly from a Linux machine, you must do it in two steps. First, cross-compile the Go engine, then use a Wine Docker image to package the executable.

**Step 1: Compile the Go engine**
```bash
cd engine && GOOS=windows GOARCH=amd64 go build -o protonvpn-engine.exe helper.go setup_windows.go && cd ..
```

**Step 2: Package using PyWine Docker**
```bash
docker run --rm -v $(pwd):/app -w /app tobix/pywine:3.11 sh -c "wine pip install pyinstaller && wine pip install -r requirements.txt && wine pyinstaller --onefile --name protonvpn-next-windows --icon=icon.ico --version-file version_info.txt --add-data 'engine/protonvpn-engine.exe;engine' --add-data 'engine/wintun.dll;engine' protonvpn-next"
```

The final output will be generated in the `dist/` directory as `protonvpn-next-windows.exe`.

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
