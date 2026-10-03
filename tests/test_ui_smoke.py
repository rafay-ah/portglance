"""Start the real GTK app in demo mode and check what it shows.

Needs PyGObject with GTK 4 and libadwaita >= 1.5 and a display (CI runs it
under Xvfb); skipped otherwise.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

gi = pytest.importorskip("gi")
try:
    gi.require_version("Gtk", "4.0")
    gi.require_version("Adw", "1")
    from gi.repository import Adw, GLib
except (ImportError, ValueError):
    pytest.skip("GTK 4 / libadwaita are not available", allow_module_level=True)

if (Adw.get_major_version(), Adw.get_minor_version()) < (1, 5):
    pytest.skip("libadwaita 1.5 or newer is required", allow_module_level=True)
if not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")):
    pytest.skip("no display", allow_module_level=True)


def test_demo_mode_window_lists_every_server(tmp_path: Path, monkeypatch) -> None:
    from portglance.core.grouping import group_entries
    from portglance.demo import DemoFleet
    from portglance.ui.app import PortGlanceApplication

    monkeypatch.setenv("PORTGLANCE_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setenv("PORTGLANCE_DISPLAY_HOME", str(tmp_path / "demo"))
    fleet = DemoFleet(tmp_path / "demo")
    fleet.start()
    seen: dict = {}
    try:
        app = PortGlanceApplication(demo=fleet)

        def inspect() -> bool:
            window, snapshot = app.window, app.snapshot
            if window is None or snapshot is None or len(snapshot.dev_entries) < 9:
                return GLib.SOURCE_CONTINUE
            pinned = app.settings.get("pinned_ports")
            seen["ports"] = sorted(e.port for e in snapshot.dev_entries)
            seen["sections"] = [
                s.title for s in group_entries(snapshot.entries, pinned_ports=pinned)
            ]
            seen["rows"] = len(window.dev_view._rows)
            seen["badge"] = window.dev_page.get_badge_number()
            app.quit()
            return GLib.SOURCE_REMOVE

        GLib.timeout_add(250, inspect)
        GLib.timeout_add_seconds(30, lambda: app.quit())
        app.run(["portglance"])
    finally:
        fleet.stop()

    servers = {server.port for server in fleet.servers}
    assert servers <= set(seen["ports"])
    assert seen["rows"] == len(seen["ports"]) == seen["badge"]
    assert seen["sections"][:2] == ["Pinned", "acme-web"]
    assert "Containers" in seen["sections"]
