# Detect number of processors for optimal build speed
NPROCS := $(shell nproc 2>/dev/null || sysctl -n hw.ncpu 2>/dev/null || echo 1)

# Enable parallel execution in make
MAKEFLAGS += -j$(NPROCS)

# Go build flags for maximum compilation speed and smaller binaries (speeds up PyInstaller)
GO_LDFLAGS := -s -w
GO_BUILD_CMD := go build -p $(NPROCS) -ldflags="$(GO_LDFLAGS)" -trimpath

PREFIX ?= /usr
DESTDIR ?=
LIBDIR ?= $(PREFIX)/lib/pvpn-next
BINDIR ?= $(PREFIX)/bin

.PHONY: all build install clean

all: build

build:
	cd engine && $(GO_BUILD_CMD) -o pvpn-engine helper.go setup_linux.go

build-windows-docker:
	DOCKER_BUILDKIT=1 docker build --build-arg NPROCS=$(NPROCS) -f Dockerfile.windows --output dist/ .
	-docker run --rm -v $$(pwd):/app -w /app alpine chown -R $$(id -u):$$(id -g) dist/ build/ *.spec 2>/dev/null

build-windows:
	cd engine && GOOS=windows GOARCH=amd64 $(GO_BUILD_CMD) -o pvpn-engine.exe helper.go setup_windows.go
	python3 -m venv .venv
	./.venv/bin/pip install pyinstaller
	./.venv/bin/pip install -r requirements.txt
	./.venv/bin/pyinstaller --noconfirm --onefile --name pvpn-next-windows --icon=icon.ico --version-file version_info.txt --add-data "engine/pvpn-engine.exe:engine" --add-data "engine/wintun.dll:engine" pvpn-next

build-linux-bin:
	cd engine && $(GO_BUILD_CMD) -o pvpn-engine helper.go setup_linux.go
	python3 -m venv .venv
	./.venv/bin/pip install pyinstaller
	./.venv/bin/pip install -r requirements.txt
	./.venv/bin/pyinstaller --noconfirm --onefile --name pvpn-next-linux --add-data "engine/pvpn-engine:engine" pvpn-next

install: build
	# Create directories
	install -d $(DESTDIR)$(LIBDIR)
	install -d $(DESTDIR)$(LIBDIR)/engine
	install -d $(DESTDIR)$(LIBDIR)/pvpn_cli
	install -d $(DESTDIR)$(BINDIR)

	# Install Python modules and scripts
	install -m 755 pvpn-next $(DESTDIR)$(LIBDIR)/pvpn-next
	cp -r pvpn_cli/* $(DESTDIR)$(LIBDIR)/pvpn_cli/

	# Install compiled Go engine
	install -m 755 engine/pvpn-engine $(DESTDIR)$(LIBDIR)/engine/pvpn-engine

	# Create a symlink in the binary directory
	ln -sf $(LIBDIR)/pvpn-next $(DESTDIR)$(BINDIR)/pvpn-next

clean:
	rm -f engine/pvpn-engine
