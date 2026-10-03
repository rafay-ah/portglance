#!/usr/bin/env bash
# Build dist/PortGlance-<version>-x86_64.AppImage.
#
# The image bundles Python, PyGObject, GTK 4 and libadwaita from the build
# machine (Ubuntu 24.04), so it runs on distributions with glibc 2.39 or newer
# even when they do not ship libadwaita 1.5. Graphics drivers, glibc, X11,
# Wayland and font libraries come from the host, following the AppImage
# project's exclude list.
#
# Build dependencies (Ubuntu 24.04):
#   python3-gi gir1.2-gtk-4.0 gir1.2-adw-1 gir1.2-girepository-2.0
#   librsvg2-common libgdk-pixbuf2.0-bin adwaita-icon-theme
#   dconf-gsettings-backend libglib2.0-bin shared-mime-info curl file
set -euo pipefail

source "$(dirname "$0")/../common.sh"

ARCH="$(uname -m)"
PYTHON="${PYTHON:-/usr/bin/python3}"
PYBIN="$(readlink -f "$PYTHON")"
PYVER="$("$PYTHON" -c 'import sys; print("%d.%d" % sys.version_info[:2])')"
MULTIARCH="$("$PYTHON" -c 'import sysconfig; print(sysconfig.get_config_var("MULTIARCH"))')"
LIBDIR="/usr/lib/$MULTIARCH"
BUILD="$ROOT/build/appimage"
APPDIR="$BUILD/AppDir"
TOOLS="$ROOT/build/tools"
OUTPUT="$DIST/PortGlance-$VERSION-$ARCH.AppImage"

rm -rf "$BUILD"
mkdir -p "$APPDIR/usr/bin" "$APPDIR/usr/lib" "$DIST" "$TOOLS"

log() { printf '\033[1m==> %s\033[0m\n' "$*"; }

# ---------------------------------------------------------------------------
log "Python $PYVER and PyGObject"
install -m 0755 "$PYBIN" "$APPDIR/usr/bin/python$PYVER"
ln -s "python$PYVER" "$APPDIR/usr/bin/python3"
cp -a "/usr/lib/python$PYVER" "$APPDIR/usr/lib/"
(
    cd "$APPDIR/usr/lib/python$PYVER"
    rm -rf test idlelib tkinter turtledemo turtle.py ensurepip lib2to3 pydoc_data \
        distutils/tests unittest/test sqlite3/test ctypes/test config-*-"$MULTIARCH" \
        site-packages dist-packages
    rm -f lib-dynload/_tkinter*.so lib-dynload/_test*.so lib-dynload/xxlimited*.so
)
mkdir -p "$APPDIR/usr/lib/python3/dist-packages"
cp -a /usr/lib/python3/dist-packages/gi "$APPDIR/usr/lib/python3/dist-packages/"
find "$APPDIR/usr/lib/python3/dist-packages/gi" -name '__pycache__' -type d -prune -exec rm -rf {} +

log "PortGlance $VERSION"
copy_package "$APPDIR/usr/lib/python3/dist-packages"
"$PYTHON" -m compileall -q -j 0 "$APPDIR/usr/lib/python3/dist-packages" >/dev/null

# ---------------------------------------------------------------------------
log "GObject introspection typelibs"
mkdir -p "$APPDIR/usr/lib/girepository-1.0"
seeds=()
while IFS=$'\t' read -r typelib libraries; do
    cp "$typelib" "$APPDIR/usr/lib/girepository-1.0/"
    IFS=',' read -ra names <<< "$libraries"
    for name in "${names[@]}"; do
        [ -n "$name" ] && seeds+=("$name")
    done
done < <("$PYTHON" "$ROOT/packaging/appimage/typelibs.py")
ls "$APPDIR/usr/lib/girepository-1.0" | sed 's/^/    /'

log "gdk-pixbuf SVG loader, MIME database, GIO modules, GSettings schemas"
pixbuf="$APPDIR/usr/lib/gdk-pixbuf-2.0/2.10.0"
mkdir -p "$pixbuf/loaders"
cp "$LIBDIR/gdk-pixbuf-2.0/2.10.0/loaders/libpixbufloader-svg.so" "$pixbuf/loaders/"
GDK_PIXBUF_MODULEDIR="$pixbuf/loaders" "$LIBDIR/gdk-pixbuf-2.0/gdk-pixbuf-query-loaders" \
    | sed "s|$pixbuf/loaders|@LOADERS_DIR@|g" > "$pixbuf/loaders.cache.in"
grep -q '@LOADERS_DIR@/libpixbufloader-svg.so' "$pixbuf/loaders.cache.in"

mkdir -p "$APPDIR/usr/lib/gio/modules"
if [ -f "$LIBDIR/gio/modules/libdconfsettings.so" ]; then
    cp "$LIBDIR/gio/modules/libdconfsettings.so" "$APPDIR/usr/lib/gio/modules/"
fi

# gdk-pixbuf recognises SVG icons through the shared-mime-info database.
mkdir -p "$APPDIR/usr/share/mime"
for file in mime.cache globs globs2 magic aliases subclasses types generic-icons icons \
    XMLnamespaces treemagic version; do
    [ -f "/usr/share/mime/$file" ] && cp "/usr/share/mime/$file" "$APPDIR/usr/share/mime/"
done

schemas="$APPDIR/usr/share/glib-2.0/schemas"
mkdir -p "$schemas"
cp /usr/share/glib-2.0/schemas/org.gtk.gtk4.*.gschema.xml "$schemas/"
glib-compile-schemas "$schemas"

# ---------------------------------------------------------------------------
log "Shared libraries"
EXCLUDES="$TOOLS/excludelist"
if [ ! -s "$EXCLUDES" ]; then
    curl -fsSL -o "$EXCLUDES" \
        https://raw.githubusercontent.com/AppImageCommunity/pkg2appimage/master/excludelist
fi
excluded() {
    grep -qx -- "$1" < <(sed -e 's/#.*//' -e 's/[[:space:]]*$//' "$EXCLUDES" | grep -v '^$')
}
resolve() {
    ldconfig -p | awk -v name="$1" '$1 == name && /x86-64|64bit/ { print $NF; exit }'
}

targets=("$APPDIR/usr/bin/python$PYVER")
targets+=("$APPDIR/usr/lib/python$PYVER"/lib-dynload/*.so)
targets+=("$APPDIR"/usr/lib/python3/dist-packages/gi/*.so)
targets+=("$pixbuf"/loaders/*.so "$APPDIR"/usr/lib/gio/modules/*.so)
for name in "${seeds[@]}" libgirepository-1.0.so.1 librsvg-2.so.2; do
    path="$(resolve "$name")"
    if [ -z "$path" ]; then
        echo "error: cannot find $name" >&2
        exit 1
    fi
    targets+=("$path")
done

declare -A bundled=()
for target in "${targets[@]}"; do
    while read -r name path; do
        [ -z "$path" ] && continue
        if excluded "$name" || [ -n "${bundled[$name]:-}" ]; then
            continue
        fi
        bundled[$name]=1
        cp -L "$path" "$APPDIR/usr/lib/$name"
    done < <(ldd "$target" | awk '/=> \// { print $1, $3 }')
    # The seed libraries themselves (ldd lists only their dependencies).
    base="$(basename "$target")"
    if [[ "$target" == /usr/lib/* || "$target" == /lib/* ]] && ! excluded "$base" \
        && [ -z "${bundled[$base]:-}" ]; then
        bundled[$base]=1
        cp -L "$target" "$APPDIR/usr/lib/$base"
    fi
done
echo "    ${#bundled[@]} libraries bundled"

# ---------------------------------------------------------------------------
log "Icons and desktop integration"
mkdir -p "$APPDIR/usr/share/icons/Adwaita" "$APPDIR/usr/share/icons/hicolor"
cp /usr/share/icons/hicolor/index.theme "$APPDIR/usr/share/icons/hicolor/"
cp -a /usr/share/icons/Adwaita/index.theme /usr/share/icons/Adwaita/symbolic \
    "$APPDIR/usr/share/icons/Adwaita/"
sed -i '/^Directories=/ s/,\?[^,=]*cursors[^,]*//g' "$APPDIR/usr/share/icons/Adwaita/index.theme"
install_desktop_files "$APPDIR/usr"
cp "$ROOT/data/$APP_ID.desktop" "$APPDIR/$APP_ID.desktop"
echo "X-AppImage-Version=$VERSION" >> "$APPDIR/$APP_ID.desktop"
cp "$ROOT/portglance/data/icons/hicolor/scalable/apps/$APP_ID.svg" "$APPDIR/$APP_ID.svg"
ln -s "$APP_ID.svg" "$APPDIR/.DirIcon"
install -m 0755 "$ROOT/packaging/appimage/AppRun" "$APPDIR/AppRun"

# ---------------------------------------------------------------------------
log "Checking the bundle"
env -i HOME="$BUILD" PATH=/usr/bin:/bin "$APPDIR/AppRun" doctor

log "Packing"
APPIMAGETOOL="$TOOLS/appimagetool-$ARCH.AppImage"
if [ ! -x "$APPIMAGETOOL" ]; then
    curl -fsSL -o "$APPIMAGETOOL" \
        "https://github.com/AppImage/appimagetool/releases/download/continuous/appimagetool-$ARCH.AppImage"
    chmod +x "$APPIMAGETOOL"
fi
rm -f "$OUTPUT"
ARCH="$ARCH" APPIMAGE_EXTRACT_AND_RUN=1 "$APPIMAGETOOL" --no-appstream "$APPDIR" "$OUTPUT"
echo "Built $OUTPUT ($(du -h "$OUTPUT" | cut -f1))"
