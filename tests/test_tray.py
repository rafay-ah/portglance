"""The dbusmenu protocol as a panel host sees it, over a private D-Bus."""

from __future__ import annotations

import shutil

import pytest

gi = pytest.importorskip("gi")
try:
    from gi.repository import Gio, GLib

    from portglance.ui.tray import MENU_INTERFACE, MENU_PATH, DBusMenu, MenuItem, escape_label
except (ImportError, ValueError):
    pytest.skip("PyGObject with GTK 4 is not available", allow_module_level=True)

if shutil.which("dbus-daemon") is None:
    pytest.skip("dbus-daemon is not installed", allow_module_level=True)


@pytest.fixture
def buses():
    test_bus = Gio.TestDBus.new(Gio.TestDBusFlags.NONE)
    test_bus.up()
    flags = (
        Gio.DBusConnectionFlags.AUTHENTICATION_CLIENT
        | Gio.DBusConnectionFlags.MESSAGE_BUS_CONNECTION
    )
    address = test_bus.get_bus_address()
    server = Gio.DBusConnection.new_for_address_sync(address, flags, None, None)
    client = Gio.DBusConnection.new_for_address_sync(address, flags, None, None)
    yield server, client
    client.close_sync(None)
    server.close_sync(None)
    test_bus.down()


def call(client, server, method, params, reply_type):
    loop = GLib.MainLoop()
    result = {}

    def done(connection, res):
        try:
            result["value"] = connection.call_finish(res).unpack()
        except GLib.Error as exc:
            result["error"] = exc
        loop.quit()

    client.call(
        server.get_unique_name(),
        MENU_PATH,
        MENU_INTERFACE,
        method,
        params,
        GLib.VariantType.new(reply_type) if reply_type else None,
        Gio.DBusCallFlags.NONE,
        2000,
        None,
        done,
    )
    GLib.timeout_add(3000, loop.quit)
    loop.run()
    if "error" in result:
        raise result["error"]
    return result["value"]


def pump(seconds=0.2):
    loop = GLib.MainLoop()
    GLib.timeout_add(int(seconds * 1000), loop.quit)
    loop.run()


def sample_items(clicked=None, header="2 dev ports in use"):
    return [
        MenuItem("header", header, enabled=False),
        MenuItem("sep", separator=True),
        MenuItem(
            "entry:5173",
            "5173  Vite",
            children=[MenuItem("open:5173", "Open http://localhost:5173", on_click=clicked)],
        ),
        MenuItem("quit", "Quit"),
    ]


def layout_of(client, server):
    revision, (root_id, props, children) = call(
        client, server, "GetLayout", GLib.Variant("(iias)", (0, -1, [])), "(u(ia{sv}av))"
    )
    return revision, root_id, props, children


def test_layout_has_submenus_and_separators(buses) -> None:
    server, client = buses
    menu = DBusMenu(server)
    menu.set_items(sample_items())

    _revision, root_id, props, children = layout_of(client, server)

    assert root_id == 0 and props["children-display"] == "submenu"
    header, separator, entry, quit_item = children
    assert header[1]["label"] == "2 dev ports in use" and header[1]["enabled"] is False
    assert separator[1] == {"type": "separator"}
    assert entry[1]["children-display"] == "submenu"
    assert entry[2][0][1]["label"] == "Open http://localhost:5173"
    assert quit_item[2] == []


def test_ids_are_stable_and_label_changes_are_announced(buses) -> None:
    server, client = buses
    menu = DBusMenu(server)
    menu.set_items(sample_items())
    revision, _, _, children = layout_of(client, server)
    ids = [child[0] for child in children]

    signals = []
    client.signal_subscribe(
        server.get_unique_name(),
        MENU_INTERFACE,
        None,
        MENU_PATH,
        None,
        Gio.DBusSignalFlags.NONE,
        lambda *args: signals.append((args[4], args[5].unpack())),
    )
    pump()
    menu.set_items(sample_items(header="1 dev port in use"))
    pump()

    new_revision, _, _, children = layout_of(client, server)
    assert [child[0] for child in children] == ids
    assert new_revision == revision  # same structure: no LayoutUpdated
    assert [name for name, _ in signals] == ["ItemsPropertiesUpdated"]
    updated, removed = signals[0][1]
    assert updated == [(ids[0], {"label": "1 dev port in use", "enabled": False})]
    assert removed == []


def test_structure_changes_bump_the_revision(buses) -> None:
    server, client = buses
    menu = DBusMenu(server)
    menu.set_items(sample_items())
    revision, *_ = layout_of(client, server)

    menu.set_items(sample_items()[:2] + sample_items()[3:])

    new_revision, _, _, children = layout_of(client, server)
    assert new_revision > revision
    assert len(children) == 3


def test_clicks_reach_the_callback(buses) -> None:
    server, client = buses
    clicks = []
    menu = DBusMenu(server)
    menu.set_items(sample_items(clicked=clicks.append))
    _, _, _, children = layout_of(client, server)
    open_id = children[2][2][0][0]

    call(
        client,
        server,
        "Event",
        GLib.Variant("(isvu)", (open_id, "clicked", GLib.Variant("i", 0), 1234)),
        None,
    )
    pump()

    assert clicks == [1234]


def test_group_properties_and_unknown_items(buses) -> None:
    server, client = buses
    menu = DBusMenu(server)
    menu.set_items(sample_items())
    _, _, _, children = layout_of(client, server)
    quit_id = children[3][0]

    (props,) = call(
        client,
        server,
        "GetGroupProperties",
        GLib.Variant("(aias)", ([quit_id], ["label"])),
        "(a(ia{sv}))",
    )
    assert props == [(quit_id, {"label": "Quit"})]

    with pytest.raises(GLib.Error):
        call(client, server, "GetLayout", GLib.Variant("(iias)", (999, -1, [])), None)


@pytest.mark.parametrize(
    ("label", "gnome", "escaped"),
    [
        ("my_app", True, "my__app"),
        ("my_cool_app", True, "my__cool_app"),
        ("my_cool_app", False, "my__cool__app"),
        ("plain", True, "plain"),
    ],
)
def test_escape_label(label: str, gnome: bool, escaped: str) -> None:
    assert escape_label(label, gnome) == escaped
