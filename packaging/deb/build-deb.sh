#!/usr/bin/env bash
# Build dist/portglance_<version>_all.deb for Ubuntu 24.04+ / Debian 13+.
#
# The package is architecture-independent: it uses the distribution's
# Python, PyGObject, GTK 4 and libadwaita.
set -euo pipefail

source "$(dirname "$0")/../common.sh"

BUILD="$ROOT/build/deb"
PKG="$BUILD/portglance_${VERSION}_all"
rm -rf "$BUILD"
mkdir -p "$PKG/DEBIAN" "$DIST"

copy_package "$PKG/usr/lib/python3/dist-packages"
install_desktop_files "$PKG/usr"

install -D -m 0755 /dev/stdin "$PKG/usr/bin/portglance" <<'EOF'
#!/usr/bin/python3
import sys

from portglance.cli import main

sys.exit(main())
EOF

install -D -m 0644 "$ROOT/LICENSE" "$PKG/usr/share/doc/portglance/copyright"
mkdir -p "$PKG/usr/share/man/man1"
gzip -9n -c "$ROOT/packaging/portglance.1" > "$PKG/usr/share/man/man1/portglance.1.gz"
gzip -9n -c > "$PKG/usr/share/doc/portglance/changelog.gz" <<EOF
portglance ($VERSION) stable; urgency=medium

  * See https://github.com/rafay-ah/portglance/releases/tag/v$VERSION

 -- rafay-ah <54492363+rafay-ah@users.noreply.github.com>  $(date -R)
EOF

INSTALLED_SIZE="$(du -sk --exclude=DEBIAN "$PKG" | cut -f1)"
cat > "$PKG/DEBIAN/control" <<EOF
Package: portglance
Version: $VERSION
Section: devel
Priority: optional
Architecture: all
Installed-Size: $INSTALLED_SIZE
Depends: python3 (>= 3.10), python3-gi (>= 3.42), gir1.2-glib-2.0, gir1.2-gtk-4.0 (>= 4.12), gir1.2-adw-1 (>= 1.5)
Recommends: adwaita-icon-theme, gnome-shell-extension-appindicator
Maintainer: rafay-ah <54492363+rafay-ah@users.noreply.github.com>
Homepage: https://github.com/rafay-ah/portglance
Description: see which dev servers are listening, at a glance
 PortGlance shows the ports your dev servers listen on, with the process,
 PID, uptime, memory and the project (git repository) each one belongs to.
 A top-panel indicator shows the count; Docker and Podman containers are
 included; servers can be stopped with one click (SIGTERM, then SIGKILL)
 and opened in the browser. Also includes a desktop widget and the
 "portglance list" and "portglance kill" terminal commands.
EOF

cat > "$PKG/DEBIAN/postinst" <<'EOF'
#!/bin/sh
set -e
if [ "$1" = "configure" ] && command -v py3compile >/dev/null 2>&1; then
    py3compile -p portglance
fi
EOF
cat > "$PKG/DEBIAN/prerm" <<'EOF'
#!/bin/sh
set -e
if command -v py3clean >/dev/null 2>&1; then
    py3clean -p portglance
else
    find /usr/lib/python3/dist-packages/portglance -type d -name __pycache__ -prune -exec rm -rf {} +
fi
EOF
chmod 0755 "$PKG/DEBIAN/postinst" "$PKG/DEBIAN/prerm"

find "$PKG" -type d -exec chmod 0755 {} +
OUTPUT="$DIST/portglance_${VERSION}_all.deb"
dpkg-deb --root-owner-group -Zxz --build "$PKG" "$OUTPUT"
echo "Built $OUTPUT"
