from __future__ import annotations

import os
from pathlib import Path

import pytest
from fakeproc import BOOT_TIME, CLK_TCK, PAGE_SIZE, FakeProc, encode_address, table_line

from portglance.core import procfs
from portglance.core.model import TCP, UDP


@pytest.mark.parametrize(
    ("address", "port"),
    [
        ("127.0.0.1", 3000),
        ("0.0.0.0", 8000),
        ("192.168.1.20", 5173),
        ("::", 5432),
        ("::1", 6379),
        ("fe80::1c2b:3aff:fe4d:5e6f", 22),
        ("::ffff:127.0.0.1", 8080),
    ],
)
def test_decode_address_round_trip(address: str, port: int) -> None:
    assert procfs.decode_address(encode_address(address, port)) == (address, port)


def test_parse_tcp_keeps_only_listening_sockets() -> None:
    text = (
        "header\n"
        + table_line(0, "127.0.0.1", 3000, inode=101, uid=1000)
        + table_line(1, "127.0.0.1", 51234, inode=102, uid=1000, state=0x01)  # ESTABLISHED
        + table_line(2, "127.0.0.1", 8000, inode=0, uid=1000)  # no inode
        + "garbage line\n"
    )

    sockets = list(procfs.parse_net_table(text, TCP, 4))

    assert [(s.port, s.inode, s.uid) for s in sockets] == [(3000, 101, 1000)]


def test_parse_udp_keeps_only_unconnected_sockets() -> None:
    text = (
        "header\n"
        + table_line(0, "0.0.0.0", 5353, inode=201, uid=0, state=0x07)
        + table_line(1, "0.0.0.0", 40000, inode=202, uid=0, state=0x07, remote=("1.1.1.1", 53))
        + table_line(2, "0.0.0.0", 41000, inode=203, uid=0, state=0x01, remote=("1.1.1.1", 53))
    )

    sockets = list(procfs.parse_net_table(text, UDP, 4))

    assert [s.port for s in sockets] == [5353]


def test_read_listening_sockets_reads_all_tables(fake_proc: FakeProc) -> None:
    fake_proc.add_socket(3000)
    fake_proc.add_socket(3000, address="::1")
    fake_proc.add_socket(5353, proto="udp", address="0.0.0.0")

    tcp_only = procfs.read_listening_sockets(fake_proc.path, (TCP,))
    everything = procfs.read_listening_sockets(fake_proc.path)

    assert sorted((s.proto, s.family, s.address) for s in tcp_only) == [
        ("tcp", 4, "127.0.0.1"),
        ("tcp", 6, "::1"),
    ]
    assert len(everything) == 3


def test_missing_tables_are_skipped(tmp_path: Path) -> None:
    (tmp_path / "net").mkdir()
    assert procfs.read_listening_sockets(str(tmp_path)) == []


def test_ephemeral_range(fake_proc: FakeProc, tmp_path: Path) -> None:
    assert procfs.ephemeral_port_range(fake_proc.path) == (32768, 60999)
    assert procfs.ephemeral_port_range(str(tmp_path / "nope")) == procfs.DEFAULT_EPHEMERAL_RANGE


def test_parse_stat_handles_tricky_comm() -> None:
    text = "4242 (weird) name (x)) S 77 4242 4242 0 -1 0 0 0 0 0 0 0 0 0 20 0 1 0 31337 0 25 0"

    comm, ppid, start_ticks, rss = procfs.parse_stat(text)

    assert comm == "weird) name (x)"
    assert (ppid, start_ticks, rss) == (77, 31337, 25 * PAGE_SIZE)


def test_read_process(fake_proc: FakeProc, tmp_path: Path) -> None:
    project = tmp_path / "work"
    project.mkdir()
    fake_proc.add_process(
        900,
        comm="node",
        cmdline=("node", "node_modules/.bin/vite", "--port", "5173"),
        cwd=str(project),
        ppid=850,
        start_ticks=12_000,
        rss_pages=1000,
    )

    info = procfs.read_process(900, fake_proc.path)

    assert info is not None
    assert info.name == "node"
    assert info.ppid == 850
    assert info.cmdline == ("node", "node_modules/.bin/vite", "--port", "5173")
    assert info.cwd == str(project)
    assert info.exe == "/usr/bin/node"
    assert info.rss == 1000 * PAGE_SIZE
    assert info.start_time == pytest.approx(BOOT_TIME + 12_000 / CLK_TCK)
    assert info.uid == os.geteuid()


def test_paths_that_are_not_utf8_can_be_displayed(fake_proc: FakeProc) -> None:
    fake_proc.add_process(901, comm="node", cwd=os.fsdecode(b"/srv/caf\xe9"))

    info = procfs.read_process(901, fake_proc.path)

    assert info is not None
    assert info.cwd == "/srv/caf\ufffd"  # GTK and D-Bus reject "caf\udce9"


def test_read_process_that_vanished(fake_proc: FakeProc) -> None:
    assert procfs.read_process(424242, fake_proc.path) is None


def test_truncated_comm_is_expanded_from_cmdline(fake_proc: FakeProc) -> None:
    fake_proc.add_process(
        901,
        comm="jupyter-noteboo",
        cmdline=("/usr/bin/python3", "/home/dev/.local/bin/jupyter-notebook"),
    )

    info = procfs.read_process(901, fake_proc.path)

    assert info is not None
    assert info.name == "jupyter-notebook"


class TestSocketOwnerResolver:
    def test_resolves_inodes_to_pids(self, fake_proc: FakeProc) -> None:
        web = fake_proc.add_socket(3000)
        api = fake_proc.add_socket(8000)
        fake_proc.add_process(100, inodes=[web])
        fake_proc.add_process(200, inodes=[api])
        fake_proc.add_process(300)  # no sockets

        resolver = procfs.SocketOwnerResolver(fake_proc.path)

        assert resolver.resolve({web, api}) == {web: (100,), api: (200,)}

    def test_shared_socket_lists_every_holder(self, fake_proc: FakeProc) -> None:
        inode = fake_proc.add_socket(8000)
        for pid in (500, 501, 502):
            fake_proc.add_process(pid, comm="gunicorn", ppid=500 if pid != 500 else 1)
            fake_proc.add_fd(pid, inode)

        resolver = procfs.SocketOwnerResolver(fake_proc.path)

        assert resolver.resolve({inode}) == {inode: (500, 501, 502)}

    def test_walks_only_for_new_sockets(self, fake_proc: FakeProc) -> None:
        first = fake_proc.add_socket(3000)
        fake_proc.add_process(100, inodes=[first])
        resolver = procfs.SocketOwnerResolver(fake_proc.path)

        resolver.resolve({first})
        resolver.resolve({first})
        resolver.resolve({first})
        assert resolver.walks == 1

        second = fake_proc.add_socket(4000)
        fake_proc.add_process(200, inodes=[second])
        assert resolver.resolve({first, second}) == {first: (100,), second: (200,)}
        assert resolver.walks == 2

    def test_unresolvable_sockets_do_not_cause_repeated_walks(self, fake_proc: FakeProc) -> None:
        orphan = fake_proc.add_socket(631, uid=0)  # e.g. cupsd, owned by root
        resolver = procfs.SocketOwnerResolver(fake_proc.path)

        assert resolver.resolve({orphan}) == {orphan: ()}
        resolver.resolve({orphan})
        assert resolver.walks == 1

    def test_rewalks_when_owner_exits(self, fake_proc: FakeProc) -> None:
        inode = fake_proc.add_socket(8000)
        fake_proc.add_process(100, inodes=[inode])
        fake_proc.add_process(101, ppid=100)
        resolver = procfs.SocketOwnerResolver(fake_proc.path)
        assert resolver.resolve({inode}) == {inode: (100,)}

        # The parent exits and its child inherited the listening socket.
        fake_proc.add_fd(101, inode)
        fake_proc.remove_process(100)

        assert resolver.resolve({inode}) == {inode: (101,)}
        assert resolver.walks == 2

    def test_skips_other_users_processes(self, fake_proc: FakeProc) -> None:
        inode = fake_proc.add_socket(3000)
        fake_proc.add_process(100, inodes=[inode])
        other_uid = os.geteuid() + 4242

        resolver = procfs.SocketOwnerResolver(fake_proc.path, uid=other_uid)

        assert resolver.resolve({inode}) == {inode: ()}
