pkgname=pvpn-next-cli
pkgver=1.0.0
pkgrel=1
pkgdesc="Next-generation CLI for PVPN featuring native AmneziaWG connections and API block bypass"
arch=('x86_64' 'aarch64')
url="https://github.com/smh01/PVPN-Next-CLI"
license=('GPL-3.0-or-later')
depends=('python' 'python-cryptography' 'python-babel' 'sudo')
makedepends=('go' 'make')
source=("git+https://github.com/smh01/PVPN-Next-CLI.git")
md5sums=('SKIP')

build() {
  cd "$srcdir/PVPN-Next-CLI"
  make build
}

package() {
  cd "$srcdir/PVPN-Next-CLI"
  make install DESTDIR="$pkgdir" PREFIX=/usr
}
