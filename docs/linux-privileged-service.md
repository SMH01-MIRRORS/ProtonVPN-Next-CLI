# Linux privileged networking service

PVPN Next does **not** require or recommend a `NOPASSWD` sudo/doas rule. Linux
network configuration needs `CAP_NET_ADMIN`, so the safe passwordless mode is a
small root service with a deliberately narrow protocol.

## Security model

The desktop app and API remain unprivileged. The system service listens on
`/run/pvpn-next-service/control.sock`, owned by `root:pvpn-next` with mode `0660`.
Only members of the `pvpn-next` group can open it.

The broker additionally checks the caller with Linux `SO_PEERCRED` and accepts
only:

- `connect <server> [awg=...] [--port=...]`
- `disconnect`
- the caller's exact `~/.config/pvpn-next` directory

It rejects executable paths, shell strings, environment variables, arbitrary
flags and arbitrary filesystem paths. Commands are executed as argv arrays,
never through a shell. If the service is absent, CLI/Desktop falls back to the
normal interactive sudo/doas password flow.

## Standalone binary / AppImage companion CLI

The one-time installer copies the frozen CLI to a root-owned location, creates
the service account group and starts systemd:

```bash
sudo ./pvpn-next-linux install-service --user "$USER"
```

With doas or systemd-run:

```bash
doas ./pvpn-next-linux install-service --user "$USER"
run0 ./pvpn-next-linux install-service --user "$USER"
```

Log out and back in once after the user is added to `pvpn-next`. Verify with:

```bash
systemctl status pvpn-next-privileged.service
journalctl -u pvpn-next-privileged.service -f
id | grep pvpn-next
```

Thereafter connect/disconnect uses the broker without a password. Do not add
PVPN Next to `/etc/sudoers.d` or `doas.conf` with `nopass`.

## Arch Linux and derivatives

The PKGBUILD installs and enables the unit. Add each desktop user once:

```bash
sudo usermod -aG pvpn-next "$USER"
```

Then log out and back in. On non-systemd Arch derivatives, keep interactive
elevation; the broker currently targets systemd.

## Ubuntu and Debian

The Debian package creates the `pvpn-next` system group and enables the unit:

```bash
sudo usermod -aG pvpn-next "$USER"
sudo systemctl enable --now pvpn-next-privileged.service
```

Then log out and back in once.

## NixOS flake module

The flake exports `nixosModules.default`. Add the CLI flake as an input and list
users that may control VPN networking:

```nix
{
  inputs.pvpn-next.url = "gitlab:SMH01/pvpn-next-cli";

  outputs = { self, nixpkgs, pvpn-next, ... }: {
    nixosConfigurations.my-host = nixpkgs.lib.nixosSystem {
      system = "x86_64-linux";
      modules = [
        pvpn-next.nixosModules.default
        ({ ... }: {
          services.pvpn-next = {
            enable = true;
            users = [ "alice" ];
          };
        })
      ];
    };
  };
}
```

Apply it:

```bash
sudo nixos-rebuild switch --flake .#my-host
```

No imperative sudoers/doas configuration is needed. NixOS creates the group,
adds configured users, installs the package and starts the hardened service.
A new login session may be required after first enabling it.

## Kernel module note

The service currently runs the bundled userspace AmneziaWG engine. Installing an
AmneziaWG kernel module can improve dataplane performance, but it does not remove
the need for `CAP_NET_ADMIN` to create interfaces, routes and DNS configuration.
A kernel backend can therefore reuse this same broker later without widening the
Desktop application's privileges.
