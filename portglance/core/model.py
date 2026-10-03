"""Plain data types shared by the scanner, the CLI and the UI."""

from __future__ import annotations

import ipaddress
from dataclasses import dataclass, field

TCP = "tcp"
UDP = "udp"

#: The port is only reachable from this machine (loopback addresses).
SCOPE_LOCAL = "local"
#: The port is bound to a wildcard or LAN address and reachable from the network.
SCOPE_NETWORK = "network"


@dataclass(frozen=True, slots=True)
class ListeningSocket:
    """One row of ``/proc/net/{tcp,tcp6,udp,udp6}`` in a listening state."""

    proto: str
    family: int  # 4 or 6
    address: str
    port: int
    inode: int
    uid: int


@dataclass(slots=True)
class ProcessInfo:
    pid: int
    ppid: int
    name: str  # /proc/<pid>/comm
    cmdline: tuple[str, ...]
    exe: str | None
    cwd: str | None
    uid: int
    start_time: float  # Unix timestamp
    start_ticks: int  # raw starttime from /proc/<pid>/stat, identifies the process
    rss: int  # resident memory in bytes

    @property
    def command(self) -> str:
        return " ".join(self.cmdline) if self.cmdline else f"[{self.name}]"


@dataclass(frozen=True, slots=True)
class Project:
    """A directory a process belongs to, usually a git repository root."""

    name: str
    root: str
    kind: str  # "git", "marker" or "compose"
    branch: str | None = None


@dataclass(frozen=True, slots=True)
class PublishedPort:
    host_ip: str
    host_port: int
    container_port: int
    proto: str


@dataclass(slots=True)
class ContainerInfo:
    id: str
    name: str
    image: str
    state: str
    status: str
    runtime: str = "docker"  # "docker" or "podman"
    started_at: float | None = None
    pid: int | None = None
    memory: int | None = None
    labels: dict[str, str] = field(default_factory=dict)
    ports: list[PublishedPort] = field(default_factory=list)

    @property
    def short_id(self) -> str:
        return self.id[:12]

    @property
    def compose_project(self) -> str | None:
        return self.labels.get("com.docker.compose.project")

    @property
    def compose_service(self) -> str | None:
        return self.labels.get("com.docker.compose.service")

    @property
    def compose_dir(self) -> str | None:
        return self.labels.get("com.docker.compose.project.working_dir")


@dataclass(slots=True)
class PortEntry:
    """A port as shown to the user: one listener, process and project."""

    proto: str
    port: int
    addresses: list[str] = field(default_factory=list)
    uid: int | None = None
    pid: int | None = None
    pids: list[int] = field(default_factory=list)
    process: ProcessInfo | None = None
    container: ContainerInfo | None = None
    container_port: int | None = None
    project: Project | None = None
    subpath: str | None = None
    framework: str | None = None
    is_dev: bool = False
    pinned: bool = False
    http: bool = False

    @property
    def key(self) -> str:
        """Stable identity used to diff entries between refreshes."""
        if self.container is not None:
            owner = f"c{self.container.id[:12]}"
        elif self.pid is not None:
            owner = f"p{self.pid}"
        else:
            owner = f"u{self.uid}"
        return f"{self.proto}:{self.port}:{owner}"

    @property
    def name(self) -> str:
        if self.container is not None:
            return self.container.name
        if self.process is not None:
            return self.process.name
        return "unknown"

    @property
    def scope(self) -> str:
        return scope_of(self.addresses)

    @property
    def url(self) -> str:
        return f"http://localhost:{self.port}"

    @property
    def start_time(self) -> float | None:
        if self.container is not None:
            return self.container.started_at
        if self.process is not None:
            return self.process.start_time
        return None

    @property
    def memory(self) -> int | None:
        if self.container is not None:
            return self.container.memory
        if self.process is not None:
            return self.process.rss
        return None

    @property
    def killable(self) -> bool:
        return self.container is not None or self.pid is not None


def is_loopback(address: str) -> bool:
    try:
        ip = ipaddress.ip_address(address.split("%", 1)[0])
    except ValueError:
        return False
    mapped = getattr(ip, "ipv4_mapped", None)
    return (mapped or ip).is_loopback


def scope_of(addresses: list[str]) -> str:
    """``local`` if every bind address is loopback, otherwise ``network``."""
    if addresses and all(is_loopback(a) for a in addresses):
        return SCOPE_LOCAL
    return SCOPE_NETWORK
