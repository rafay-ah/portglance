"""The main window: dev ports grouped by project, plus an "All" page."""

from __future__ import annotations

import time

from gi.repository import Adw, Gio, GLib, GObject, Gtk

from .. import APP_ID, APP_NAME
from ..core.grouping import PROJECT, Section, group_entries
from ..core.scanner import Snapshot
from .rows import FreePortRow, PortRow, SectionHeader


class PortListView(Adw.Bin):
    """One page of the window: sections of rows, reused between refreshes."""

    def __init__(self, window: MainWindow, *, include_system: bool) -> None:
        super().__init__()
        self.window = window
        self.include_system = include_system
        self._rows: dict[str, PortRow] = {}
        self._free_rows: dict[int, FreePortRow] = {}
        self._headers: dict[str, SectionHeader] = {}
        self._lists: dict[str, Gtk.ListBox] = {}
        self._layout: tuple | None = None

        self.content = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            margin_start=12,
            margin_end=12,
            margin_top=2,
            margin_bottom=24,
        )
        clamp = Adw.Clamp(maximum_size=760, tightening_threshold=560, child=self.content)
        scrolled = Gtk.ScrolledWindow(
            hscrollbar_policy=Gtk.PolicyType.NEVER, vexpand=True, child=clamp
        )

        self.empty = Adw.StatusPage(icon_name=f"{APP_ID}-symbolic", vexpand=True)
        self.empty.add_css_class("compact")
        self.empty_button = Gtk.Button(
            label="Show All Ports",
            halign=Gtk.Align.CENTER,
            css_classes=["pill"],
            action_name="win.page",
            action_target=GLib.Variant("s", "all"),
        )
        self.empty.set_child(self.empty_button)

        self.loading = Gtk.Spinner(
            spinning=True, halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER, width_request=32
        )

        self.stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE)
        self.stack.add_named(self.loading, "loading")
        self.stack.add_named(scrolled, "list")
        self.stack.add_named(self.empty, "empty")
        self.set_child(self.stack)

    def row_for(self, key: str) -> PortRow | None:
        return self._rows.get(key)

    def update(self, snapshot: Snapshot, pinned: list[int], query: str) -> None:
        now = time.time()
        sections = group_entries(
            snapshot.entries,
            pinned_ports=pinned,
            include_system=self.include_system,
            query=query,
        )
        live_keys = set()
        for section in sections:
            for entry in section.entries:
                live_keys.add(entry.key)
                row = self._rows.get(entry.key)
                if row is None:
                    row = self._rows[entry.key] = PortRow(entry)
                else:
                    row.update(entry, now)
        for key in set(self._rows) - live_keys:
            del self._rows[key]

        layout = tuple(s.signature for s in sections)
        if layout != self._layout:
            self._rebuild(sections)
            self._layout = layout
        else:
            for section in sections:
                self._headers[section.id].update(section)

        if sections:
            self.stack.set_visible_child_name("list")
        else:
            self._show_empty(query)

    def _rebuild(self, sections: list[Section]) -> None:
        for listbox in self._lists.values():
            listbox.remove_all()
        child = self.content.get_first_child()
        while child is not None:
            following = child.get_next_sibling()
            self.content.remove(child)
            child = following

        live_sections = set()
        for section in sections:
            live_sections.add(section.id)
            header = self._headers.get(section.id)
            if header is None:
                header = self._headers[section.id] = SectionHeader()
            header.update(section)
            listbox = self._lists.get(section.id)
            if listbox is None:
                listbox = self._lists[section.id] = Gtk.ListBox(
                    selection_mode=Gtk.SelectionMode.NONE,
                    css_classes=["boxed-list", "port-list"],
                )
                listbox.connect("row-activated", _on_row_activated)
            items: list[tuple[int, int, Gtk.ListBoxRow]] = []
            for port in section.free_ports:
                row = self._free_rows.get(port)
                if row is None:
                    row = self._free_rows[port] = FreePortRow(port)
                items.append((port, 1, row))
            for entry in section.entries:
                port_row = self._rows[entry.key]
                port_row.set_show_project(section.kind != PROJECT)
                items.append((entry.port, 0, port_row))
            for _port, _order, row in sorted(items, key=lambda item: item[:2]):
                listbox.append(row)
            self.content.append(header)
            self.content.append(listbox)
        for stale in set(self._headers) - live_sections:
            del self._headers[stale]
            del self._lists[stale]

    def _show_empty(self, query: str) -> None:
        if query.strip():
            self.empty.set_icon_name("system-search-symbolic")
            self.empty.set_title("No Results")
            self.empty.set_description(f"Nothing matches “{GLib.markup_escape_text(query)}”.")
            self.empty_button.set_visible(False)
        elif self.include_system:
            self.empty.set_icon_name(f"{APP_ID}-symbolic")
            self.empty.set_title("No Listening Ports")
            self.empty.set_description("Nothing on this machine is listening for connections.")
            self.empty_button.set_visible(False)
        else:
            self.empty.set_icon_name(f"{APP_ID}-symbolic")
            self.empty.set_title("No Dev Servers Running")
            self.empty.set_description(
                "Start a dev server and it shows up here right away, grouped by project."
            )
            self.empty_button.set_visible(True)
        self.stack.set_visible_child_name("empty")


class MainWindow(Adw.ApplicationWindow):
    def __init__(self, app) -> None:
        super().__init__(application=app, title=APP_NAME, default_width=560, default_height=720)
        self.set_size_request(360, 400)
        self.app = app
        self._query = ""
        self._snapshot: Snapshot | None = None

        self.toasts = Adw.ToastOverlay()
        toolbar = Adw.ToolbarView(top_bar_style=Adw.ToolbarStyle.FLAT)

        header = Adw.HeaderBar()
        self.view_stack = Adw.ViewStack()
        switcher = Adw.ViewSwitcher(stack=self.view_stack, policy=Adw.ViewSwitcherPolicy.WIDE)
        header.set_title_widget(switcher)

        self.search_button = Gtk.ToggleButton(
            icon_name="system-search-symbolic", tooltip_text="Search (Ctrl+F)"
        )
        header.pack_start(self.search_button)
        menu_button = Gtk.MenuButton(
            icon_name="open-menu-symbolic",
            menu_model=_primary_menu(),
            primary=True,
            tooltip_text="Main Menu",
        )
        header.pack_end(menu_button)
        toolbar.add_top_bar(header)

        self.search_entry = Gtk.SearchEntry(
            placeholder_text="Search ports, processes, projects…", hexpand=True
        )
        self.search_entry.connect("search-changed", self._on_search_changed)
        self.search_bar = Gtk.SearchBar(child=Adw.Clamp(maximum_size=520, child=self.search_entry))
        self.search_bar.connect_entry(self.search_entry)
        self.search_bar.set_key_capture_widget(self)
        self.search_button.bind_property(
            "active",
            self.search_bar,
            "search-mode-enabled",
            GObject.BindingFlags.BIDIRECTIONAL | GObject.BindingFlags.SYNC_CREATE,
        )
        toolbar.add_top_bar(self.search_bar)

        self.banner = Adw.Banner()
        toolbar.add_top_bar(self.banner)

        self.dev_view = PortListView(self, include_system=False)
        self.all_view = PortListView(self, include_system=True)
        self.dev_page = self.view_stack.add_titled_with_icon(
            self.dev_view, "dev", "Dev", "portglance-terminal-symbolic"
        )
        self.all_page = self.view_stack.add_titled_with_icon(
            self.all_view, "all", "All", "portglance-lan-symbolic"
        )
        self.view_stack.connect("notify::visible-child-name", self._on_page_changed)
        toolbar.set_content(self.view_stack)

        self.toasts.set_child(toolbar)
        self.set_content(self.toasts)

        page_action = Gio.SimpleAction.new_stateful(
            "page", GLib.VariantType.new("s"), GLib.Variant("s", "dev")
        )
        page_action.connect("activate", self._on_page_action)
        self.add_action(page_action)
        search_action = Gio.SimpleAction.new("search", None)
        search_action.connect("activate", lambda *_: self.search_button.set_active(True))
        self.add_action(search_action)

        if app.settings.get("show_all"):
            self.view_stack.set_visible_child_name("all")

    # -- public ---------------------------------------------------------------------

    def update(self, snapshot: Snapshot) -> None:
        self._snapshot = snapshot
        pinned = list(self.app.settings.get("pinned_ports"))
        self.dev_view.update(snapshot, pinned, self._query)
        self.all_view.update(snapshot, pinned, self._query)
        dev_count = sum(1 for e in snapshot.entries if e.is_dev)
        self.dev_page.set_badge_number(dev_count)
        self.all_page.set_badge_number(len(snapshot.entries))

    def toast(self, title: str, *, button: str | None = None, action=None, timeout=4) -> None:
        toast = Adw.Toast(title=title, timeout=timeout)
        if button and action:
            toast.set_button_label(button)
            toast.set_action_name(action[0])
            if len(action) > 1:
                toast.set_action_target_value(action[1])
        self.toasts.add_toast(toast)

    def row_for(self, key: str) -> list[PortRow]:
        return [r for r in (self.dev_view.row_for(key), self.all_view.row_for(key)) if r]

    def show_banner(self, title: str | None) -> None:
        if title:
            self.banner.set_title(title)
        self.banner.set_revealed(bool(title))

    # -- handlers --------------------------------------------------------------------

    def _on_search_changed(self, entry: Gtk.SearchEntry) -> None:
        self._query = entry.get_text()
        if self._snapshot is not None:
            self.update(self._snapshot)

    def _on_page_changed(self, stack: Adw.ViewStack, _pspec) -> None:
        name = stack.get_visible_child_name()
        self.app.settings.set("show_all", name == "all")
        action = self.lookup_action("page")
        if action is not None:
            action.set_state(GLib.Variant("s", name))

    def _on_page_action(self, action: Gio.SimpleAction, value: GLib.Variant) -> None:
        self.view_stack.set_visible_child_name(value.get_string())


def _on_row_activated(_listbox: Gtk.ListBox, row: Gtk.ListBoxRow) -> None:
    if isinstance(row, PortRow):
        row.activate_default()


def _primary_menu() -> Gio.Menu:
    menu = Gio.Menu()
    view = Gio.Menu()
    view.append("Desktop Widget", "app.widget")
    view.append("Include UDP Sockets", "app.include-udp")
    menu.append_section(None, view)
    app = Gio.Menu()
    app.append("Refresh", "app.refresh")
    app.append("Preferences", "app.preferences")
    app.append(f"About {APP_NAME}", "app.about")
    menu.append_section(None, app)
    quit_section = Gio.Menu()
    quit_section.append("Quit", "app.quit")
    menu.append_section(None, quit_section)
    return menu
