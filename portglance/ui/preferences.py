"""Preferences dialog."""

from __future__ import annotations

from gi.repository import Adw, GLib, Gtk

from ..core import autostart
from ..core.docker import CONNECTED, PERMISSION_DENIED
from . import pinning


class PreferencesDialog(Adw.PreferencesDialog):
    def __init__(self, app) -> None:
        super().__init__(title="Preferences", search_enabled=False)
        self.app = app
        settings = app.settings
        self.set_content_width(560)

        page = Adw.PreferencesPage(title="General", icon_name="emblem-system-symbolic")
        self.add(page)

        # -- Startup ----------------------------------------------------------------
        startup = Adw.PreferencesGroup(title="Startup")
        self.autostart_row = Adw.SwitchRow(
            title="Start on Login",
            subtitle="Show the top-panel indicator when you log in",
            active=autostart.is_enabled(),
        )
        self.autostart_row.connect("notify::active", self._on_autostart)
        if app.demo:
            self.autostart_row.set_sensitive(False)
            self.autostart_row.set_subtitle("Not available in demo mode")
        startup.add(self.autostart_row)

        widget_row = Adw.SwitchRow(
            title="Desktop Widget",
            subtitle="A small list of your dev ports that stays on the desktop",
            active=settings.get("widget_visible"),
        )
        widget_row.connect("notify::active", lambda r, _p: app.set_widget_visible(r.get_active()))
        self._widget_row = widget_row
        startup.add(widget_row)
        page.add(startup)

        # -- Ports ----------------------------------------------------------------------
        ports = Adw.PreferencesGroup(
            title="Pinned Ports",
            description=(
                "Pinned ports are always listed at the top, with their status, "
                "even when nothing uses them or they belong to a system service."
            ),
        )
        self.pin_entry = Adw.EntryRow(
            title="Add a port",
            show_apply_button=True,
            input_purpose=Gtk.InputPurpose.DIGITS,
        )
        self.pin_entry.connect("apply", self._on_add_pin)
        self.pin_entry.connect("entry-activated", self._on_add_pin)
        ports.add(self.pin_entry)
        self._pinned_group = ports
        self._pin_rows: list[Adw.ActionRow] = []
        page.add(ports)

        listing = Adw.PreferencesGroup(title="Listing")
        udp_row = Adw.SwitchRow(
            title="Include UDP Sockets",
            subtitle="Also list bound UDP sockets on the All page",
            active=settings.get("include_udp"),
        )
        udp_row.connect("notify::active", lambda r, _p: settings.set("include_udp", r.get_active()))
        listing.add(udp_row)
        interval = Adw.SpinRow.new_with_range(1, 30, 1)
        interval.set_title("Refresh Interval")
        interval.set_subtitle(
            "Seconds between checks for new listeners while a window is open. "
            "Stopped servers disappear instantly either way."
        )
        interval.set_value(settings.get("refresh_interval"))
        interval.connect(
            "notify::value", lambda r, _p: settings.set("refresh_interval", r.get_value())
        )
        listing.add(interval)
        page.add(listing)

        # -- Stopping ------------------------------------------------------------------------
        stopping = Adw.PreferencesGroup(title="Stopping Servers")
        confirm = Adw.SwitchRow(
            title="Ask for Confirmation",
            subtitle="Confirm before a process or container is stopped",
            active=settings.get("confirm_kill"),
        )
        confirm.connect(
            "notify::active", lambda r, _p: settings.set("confirm_kill", r.get_active())
        )
        stopping.add(confirm)
        timeout = Adw.SpinRow.new_with_range(1, 60, 1)
        timeout.set_title("Force Kill After")
        timeout.set_subtitle("Seconds to wait after SIGTERM before sending SIGKILL")
        timeout.set_value(settings.get("kill_timeout"))
        timeout.connect("notify::value", lambda r, _p: settings.set("kill_timeout", r.get_value()))
        stopping.add(timeout)
        page.add(stopping)

        # -- Docker --------------------------------------------------------------------------
        docker = Adw.PreferencesGroup(title="Containers")
        docker_row = Adw.SwitchRow(
            title="Show Docker and Podman Containers",
            subtitle="Published ports, container names and Compose projects",
            active=settings.get("docker_enabled"),
        )
        docker_row.connect(
            "notify::active", lambda r, _p: settings.set("docker_enabled", r.get_active())
        )
        docker.add(docker_row)
        self.docker_status = Adw.ActionRow(title="Status")
        self.docker_status_icon = Gtk.Image(valign=Gtk.Align.CENTER)
        self.docker_status.add_prefix(self.docker_status_icon)
        docker.add(self.docker_status)
        page.add(docker)

        # -- Widget pinning on GNOME ---------------------------------------------------------
        self.helper_group = Adw.PreferencesGroup(
            title="Keep the Widget on Top",
            description=(
                "Wayland does not let apps raise their own windows above others. "
                "On GNOME, a tiny helper extension does it for the PortGlance widget."
            ),
        )
        self.helper_row = Adw.ActionRow(title="GNOME Shell Helper")
        self.helper_button = Gtk.Button(valign=Gtk.Align.CENTER, css_classes=["suggested-action"])
        self.helper_button.connect("clicked", self._on_install_helper)
        self.helper_row.add_suffix(self.helper_button)
        self.helper_group.add(self.helper_row)
        page.add(self.helper_group)

        self._settings_handler = settings.connect(self._on_setting_changed)
        self.connect("closed", lambda _d: settings.disconnect(self._settings_handler))
        self._refresh_pins()
        self._refresh_docker()
        self._refresh_helper()

    # -- pinned ports ----------------------------------------------------------------

    def _refresh_pins(self) -> None:
        for row in self._pin_rows:
            self._pinned_group.remove(row)
        self._pin_rows = []
        for port in self.app.settings.get("pinned_ports"):
            row = Adw.ActionRow(title=str(port), css_classes=["numeric"])
            remove = Gtk.Button(
                icon_name="user-trash-symbolic",
                valign=Gtk.Align.CENTER,
                tooltip_text="Unpin",
                css_classes=["flat", "circular"],
                action_name="app.toggle-pin",
                action_target=GLib.Variant("i", port),
            )
            remove.update_property([Gtk.AccessibleProperty.LABEL], [f"Unpin port {port}"])
            row.add_suffix(remove)
            self._pinned_group.add(row)
            self._pin_rows.append(row)

    def _on_add_pin(self, entry: Adw.EntryRow) -> None:
        text = entry.get_text().strip().lstrip(":")
        try:
            port = int(text)
        except ValueError:
            port = 0
        if not 0 < port < 65536:
            entry.add_css_class("error")
            return
        entry.remove_css_class("error")
        entry.set_text("")
        if port not in self.app.settings.get("pinned_ports"):
            self.app.settings.toggle_pinned(port)

    # -- status rows ---------------------------------------------------------------------

    def _refresh_docker(self) -> None:
        status = self.app.docker_status()
        if not self.app.settings.get("docker_enabled"):
            icon, text = "media-playback-pause-symbolic", "Turned off"
        elif status is None or status.state == "unavailable":
            icon, text = "dialog-information-symbolic", "Docker or Podman is not running"
        elif status.state == CONNECTED:
            runtime = (status.runtime or "docker").capitalize()
            version = f" {status.version}" if status.version else ""
            icon, text = "object-select-symbolic", f"Connected to {runtime}{version}"
        elif status.state == PERMISSION_DENIED:
            icon = "dialog-warning-symbolic"
            text = "Permission denied: add yourself to the “docker” group and log in again"
        else:
            icon, text = "dialog-warning-symbolic", status.message or "Cannot reach the daemon"
        self.docker_status_icon.set_from_icon_name(icon)
        self.docker_status.set_subtitle(text)

    def _refresh_helper(self) -> None:
        state = pinning.helper_state()
        self.helper_group.set_visible(state != pinning.UNSUPPORTED)
        labels = {
            pinning.ACTIVE: ("Installed and running", None),
            pinning.NEEDS_RELOGIN: (
                "Installed. Log out and back in to finish — GNOME loads new extensions at login.",
                "Reinstall",
            ),
            pinning.NOT_INSTALLED: ("Not installed", "Install"),
        }
        subtitle, button = labels.get(state, ("", None))
        self.helper_row.set_subtitle(subtitle)
        self.helper_button.set_visible(button is not None)
        if button:
            self.helper_button.set_label(button)

    # -- handlers --------------------------------------------------------------------------

    def _on_autostart(self, row: Adw.SwitchRow, _pspec) -> None:
        try:
            autostart.set_enabled(row.get_active())
        except OSError as exc:
            self.add_toast(Adw.Toast(title=f"Could not change autostart: {exc.strerror}"))

    def _on_install_helper(self, _button: Gtk.Button) -> None:
        try:
            pinning.install_helper()
        except OSError as exc:
            self.add_toast(Adw.Toast(title=f"Could not install the helper: {exc.strerror}"))
        self._refresh_helper()

    def _on_setting_changed(self, key: str, value) -> None:
        if key == "pinned_ports":
            self._refresh_pins()
        elif key == "docker_enabled":
            GLib.timeout_add(800, lambda: (self._refresh_docker(), GLib.SOURCE_REMOVE)[1])
        elif key == "widget_visible" and self._widget_row.get_active() != value:
            self._widget_row.set_active(value)
