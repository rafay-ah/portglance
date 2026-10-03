"""Combine sockets, processes, projects and containers into port entries."""

from __future__ import annotations

import os
import time
from collections.abc import Iterable
from dataclasses import dataclass, field, replace

from . import procfs
from .classify import is_dev_port
from .docker import DockerSource, DockerStatus
from .frameworks import detect_framework, looks_like_http
from .model import TCP, UDP, ContainerInfo, PortEntry, ProcessInfo, Project, PublishedPort
from .projects import ProjectResolver, Resolution

#: Host-side processes that forward published container ports.
CONTAINER_FORWARDERS = frozenset(
    {"docker-proxy", "rootlesskit", "rootlessport", "pasta", "pasta.avx2", "slirp4netns"}
)


@dataclass(slots=True)
class Snapshot:
    entries: list[PortEntry]
    taken_at: float
    docker: DockerStatus | None = None

    @property
    def dev_entries(self) -> list[PortEntry]:
        return [e for e in self.entries if e.is_dev]

    def find(self, port: int) -> list[PortEntry]:
        return [e for e in self.entries if e.port == port]


@dataclass(slots=True)
class _Process:
    info: ProcessInfo
    resolution: Resolution
    framework: str | None


@dataclass(slots=True)
class _Group:
    uid: int
    pid: int | None
    addresses: set[str] = field(default_factory=set)
    pids: set[int] = field(default_factory=set)


class Scanner:
    """Produces :class:`Snapshot` objects; keeps caches between calls.

    A scan reads the kernel's socket tables, resolves the owners of sockets
    it has not seen before, refreshes memory usage of the (few) listening
    processes and merges in Docker containers. On a quiet system a scan
    costs a handful of small file reads.
    """

    def __init__(
        self,
        *,
        proc_root: str = "/proc",
        uid: int | None = None,
        home: str | None = None,
        docker: DockerSource | None = None,
        clock=time.time,
    ) -> None:
        self.proc_root = proc_root
        self.uid = os.geteuid() if uid is None else uid
        self.owners = procfs.SocketOwnerResolver(proc_root, self.uid)
        self.projects = ProjectResolver(home)
        self.docker = docker
        self.ephemeral = procfs.ephemeral_port_range(proc_root)
        self._clock = clock
        self._boot_time = procfs.read_boot_time(proc_root)
        self._processes: dict[int, _Process] = {}

    def scan(self, pinned: Iterable[int] = (), *, include_udp: bool = False) -> Snapshot:
        protos = (TCP, UDP) if include_udp else (TCP,)
        sockets = procfs.read_listening_sockets(self.proc_root, protos)
        owners = self.owners.resolve({s.inode for s in sockets})
        processes = self._refresh_processes({pid for pids in owners.values() for pid in pids})

        groups: dict[tuple[str, int, int], _Group] = {}
        for sock in sockets:
            pids = [pid for pid in owners.get(sock.inode, ()) if pid in processes]
            main = _main_pid(pids, processes)
            owner_key = main if main is not None else -1 - sock.uid
            group = groups.get((sock.proto, sock.port, owner_key))
            if group is None:
                group = groups[(sock.proto, sock.port, owner_key)] = _Group(sock.uid, main)
            group.addresses.add(sock.address)
            group.pids.update(pids)

        entries = [
            self._entry(proto, port, group, processes) for (proto, port, _), group in groups.items()
        ]

        docker_status = None
        if self.docker is not None:
            docker_status, containers = self.docker.snapshot()
            entries = self._merge_containers(entries, containers)

        pinned_ports = set(pinned)
        for entry in entries:
            entry.pinned = entry.port in pinned_ports
            entry.http = looks_like_http(
                entry.port,
                entry.proto,
                entry.framework if entry.container is None else None,
                entry.container.image if entry.container else None,
                entry.container_port,
            )
            entry.is_dev = is_dev_port(entry, uid=self.uid, ephemeral=self.ephemeral)

        entries.sort(key=lambda e: (e.port, e.proto, e.key))
        return Snapshot(entries, self._clock(), docker_status)

    # -- processes ----------------------------------------------------------

    def _refresh_processes(self, pids: set[int]) -> dict[int, _Process]:
        fresh: dict[int, _Process] = {}
        for pid in pids:
            stat = procfs.read_stat(pid, self.proc_root)
            if stat is None:
                continue
            _comm, _ppid, start_ticks, rss = stat
            cached = self._processes.get(pid)
            if cached is None or cached.info.start_ticks != start_ticks:
                info = procfs.read_process(pid, self.proc_root, self._boot_time)
                if info is None:
                    continue
                cached = _Process(
                    info,
                    self.projects.resolve(info.cwd, info.cmdline),
                    detect_framework(info.name, info.cmdline),
                )
            else:
                cached.info.rss = rss
                project = cached.resolution.project
                if project is not None and project.kind == "git":
                    branch = self.projects.branch(project.root)
                    if branch != project.branch:
                        cached.resolution = replace(
                            cached.resolution, project=replace(project, branch=branch)
                        )
            fresh[pid] = cached
        self._processes = fresh
        return fresh

    def _entry(
        self, proto: str, port: int, group: _Group, processes: dict[int, _Process]
    ) -> PortEntry:
        entry = PortEntry(
            proto=proto,
            port=port,
            addresses=sorted(group.addresses, key=_address_order),
            uid=group.uid,
            pid=group.pid,
            pids=sorted(group.pids),
        )
        if group.pid is not None:
            process = processes[group.pid]
            entry.process = process.info
            entry.project = process.resolution.project
            entry.subpath = process.resolution.subpath
            entry.framework = process.framework
        return entry

    # -- containers ---------------------------------------------------------

    def _merge_containers(
        self, entries: list[PortEntry], containers: list[ContainerInfo]
    ) -> list[PortEntry]:
        if not containers:
            return entries
        index: dict[tuple[str, int], list[PortEntry]] = {}
        for entry in entries:
            index.setdefault((entry.proto, entry.port), []).append(entry)

        absorbed: set[int] = set()
        merged: list[PortEntry] = []
        for container in containers:
            project = self._container_project(container)
            published: dict[tuple[str, int], list[PublishedPort]] = {}
            for port in container.ports:
                published.setdefault((port.proto, port.host_port), []).append(port)
            for (proto, host_port), ports in published.items():
                forwarders = [e for e in index.get((proto, host_port), []) if _is_forwarder(e)]
                absorbed.update(id(e) for e in forwarders)
                addresses = {a for e in forwarders for a in e.addresses} or {
                    p.host_ip for p in ports
                }
                merged.append(
                    PortEntry(
                        proto=proto,
                        port=host_port,
                        addresses=sorted(addresses, key=_address_order),
                        uid=forwarders[0].uid if forwarders else None,
                        pid=container.pid,
                        container=container,
                        container_port=ports[0].container_port,
                        project=project,
                        framework=container.runtime.capitalize(),
                    )
                )
        return [e for e in entries if id(e) not in absorbed] + merged

    def _container_project(self, container: ContainerInfo) -> Project | None:
        name = container.compose_project
        directory = container.compose_dir
        if directory:
            return self.projects.resolve_directory(directory, fallback_name=name)
        if name:
            return Project(name, "", "compose")
        return None


def _main_pid(pids: list[int], processes: dict[int, _Process]) -> int | None:
    """The process at the top of the tree that shares a socket (e.g. the pre-fork master)."""
    if not pids:
        return None
    members = set(pids)
    roots = [pid for pid in pids if processes[pid].info.ppid not in members]
    return min(roots) if roots else min(pids)


def _is_forwarder(entry: PortEntry) -> bool:
    if entry.process is None:
        return entry.uid == 0
    return entry.process.name in CONTAINER_FORWARDERS


def _address_order(address: str) -> tuple[int, str]:
    return (":" in address, address)
