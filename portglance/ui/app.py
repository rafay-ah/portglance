"""The PortGlance application: windows, actions, panel indicator, stopping."""

from __future__ import annotations

import signal
import threading

from gi.repository import Adw, Gdk, Gio, GLib, Gtk

from .. import APP_ID, APP_NAME, ISSUES_URL, WEBSITE, __version__
from ..cli import gui_parser
from ..core import autostart, killer
from ..core.docker import DockerClient, DockerError, DockerStatus
from ..core.grouping import display_label
from ..core.model import PortEntry
from ..core.scanner import Snapshot
from ..core.settings import SettingsStore
from .indicator import PanelIndicator
from .launch import launch_context
from .monitor import PortMonitor
from .resources import ICONS_DIR, UI_DIR
from .window import MainWindow


class PortGlanceApplication(Adw.Application):
    def __init__(self, demo=None) -> None:
        flags = Gio.ApplicationFlags.HANDLES_COMMAND_LINE
        if demo is not None:
            flags |= Gio.ApplicationFlags.NON_UNIQUE
        super().__init__(application_id=APP_ID if demo is None else f"{APP_ID}.Demo", flags=flags)
        self.demo = demo
        self.settings: SettingsStore | None = None
        self.monitor: PortMonitor | None = None
        self.indicator: PanelIndicator | None = None
        self.window: MainWindow | None = None
        self.widget = None
        self.snapshot: Snapshot | None = None
        self._stopping: dict[str, PortEntry] = {}
        self._dark_css: Gtk.CssProvider | None = None
        self._started = False
        GLib.set_application_name(APP_NAME)

    # -- lifecycle ---------------------------------------------------------------------

    def do_startup(self) -> None:
        Adw.Application.do_startup(self)
        Gtk.Window.set_default_icon_name(APP_ID)
        self.settings = SettingsStore()
        first_run = self.settings.is_first_run
        if first_run:
            self.settings.save()
        self._load_style()
        self._setup_actions()

        docker = self.demo.docker_source() if self.demo is not None else None
        self.monitor = PortMonitor(self.settings, docker=docker)
        self.monitor.connect("updated", self._on_snapshot)
        self.settings.connect(self._on_setting_changed)

        self.indicator = PanelIndicator(self)
        self.indicator.start()

        if self.demo is None:
            try:
                if first_run:
                    autostart.set_enabled(True)  # start on login unless turned off
                else:
                    autostart.refresh()
            except OSError:
                pass

        for signum in (signal.SIGINT, signal.SIGTERM):
            GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, signum, self._on_unix_signal)
        self.monitor.start()
        self.hold()  # keep running in the background with only the indicator

    def do_shutdown(self) -> None:
        if self.monitor is not None:
            self.monitor.stop()
        if self.indicator is not None:
            self.indicator.stop()
        Adw.Application.do_shutdown(self)

    def do_activate(self) -> None:
        self.present_window()

    def do_command_line(self, command_line: Gio.ApplicationCommandLine) -> int:
        args = [a for a in command_line.get_arguments()[1:] if a != "--demo"]
        try:
            options = gui_parser().parse_args(args)
        except SystemExit:
            return 2
        if options.quit:
            self.quit()
            return 0
        first = not self._started
        self._started = True
        token = None
        platform_data = command_line.get_platform_data()
        if platform_data is not None:
            value = platform_data.lookup_value("activation-token", None)
            token = value.get_string() if value is not None else None
        if options.widget or (first and self.settings.get("widget_visible")):
            self.set_widget_visible(True)
        if not options.background and not options.widget:
            self.present_window(token=token)
        return 0

    def _on_unix_signal(self) -> bool:
        self.quit()
        return GLib.SOURCE_REMOVE

    # -- windows -------------------------------------------------------------------------

    def present_window(self, token: str | None = None) -> MainWindow:
        if self.window is None:
            self.window = MainWindow(self)
            self.window.set_hide_on_close(True)
            self.window.connect("close-request", self._on_window_close_request)
            self.window.connect("notify::visible", self._on_visibility_changed)
            if self.snapshot is not None:
                self.window.update(self.snapshot)
            if self.demo is not None:
                self.window.show_banner(self.demo.banner)
        if token:
            self.window.set_startup_id(token)
        self.window.present()
        self._on_visibility_changed()
        return self.window

    def _on_window_close_request(self, _window) -> bool:
        if not self._can_run_hidden():
            self.quit()
        return False  # hide-on-close does the rest

    def _can_run_hidden(self) -> bool:
        widget_visible = self.widget is not None and self.widget.get_visible()
        return bool(self.indicator and self.indicator.registered) or widget_visible

    def set_widget_visible(self, visible: bool, token: str | None = None) -> None:
        from .widget import DesktopWidget

        if visible:
            if self.widget is None:
                self.widget = DesktopWidget(self)
                self.widget.set_hide_on_close(True)
                self.widget.connect("notify::visible", self._on_visibility_changed)
                if self.snapshot is not None:
                    self.widget.update(self.snapshot)
            if token:
                self.widget.set_startup_id(token)
            self.widget.present()
        elif self.widget is not None:
            self.widget.set_visible(False)
            if not self._can_run_hidden() and not (self.window and self.window.get_visible()):
                self.quit()
        self.settings.set("widget_visible", visible)
        self._sync_action_states()

    def show_preferences(self, token: str | None = None) -> None:
        from .preferences import PreferencesDialog

        window = self.present_window(token=token)
        PreferencesDialog(self).present(window)

    def show_about(self) -> None:
        about = Adw.AboutDialog(
            application_name=APP_NAME,
            application_icon=APP_ID,
            developer_name="rafay-ah",
            version=__version__,
            website=WEBSITE,
            issue_url=ISSUES_URL,
            license_type=Gtk.License.MIT_X11,
            copyright="© 2026 rafay-ah",
            comments="See which dev servers are listening on your machine, at a glance.",
        )
        about.present(self.present_window())

    def _on_visibility_changed(self, *_args) -> None:
        if self.monitor is None:
            return
        visible = any(w is not None and w.get_visible() for w in (self.window, self.widget))
        self.monitor.set_foreground(visible)

    def on_indicator_changed(self, _registered: bool) -> None:
        """Called when the panel indicator appears or disappears."""

    # -- snapshot --------------------------------------------------------------------------

    def _on_snapshot(self, _monitor, snapshot: Snapshot) -> None:
        self.snapshot = snapshot
        live = {e.key for e in snapshot.entries}
        for key in list(self._stopping):
            if key not in live:
                del self._stopping[key]
        if self.window is not None:
            self.window.update(snapshot)
            for key in self._stopping:
                for row in self.window.row_for(key):
                    row.set_stopping(True)
        if self.widget is not None:
            self.widget.update(snapshot)
        if self.indicator is not None:
            self.indicator.update(snapshot)

    def find_entry(self, key: str) -> PortEntry | None:
        if self.snapshot is None:
            return None
        return next((e for e in self.snapshot.entries if e.key == key), None)

    def docker_status(self) -> DockerStatus | None:
        return self.monitor.docker.status if self.monitor is not None else None

    # -- actions ----------------------------------------------------------------------------

    def _setup_actions(self) -> None:
        simple = {
            "show-window": lambda *_: self.present_window(),
            "preferences": lambda *_: self.show_preferences(),
            "about": lambda *_: self.show_about(),
            "refresh": lambda *_: self.monitor.refresh(),
            "quit": lambda *_: self.quit(),
        }
        for name, callback in simple.items():
            action = Gio.SimpleAction.new(name, None)
            action.connect("activate", callback)
            self.add_action(action)

        with_string = {
            "open-url": lambda _a, v: self.open_url(v.get_string()),
            "open-folder": lambda _a, v: self.open_folder(v.get_string()),
            "copy": lambda _a, v: self.copy_text(v.get_string()),
            "stop": lambda _a, v: self.request_stop(v.get_string()),
            "force-stop": lambda _a, v: self.request_stop(v.get_string(), force=True),
        }
        for name, callback in with_string.items():
            action = Gio.SimpleAction.new(name, GLib.VariantType.new("s"))
            action.connect("activate", callback)
            self.add_action(action)

        pin = Gio.SimpleAction.new("toggle-pin", GLib.VariantType.new("i"))
        pin.connect("activate", lambda _a, v: self.toggle_pin(v.get_int32()))
        self.add_action(pin)

        widget = Gio.SimpleAction.new_stateful("widget", None, GLib.Variant("b", False))
        widget.connect(
            "activate", lambda a, _v: self.set_widget_visible(not a.get_state().get_boolean())
        )
        self.add_action(widget)
        udp = Gio.SimpleAction.new_stateful("include-udp", None, GLib.Variant("b", False))
        udp.connect(
            "activate",
            lambda a, _v: self.settings.set("include_udp", not a.get_state().get_boolean()),
        )
        self.add_action(udp)
        self._sync_action_states()

        self.set_accels_for_action("app.quit", ["<Control>q"])
        self.set_accels_for_action("app.refresh", ["<Control>r", "F5"])
        self.set_accels_for_action("app.preferences", ["<Control>comma"])
        self.set_accels_for_action("win.search", ["<Control>f"])
        self.set_accels_for_action("window.close", ["<Control>w"])
        self.set_accels_for_action("win.page('dev')", ["<Alt>1"])
        self.set_accels_for_action("win.page('all')", ["<Alt>2"])

    def _sync_action_states(self) -> None:
        for name, key in (("widget", "widget_visible"), ("include-udp", "include_udp")):
            action = self.lookup_action(name)
            if action is not None:
                action.set_state(GLib.Variant("b", bool(self.settings.get(key))))

    def _on_setting_changed(self, key: str, _value) -> None:
        self._sync_action_states()
        if key == "widget_visible" and self.indicator is not None:
            self.indicator.refresh_menu()
        if key == "pinned_ports" and self.window is not None and self.snapshot is not None:
            self.window.update(self.snapshot)

    # -- operations ----------------------------------------------------------------------------

    def _parent(self) -> Gtk.Window | None:
        if self.window is not None and self.window.get_visible():
            return self.window
        return None

    def notify(self, title: str, body: str = "", *, toast: bool = True) -> None:
        """Toast in the window when it is visible, desktop notification otherwise."""
        if toast and self._parent() is not None:
            self.window.toast(title if not body else f"{title}. {body}")
            return
        if not self._freedesktop_notify(title, body):
            notification = Gio.Notification.new(title)
            if body:
                notification.set_body(body)
            notification.set_icon(Gio.ThemedIcon.new(APP_ID))
            self.send_notification(None, notification)

    def _freedesktop_notify(self, title: str, body: str) -> bool:
        """Use org.freedesktop.Notifications directly.

        Unlike GApplication notifications, this also works when no desktop
        file is installed for the app id, as with the AppImage.
        """
        icon = ICONS_DIR / "hicolor" / "scalable" / "apps" / f"{APP_ID}.svg"
        hints = {"desktop-entry": GLib.Variant("s", APP_ID)}
        try:
            bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
            bus.call_sync(
                "org.freedesktop.Notifications",
                "/org/freedesktop/Notifications",
                "org.freedesktop.Notifications",
                "Notify",
                GLib.Variant(
                    "(susssasa{sv}i)", (APP_NAME, 0, str(icon), title, body, [], hints, -1)
                ),
                GLib.VariantType.new("(u)"),
                Gio.DBusCallFlags.NONE,
                1000,
                None,
            )
        except GLib.Error:
            return False
        return True

    def open_url(self, url: str, token: str | None = None) -> None:
        self._launch_uri(url, token, "Could not open the browser")

    def open_folder(self, path: str, token: str | None = None) -> None:
        uri = Gio.File.new_for_path(path).get_uri()
        self._launch_uri(uri, token, "Could not open the folder")

    def _launch_uri(self, uri: str, token: str | None, error_title: str) -> None:
        def done(_source, result) -> None:
            try:
                Gio.AppInfo.launch_default_for_uri_finish(result)
            except GLib.Error as exc:
                self.notify(error_title, exc.message)

        Gio.AppInfo.launch_default_for_uri_async(uri, launch_context(token), None, done)

    def copy_text(self, text: str) -> None:
        display = Gdk.Display.get_default()
        if display is None:
            return
        display.get_clipboard().set(text)
        shown = text if len(text) <= 48 else text[:45] + "…"
        if self._parent() is not None:
            self.window.toast(f"Copied “{shown}”", timeout=2)

    def toggle_pin(self, port: int) -> None:
        pinned = self.settings.toggle_pinned(port)
        if self._parent() is not None:
            self.window.toast(f"{'Pinned' if pinned else 'Unpinned'} port {port}", timeout=2)

    # -- stopping ---------------------------------------------------------------------------------

    def request_stop(self, key: str, *, force: bool = False, token: str | None = None) -> None:
        entry = self.find_entry(key)
        if entry is None:
            self.notify("Already gone", "Nothing is listening there any more.")
            return
        if key in self._stopping or not entry.killable:
            return
        if not self.settings.get("confirm_kill"):
            self._stop(entry, force)
            return
        window = self.present_window(token=token)
        dialog = Adw.AlertDialog(
            heading=_stop_heading(entry, force), body=self._stop_body(entry, force)
        )
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("stop", "Kill" if force else "Stop")
        dialog.set_response_appearance("stop", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response("cancel")
        dialog.set_close_response("cancel")
        dont_ask = Gtk.CheckButton(label="Don’t ask again", halign=Gtk.Align.CENTER)
        dialog.set_extra_child(dont_ask)

        def on_response(_dialog, response: str) -> None:
            if response != "stop":
                return
            if dont_ask.get_active():
                self.settings.set("confirm_kill", False)
            current = self.find_entry(key)
            if current is None:
                self.notify(f"{display_label(entry)} already exited")
                return
            self._stop(current, force)

        dialog.connect("response", on_response)
        dialog.present(window)

    def _stop_body(self, entry: PortEntry, force: bool) -> str:
        timeout = self.settings.get("kill_timeout")
        facts = []
        if entry.container is not None:
            facts.append(entry.container.image)
        else:
            if entry.process is not None:
                facts.append(entry.process.name)
            if entry.pid is not None:
                facts.append(f"PID {entry.pid}")
        if entry.project is not None:
            facts.append(entry.project.name)
        summary = " · ".join(facts)
        if entry.container is not None:
            how = (
                "The container is killed right away."
                if force
                else f"Runs “{entry.container.runtime} stop”: SIGTERM, then SIGKILL after "
                f"{timeout:g} seconds."
            )
        elif force:
            how = "SIGKILL ends it immediately, without a chance to clean up."
        else:
            how = (
                f"SIGTERM asks it to shut down. If it is still running after {timeout:g} "
                "seconds, it is force-killed with SIGKILL."
            )
        return f"{summary}\n\n{how}"

    def _stop(self, entry: PortEntry, force: bool) -> None:
        key = entry.key
        self._stopping[key] = entry
        self._set_row_state(key, True)
        timeout = 0.0 if force else float(self.settings.get("kill_timeout"))

        def escalate() -> None:
            GLib.idle_add(self._on_escalate, key)

        def work() -> None:
            if entry.container is not None:
                outcome, message = self._stop_container(entry, timeout)
            else:
                result = killer.terminate(
                    entry.pid,
                    timeout=timeout,
                    start_ticks=entry.process.start_ticks if entry.process else None,
                    companions=[(pid, None) for pid in entry.pids if pid != entry.pid],
                    on_escalate=escalate,
                )
                outcome, message = result.outcome, result.message
            GLib.idle_add(self._on_stopped, entry, outcome, message, timeout)

        threading.Thread(target=work, name="portglance-stop", daemon=True).start()

    def _stop_container(self, entry: PortEntry, timeout: float) -> tuple[str, str]:
        assert entry.container is not None
        client = self.monitor.docker.client if self.monitor else None
        if client is None:
            client = DockerClient.discover()
        if client is None:
            return killer.FAILED, "Docker is not reachable."
        try:
            client.stop(entry.container.id, timeout)
        except DockerError as exc:
            return killer.FAILED, str(exc)
        return killer.TERMINATED, ""

    def _set_row_state(self, key: str, stopping: bool) -> None:
        if self.window is not None:
            for row in self.window.row_for(key):
                row.set_stopping(stopping)
        if self.indicator is not None:
            self.indicator.set_stopping(key, stopping)

    def _on_escalate(self, key: str) -> bool:
        if self.window is not None:
            for row in self.window.row_for(key):
                row.set_busy_label("Force killing…")
        return GLib.SOURCE_REMOVE

    def _on_stopped(self, entry: PortEntry, outcome: str, message: str, timeout: float) -> bool:
        key = entry.key
        self._stopping.pop(key, None)
        name = display_label(entry)
        port = entry.port
        if outcome == killer.TERMINATED:
            noun = "container " if entry.container else ""
            self.notify(f"Stopped {noun}{name} on port {port}")
        elif outcome == killer.KILLED:
            if timeout:
                self.notify(f"Force-killed {name} on port {port} after {timeout:g}s")
            else:
                self.notify(f"Killed {name} on port {port}")
        elif outcome == killer.GONE:
            self.notify(f"{name} had already exited")
        else:
            self._set_row_state(key, False)
            if outcome == killer.DENIED and entry.pid is not None:
                if self._parent() is not None:
                    self.window.toast(
                        f"Can’t stop {name}: it belongs to another user",
                        button="Copy Command",
                        action=("app.copy", GLib.Variant("s", f"sudo kill {entry.pid}")),
                        timeout=8,
                    )
                else:
                    self.notify(f"Can’t stop {name}", "It belongs to another user.", toast=False)
            else:
                self.notify(f"Could not stop {name}", message)
        self.monitor.refresh()
        return GLib.SOURCE_REMOVE

    # -- styling ---------------------------------------------------------------------------------

    def _load_style(self) -> None:
        display = Gdk.Display.get_default()
        if display is None:
            return
        Gtk.IconTheme.get_for_display(display).add_search_path(str(ICONS_DIR))
        css = Gtk.CssProvider()
        css.load_from_path(str(UI_DIR / "style.css"))
        Gtk.StyleContext.add_provider_for_display(
            display, css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )
        self._dark_css = Gtk.CssProvider()
        self._dark_css.load_from_path(str(UI_DIR / "style-dark.css"))
        style = Adw.StyleManager.get_default()
        style.connect("notify::dark", self._on_dark_changed)
        self._on_dark_changed(style)

    def _on_dark_changed(self, style: Adw.StyleManager, *_args) -> None:
        display = Gdk.Display.get_default()
        if display is None or self._dark_css is None:
            return
        if style.get_dark():
            Gtk.StyleContext.add_provider_for_display(
                display, self._dark_css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION + 1
            )
        else:
            Gtk.StyleContext.remove_provider_for_display(display, self._dark_css)


def _stop_heading(entry: PortEntry, force: bool) -> str:
    verb = "Kill" if force else "Stop"
    if entry.container is not None:
        return f"{verb} container {entry.container.name}?"
    return f"{verb} {display_label(entry)} on port {entry.port}?"


def run(argv: list[str], demo=None) -> int:
    app = PortGlanceApplication(demo=demo)
    return app.run(argv)
