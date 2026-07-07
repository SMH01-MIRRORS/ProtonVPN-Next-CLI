PREFIX ?= /usr
DESTDIR ?=
LIBDIR ?= $(PREFIX)/lib/pvpn-next
BINDIR ?= $(PREFIX)/bin

.PHONY: all build install clean

all: build

build:
	cd engine && go build -o pvpn-engine helper.go setup_linux.go

build-windows-docker:
	docker build -f Dockerfile.windows --output dist/ .
	-docker run --rm -v $$(pwd):/app -w /app alpine chown -R $$(id -u):$$(id -g) dist/ build/ *.spec 2>/dev/null

build-windows:
	cd engine && GOOS=windows GOARCH=amd64 go build -o pvpn-engine.exe helper.go setup_windows.go
	python3 -m venv .venv
	./.venv/bin/pip install pyinstaller
	./.venv/bin/pip install -r requirements.txt
	./.venv/bin/pyinstaller --onefile --name pvpn-next-windows --icon=icon.ico --version-file version_info.txt --add-data "engine/pvpn-engine.exe:engine" --add-data "engine/wintun.dll:engine" pvpn-next

build-linux-bin:
	cd engine && go build -o pvpn-engine helper.go setup_linux.go
	python3 -m venv .venv
	./.venv/bin/pip install pyinstaller
	./.venv/bin/pip install -r requirements.txt
	./.venv/bin/pyinstaller --onefile --name pvpn-next-linux --add-data "engine/pvpn-engine:engine" pvpn-next

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
