"""Build a fake ``/proc`` tree so scanning can be tested without real processes."""

from __future__ import annotations

import ipaddress
import itertools
import os
import sys
from collections.abc import Sequence
from pathlib import Path

BOOT_TIME = 1_700_000_000
CLK_TCK = os.sysconf("SC_CLK_TCK")
PAGE_SIZE = os.sysconf("SC_PAGE_SIZE")

_HEADER = (
    "  sl  local_address rem_address   st tx_queue rx_queue tr tm->when retrnsmt"
    "   uid  timeout inode\n"
)


def encode_address(address: str, port: int) -> str:
    """Inverse of ``procfs.decode_address``: how the kernel prints an address."""
    packed = ipaddress.ip_address(address).packed
    words = (int.from_bytes(packed[i : i + 4], sys.byteorder) for i in range(0, len(packed), 4))
    return "".join(f"{w:08X}" for w in words) + f":{port:04X}"


def table_line(
    index: int,
    address: str,
    port: int,
    inode: int,
    uid: int,
    state: int = 0x0A,
    remote: tuple[str, int] | None = None,
) -> str:
    family_any = "::" if ":" in address else "0.0.0.0"
    rem_addr, rem_port = remote or (family_any, 0)
    return (
        f"{index:4d}: {encode_address(address, port)} {encode_address(rem_addr, rem_port)} "
        f"{state:02X} 00000000:00000000 00:00000000 00000000 {uid:5d}        0 {inode} 1 "
        "0000000000000000 100 0 0 10 0\n"
    )


class FakeProc:
    def __init__(self, root: Path) -> None:
        self.root = root
        self._inodes = itertools.count(40_000)
        self._lines: dict[str, list[str]] = {"tcp": [], "tcp6": [], "udp": [], "udp6": []}
        (root / "net").mkdir(parents=True)
        (root / "sys/net/ipv4").mkdir(parents=True)
        (root / "sys/net/ipv4/ip_local_port_range").write_text("32768\t60999\n")
        (root / "stat").write_text(f"cpu  1 2 3 4\nbtime {BOOT_TIME}\nprocesses 100\n")
        self.write_tables()

    @property
    def path(self) -> str:
        return str(self.root)

    def add_socket(
        self,
        port: int,
        *,
        proto: str = "tcp",
        address: str = "127.0.0.1",
        uid: int | None = None,
        state: int | None = None,
        inode: int | None = None,
        remote: tuple[str, int] | None = None,
    ) -> int:
        inode = next(self._inodes) if inode is None else inode
        table = proto + ("6" if ":" in address else "")
        if state is None:
            state = 0x0A if proto == "tcp" else 0x07
        uid = os.geteuid() if uid is None else uid
        lines = self._lines[table]
        lines.append(table_line(len(lines), address, port, inode, uid, state, remote))
        self.write_tables()
        return inode

    def remove_socket(self, inode: int) -> None:
        for lines in self._lines.values():
            lines[:] = [line for line in lines if f" {inode} " not in line]
        self.write_tables()

    def write_tables(self) -> None:
        for table, lines in self._lines.items():
            (self.root / "net" / table).write_text(_HEADER + "".join(lines))

    def add_process(
        self,
        pid: int,
        *,
        comm: str = "node",
        cmdline: Sequence[str] = ("node", "server.js"),
        cwd: str | None = None,
        ppid: int = 1,
        start_ticks: int = 5_000,
        rss_pages: int = 2_560,
        inodes: Sequence[int] = (),
    ) -> Path:
        proc = self.root / str(pid)
        (proc / "fd").mkdir(parents=True)
        (proc / "stat").write_text(
            f"{pid} ({comm}) S {ppid} {pid} {pid} 0 -1 4194560 0 0 0 0 0 0 0 0 20 0 1 0 "
            f"{start_ticks} 10000000 {rss_pages} 18446744073709551615 0 0 0 0 0 0 0 0 0 0\n"
        )
        (proc / "cmdline").write_bytes(b"".join(a.encode() + b"\0" for a in cmdline))
        (proc / "exe").symlink_to(f"/usr/bin/{comm}")
        if cwd is not None:
            (proc / "cwd").symlink_to(cwd)
        for number, inode in enumerate(inodes, start=3):
            self.add_fd(pid, inode, number)
        (proc / "fd" / "0").symlink_to("/dev/null")
        return proc

    def add_fd(self, pid: int, inode: int, number: int | None = None) -> None:
        fd_dir = self.root / str(pid) / "fd"
        number = number if number is not None else len(list(fd_dir.iterdir())) + 3
        (fd_dir / str(number)).symlink_to(f"socket:[{inode}]")

    def remove_process(self, pid: int) -> None:
        proc = self.root / str(pid)
        for path in sorted(proc.rglob("*"), key=lambda p: len(p.parts), reverse=True):
            if path.is_symlink() or path.is_file():
                path.unlink()
            else:
                path.rmdir()
        proc.rmdir()
