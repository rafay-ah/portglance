"""End-to-end: fake /proc -> socket owners -> processes -> projects -> entries."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from conftest import make_repo
from fakeproc import FakeProc

from portglance.core.docker import CONNECTED, DockerSource, DockerStatus
from portglance.core.model import SCOPE_LOCAL, SCOPE_NETWORK, ContainerInfo, PublishedPort
from portglance.core.scanner import Scanner

UID = os.geteuid()


@pytest.fixture
def scanner(fake_proc: FakeProc, home: Path) -> Scanner:
    return Scanner(proc_root=fake_proc.path, uid=UID, home=str(home))


def by_port(snapshot, port):
    matches = snapshot.find(port)
    assert len(matches) == 1, matches
    return matches[0]


def test_process_maps_to_its_git_project(scanner: Scanner, fake_proc: FakeProc, home: Path):
    repo = make_repo(home / "code" / "acme-web", branch="feat/checkout")
    inode = fake_proc.add_socket(5173)
    fake_proc.add_process(
        4100,
        comm="node",
        cmdline=("node", str(repo / "node_modules/.bin/vite")),
        cwd=str(repo),
        inodes=[inode],
    )

    entry = by_port(scanner.scan(), 5173)

    assert entry.pid == 4100
    assert entry.name == "node"
    assert entry.framework == "Vite"
    assert entry.project is not None
    assert entry.project.name == "acme-web"
    assert entry.project.branch == "feat/checkout"
    assert entry.is_dev and entry.http
    assert entry.scope == SCOPE_LOCAL


def test_subdirectory_of_repo(scanner: Scanner, fake_proc: FakeProc, home: Path) -> None:
    repo = make_repo(home / "code" / "platform")
    service = repo / "services" / "billing"
    service.mkdir(parents=True)
    inode = fake_proc.add_socket(8000, address="0.0.0.0")
    fake_proc.add_process(
        4200,
        comm="uvicorn",
        cmdline=("/usr/bin/python3", "-m", "uvicorn", "app:app"),
        cwd=str(service),
        inodes=[inode],
    )

    entry = by_port(scanner.scan(), 8000)

    assert entry.project is not None and entry.project.root == str(repo)
    assert entry.subpath == os.path.join("services", "billing")
    assert entry.framework == "Uvicorn"
    assert entry.scope == SCOPE_NETWORK


def test_ipv4_and_ipv6_sockets_of_one_process_are_one_entry(
    scanner: Scanner, fake_proc: FakeProc, home: Path
) -> None:
    v4 = fake_proc.add_socket(3000, address="0.0.0.0")
    v6 = fake_proc.add_socket(3000, address="::")
    fake_proc.add_process(4300, cwd=str(make_repo(home / "code" / "blog")), inodes=[v4, v6])

    snapshot = scanner.scan()

    entry = by_port(snapshot, 3000)
    assert entry.addresses == ["0.0.0.0", "::"]


def test_prefork_workers_collapse_into_master(
    scanner: Scanner, fake_proc: FakeProc, home: Path
) -> None:
    repo = make_repo(home / "code" / "shop")
    inode = fake_proc.add_socket(8080)
    fake_proc.add_process(5000, comm="gunicorn", cwd=str(repo), ppid=1, inodes=[inode])
    for worker in (5001, 5002, 5003):
        fake_proc.add_process(
            worker,
            comm="gunicorn",
            cwd=str(repo),
            ppid=5000,
            start_ticks=worker * 10,
            inodes=[inode],
        )

    entry = by_port(scanner.scan(), 8080)

    assert entry.pid == 5000
    assert entry.pids == [5000, 5001, 5002, 5003]
    # Stopping checks each worker's identity before signalling it.
    assert entry.workers == [(5001, 50010), (5002, 50020), (5003, 50030)]
    assert entry.framework == "Gunicorn"


def test_classification(scanner: Scanner, fake_proc: FakeProc, home: Path) -> None:
    repo = make_repo(home / "code" / "app")
    # A dev server inside a project.
    fake_proc.add_process(10, cwd=str(repo), inodes=[fake_proc.add_socket(3000)])
    # A Python server started from ~/Downloads: no project, but a dev runtime.
    downloads = home / "Downloads"
    downloads.mkdir()
    fake_proc.add_process(
        11,
        comm="python3",
        cmdline=("python3", "-m", "http.server"),
        cwd=str(downloads),
        inodes=[fake_proc.add_socket(8001)],
    )
    # A desktop app.
    fake_proc.add_process(
        12, comm="spotify", cmdline=("spotify",), cwd=str(home), inodes=[fake_proc.add_socket(4070)]
    )
    # A language server on a random high port, inside the project.
    fake_proc.add_process(13, cwd=str(repo), inodes=[fake_proc.add_socket(41234)])
    # Root's cupsd: the owner cannot be resolved.
    fake_proc.add_socket(631, uid=0)

    snapshot = scanner.scan()

    dev_ports = sorted(e.port for e in snapshot.dev_entries)
    assert dev_ports == [3000, 8001]
    assert sorted(e.port for e in snapshot.entries) == [631, 3000, 4070, 8001, 41234]


def test_pinned_ports_are_always_dev(scanner: Scanner, fake_proc: FakeProc) -> None:
    fake_proc.add_socket(5432, uid=0)  # a system PostgreSQL

    entry = by_port(scanner.scan(pinned=[5432]), 5432)

    assert entry.pinned and entry.is_dev
    assert not entry.http  # PostgreSQL does not speak HTTP


def test_udp_is_opt_in(scanner: Scanner, fake_proc: FakeProc) -> None:
    fake_proc.add_socket(5353, proto="udp", address="0.0.0.0", uid=0)

    assert scanner.scan().find(5353) == []
    assert len(scanner.scan(include_udp=True).find(5353)) == 1


def test_memory_is_refreshed_without_rereading_process(
    scanner: Scanner, fake_proc: FakeProc, home: Path
) -> None:
    proc_dir = fake_proc.add_process(
        20, cwd=str(make_repo(home / "code" / "x")), inodes=[fake_proc.add_socket(3000)]
    )
    first = by_port(scanner.scan(), 3000).memory

    stat = (proc_dir / "stat").read_text().replace(" 2560 ", " 5120 ")
    (proc_dir / "stat").write_text(stat)

    assert by_port(scanner.scan(), 3000).memory == first * 2


def test_restarted_process_with_reused_pid_is_reread(
    scanner: Scanner, fake_proc: FakeProc, home: Path
) -> None:
    first_repo = make_repo(home / "code" / "first")
    second_repo = make_repo(home / "code" / "second")
    inode = fake_proc.add_socket(3000)
    fake_proc.add_process(30, cwd=str(first_repo), start_ticks=100, inodes=[inode])
    assert by_port(scanner.scan(), 3000).project.name == "first"

    fake_proc.remove_process(30)
    fake_proc.remove_socket(inode)
    inode = fake_proc.add_socket(3000)
    fake_proc.add_process(30, cwd=str(second_repo), start_ticks=999, inodes=[inode])

    assert by_port(scanner.scan(), 3000).project.name == "second"


class FakeDockerClient:
    socket_path = "/fake/docker.sock"
    runtime = "docker"

    def __init__(self, containers: list[ContainerInfo]) -> None:
        self._containers = containers

    def status(self) -> DockerStatus:
        return DockerStatus(CONNECTED, self.socket_path, "docker", "27.0")

    def containers(self) -> list[ContainerInfo]:
        return [
            ContainerInfo(c.id, c.name, c.image, c.state, c.status, labels=c.labels, ports=c.ports)
            for c in self._containers
        ]

    def inspect(self, container_id: str) -> dict:
        return {"State": {"StartedAt": "2026-10-01T10:00:00.123456789Z", "Pid": 0}}


def test_docker_container_replaces_docker_proxy_socket(fake_proc: FakeProc, home: Path) -> None:
    repo = make_repo(home / "code" / "acme-web")
    container = ContainerInfo(
        id="f" * 64,
        name="acme-web-db-1",
        image="postgres:16",
        state="running",
        status="Up 2 hours",
        labels={
            "com.docker.compose.project": "acme-web",
            "com.docker.compose.project.working_dir": str(repo),
            "com.docker.compose.service": "db",
        },
        ports=[
            PublishedPort("0.0.0.0", 5432, 5432, "tcp"),
            PublishedPort("::", 5432, 5432, "tcp"),
        ],
    )
    # docker-proxy runs as root, so its sockets cannot be resolved.
    fake_proc.add_socket(5432, address="0.0.0.0", uid=0)
    fake_proc.add_socket(5432, address="::", uid=0)
    docker = DockerSource(FakeDockerClient([container]), proc_root=fake_proc.path)
    scanner = Scanner(proc_root=fake_proc.path, uid=UID, home=str(home), docker=docker)

    snapshot = scanner.scan()

    entry = by_port(snapshot, 5432)
    assert entry.container is not None
    assert entry.name == "acme-web-db-1"
    assert entry.project is not None and entry.project.root == str(repo)
    assert entry.framework == "Docker"
    assert entry.is_dev
    assert not entry.http
    assert entry.start_time is not None
    assert snapshot.docker is not None and snapshot.docker.connected


def test_container_without_host_socket_still_listed(fake_proc: FakeProc, home: Path) -> None:
    container = ContainerInfo(
        id="a" * 64,
        name="adminer",
        image="adminer",
        state="running",
        status="Up 5 minutes",
        ports=[PublishedPort("127.0.0.1", 8081, 8080, "tcp")],
    )
    docker = DockerSource(FakeDockerClient([container]), proc_root=fake_proc.path)
    scanner = Scanner(proc_root=fake_proc.path, uid=UID, home=str(home), docker=docker)

    entry = by_port(scanner.scan(), 8081)

    assert entry.container is not None
    assert entry.container_port == 8080
    assert entry.addresses == ["127.0.0.1"]
    assert entry.project is None
    assert entry.http
