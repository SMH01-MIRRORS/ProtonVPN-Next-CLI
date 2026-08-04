pkgname=pvpn-next
pkgver=1.1.0
pkgrel=1
pkgdesc="Next-generation CLI for PVPN featuring native AmneziaWG connections and API block bypass"
arch=('x86_64' 'aarch64')
url="https://gitlab.com/SMH01/pvpn-next-cli"
license=('GPL-3.0-or-later')
depends=('python' 'python-cryptography' 'python-babel' 'iproute2')
optdepends=('sudo: interactive elevation fallback' 'opendoas: interactive elevation fallback' 'systemd: passwordless restricted networking broker')
makedepends=('go' 'make')
install=pvpn-next-cli.install
source=("git+https://gitlab.com/SMH01/pvpn-next-cli.git")
md5sums=('SKIP')

build() {
  cd "$srcdir/pvpn-next-cli"
  make build
}

package() {
  cd "$srcdir/pvpn-next-cli"
  make install DESTDIR="$pkgdir" PREFIX=/usr
}
