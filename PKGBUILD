pkgname=picpro-gui
pkgver=0.1.1
pkgrel=1
pkgdesc='Qt desktop interface for the PicPro PIC programmer'
arch=('any')
url='https://github.com/Salamek/picpro'
license=('GPL-2.0-only')
depends=('picpro>=0.5.0' 'python' 'python-pyqt6' 'python-pyserial' 'python-intelhex' 'python-yaml' 'shared-mime-info' 'desktop-file-utils')
makedepends=('python-build' 'python-installer' 'python-setuptools' 'python-wheel')
checkdepends=('python-pytest')

prepare() {
    mkdir -p "$srcdir/$pkgname"
    cp -r "$startdir/picpro_gui" "$startdir/tests" "$srcdir/$pkgname/"
    cp "$startdir/"{pyproject.toml,MANIFEST.in,README.md,LICENSE} "$srcdir/$pkgname/"
}

build() {
    cd "$srcdir/$pkgname"
    python -m build --wheel --no-isolation
}

check() {
    cd "$srcdir/$pkgname"
    QT_QPA_PLATFORM=offscreen python -m pytest
}

package() {
    cd "$srcdir/$pkgname"
    python -m installer --destdir="$pkgdir" dist/*.whl
    install -Dm644 "$startdir/data/picpro-gui.desktop" "$pkgdir/usr/share/applications/picpro-gui.desktop"
    install -Dm644 "$startdir/data/picpro-gui.svg" "$pkgdir/usr/share/icons/hicolor/scalable/apps/picpro-gui.svg"
    install -Dm644 "$startdir/data/picpro-project.xml" "$pkgdir/usr/share/mime/packages/picpro-project.xml"
}
