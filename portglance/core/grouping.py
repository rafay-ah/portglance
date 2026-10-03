"""Arrange port entries into the sections shown by the UI."""

from __future__ import annotations

import time
from collections.abc import Iterable
from dataclasses import dataclass, field

from .formatting import describe_addresses, format_bytes, format_duration, shorten_path, user_name
from .frameworks import GENERIC_FRAMEWORKS
from .model import PortEntry, Project

PINNED = "pinned"
PROJECT = "project"
CONTAINERS = "containers"
OTHER = "other"
SYSTEM = "system"

_ORDER = {PINNED: 0, PROJECT: 1, CONTAINERS: 2, OTHER: 3, SYSTEM: 4}


@dataclass(slots=True)
class Section:
    id: str
    kind: str
    title: str
    subtitle: str = ""
    project: Project | None = None
    entries: list[PortEntry] = field(default_factory=list)
    free_ports: list[int] = field(default_factory=list)

    @property
    def signature(self) -> tuple:
        return (self.id, tuple(e.key for e in self.entries), tuple(self.free_ports))


def matches(entry: PortEntry, query: str) -> bool:
    """Case-insensitive search over port, names, framework, project and command."""
    query = query.strip().lower()
    if not query:
        return True
    haystack = [str(entry.port), entry.name, entry.framework or "", entry.proto]
    if entry.project is not None:
        haystack += [entry.project.name, entry.project.branch or "", entry.subpath or ""]
    if entry.container is not None:
        haystack += [entry.container.image, entry.container.compose_service or ""]
    if entry.process is not None:
        haystack += [entry.process.command, entry.process.cwd or ""]
    text = " ".join(haystack).lower()
    return all(word in text for word in query.split())


def group_entries(
    entries: Iterable[PortEntry],
    *,
    pinned_ports: Iterable[int] = (),
    include_system: bool = False,
    query: str = "",
    home: str | None = None,
) -> list[Section]:
    entries = [e for e in entries if matches(e, query)]
    pinned_set = sorted(set(pinned_ports))
    sections: dict[str, Section] = {}

    def section(key: str, kind: str, title: str, subtitle: str = "", project=None) -> Section:
        if key not in sections:
            sections[key] = Section(key, kind, title, subtitle, project)
        return sections[key]

    pinned = section(PINNED, PINNED, "Pinned", "Always shown, even when free")
    busy_pinned = set()
    for entry in entries:
        if entry.pinned:
            pinned.entries.append(entry)
            busy_pinned.add(entry.port)
            continue
        if not entry.is_dev and not include_system:
            continue
        if not entry.is_dev:
            section(SYSTEM, SYSTEM, "System & apps", "Services of other users and desktop apps")
            sections[SYSTEM].entries.append(entry)
        elif entry.project is not None:
            project = entry.project
            key = f"{PROJECT}:{project.root or project.name}"
            subtitle = shorten_path(project.root, home) if project.root else "Compose project"
            section(key, PROJECT, project.name, subtitle, project).entries.append(entry)
        elif entry.container is not None:
            runtime = entry.container.runtime.capitalize()
            section(CONTAINERS, CONTAINERS, "Containers", runtime).entries.append(entry)
        else:
            section(OTHER, OTHER, "Other dev servers", "Not inside a project folder")
            sections[OTHER].entries.append(entry)

    if not query.strip():
        pinned.free_ports = [p for p in pinned_set if p not in busy_pinned]
    else:
        pinned.free_ports = [
            p for p in pinned_set if p not in busy_pinned and query.strip() in str(p)
        ]

    result = [s for s in sections.values() if s.entries or s.free_ports]
    for s in result:
        s.entries.sort(key=lambda e: (e.port, e.proto, e.key))
    result.sort(key=lambda s: (_ORDER[s.kind], s.title.lower(), s.id))
    return result


def display_label(entry: PortEntry) -> str:
    """The friendliest short name: ``Vite``, ``uvicorn``, ``acme-web-db-1``."""
    if entry.container is not None:
        return entry.container.name
    if entry.framework and entry.framework not in GENERIC_FRAMEWORKS:
        return entry.framework
    if entry.process is not None:
        return entry.process.name
    return f"port {entry.port}"


def describe_entry(
    entry: PortEntry, now: float | None = None, *, with_project: bool = False
) -> str:
    """One-line details: ``PID 4100 · up 2h 14m · 182 MB · localhost``."""
    now = time.time() if now is None else now
    parts: list[str] = []
    if with_project and entry.project is not None:
        parts.append(entry.project.name + (f"/{entry.subpath}" if entry.subpath else ""))
    if entry.container is not None:
        container = entry.container
        parts.append(container.image)
        if entry.container_port:
            parts.append(f"→ {entry.container_port}")
    elif entry.pid is not None:
        workers = len(entry.pids) - 1
        parts.append(f"PID {entry.pid}" + (f" +{workers}" if workers > 0 else ""))
    else:
        parts.append(f"owned by {user_name(entry.uid)}")
    if entry.start_time:
        parts.append(f"up {format_duration(now - entry.start_time)}")
    if entry.memory:
        parts.append(format_bytes(entry.memory))
    if entry.subpath and not with_project:
        parts.append(entry.subpath)
    parts.append(describe_addresses(entry.addresses))
    return " · ".join(parts)
