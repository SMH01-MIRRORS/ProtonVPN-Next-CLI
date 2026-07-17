# Build Instructions

The PVPN-Next CLI consists of two main components:
1. **The Go Engine (`pvpn-engine`)**: A compiled Go binary that manages the low-level AmneziaWG connections and OS network interfaces.
2. **The Python CLI (`pvpn-next`)**: A Python script that handles API requests, configuration, and user interaction.

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
1. Compiles the Go engine (`engine/setup_linux.go`, `engine/helper.go`) into an ELF binary `pvpn-engine`.
2. Creates a Python virtual environment (`.venv`).
3. Installs `pyinstaller` inside the virtual environment.
4. Bundles the Python scripts and the `pvpn-engine` binary into a single standalone executable using `pyinstaller --onefile`.
5. The final output is placed in the `dist/` directory as `pvpn-next-linux`.

You can also install it to your system (`/usr/local/bin` and `/usr/local/share/pvpn-next`):
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

**Step 1: Just run make!**
If you have Docker with BuildKit enabled (default in modern Docker), simply run:
```bash
make build-windows-docker
```

This uses a multi-stage `Dockerfile.windows` to:
1. Cross-compile the Go engine in a lightweight `golang` Alpine container.
2. Bundle the executable using PyInstaller inside a `pywine` Wine container.
3. Export the final `pvpn-next-windows.exe` binary directly into your `dist/` folder.

## Continuous Integration (Woodpecker CI)

The repository includes a `.woodpecker.yml` file designed for Codeberg / Woodpecker CI. 
Because Woodpecker primarily utilizes Linux runners, the pipeline is configured as follows:

1. **Linux Engine**: Builds the Go engine natively using the `golang:latest` image.
2. **Windows Engine**: Cross-compiles the Go engine for Windows using the `golang:latest` image.
3. **Linux CLI**: Bundles the Linux release using the `python:3.11` image.
4. **Windows CLI**: Uses the `tobix/pywine:3.11` Docker image (which includes a pre-configured Wine emulator and Windows Python) to successfully build the final `pvpn-next-windows.exe` without requiring a native Windows runner.

## Linux Packaging (Nix & Arch Linux)

The repository also includes configuration files for native Linux package managers:

- **NixOS**: Use `flake.nix` to build or run the package within a reproducible Nix environment.
- **Arch Linux**: A `PKGBUILD` is provided to build and install the application using `makepkg`.
