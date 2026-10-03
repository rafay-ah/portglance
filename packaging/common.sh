# Shared helpers for the packaging scripts. Source, do not execute.

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
APP_ID="io.github.rafay_ah.PortGlance"
VERSION="$(sed -n 's/^__version__ = "\(.*\)"$/\1/p' "$ROOT/portglance/__init__.py")"
DIST="$ROOT/dist"

if [ -z "$VERSION" ]; then
    echo "error: could not read __version__ from portglance/__init__.py" >&2
    exit 1
fi

# Copy the Python package without caches or editor leftovers.
copy_package() {
    local target="$1"
    mkdir -p "$target"
    (cd "$ROOT" && find portglance -type f \
        ! -path '*/__pycache__/*' ! -name '*.pyc' ! -name '*.swp' -print0 |
        xargs -0 -I{} install -D -m 0644 {} "$target/{}")
}

# Install the desktop integration files under a usr/ prefix.
install_desktop_files() {
    local usr="$1"
    install -D -m 0644 "$ROOT/data/$APP_ID.desktop" "$usr/share/applications/$APP_ID.desktop"
    install -D -m 0644 "$ROOT/data/$APP_ID.metainfo.xml" "$usr/share/metainfo/$APP_ID.metainfo.xml"
    local icons="$ROOT/portglance/data/icons/hicolor"
    install -D -m 0644 "$icons/scalable/apps/$APP_ID.svg" \
        "$usr/share/icons/hicolor/scalable/apps/$APP_ID.svg"
    install -D -m 0644 "$icons/symbolic/apps/$APP_ID-symbolic.svg" \
        "$usr/share/icons/hicolor/symbolic/apps/$APP_ID-symbolic.svg"
}
