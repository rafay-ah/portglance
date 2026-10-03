"""A compact, pinnable desktop widget listing the dev ports."""

from __future__ import annotations

from gi.repository import GLib, Gtk, Pango

from .. import APP_ID, APP_NAME
from ..core.grouping import display_label
from ..core.model import PortEntry
from ..core.scanner import Snapshot
from . import pinning
from .indicator import sort_key
from .rows import address_for, set_accessible_label

WIDGET_TITLE = f"{APP_NAME} Widget"
MAX_ROWS = 12


class WidgetRow(Gtk.ListBoxRow):
    def __init__(self, entry: PortEntry) -> None:
        super().__init__(activatable=True)
        self.entry = entry
        box = Gtk.Box(spacing=8)
        port = Gtk.Label(
            label=str(entry.port), width_chars=5, xalign=0, css_classes=["widget-port", "numeric"]
        )
        if entry.container is not None:
            port.add_css_class("container")
        box.append(port)

        box.append(
            Gtk.Label(
                label=display_label(entry),
                xalign=0,
                hexpand=True,
                ellipsize=Pango.EllipsizeMode.END,
                css_classes=["widget-name"],
            )
        )
        if entry.project is not None:
            box.append(
                Gtk.Label(
                    label=entry.project.name,
                    xalign=1,
                    max_width_chars=14,
                    ellipsize=Pango.EllipsizeMode.END,
                    css_classes=["widget-project", "dim-label"],
                )
            )
        stop = Gtk.Button(
            icon_name="portglance-stop-symbolic",
            valign=Gtk.Align.CENTER,
            css_classes=["flat", "circular", "widget-stop"],
            tooltip_text="Stop",
            action_name="app.stop",
            action_target=GLib.Variant("s", entry.key),
            sensitive=entry.killable,
        )
        set_accessible_label(stop, f"Stop {display_label(entry)} on port {entry.port}")
        box.append(stop)
        self.set_child(box)
        self.set_tooltip_text(entry.url if entry.http else address_for(entry))


class DesktopWidget(Gtk.Window):
    def __init__(self, app) -> None:
        super().__init__(
            application=app,
            title=WIDGET_TITLE,
            decorated=False,
            resizable=False,
            icon_name=APP_ID,
        )
        self.app = app
        self.add_css_class("portglance-widget")
        self._signature: tuple | None = None

        card = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=4,
            width_request=288,
            css_classes=["widget-card"],
        )
        header = Gtk.Box(spacing=7, margin_start=6, margin_end=2, margin_bottom=2)
        header.append(Gtk.Image(icon_name=APP_ID, pixel_size=18))
        header.append(Gtk.Label(label=APP_NAME, xalign=0, css_classes=["widget-title"]))
        self.count = Gtk.Label(css_classes=["widget-count", "numeric"], valign=Gtk.Align.CENTER)
        header.append(self.count)
        header.append(Gtk.Box(hexpand=True))

        self.pin_button = Gtk.ToggleButton(
            icon_name="view-pin-symbolic",
            css_classes=["flat", "circular", "widget-button"],
            tooltip_text="Keep on Top",
            valign=Gtk.Align.CENTER,
        )
        set_accessible_label(self.pin_button, "Keep on top")
        self.pin_button.set_active(bool(app.settings.get("widget_pinned")))
        self.pin_button.connect("toggled", self._on_pin_toggled)
        header.append(self.pin_button)
        open_app = Gtk.Button(
            icon_name="view-list-bullet-symbolic",
            css_classes=["flat", "circular", "widget-button"],
            tooltip_text=f"Open {APP_NAME}",
            action_name="app.show-window",
            valign=Gtk.Align.CENTER,
        )
        set_accessible_label(open_app, f"Open {APP_NAME}")
        header.append(open_app)
        close = Gtk.Button(
            icon_name="window-close-symbolic",
            css_classes=["flat", "circular", "widget-button"],
            tooltip_text="Hide Widget",
            valign=Gtk.Align.CENTER,
        )
        set_accessible_label(close, "Hide widget")
        close.connect("clicked", lambda _b: app.set_widget_visible(False))
        header.append(close)
        card.append(header)

        self.list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE, css_classes=["widget-list"])
        self.list.connect("row-activated", self._on_row_activated)
        card.append(self.list)
        self.empty = Gtk.Label(
            label="No dev servers running", css_classes=["widget-empty", "dim-label"]
        )
        card.append(self.empty)

        self.hint = Gtk.Label(
            wrap=True,
            max_width_chars=34,
            xalign=0,
            css_classes=["caption", "dim-label"],
            margin_start=8,
            margin_end=8,
            margin_bottom=4,
            visible=False,
        )
        card.append(self.hint)

        handle = Gtk.WindowHandle(child=card)
        self.set_child(handle)
        self.connect("map", lambda _w: GLib.timeout_add(250, self._apply_pin))

    def update(self, snapshot: Snapshot) -> None:
        entries = sorted(snapshot.dev_entries, key=sort_key)
        signature = tuple((e.key, e.framework, e.killable, e.http) for e in entries)
        self.count.set_label(str(len(entries)))
        if signature == self._signature:
            return
        self._signature = signature
        self.list.remove_all()
        for entry in entries[:MAX_ROWS]:
            self.list.append(WidgetRow(entry))
        self.list.set_visible(bool(entries))
        self.empty.set_visible(not entries)

    def _on_row_activated(self, _list: Gtk.ListBox, row: WidgetRow) -> None:
        entry = row.entry
        if entry.http:
            self.app.open_url(entry.url)
        else:
            self.app.copy_text(address_for(entry))

    def _on_pin_toggled(self, button: Gtk.ToggleButton) -> None:
        self.app.settings.set("widget_pinned", button.get_active())
        self._apply_pin()

    def _apply_pin(self) -> bool:
        pinned = self.pin_button.get_active()
        ok = pinning.set_keep_above(self, pinned)
        if pinned and not ok:
            self.hint.set_label(
                "To keep the widget on top, turn on the GNOME Shell helper in "
                "Preferences, or press Alt+Space and choose “Always on Top”."
            )
            self.hint.set_visible(True)
        else:
            self.hint.set_visible(False)
        return GLib.SOURCE_REMOVE
