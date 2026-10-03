"""Top-panel indicator via StatusNotifierItem and com.canonical.dbusmenu.

GTK 4 has no tray API and libayatana-appindicator is GTK 3 only, so both
D-Bus protocols are implemented here directly with GDBus. On Ubuntu the
indicator is shown by the AppIndicator extension that ships enabled by
default; KDE Plasma, Budgie, Cinnamon, XFCE and others have hosts too.

Ubuntu's extension shows ``XAyatanaLabel`` next to the icon, which is how the
port count gets into the top bar.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from dataclasses import dataclass, field

from gi.repository import Gio, GLib

SNI_INTERFACE = "org.kde.StatusNotifierItem"
SNI_PATH = "/StatusNotifierItem"
MENU_INTERFACE = "com.canonical.dbusmenu"
MENU_PATH = "/MenuBar"
WATCHER_NAME = "org.kde.StatusNotifierWatcher"
WATCHER_PATH = "/StatusNotifierWatcher"

# No "Activate" method on purpose: without it, a left click opens the menu
# immediately instead of waiting to rule out a double click.
SNI_XML = """
<node>
  <interface name="org.kde.StatusNotifierItem">
    <property name="Category" type="s" access="read"/>
    <property name="Id" type="s" access="read"/>
    <property name="Title" type="s" access="read"/>
    <property name="Status" type="s" access="read"/>
    <property name="WindowId" type="i" access="read"/>
    <property name="IconThemePath" type="s" access="read"/>
    <property name="IconName" type="s" access="read"/>
    <property name="IconPixmap" type="a(iiay)" access="read"/>
    <property name="OverlayIconName" type="s" access="read"/>
    <property name="OverlayIconPixmap" type="a(iiay)" access="read"/>
    <property name="AttentionIconName" type="s" access="read"/>
    <property name="AttentionIconPixmap" type="a(iiay)" access="read"/>
    <property name="AttentionMovieName" type="s" access="read"/>
    <property name="ToolTip" type="(sa(iiay)ss)" access="read"/>
    <property name="ItemIsMenu" type="b" access="read"/>
    <property name="Menu" type="o" access="read"/>
    <property name="XAyatanaLabel" type="s" access="read"/>
    <property name="XAyatanaLabelGuide" type="s" access="read"/>
    <property name="XAyatanaOrderingIndex" type="u" access="read"/>
    <method name="ContextMenu">
      <arg name="x" type="i" direction="in"/>
      <arg name="y" type="i" direction="in"/>
    </method>
    <method name="SecondaryActivate">
      <arg name="x" type="i" direction="in"/>
      <arg name="y" type="i" direction="in"/>
    </method>
    <method name="Scroll">
      <arg name="delta" type="i" direction="in"/>
      <arg name="orientation" type="s" direction="in"/>
    </method>
    <method name="ProvideXdgActivationToken">
      <arg name="token" type="s" direction="in"/>
    </method>
    <signal name="NewTitle"/>
    <signal name="NewIcon"/>
    <signal name="NewAttentionIcon"/>
    <signal name="NewOverlayIcon"/>
    <signal name="NewToolTip"/>
    <signal name="NewStatus"><arg name="status" type="s"/></signal>
    <signal name="NewMenu"/>
    <signal name="XAyatanaNewLabel">
      <arg name="label" type="s"/>
      <arg name="guide" type="s"/>
    </signal>
  </interface>
</node>
"""

MENU_XML = """
<node>
  <interface name="com.canonical.dbusmenu">
    <property name="Version" type="u" access="read"/>
    <property name="TextDirection" type="s" access="read"/>
    <property name="Status" type="s" access="read"/>
    <property name="IconThemePath" type="as" access="read"/>
    <method name="GetLayout">
      <arg type="i" name="parentId" direction="in"/>
      <arg type="i" name="recursionDepth" direction="in"/>
      <arg type="as" name="propertyNames" direction="in"/>
      <arg type="u" name="revision" direction="out"/>
      <arg type="(ia{sv}av)" name="layout" direction="out"/>
    </method>
    <method name="GetGroupProperties">
      <arg type="ai" name="ids" direction="in"/>
      <arg type="as" name="propertyNames" direction="in"/>
      <arg type="a(ia{sv})" name="properties" direction="out"/>
    </method>
    <method name="GetProperty">
      <arg type="i" name="id" direction="in"/>
      <arg type="s" name="name" direction="in"/>
      <arg type="v" name="value" direction="out"/>
    </method>
    <method name="Event">
      <arg type="i" name="id" direction="in"/>
      <arg type="s" name="eventId" direction="in"/>
      <arg type="v" name="data" direction="in"/>
      <arg type="u" name="timestamp" direction="in"/>
    </method>
    <method name="EventGroup">
      <arg type="a(isvu)" name="events" direction="in"/>
      <arg type="ai" name="idErrors" direction="out"/>
    </method>
    <method name="AboutToShow">
      <arg type="i" name="id" direction="in"/>
      <arg type="b" name="needUpdate" direction="out"/>
    </method>
    <method name="AboutToShowGroup">
      <arg type="ai" name="ids" direction="in"/>
      <arg type="ai" name="updatesNeeded" direction="out"/>
      <arg type="ai" name="idErrors" direction="out"/>
    </method>
    <signal name="ItemsPropertiesUpdated">
      <arg type="a(ia{sv})" name="updatedProps"/>
      <arg type="a(ias)" name="removedProps"/>
    </signal>
    <signal name="LayoutUpdated">
      <arg type="u" name="revision"/>
      <arg type="i" name="parent"/>
    </signal>
  </interface>
</node>
"""


# --------------------------------------------------------------------------
# Menu model
# --------------------------------------------------------------------------


@dataclass(slots=True)
class MenuItem:
    """A dbusmenu item. ``key`` keeps item ids stable across rebuilds."""

    key: str
    label: str = ""
    separator: bool = False
    enabled: bool = True
    visible: bool = True
    icon_name: str | None = None
    toggle: bool | None = None  # None: not a check item
    children: list[MenuItem] = field(default_factory=list)
    on_click: Callable[[int], None] | None = None  # receives the event timestamp


def escape_label(label: str, gnome: bool) -> str:
    """Escape underscores, which dbusmenu treats as mnemonic markers.

    GNOME's AppIndicator extension only unescapes the first occurrence, so
    there only the first underscore is doubled.
    """
    if gnome:
        return label.replace("_", "__", 1)
    return label.replace("_", "__")


def _desktop_is_gnome() -> bool:
    desktops = os.environ.get("XDG_CURRENT_DESKTOP", "").lower().split(":")
    return "gnome" in desktops or "ubuntu" in desktops


class DBusMenu:
    def __init__(self, connection: Gio.DBusConnection) -> None:
        self.connection = connection
        self.revision = 1
        self._ids: dict[str, int] = {}
        self._next_id = 1
        self._items: dict[int, MenuItem] = {}
        self._children: dict[int, list[int]] = {0: []}
        self._gnome = _desktop_is_gnome()
        info = Gio.DBusNodeInfo.new_for_xml(MENU_XML).interfaces[0]
        self._registration = connection.register_object(
            MENU_PATH, info, self._on_method_call, self._on_get_property, None
        )

    def unregister(self) -> None:
        if self._registration:
            self.connection.unregister_object(self._registration)
            self._registration = 0

    def set_items(self, items: list[MenuItem]) -> None:
        self._items = {}
        self._children = {0: self._assign(items)}
        self.revision += 1
        self._emit("LayoutUpdated", GLib.Variant("(ui)", (self.revision, 0)))

    def _assign(self, items: list[MenuItem]) -> list[int]:
        ids = []
        for item in items:
            item_id = self._ids.get(item.key)
            if item_id is None:
                item_id = self._ids[item.key] = self._next_id
                self._next_id += 1
            self._items[item_id] = item
            self._children[item_id] = self._assign(item.children)
            ids.append(item_id)
        return ids

    # -- serialisation -------------------------------------------------------------

    def _properties(self, item_id: int, names: list[str] | None = None) -> dict[str, GLib.Variant]:
        if item_id == 0:
            props = {"children-display": GLib.Variant("s", "submenu")}
        else:
            item = self._items[item_id]
            if item.separator:
                props = {"type": GLib.Variant("s", "separator")}
            else:
                props = {"label": GLib.Variant("s", escape_label(item.label, self._gnome))}
                if item.icon_name:
                    props["icon-name"] = GLib.Variant("s", item.icon_name)
                if item.toggle is not None:
                    props["toggle-type"] = GLib.Variant("s", "checkmark")
                    props["toggle-state"] = GLib.Variant("i", 1 if item.toggle else 0)
                if self._children.get(item_id):
                    props["children-display"] = GLib.Variant("s", "submenu")
            if not item.enabled:
                props["enabled"] = GLib.Variant("b", False)
            if not item.visible:
                props["visible"] = GLib.Variant("b", False)
        if names:
            props = {k: v for k, v in props.items() if k in names}
        return props

    def _layout(self, item_id: int, depth: int, names: list[str]) -> GLib.Variant:
        children = []
        if depth != 0:
            for child in self._children.get(item_id, []):
                children.append(self._layout(child, depth - 1, names))
        return GLib.Variant("(ia{sv}av)", (item_id, self._properties(item_id, names), children))

    # -- D-Bus -------------------------------------------------------------------------

    def _on_method_call(self, _conn, _sender, _path, _iface, method, params, invocation):
        args = params.unpack()
        if method == "GetLayout":
            parent, depth, names = args
            if parent != 0 and parent not in self._items:
                invocation.return_dbus_error(
                    "org.freedesktop.DBus.Error.InvalidArgs", f"no item {parent}"
                )
                return
            layout = self._layout(parent, depth, list(names))
            invocation.return_value(
                GLib.Variant.new_tuple(GLib.Variant("u", self.revision), layout)
            )
        elif method == "GetGroupProperties":
            ids, names = args
            result = [
                (i, self._properties(i, list(names)))
                for i in (ids or list(self._items))
                if i == 0 or i in self._items
            ]
            invocation.return_value(GLib.Variant("(a(ia{sv}))", (result,)))
        elif method == "GetProperty":
            item_id, name = args
            value = (
                self._properties(item_id).get(name)
                if (item_id == 0 or item_id in self._items)
                else None
            )
            if value is None:
                invocation.return_dbus_error(
                    "org.freedesktop.DBus.Error.InvalidArgs", f"no property {name}"
                )
                return
            invocation.return_value(GLib.Variant("(v)", (value,)))
        elif method == "Event":
            item_id, event_id, _data, timestamp = args
            self._handle_event(item_id, event_id, timestamp)
            invocation.return_value(None)
        elif method == "EventGroup":
            (events,) = args
            errors = []
            for item_id, event_id, _data, timestamp in events:
                if item_id not in self._items:
                    errors.append(item_id)
                else:
                    self._handle_event(item_id, event_id, timestamp)
            invocation.return_value(GLib.Variant("(ai)", (errors,)))
        elif method == "AboutToShow":
            invocation.return_value(GLib.Variant("(b)", (False,)))
        elif method == "AboutToShowGroup":
            invocation.return_value(GLib.Variant("(aiai)", ([], [])))
        else:
            invocation.return_dbus_error(
                "org.freedesktop.DBus.Error.UnknownMethod", f"unknown method {method}"
            )

    def _handle_event(self, item_id: int, event_id: str, timestamp: int) -> None:
        item = self._items.get(item_id)
        if item is None or event_id != "clicked" or item.on_click is None:
            return
        callback = item.on_click
        GLib.idle_add(lambda: (callback(timestamp), GLib.SOURCE_REMOVE)[1])

    def _on_get_property(self, _conn, _sender, _path, _iface, name):
        return {
            "Version": GLib.Variant("u", 3),
            "TextDirection": GLib.Variant("s", "ltr"),
            "Status": GLib.Variant("s", "normal"),
            "IconThemePath": GLib.Variant("as", []),
        }.get(name)

    def _emit(self, signal: str, params: GLib.Variant) -> None:
        try:
            self.connection.emit_signal(None, MENU_PATH, MENU_INTERFACE, signal, params)
        except GLib.Error:
            pass


# --------------------------------------------------------------------------
# StatusNotifierItem
# --------------------------------------------------------------------------


class StatusNotifierItem:
    """Exports the indicator and registers it with the panel's watcher.

    Registration is retried whenever the watcher appears, so the indicator
    shows up even when the app starts before GNOME Shell's extension at login,
    or after the extension is re-enabled.
    """

    def __init__(
        self,
        app_id: str,
        *,
        title: str,
        icon_name: str,
        icon_theme_path: str = "",
        on_secondary_activate: Callable[[], None] | None = None,
        on_registered: Callable[[bool], None] | None = None,
    ) -> None:
        self.app_id = app_id
        self.title = title
        self.icon_name = icon_name
        self.icon_theme_path = icon_theme_path
        self.label = ""
        self.tooltip = (title, "")
        self.status = "Active"
        self.on_secondary_activate = on_secondary_activate
        self.on_registered = on_registered
        self.activation_token: str | None = None
        self.registered = False
        self.connection: Gio.DBusConnection | None = None
        self.menu: DBusMenu | None = None
        self.bus_name = f"org.kde.StatusNotifierItem-{os.getpid()}-1"
        self._registration = 0
        self._owner_id = 0
        self._watch_id = 0

    def start(self) -> bool:
        try:
            self.connection = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        except GLib.Error:
            return False
        info = Gio.DBusNodeInfo.new_for_xml(SNI_XML).interfaces[0]
        self._registration = self.connection.register_object(
            SNI_PATH, info, self._on_method_call, self._on_get_property, None
        )
        self.menu = DBusMenu(self.connection)
        self._owner_id = Gio.bus_own_name_on_connection(
            self.connection, self.bus_name, Gio.BusNameOwnerFlags.NONE, None, None
        )
        self._watch_id = Gio.bus_watch_name_on_connection(
            self.connection,
            WATCHER_NAME,
            Gio.BusNameWatcherFlags.NONE,
            self._on_watcher_appeared,
            self._on_watcher_vanished,
        )
        return True

    def stop(self) -> None:
        if self.connection is None:
            return
        if self._watch_id:
            Gio.bus_unwatch_name(self._watch_id)
            self._watch_id = 0
        if self._owner_id:
            Gio.bus_unown_name(self._owner_id)
            self._owner_id = 0
        if self.menu is not None:
            self.menu.unregister()
        if self._registration:
            self.connection.unregister_object(self._registration)
            self._registration = 0
        self._set_registered(False)

    # -- updates ---------------------------------------------------------------------

    def set_label(self, label: str, guide: str = "") -> None:
        if label == self.label:
            return
        self.label = label
        self._emit("XAyatanaNewLabel", GLib.Variant("(ss)", (label, guide)))
        self._properties_changed({"XAyatanaLabel": GLib.Variant("s", label)})

    def set_tooltip(self, title: str, body: str) -> None:
        if (title, body) == self.tooltip:
            return
        self.tooltip = (title, body)
        self._emit("NewToolTip", None)
        self._properties_changed({"ToolTip": self._tooltip_variant()})

    def set_icon(self, icon_name: str) -> None:
        if icon_name == self.icon_name:
            return
        self.icon_name = icon_name
        self._emit("NewIcon", None)
        self._properties_changed({"IconName": GLib.Variant("s", icon_name)})

    # -- D-Bus ---------------------------------------------------------------------------

    def _on_watcher_appeared(self, connection, _name, _owner) -> None:
        connection.call(
            WATCHER_NAME,
            WATCHER_PATH,
            WATCHER_NAME,
            "RegisterStatusNotifierItem",
            GLib.Variant("(s)", (self.bus_name,)),
            None,
            Gio.DBusCallFlags.NONE,
            -1,
            None,
            self._on_register_done,
        )

    def _on_register_done(self, connection, result) -> None:
        try:
            connection.call_finish(result)
        except GLib.Error:
            self._set_registered(False)
            return
        self._set_registered(True)

    def _on_watcher_vanished(self, _connection, _name) -> None:
        self._set_registered(False)

    def _set_registered(self, registered: bool) -> None:
        if registered == self.registered:
            return
        self.registered = registered
        if self.on_registered is not None:
            self.on_registered(registered)

    def _on_method_call(self, _conn, _sender, _path, _iface, method, params, invocation):
        if method == "ProvideXdgActivationToken":
            (self.activation_token,) = params.unpack()
        elif method == "SecondaryActivate" and self.on_secondary_activate is not None:
            GLib.idle_add(lambda: (self.on_secondary_activate(), GLib.SOURCE_REMOVE)[1])
        invocation.return_value(None)

    def _tooltip_variant(self) -> GLib.Variant:
        title, body = self.tooltip
        return GLib.Variant("(sa(iiay)ss)", ("", [], title, body))

    def _on_get_property(self, _conn, _sender, _path, _iface, name):
        values = {
            "Category": GLib.Variant("s", "ApplicationStatus"),
            "Id": GLib.Variant("s", self.app_id),
            "Title": GLib.Variant("s", self.title),
            "Status": GLib.Variant("s", self.status),
            "WindowId": GLib.Variant("i", 0),
            "IconThemePath": GLib.Variant("s", self.icon_theme_path),
            "IconName": GLib.Variant("s", self.icon_name),
            "IconPixmap": GLib.Variant("a(iiay)", []),
            "OverlayIconName": GLib.Variant("s", ""),
            "OverlayIconPixmap": GLib.Variant("a(iiay)", []),
            "AttentionIconName": GLib.Variant("s", ""),
            "AttentionIconPixmap": GLib.Variant("a(iiay)", []),
            "AttentionMovieName": GLib.Variant("s", ""),
            "ToolTip": self._tooltip_variant(),
            "ItemIsMenu": GLib.Variant("b", True),
            "Menu": GLib.Variant("o", MENU_PATH),
            "XAyatanaLabel": GLib.Variant("s", self.label),
            "XAyatanaLabelGuide": GLib.Variant("s", ""),
            "XAyatanaOrderingIndex": GLib.Variant("u", 0),
        }
        return values.get(name)

    def _emit(self, signal: str, params: GLib.Variant | None) -> None:
        if self.connection is None:
            return
        try:
            self.connection.emit_signal(None, SNI_PATH, SNI_INTERFACE, signal, params)
        except GLib.Error:
            pass

    def _properties_changed(self, changed: dict[str, GLib.Variant]) -> None:
        if self.connection is None:
            return
        try:
            self.connection.emit_signal(
                None,
                SNI_PATH,
                "org.freedesktop.DBus.Properties",
                "PropertiesChanged",
                GLib.Variant("(sa{sv}as)", (SNI_INTERFACE, changed, [])),
            )
        except GLib.Error:
            pass
