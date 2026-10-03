"""``portglance doctor``: report what PortGlance finds in this environment."""

from __future__ import annotations

import os
import platform
import sys

from . import APP_NAME, __version__
from .core import autostart
from .core.docker import DockerClient

OK = "ok"
WARN = "warn"
FAIL = "fail"


def _row(label: str, value: str, state: str = OK) -> None:
    mark = {OK: "✓", WARN: "!", FAIL: "✗"}[state]
    print(f"  {mark} {label:<12} {value}")


def run_doctor() -> int:
    failures = 0
    print(f"{APP_NAME} {__version__}")
    _row("Python", f"{platform.python_version()} ({sys.executable})")
    _row("Kernel", platform.release())

    try:
        import gi

        gi.require_version("Gtk", "4.0")
        gi.require_version("Adw", "1")
        from gi.repository import Adw, Gtk
    except (ImportError, ValueError) as exc:
        _row("GTK", f"not usable: {exc}", FAIL)
        print("\nThe app needs PyGObject with GTK 4 and libadwaita (python3-gi,")
        print("gir1.2-gtk-4.0 and gir1.2-adw-1 on Debian and Ubuntu).")
        return 1
    _row("PyGObject", ".".join(str(v) for v in gi.version_info))
    gtk = f"{Gtk.get_major_version()}.{Gtk.get_minor_version()}.{Gtk.get_micro_version()}"
    _row("GTK", gtk)
    adw = f"{Adw.get_major_version()}.{Adw.get_minor_version()}.{Adw.get_micro_version()}"
    adw_ok = (Adw.get_major_version(), Adw.get_minor_version()) >= (1, 5)
    _row("libadwaita", adw if adw_ok else f"{adw} (1.5 or newer needed)", OK if adw_ok else FAIL)
    failures += not adw_ok

    session = os.environ.get("XDG_SESSION_TYPE", "unknown")
    desktop = os.environ.get("XDG_CURRENT_DESKTOP", "unknown")
    _row("Session", f"{session}, {desktop}")

    from gi.repository import Gio, GLib

    try:
        bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
    except GLib.Error as exc:
        bus = None
        _row("D-Bus", f"no session bus: {exc.message}", WARN)
    if bus is not None:
        try:
            owner = bus.call_sync(
                "org.freedesktop.DBus",
                "/org/freedesktop/DBus",
                "org.freedesktop.DBus",
                "NameHasOwner",
                GLib.Variant("(s)", ("org.kde.StatusNotifierWatcher",)),
                GLib.VariantType.new("(b)"),
                Gio.DBusCallFlags.NONE,
                1000,
                None,
            ).unpack()[0]
        except GLib.Error:
            owner = False
        if owner:
            _row("Indicator", "a panel host is running")
        else:
            _row(
                "Indicator",
                "no panel host; on GNOME install and enable the AppIndicator extension",
                WARN,
            )

    client = DockerClient.discover()
    if client is None:
        _row("Containers", "Docker/Podman socket not found", WARN)
    else:
        status = client.status()
        if status.connected:
            _row("Containers", f"{status.runtime} {status.version} at {status.endpoint}")
        else:
            _row("Containers", status.message or status.state, WARN)

    enabled = autostart.is_enabled()
    _row("Autostart", "on" if enabled else "off", OK if enabled else WARN)
    return 1 if failures else 0
