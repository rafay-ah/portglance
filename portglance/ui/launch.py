"""Start other programs (browser, file manager) with the host's environment."""

from __future__ import annotations

from gi.repository import Gdk, Gio

from ..core.hostenv import BUNDLE_VARIABLES, PREFIX, host_overrides


def launch_context(token: str | None = None) -> Gio.AppLaunchContext:
    """A launch context with the host environment and, optionally, a focus token.

    With a token (handed over by the panel), a plain context is used so the
    token reaches the launched program unchanged. Otherwise GDK's context
    obtains one from the compositor for the focused window.
    """
    display = Gdk.Display.get_default()
    if token or display is None:
        context = Gio.AppLaunchContext()
    else:
        context = display.get_app_launch_context()
    for name, value in host_overrides().items():
        if value is None:
            context.unsetenv(name)
        else:
            context.setenv(name, value)
    for name in BUNDLE_VARIABLES:
        context.unsetenv(PREFIX + name)
    if token:
        context.setenv("XDG_ACTIVATION_TOKEN", token)
    return context
