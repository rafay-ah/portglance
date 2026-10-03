"""Read listening sockets and process details straight from ``/proc``.

This is deliberately lighter than ``psutil.net_connections()``, which walks
every file descriptor of every process on each call. Here the socket tables
are parsed on each refresh (a few kilobytes of text), and the expensive
socket-to-PID walk only runs when a listening socket appears that has not
been seen before.
"""

from __future__ import annotations

import ipaddress
import os
import sys
from collections.abc import Iterable, Iterator

from .model import TCP, UDP, ListeningSocket, ProcessInfo

TCP_LISTEN = 0x0A
#: TCP_CLOSE, which is how a bound but unconnected UDP socket is reported.
UDP_UNCONNECTED = 0x07

NET_TABLES = (
    ("tcp", TCP, 4),
    ("tcp6", TCP, 6),
    ("udp", UDP, 4),
    ("udp6", UDP, 6),
)

DEFAULT_EPHEMERAL_RANGE = (32768, 60999)
COMM_MAX = 15  # the kernel truncates /proc/<pid>/comm to 15 characters

_CLK_TCK = os.sysconf("SC_CLK_TCK")
_PAGE_SIZE = os.sysconf("SC_PAGE_SIZE")


# --------------------------------------------------------------------------
# Socket tables
# --------------------------------------------------------------------------


def decode_address(hex_addr: str) -> tuple[str, int]:
    """Decode ``0100007F:0BB8`` (as printed by the kernel) to ``("127.0.0.1", 3000)``.

    The kernel prints each 32-bit word of the address in host byte order,
    so the words have to be converted back using the native byte order.
    """
    host, port = hex_addr.split(":")
    raw = b"".join(
        int(host[i : i + 8], 16).to_bytes(4, sys.byteorder) for i in range(0, len(host), 8)
    )
    return str(ipaddress.ip_address(raw)), int(port, 16)


def parse_net_table(text: str, proto: str, family: int) -> Iterator[ListeningSocket]:
    """Yield the listening sockets of one ``/proc/net/*`` table."""
    lines = text.splitlines()
    for line in lines[1:]:
        fields = line.split()
        if len(fields) < 10:
            continue
        try:
            state = int(fields[3], 16)
            if proto == TCP and state != TCP_LISTEN:
                continue
            if proto == UDP and (state != UDP_UNCONNECTED or not fields[2].endswith(":0000")):
                continue
            inode = int(fields[9])
            if inode == 0:
                continue
            address, port = decode_address(fields[1])
            uid = int(fields[7])
        except ValueError:
            continue
        yield ListeningSocket(proto, family, address, port, inode, uid)


def read_listening_sockets(
    proc_root: str = "/proc", protos: Iterable[str] = (TCP, UDP)
) -> list[ListeningSocket]:
    wanted = set(protos)
    sockets: list[ListeningSocket] = []
    for table, proto, family in NET_TABLES:
        if proto not in wanted:
            continue
        try:
            with open(f"{proc_root}/net/{table}", encoding="ascii", errors="replace") as fh:
                text = fh.read()
        except OSError:
            continue  # e.g. IPv6 disabled
        sockets.extend(parse_net_table(text, proto, family))
    return sockets


def ephemeral_port_range(proc_root: str = "/proc") -> tuple[int, int]:
    try:
        with open(f"{proc_root}/sys/net/ipv4/ip_local_port_range", encoding="ascii") as fh:
            low, high = (int(v) for v in fh.read().split()[:2])
        return low, high
    except (OSError, ValueError):
        return DEFAULT_EPHEMERAL_RANGE


# --------------------------------------------------------------------------
# Socket inode -> PID
# --------------------------------------------------------------------------


def list_pids(proc_root: str = "/proc") -> list[int]:
    try:
        return [int(name) for name in os.listdir(proc_root) if name.isdigit()]
    except OSError:
        return []


def pid_exists(pid: int, proc_root: str = "/proc") -> bool:
    return os.path.exists(f"{proc_root}/{pid}")


class SocketOwnerResolver:
    """Maps socket inodes to the PIDs holding them, with caching.

    Finding the owner of a socket means reading every ``/proc/<pid>/fd/*``
    link, so results are cached per inode. A full walk happens only when an
    inode shows up that has not been seen before, or when a cached owner has
    exited. Sockets owned by other users cannot be resolved without root;
    those are cached as unresolvable so they do not trigger a walk on every
    refresh.
    """

    def __init__(self, proc_root: str = "/proc", uid: int | None = None) -> None:
        self.proc_root = proc_root
        self.uid = os.geteuid() if uid is None else uid
        self._owners: dict[int, tuple[int, ...]] = {}
        self.walks = 0  # number of fd walks performed, for diagnostics and tests

    def resolve(self, inodes: Iterable[int]) -> dict[int, tuple[int, ...]]:
        wanted = set(inodes)
        for inode in list(self._owners):
            if inode not in wanted:
                del self._owners[inode]
            elif any(not pid_exists(pid, self.proc_root) for pid in self._owners[inode]):
                del self._owners[inode]  # an owner exited; the socket may have moved

        missing = wanted.difference(self._owners)
        if missing:
            found = self._walk(missing)
            for inode in missing:
                self._owners[inode] = tuple(sorted(found.get(inode, ())))
        return {inode: self._owners[inode] for inode in wanted}

    def forget(self, inode: int) -> None:
        self._owners.pop(inode, None)

    def _walk(self, targets: set[int]) -> dict[int, list[int]]:
        self.walks += 1
        found: dict[int, list[int]] = {}
        is_root = self.uid == 0
        for pid in list_pids(self.proc_root):
            base = f"{self.proc_root}/{pid}"
            if not is_root:
                try:
                    if os.stat(base).st_uid != self.uid:
                        continue
                except OSError:
                    continue
            try:
                dir_fd = os.open(f"{base}/fd", os.O_RDONLY | os.O_DIRECTORY)
            except OSError:
                continue
            try:
                for name in os.listdir(dir_fd):
                    try:
                        target = os.readlink(name, dir_fd=dir_fd)
                    except OSError:
                        continue
                    if target.startswith("socket:["):
                        try:
                            inode = int(target[8:-1])
                        except ValueError:
                            continue
                        if inode in targets:
                            found.setdefault(inode, []).append(pid)
            except OSError:
                pass
            finally:
                os.close(dir_fd)
        return found


# --------------------------------------------------------------------------
# Processes
# --------------------------------------------------------------------------


def read_boot_time(proc_root: str = "/proc") -> float:
    try:
        with open(f"{proc_root}/stat", encoding="ascii") as fh:
            for line in fh:
                if line.startswith("btime "):
                    return float(line.split()[1])
    except (OSError, ValueError):
        pass
    return 0.0


def parse_stat(text: str) -> tuple[str, int, int, int]:
    """Parse ``/proc/<pid>/stat`` into ``(comm, ppid, start_ticks, rss_bytes)``.

    ``comm`` may contain spaces and parentheses, so it is located using the
    *last* closing parenthesis.
    """
    lpar = text.index("(")
    rpar = text.rindex(")")
    comm = text[lpar + 1 : rpar]
    rest = text[rpar + 2 :].split()
    # rest[0] is field 3 (state) of proc(5); field N is rest[N - 3].
    ppid = int(rest[1])
    start_ticks = int(rest[19])
    rss_pages = int(rest[21])
    return comm, ppid, start_ticks, rss_pages * _PAGE_SIZE


def read_stat(pid: int, proc_root: str = "/proc") -> tuple[str, int, int, int] | None:
    try:
        with open(f"{proc_root}/{pid}/stat", encoding="utf-8", errors="replace") as fh:
            return parse_stat(fh.read())
    except (OSError, ValueError, IndexError):
        return None


def _readlink(path: str) -> str | None:
    try:
        target = os.readlink(path)
    except OSError:
        return None
    if target.endswith(" (deleted)"):
        target = target[: -len(" (deleted)")]
    return target


def read_cmdline(pid: int, proc_root: str = "/proc") -> tuple[str, ...]:
    try:
        with open(f"{proc_root}/{pid}/cmdline", "rb") as fh:
            raw = fh.read()
    except OSError:
        return ()
    args = raw.split(b"\0")
    while args and not args[-1]:
        args.pop()
    return tuple(a.decode("utf-8", errors="replace") for a in args)


def display_name(comm: str, cmdline: tuple[str, ...]) -> str:
    """Undo the kernel's 15 character truncation of ``comm`` when possible."""
    if len(comm) >= COMM_MAX:
        for arg in cmdline[:3]:
            base = os.path.basename(arg)
            if base.startswith(comm) and len(base) > len(comm):
                return base
    return comm


def read_process(
    pid: int, proc_root: str = "/proc", boot_time: float | None = None
) -> ProcessInfo | None:
    stat = read_stat(pid, proc_root)
    if stat is None:
        return None
    comm, ppid, start_ticks, rss = stat
    try:
        uid = os.stat(f"{proc_root}/{pid}").st_uid
    except OSError:
        return None
    if boot_time is None:
        boot_time = read_boot_time(proc_root)
    cmdline = read_cmdline(pid, proc_root)
    return ProcessInfo(
        pid=pid,
        ppid=ppid,
        name=display_name(comm, cmdline),
        cmdline=cmdline,
        exe=_readlink(f"{proc_root}/{pid}/exe"),
        cwd=_readlink(f"{proc_root}/{pid}/cwd"),
        uid=uid,
        start_time=boot_time + start_ticks / _CLK_TCK,
        start_ticks=start_ticks,
        rss=rss,
    )
