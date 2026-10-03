"""Print the GObject-Introspection typelibs PortGlance needs, with their libraries.

Output: one line per typelib, "<path>\t<comma-separated shared libraries>".
Run with the same Python and typelib path the AppImage is built from.
"""

import os
import sys

import gi

gi.require_version("GIRepository", "2.0")
from gi.repository import GIRepository  # noqa: E402

ROOTS = [
    ("Adw", "1"),
    ("Gtk", "4.0"),
    ("Gdk", "4.0"),
    ("GdkX11", "4.0"),
    ("GdkWayland", "4.0"),
    ("Gio", "2.0"),
    ("GioUnix", "2.0"),
    ("GLibUnix", "2.0"),
    ("Pango", "1.0"),
]

repo = GIRepository.Repository.get_default()
wanted: set[str] = set()
for namespace, version in ROOTS:
    try:
        repo.require(namespace, version, 0)
    except gi.repository.GLib.Error:
        continue  # e.g. GioUnix only exists on GLib >= 2.80
    wanted.add(f"{namespace}-{version}")
    wanted.update(repo.get_dependencies(namespace) or [])

for item in sorted(wanted):
    namespace, version = item.rsplit("-", 1)
    repo.require(namespace, version, 0)
    path = repo.get_typelib_path(namespace)
    libraries = repo.get_shared_library(namespace) or ""
    if not path or not os.path.exists(path):
        sys.exit(f"typelib for {item} not found")
    print(f"{path}\t{libraries}")
