%global debug_package %{nil}

Name:           pvpn-next
Version:        1.1.0
Release:        1%{?dist}
Summary:        Next-generation CLI for PVPN with native AmneziaWG support

License:        GPL-3.0-or-later
URL:            https://github.com/smh01/PVPN-Next-CLI
Source0:        %{name}-%{version}.tar.gz

BuildRequires:  golang
BuildRequires:  make
BuildRequires:  systemd-rpm-macros

Requires:       python3
Requires:       python3-cryptography
Requires:       python3-babel
Requires:       iproute
Recommends:     systemd

%description
A lightweight, high-performance CLI client for PVPN featuring native
AmneziaWG connections, API block bypass and cross-platform traffic routing.
Privileged networking runs through a restricted systemd broker, so desktop
users in the pvpn-next group connect without a sudo password.

%prep
%autosetup -n %{name}-%{version}

%build
%make_build build

%install
%make_install PREFIX=/usr

%files
%license LICENSE
%{_libdir}/pvpn-next
%{_bindir}/pvpn-next
%{_prefix}/lib/systemd/system/pvpn-next-privileged.service

%post
# The broker socket is group-owned by pvpn-next; members skip sudo entirely.
getent group pvpn-next >/dev/null || groupadd --system pvpn-next
%systemd_post pvpn-next-privileged.service
systemctl enable --now pvpn-next-privileged.service >/dev/null 2>&1 || true
echo "Add desktop users with: sudo usermod -aG pvpn-next <user>"
echo "Then log out and back in once. No NOPASSWD rule is required."

%preun
%systemd_preun pvpn-next-privileged.service

%postun
%systemd_postun_with_restart pvpn-next-privileged.service

%changelog
* Tue Aug 04 2026 SMH01 <vpn-next@outlook.com> - 1.1.0-1
- Handshake-based connection verification with automatic reconnect
- Restricted systemd privileged broker
