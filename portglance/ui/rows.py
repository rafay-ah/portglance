"""Rows and section headers of the port list."""

from __future__ import annotations

import os
import re

from gi.repository import Gio, GLib, Gtk, Pango

from ..core.frameworks import GENERIC_FRAMEWORKS
from ..core.grouping import CONTAINERS, OTHER, PINNED, PROJECT, SYSTEM, Section, describe_entry
from ..core.model import SCOPE_NETWORK, PortEntry

LAN_TOOLTIP = (
    "Listening on all network interfaces: other devices on your network can connect to it."
)

SECTION_ICONS = {
    PINNED: "view-pin-symbolic",
    PROJECT: "portglance-folder-symbolic",
    CONTAINERS: "portglance-container-symbolic",
    OTHER: "portglance-terminal-symbolic",
    SYSTEM: "emblem-system-symbolic",
}


def framework_class(label: str) -> str:
    return "fw-" + re.sub(r"[^a-z0-9]+", "", label.lower())


def address_for(entry: PortEntry) -> str:
    return f"localhost:{entry.port}"


class PortBadge(Gtk.Box):
    """The rounded tile with the port number and protocol."""

    def __init__(self) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, valign=Gtk.Align.CENTER)
        self.add_css_class("port-badge")
        inner = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, valign=Gtk.Align.CENTER, vexpand=True)
        self.number = Gtk.Label(css_classes=["port-number", "numeric"])
        self.proto = Gtk.Label(css_classes=["port-proto"])
        inner.append(self.number)
        inner.append(self.proto)
        self.append(inner)
        self._variant: tuple[str, ...] = ()

    def update(self, port: int, proto: str, *variants: str) -> None:
        self.number.set_label(str(port))
        self.proto.set_label(proto.upper())
        if variants != self._variant:
            for css in self._variant:
                self.remove_css_class(css)
            for css in variants:
                self.add_css_class(css)
            self._variant = variants


class PortRow(Gtk.ListBoxRow):
    def __init__(self, entry: PortEntry) -> None:
        super().__init__(activatable=True)
        self.add_css_class("port-row")
        self.entry = entry
        self.stopping = False
        self.show_project = False
        self._menu_signature: tuple | None = None
        self._chip_class: str | None = None

        layout = Gtk.Box(spacing=12)
        self.badge = PortBadge()
        layout.append(self.badge)

        text = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL, spacing=3, valign=Gtk.Align.CENTER, hexpand=True
        )
        title = Gtk.Box(spacing=6)
        self.name_label = Gtk.Label(
            xalign=0, ellipsize=Pango.EllipsizeMode.END, css_classes=["process-name"]
        )
        self.chip = Gtk.Label(css_classes=["chip"], valign=Gtk.Align.CENTER)
        self.lan_chip = Gtk.Label(
            label="LAN",
            css_classes=["chip", "exposed"],
            valign=Gtk.Align.CENTER,
            tooltip_text=LAN_TOOLTIP,
        )
        for widget in (self.name_label, self.chip, self.lan_chip):
            title.append(widget)
        self.details = Gtk.Label(
            xalign=0, ellipsize=Pango.EllipsizeMode.END, css_classes=["details", "dim-label"]
        )
        text.append(title)
        text.append(self.details)
        layout.append(text)

        self.open_button = Gtk.Button(
            icon_name="portglance-open-symbolic",
            valign=Gtk.Align.CENTER,
            css_classes=["flat", "circular", "row-action"],
        )
        self.stop_button = Gtk.Button(
            icon_name="portglance-stop-symbolic",
            valign=Gtk.Align.CENTER,
            css_classes=["flat", "circular", "row-action", "stop"],
        )
        self.menu_button = Gtk.MenuButton(
            icon_name="view-more-symbolic",
            valign=Gtk.Align.CENTER,
            css_classes=["flat", "circular", "row-action"],
            tooltip_text="More Actions",
        )
        actions = Gtk.Box(spacing=2, valign=Gtk.Align.CENTER)
        for button in (self.open_button, self.stop_button, self.menu_button):
            actions.append(button)

        busy = Gtk.Box(spacing=8, valign=Gtk.Align.CENTER, margin_end=6)
        self.spinner = Gtk.Spinner()
        self.busy_label = Gtk.Label(label="Stopping…", css_classes=["dim-label", "caption"])
        busy.append(self.spinner)
        busy.append(self.busy_label)

        self.action_stack = Gtk.Stack(
            transition_type=Gtk.StackTransitionType.CROSSFADE, valign=Gtk.Align.CENTER
        )
        self.action_stack.add_named(actions, "actions")
        self.action_stack.add_named(busy, "busy")
        layout.append(self.action_stack)

        self.set_child(layout)
        self.update(entry)

    # -- state ---------------------------------------------------------------------

    def update(self, entry: PortEntry, now: float | None = None) -> None:
        self.entry = entry
        container = entry.container
        if container is not None:
            variants: tuple[str, ...] = ("container",)
            if container.runtime == "podman":
                variants += ("podman",)
        elif not entry.is_dev:
            variants = ("system",)
        else:
            variants = ()
        if self.stopping:
            variants += ("stopping",)
        self.badge.update(entry.port, entry.proto, *variants)

        name = entry.name if (entry.process or container) else "Unknown process"
        self.name_label.set_label(name)
        if self._chip_class:
            self.chip.remove_css_class(self._chip_class)
            self._chip_class = None
        chip = entry.framework if entry.framework not in GENERIC_FRAMEWORKS else None
        if container is not None:
            chip = container.runtime.capitalize()
        if chip:
            self.chip.set_label(chip)
            self._chip_class = framework_class(chip)
            self.chip.add_css_class(self._chip_class)
        self.chip.set_visible(bool(chip))
        self.lan_chip.set_visible(entry.scope == SCOPE_NETWORK)
        self.details.set_label(describe_entry(entry, now, with_project=self.show_project))
        self.set_tooltip_text(_tooltip(entry))

        # Hidden buttons keep their space so the action column stays aligned.
        self.open_button.set_opacity(1 if entry.http else 0)
        self.open_button.set_can_target(entry.http)
        self.open_button.set_can_focus(entry.http)
        _bind(self.open_button, "app.open-url", GLib.Variant("s", entry.url))
        self.open_button.set_tooltip_text(f"Open {entry.url}" if entry.http else None)
        self.stop_button.set_visible(entry.killable)
        _bind(self.stop_button, "app.stop", GLib.Variant("s", entry.key))
        self.stop_button.set_tooltip_text(
            "Stop Container" if container is not None else "Stop Process"
        )
        self._update_menu()

    def set_show_project(self, show: bool) -> None:
        """Rows outside their project's section mention the project."""
        if show != self.show_project:
            self.show_project = show
            self.details.set_label(describe_entry(self.entry, with_project=show))

    def set_stopping(self, stopping: bool) -> None:
        if stopping == self.stopping:
            return
        self.stopping = stopping
        if stopping:
            self.add_css_class("stopping")
            self.spinner.start()
            self.action_stack.set_visible_child_name("busy")
        else:
            self.remove_css_class("stopping")
            self.spinner.stop()
            self.action_stack.set_visible_child_name("actions")
        self.set_activatable(not stopping)
        self.update(self.entry)

    def set_busy_label(self, text: str) -> None:
        self.busy_label.set_label(text)

    def activate_default(self) -> None:
        """Clicking a row opens it in the browser, or copies its address."""
        if self.stopping:
            return
        if self.entry.http:
            self.activate_action("app.open-url", GLib.Variant("s", self.entry.url))
        else:
            self.activate_action("app.copy", GLib.Variant("s", address_for(self.entry)))

    def _update_menu(self) -> None:
        entry = self.entry
        project_dir = entry.project.root if entry.project and entry.project.root else None
        cwd = entry.process.cwd if entry.process else None
        signature = (
            entry.key,
            entry.http,
            entry.pinned,
            entry.killable,
            project_dir,
            cwd,
            entry.process.command if entry.process else None,
        )
        if signature == self._menu_signature:
            return
        self._menu_signature = signature

        menu = Gio.Menu()
        primary = Gio.Menu()
        if entry.http:
            _item(primary, "Open in Browser", "app.open-url", GLib.Variant("s", entry.url))
            _item(primary, "Copy URL", "app.copy", GLib.Variant("s", entry.url))
        else:
            _item(primary, "Copy Address", "app.copy", GLib.Variant("s", address_for(entry)))
        if entry.container is not None:
            _item(primary, "Copy Container ID", "app.copy", GLib.Variant("s", entry.container.id))
        elif entry.pid is not None:
            _item(primary, "Copy PID", "app.copy", GLib.Variant("s", str(entry.pid)))
        if entry.process is not None and entry.process.cmdline:
            _item(primary, "Copy Command", "app.copy", GLib.Variant("s", entry.process.command))
        menu.append_section(None, primary)

        places = Gio.Menu()
        if project_dir and os.path.isdir(project_dir):
            _item(places, "Open Project Folder", "app.open-folder", GLib.Variant("s", project_dir))
        elif cwd and os.path.isdir(cwd):
            _item(places, "Open Working Folder", "app.open-folder", GLib.Variant("s", cwd))
        _item(
            places,
            "Unpin Port" if entry.pinned else "Pin Port",
            "app.toggle-pin",
            GLib.Variant("i", entry.port),
        )
        menu.append_section(None, places)

        if entry.killable:
            danger = Gio.Menu()
            noun = "Container" if entry.container is not None else "Process"
            _item(danger, f"Stop {noun}…", "app.stop", GLib.Variant("s", entry.key))
            _item(danger, "Kill Immediately", "app.force-stop", GLib.Variant("s", entry.key))
            menu.append_section(None, danger)
        self.menu_button.set_menu_model(menu)


class FreePortRow(Gtk.ListBoxRow):
    """A pinned port that nothing is listening on."""

    def __init__(self, port: int) -> None:
        super().__init__(activatable=False, selectable=False)
        self.add_css_class("port-row")
        self.port = port
        layout = Gtk.Box(spacing=12)
        badge = PortBadge()
        badge.update(port, "tcp", "free")
        layout.append(badge)
        text = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL, spacing=3, valign=Gtk.Align.CENTER, hexpand=True
        )
        text.append(
            Gtk.Label(label="Available", xalign=0, css_classes=["process-name", "dim-label"])
        )
        text.append(
            Gtk.Label(
                label="Nothing is listening on this port",
                xalign=0,
                css_classes=["details", "dim-label"],
            )
        )
        layout.append(text)
        unpin = Gtk.Button(
            icon_name="window-close-symbolic",
            valign=Gtk.Align.CENTER,
            css_classes=["flat", "circular", "row-action"],
            tooltip_text="Unpin Port",
            action_name="app.toggle-pin",
            action_target=GLib.Variant("i", port),
        )
        layout.append(unpin)
        self.set_child(layout)


class SectionHeader(Gtk.Box):
    def __init__(self) -> None:
        super().__init__(spacing=10, css_classes=["section-header"])
        self.icon = Gtk.Image(pixel_size=16, valign=Gtk.Align.CENTER, css_classes=["section-icon"])
        self.append(self.icon)

        labels = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL, spacing=1, hexpand=True, valign=Gtk.Align.CENTER
        )
        title_row = Gtk.Box(spacing=8)
        self.title = Gtk.Label(
            xalign=0, ellipsize=Pango.EllipsizeMode.END, css_classes=["section-title"]
        )
        self.branch = Gtk.Box(spacing=4, css_classes=["chip", "branch"], valign=Gtk.Align.CENTER)
        self.branch.append(Gtk.Image(icon_name="portglance-branch-symbolic", pixel_size=10))
        self.branch_label = Gtk.Label()
        self.branch.append(self.branch_label)
        title_row.append(self.title)
        title_row.append(self.branch)
        self.subtitle = Gtk.Label(
            xalign=0,
            ellipsize=Pango.EllipsizeMode.MIDDLE,
            css_classes=["section-subtitle", "dim-label"],
        )
        labels.append(title_row)
        labels.append(self.subtitle)
        self.append(labels)

        self.folder_button = Gtk.Button(
            icon_name="folder-open-symbolic",
            valign=Gtk.Align.CENTER,
            css_classes=["flat", "circular"],
            tooltip_text="Open Folder",
        )
        self.append(self.folder_button)
        self._icon_class: str | None = None

    def update(self, section: Section) -> None:
        self.title.set_label(section.title)
        self.subtitle.set_label(section.subtitle)
        self.subtitle.set_visible(bool(section.subtitle))
        self.icon.set_from_icon_name(SECTION_ICONS.get(section.kind, "folder-symbolic"))
        icon_class = "git" if section.project and section.project.kind == "git" else None
        if icon_class != self._icon_class:
            if self._icon_class:
                self.icon.remove_css_class(self._icon_class)
            if icon_class:
                self.icon.add_css_class(icon_class)
            self._icon_class = icon_class
        branch = section.project.branch if section.project else None
        self.branch.set_visible(bool(branch))
        self.branch_label.set_label(branch or "")
        root = section.project.root if section.project else None
        has_folder = bool(root) and os.path.isdir(root)
        self.folder_button.set_visible(has_folder)
        if has_folder:
            _bind(self.folder_button, "app.open-folder", GLib.Variant("s", root))


def _bind(widget: Gtk.Actionable, action: str, target: GLib.Variant) -> None:
    """Set the target before the action name, so GTK never sees a mismatch."""
    widget.set_action_target_value(target)
    if widget.get_action_name() != action:
        widget.set_action_name(action)


def _item(menu: Gio.Menu, label: str, action: str, target: GLib.Variant) -> None:
    item = Gio.MenuItem.new(label, None)
    item.set_action_and_target_value(action, target)
    menu.append_item(item)


def _tooltip(entry: PortEntry) -> str:
    if entry.container is not None:
        container = entry.container
        lines = [
            f"{container.name} ({container.image})",
            f"{container.runtime} · {container.status}",
        ]
        return "\n".join(lines)
    if entry.process is not None:
        return entry.process.command
    return "The process belongs to another user, so its details are hidden."
