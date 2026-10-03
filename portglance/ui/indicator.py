"""Turns port snapshots into the top-panel indicator's label and menu."""

from __future__ import annotations

from .. import APP_ID, APP_NAME
from ..core.classify import is_dev_runtime
from ..core.grouping import display_label
from ..core.model import PortEntry
from ..core.scanner import Snapshot
from .resources import ICONS_DIR
from .tray import MenuItem, StatusNotifierItem

MAX_MENU_ENTRIES = 15


def menu_label(entry: PortEntry) -> str:
    label = f"{entry.port}  {display_label(entry)}"
    if entry.project is not None:
        label += f"  ·  {entry.project.name}"
    return label


def sort_key(entry: PortEntry) -> tuple:
    project = entry.project.name.lower() if entry.project else "￿"
    return (not entry.pinned, project, entry.port)


class PanelIndicator:
    def __init__(self, app) -> None:
        self.app = app
        self.sni = StatusNotifierItem(
            app.get_application_id() or APP_ID,
            title=APP_NAME,
            icon_name=f"{APP_ID}-symbolic",
            icon_theme_path=str(ICONS_DIR),
            on_secondary_activate=lambda: app.present_window(),
            on_registered=self._on_registered,
        )
        self._snapshot: Snapshot | None = None
        self._stopping: set[str] = set()
        self._signature: tuple | None = None

    @property
    def registered(self) -> bool:
        return self.sni.registered

    def start(self) -> None:
        if self.sni.start():
            self._rebuild(force=True)

    def stop(self) -> None:
        self.sni.stop()

    def update(self, snapshot: Snapshot) -> None:
        self._snapshot = snapshot
        self._stopping &= {e.key for e in snapshot.entries}
        self._rebuild()

    def set_stopping(self, key: str, stopping: bool) -> None:
        if stopping:
            self._stopping.add(key)
        else:
            self._stopping.discard(key)
        self._rebuild()

    def refresh_menu(self) -> None:
        self._rebuild(force=True)

    def _token(self) -> str | None:
        token, self.sni.activation_token = self.sni.activation_token, None
        return token

    def _on_registered(self, registered: bool) -> None:
        self.app.on_indicator_changed(registered)

    def _rebuild(self, force: bool = False) -> None:
        if self.sni.menu is None:
            return
        entries = sorted(self._snapshot.dev_entries if self._snapshot else [], key=sort_key)
        widget_on = bool(self.app.settings.get("widget_visible"))
        signature = (
            tuple(
                (e.key, menu_label(e), e.http, e.killable, e.key in self._stopping) for e in entries
            ),
            widget_on,
        )
        if signature == self._signature and not force:
            return
        self._signature = signature

        count = len(entries)
        self.sni.set_label(str(count) if count else "")
        if count:
            summary = f"{count} dev port{'s' if count != 1 else ''} in use"
        else:
            summary = "No dev servers running"
        projects = sorted({e.project.name for e in entries if e.project})
        self.sni.set_tooltip(APP_NAME, summary + (f"\n{', '.join(projects)}" if projects else ""))

        app = self.app
        items = [MenuItem("header", summary, enabled=False), MenuItem("sep-top", separator=True)]
        for entry in entries[:MAX_MENU_ENTRIES]:
            items.append(self._entry_item(entry))
        if count > MAX_MENU_ENTRIES:
            more = count - MAX_MENU_ENTRIES
            items.append(
                MenuItem(
                    "more",
                    f"{more} more…",
                    on_click=lambda _t: app.present_window(token=self._token()),
                )
            )
        if count:
            items.append(MenuItem("sep-entries", separator=True))
        items += [
            MenuItem(
                "show",
                f"Open {APP_NAME}",
                on_click=lambda _t: app.present_window(token=self._token()),
            ),
            MenuItem(
                "widget",
                "Desktop Widget",
                toggle=widget_on,
                on_click=lambda _t: app.set_widget_visible(not widget_on, token=self._token()),
            ),
            MenuItem(
                "preferences",
                "Preferences",
                on_click=lambda _t: app.show_preferences(token=self._token()),
            ),
            MenuItem("sep-bottom", separator=True),
            MenuItem("quit", f"Quit {APP_NAME}", on_click=lambda _t: app.quit()),
        ]
        self.sni.menu.set_items(items)

    def _entry_item(self, entry: PortEntry) -> MenuItem:
        app = self.app
        key = entry.key
        children = []
        if entry.http:
            children.append(
                MenuItem(
                    f"open:{key}",
                    f"Open {entry.url}",
                    icon_name="web-browser-symbolic",
                    on_click=lambda _t, url=entry.url: app.open_url(url, token=self._token()),
                )
            )
        if entry.project is not None and entry.project.root:
            children.append(
                MenuItem(
                    f"folder:{key}",
                    "Open Project Folder",
                    icon_name="folder-open-symbolic",
                    on_click=lambda _t, path=entry.project.root: app.open_folder(
                        path, token=self._token()
                    ),
                )
            )
        if entry.killable:
            stopping = key in self._stopping
            noun = "Container" if entry.container is not None else _noun(entry)
            children.append(
                MenuItem(
                    f"stop:{key}",
                    "Stopping…" if stopping else f"Stop {noun}…",
                    icon_name="process-stop-symbolic",
                    enabled=not stopping,
                    on_click=lambda _t, k=key: app.request_stop(k, token=self._token()),
                )
            )
        return MenuItem(f"entry:{key}", menu_label(entry), children=children)


def _noun(entry: PortEntry) -> str:
    if entry.process is not None and not is_dev_runtime(entry.process.name):
        return entry.process.name
    return "Process"
