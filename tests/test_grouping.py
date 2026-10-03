from __future__ import annotations

from portglance.core.grouping import (
    CONTAINERS,
    OTHER,
    PINNED,
    PROJECT,
    SYSTEM,
    describe_entry,
    display_label,
    group_entries,
    matches,
)
from portglance.core.model import ContainerInfo, PortEntry, ProcessInfo, Project

NOW = 1_800_000_000.0


def process(name: str = "node", cwd: str = "/home/dev/code/web", **kwargs) -> ProcessInfo:
    defaults = dict(
        pid=100,
        ppid=1,
        name=name,
        cmdline=(name, "server.js"),
        exe=f"/usr/bin/{name}",
        cwd=cwd,
        uid=1000,
        start_time=NOW - 8040,
        start_ticks=1,
        rss=182_000_000,
    )
    defaults.update(kwargs)
    return ProcessInfo(**defaults)


def entry(port: int, *, project: str | None = "web", dev: bool = True, **kwargs) -> PortEntry:
    proj = Project(project, f"/home/dev/code/{project}", "git", "main") if project else None
    values = dict(
        proto="tcp",
        port=port,
        addresses=["127.0.0.1"],
        uid=1000,
        pid=port,
        pids=[port],
        process=process(pid=port),
        project=proj,
        is_dev=dev,
    )
    values.update(kwargs)
    return PortEntry(**values)


def test_sections_are_ordered_pinned_projects_containers_other_system() -> None:
    container = ContainerInfo("c" * 64, "cache", "redis:7", "running", "Up")
    entries = [
        entry(8000, project="zeta"),
        entry(5173, project="alpha"),
        entry(3000, project="alpha", pinned=True),
        entry(6379, project=None, container=container, process=None, pid=None),
        entry(8080, project=None),
        entry(631, project=None, dev=False, process=None, pid=None, uid=0),
    ]

    sections = group_entries(
        entries, pinned_ports=[3000, 9000], include_system=True, home="/home/dev"
    )

    assert [(s.kind, s.title) for s in sections] == [
        (PINNED, "Pinned"),
        (PROJECT, "alpha"),
        (PROJECT, "zeta"),
        (CONTAINERS, "Containers"),
        (OTHER, "Other dev servers"),
        (SYSTEM, "System & apps"),
    ]
    pinned = sections[0]
    assert [e.port for e in pinned.entries] == [3000]
    assert pinned.free_ports == [9000]
    assert sections[1].subtitle == "~/code/alpha"


def test_system_section_only_when_asked() -> None:
    entries = [entry(5173), entry(631, project=None, dev=False)]

    kinds = [s.kind for s in group_entries(entries)]

    assert kinds == [PROJECT]


def test_search_filters_entries_and_free_pins() -> None:
    entries = [entry(5173, project="acme-web", framework="Vite"), entry(8000, project="api")]

    sections = group_entries(entries, pinned_ports=[3000, 9000], query="vite")

    assert [e.port for s in sections for e in s.entries] == [5173]
    assert all(not s.free_ports for s in sections)
    assert matches(entries[1], "api 8000")
    assert not matches(entries[1], "api 9999")


def test_describe_entry() -> None:
    e = entry(5173, subpath="apps/web", addresses=["0.0.0.0", "::"])

    assert describe_entry(e, NOW) == "PID 5173 · up 2h 14m · 182 MB · apps/web · all interfaces"
    assert describe_entry(e, NOW, with_project=True).startswith("web/apps/web · PID 5173")


def test_describe_container_and_unknown_owner() -> None:
    container = ContainerInfo("c" * 64, "db", "postgres:16", "running", "Up", started_at=NOW - 30)
    docker = entry(5432, container=container, container_port=5432, process=None, pid=None)
    unknown = entry(631, project=None, process=None, pid=None, uid=0, dev=False)

    assert describe_entry(docker, NOW).startswith("postgres:16 · → 5432 · up 30s")
    assert describe_entry(unknown, NOW).startswith("owned by root")


def test_display_label() -> None:
    assert display_label(entry(5173, framework="Vite")) == "Vite"
    assert display_label(entry(3000, framework="Node.js")) == "node"
    container = ContainerInfo("c" * 64, "db", "postgres:16", "running", "Up")
    assert display_label(entry(5432, container=container)) == "db"
