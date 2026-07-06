Name:           protonvpn-next-cli
Version:        12.0.0~alpha2
Release:        1%{?dist}
Summary:        Next-generation CLI for ProtonVPN

License:        GPL-3.0-or-later
URL:            https://github.com/smh01/ProtonVPN-Next-CLI
Source0:        %{name}-%{version}.tar.gz

BuildRequires:  golang
BuildRequires:  make
Requires:       python3
Requires:       python3-cryptography
Requires:       python3-babel
Requires:       sudo

%description
A lightweight, high-performance CLI client for ProtonVPN featuring
native AmneziaWG connections, API block bypass, and cross-platform
traffic routing.

%prep
%setup -q

%build
make build

%install
rm -rf $RPM_BUILD_ROOT
make install DESTDIR=$RPM_BUILD_ROOT PREFIX=/usr

%files
/usr/bin/protonvpn-next
/usr/lib/protonvpn-next/protonvpn-next
/usr/lib/protonvpn-next/protonvpn_cli/
/usr/lib/protonvpn-next/engine/

%changelog
* Mon Jul 06 2026 SMH01 <vpn-next@outlook.com> - 12.0.0~alpha2-1
- Initial package
