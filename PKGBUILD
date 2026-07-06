pkgname=protonvpn-next-cli
pkgver=12.0.0_alpha2
pkgrel=1
pkgdesc="Next-generation CLI for ProtonVPN featuring native AmneziaWG connections and API block bypass"
arch=('x86_64' 'aarch64')
url="https://github.com/smh01/ProtonVPN-Next-CLI"
license=('GPL-3.0-or-later')
depends=('python' 'python-cryptography' 'python-babel' 'sudo')
makedepends=('go' 'make')
source=("git+https://github.com/smh01/ProtonVPN-Next-CLI.git")
md5sums=('SKIP')

build() {
  cd "$srcdir/ProtonVPN-Next-CLI"
  make build
}

package() {
  cd "$srcdir/ProtonVPN-Next-CLI"
  make install DESTDIR="$pkgdir" PREFIX=/usr
}
