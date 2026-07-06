PREFIX ?= /usr
DESTDIR ?=
LIBDIR ?= $(PREFIX)/lib/protonvpn-next
BINDIR ?= $(PREFIX)/bin

.PHONY: all build install clean

all: build

build:
	cd engine && go build -o protonvpn-engine helper.go setup_linux.go

build-windows-docker:
	docker build -f Dockerfile.windows --output dist/ .

build-windows:
	cd engine && GOOS=windows GOARCH=amd64 go build -o protonvpn-engine.exe helper.go setup_windows.go
	python3 -m venv .venv
	./.venv/bin/pip install pyinstaller
	./.venv/bin/pip install -r requirements.txt
	./.venv/bin/pyinstaller --onefile --name protonvpn-next-windows --icon=icon.ico --version-file version_info.txt --add-data "engine/protonvpn-engine.exe:engine" --add-data "engine/wintun.dll:engine" protonvpn-next

build-linux-bin:
	cd engine && go build -o protonvpn-engine helper.go setup_linux.go
	python3 -m venv .venv
	./.venv/bin/pip install pyinstaller
	./.venv/bin/pip install -r requirements.txt
	./.venv/bin/pyinstaller --onefile --name protonvpn-next-linux --add-data "engine/protonvpn-engine:engine" protonvpn-next

install: build
	# Create directories
	install -d $(DESTDIR)$(LIBDIR)
	install -d $(DESTDIR)$(LIBDIR)/engine
	install -d $(DESTDIR)$(LIBDIR)/protonvpn_cli
	install -d $(DESTDIR)$(BINDIR)

	# Install Python modules and scripts
	install -m 755 protonvpn-next $(DESTDIR)$(LIBDIR)/protonvpn-next
	cp -r protonvpn_cli/* $(DESTDIR)$(LIBDIR)/protonvpn_cli/

	# Install compiled Go engine
	install -m 755 engine/protonvpn-engine $(DESTDIR)$(LIBDIR)/engine/protonvpn-engine

	# Create a symlink in the binary directory
	ln -sf $(LIBDIR)/protonvpn-next $(DESTDIR)$(BINDIR)/protonvpn-next

clean:
	rm -f engine/protonvpn-engine
