"""Keep the desktop widget above other windows and on every workspace.

Wayland does not let applications change their own stacking, so on GNOME
this goes through a tiny optional GNOME Shell extension shipped with
PortGlance. On X11 the standard EWMH ``_NET_WM_STATE`` request is used.
"""

from __future__ import annotations

import ctypes
import ctypes.util
import shutil
import subprocess
from pathlib import Path

from gi.repository import Gio, GLib, Gtk

from ..core.hostenv import host_environ
from .resources import SHELL_EXTENSION_DIR

EXTENSION_UUID = "portglance-helper@rafay-ah.github.io"
HELPER_PATH = "/io/github/rafay_ah/PortGlance/ShellHelper"
HELPER_INTERFACE = "io.github.rafay_ah.PortGlance.ShellHelper"

# Helper extension states
ACTIVE = "active"
NEEDS_RELOGIN = "needs-relogin"
NOT_INSTALLED = "not-installed"
UNSUPPORTED = "unsupported"


# --------------------------------------------------------------------------
# GNOME Shell helper extension
# --------------------------------------------------------------------------


def _session_bus() -> Gio.DBusConnection | None:
    try:
        return Gio.bus_get_sync(Gio.BusType.SESSION, None)
    except GLib.Error:
        return None


def helper_active() -> bool:
    bus = _session_bus()
    if bus is None:
        return False
    try:
        bus.call_sync(
            "org.gnome.Shell",
            HELPER_PATH,
            "org.freedesktop.DBus.Properties",
            "Get",
            GLib.Variant("(ss)", (HELPER_INTERFACE, "Version")),
            None,
            Gio.DBusCallFlags.NONE,
            500,
            None,
        )
    except GLib.Error:
        return False
    return True


def user_extension_dir() -> Path:
    return Path(GLib.get_user_data_dir()) / "gnome-shell" / "extensions" / EXTENSION_UUID


def helper_installed() -> bool:
    system = Path("/usr/share/gnome-shell/extensions") / EXTENSION_UUID
    return user_extension_dir().is_dir() or system.is_dir()


def gnome_shell_running() -> bool:
    bus = _session_bus()
    if bus is None:
        return False
    try:
        result = bus.call_sync(
            "org.freedesktop.DBus",
            "/org/freedesktop/DBus",
            "org.freedesktop.DBus",
            "NameHasOwner",
            GLib.Variant("(s)", ("org.gnome.Shell",)),
            GLib.VariantType.new("(b)"),
            Gio.DBusCallFlags.NONE,
            500,
            None,
        )
    except GLib.Error:
        return False
    return bool(result.unpack()[0])


def helper_state() -> str:
    if helper_active():
        return ACTIVE
    if not gnome_shell_running():
        return UNSUPPORTED
    if helper_installed():
        return NEEDS_RELOGIN
    return NOT_INSTALLED


def install_helper() -> str:
    """Copy the extension to the user's extension folder and enable it."""
    target = user_extension_dir()
    if target.exists():
        shutil.rmtree(target)
    shutil.copytree(SHELL_EXTENSION_DIR / EXTENSION_UUID, target)
    _enable_extension()
    return helper_state()


def _enable_extension() -> None:
    bus = _session_bus()
    if bus is not None:
        try:
            bus.call_sync(
                "org.gnome.Shell.Extensions",
                "/org/gnome/Shell/Extensions",
                "org.gnome.Shell.Extensions",
                "EnableExtension",
                GLib.Variant("(s)", (EXTENSION_UUID,)),
                None,
                Gio.DBusCallFlags.NONE,
                2000,
                None,
            )
            return
        except GLib.Error:
            pass
    if shutil.which("gnome-extensions"):
        subprocess.run(
            ["gnome-extensions", "enable", EXTENSION_UUID],
            check=False,
            capture_output=True,
            env=host_environ(),
        )


def _helper_keep_above(title: str, above: bool) -> bool:
    bus = _session_bus()
    if bus is None:
        return False
    try:
        result = bus.call_sync(
            "org.gnome.Shell",
            HELPER_PATH,
            HELPER_INTERFACE,
            "SetKeepAbove",
            GLib.Variant("(sb)", (title, above)),
            GLib.VariantType.new("(b)"),
            Gio.DBusCallFlags.NONE,
            1000,
            None,
        )
    except GLib.Error:
        return False
    return bool(result.unpack()[0])


# --------------------------------------------------------------------------
# X11
# --------------------------------------------------------------------------


class _XClientMessage(ctypes.Structure):
    _fields_ = [
        ("type", ctypes.c_int),
        ("serial", ctypes.c_ulong),
        ("send_event", ctypes.c_int),
        ("display", ctypes.c_void_p),
        ("window", ctypes.c_ulong),
        ("message_type", ctypes.c_ulong),
        ("format", ctypes.c_int),
        ("data", ctypes.c_long * 5),
    ]


class _XEvent(ctypes.Union):
    _fields_ = [("xclient", _XClientMessage), ("pad", ctypes.c_long * 24)]


_CLIENT_MESSAGE = 33
_SUBSTRUCTURE_MASK = (1 << 19) | (1 << 20)  # SubstructureNotify | SubstructureRedirect


def _x11_keep_above(window: Gtk.Window, above: bool) -> bool:
    try:
        import gi

        gi.require_version("GdkX11", "4.0")
        from gi.repository import GdkX11
    except (ImportError, ValueError):
        return False
    surface = window.get_surface()
    if not isinstance(surface, GdkX11.X11Surface):
        return False
    library = ctypes.util.find_library("X11")
    if not library:
        return False
    xlib = ctypes.CDLL(library)
    xlib.XOpenDisplay.restype = ctypes.c_void_p
    xlib.XOpenDisplay.argtypes = [ctypes.c_char_p]
    xlib.XDefaultRootWindow.restype = ctypes.c_ulong
    xlib.XDefaultRootWindow.argtypes = [ctypes.c_void_p]
    xlib.XInternAtom.restype = ctypes.c_ulong
    xlib.XInternAtom.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_int]
    xlib.XSendEvent.argtypes = [
        ctypes.c_void_p,
        ctypes.c_ulong,
        ctypes.c_int,
        ctypes.c_long,
        ctypes.POINTER(_XEvent),
    ]
    xlib.XFlush.argtypes = [ctypes.c_void_p]
    xlib.XCloseDisplay.argtypes = [ctypes.c_void_p]

    display = xlib.XOpenDisplay(None)
    if not display:
        return False
    try:
        root = xlib.XDefaultRootWindow(display)
        state = xlib.XInternAtom(display, b"_NET_WM_STATE", False)
        event = _XEvent()
        message = event.xclient
        message.type = _CLIENT_MESSAGE
        message.send_event = 1
        message.window = surface.get_xid()
        message.message_type = state
        message.format = 32
        for first, second in (("_NET_WM_STATE_ABOVE", "_NET_WM_STATE_STICKY"),):
            message.data[0] = 1 if above else 0  # _NET_WM_STATE_ADD / _REMOVE
            message.data[1] = xlib.XInternAtom(display, first.encode(), False)
            message.data[2] = xlib.XInternAtom(display, second.encode(), False)
            message.data[3] = 1  # source: application
            xlib.XSendEvent(display, root, False, _SUBSTRUCTURE_MASK, ctypes.byref(event))
        xlib.XFlush(display)
    finally:
        xlib.XCloseDisplay(display)
    return True


# --------------------------------------------------------------------------


def set_keep_above(window: Gtk.Window, above: bool) -> bool:
    """Try every available mechanism. Returns False when none is available."""
    if _x11_keep_above(window, above):
        return True
    return _helper_keep_above(window.get_title() or "", above)
