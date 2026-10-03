"""Docker Engine API client, against a tiny fake daemon on a unix socket."""

from __future__ import annotations

import json
import socketserver
import threading
import time
from http.server import BaseHTTPRequestHandler
from pathlib import Path

import pytest

from portglance.core import docker
from portglance.core.docker import DockerClient, DockerSource, parse_containers, parse_timestamp

CONTAINERS = [
    {
        "Id": "c0ffee" + "0" * 58,
        "Names": ["/acme-web-db-1"],
        "Image": "postgres:16",
        "State": "running",
        "Status": "Up 2 hours",
        "Ports": [
            {"IP": "0.0.0.0", "PrivatePort": 5432, "PublicPort": 5432, "Type": "tcp"},
            {"IP": "::", "PrivatePort": 5432, "PublicPort": 5432, "Type": "tcp"},
            {"PrivatePort": 9187, "Type": "tcp"},  # exposed, not published
        ],
        "Labels": {"com.docker.compose.project": "acme-web"},
    },
    {
        "Id": "beef" + "1" * 60,
        "Names": ["/redis"],
        "Image": "redis:7",
        "State": "running",
        "Status": "Up 3 minutes",
        "Ports": [{"IP": "127.0.0.1", "PrivatePort": 6379, "PublicPort": 6380, "Type": "tcp"}],
        "Labels": {},
    },
]


class FakeDaemon(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
    daemon_threads = True

    def __init__(self, path: str) -> None:
        self.requests: list[tuple[str, str]] = []
        self.containers = list(CONTAINERS)
        self.events: list[dict] = []
        self.events_ready = threading.Event()
        super().__init__(path, Handler)


class Handler(BaseHTTPRequestHandler):
    server: FakeDaemon
    protocol_version = "HTTP/1.1"

    def log_message(self, *args) -> None:  # keep test output quiet
        pass

    def address_string(self) -> str:
        return "unix"

    def _json(self, status: int, payload) -> None:
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        self.server.requests.append(("GET", self.path))
        if self.path == "/version":
            self._json(200, {"Version": "27.3.1", "ApiVersion": "1.47"})
        elif self.path == "/containers/json":
            self._json(200, self.server.containers)
        elif self.path.startswith("/containers/") and self.path.endswith("/json"):
            self._json(200, {"State": {"StartedAt": "2026-10-03T08:00:00.5Z", "Pid": 4321}})
        elif self.path.startswith("/events"):
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Transfer-Encoding", "chunked")
            self.end_headers()
            self.server.events_ready.wait(5)
            for event in self.server.events:
                chunk = (json.dumps(event) + "\n").encode()
                self.wfile.write(f"{len(chunk):x}\r\n".encode() + chunk + b"\r\n")
                self.wfile.flush()
            self.wfile.write(b"0\r\n\r\n")
        else:
            self._json(404, {"message": "page not found"})

    def do_POST(self) -> None:
        self.server.requests.append(("POST", self.path))
        if self.path.startswith("/containers/") and "/stop" in self.path:
            if "missing" in self.path:
                self._json(404, {"message": "No such container: missing"})
                return
            self.send_response(204)
            self.send_header("Content-Length", "0")
            self.end_headers()
        else:
            self._json(404, {"message": "page not found"})


@pytest.fixture
def daemon(tmp_path: Path):
    path = str(tmp_path / "docker.sock")
    server = FakeDaemon(path)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server
    server.shutdown()
    server.server_close()


def test_parse_containers_keeps_only_published_ports() -> None:
    containers = parse_containers(CONTAINERS)

    db, redis = containers
    assert db.name == "acme-web-db-1"
    assert db.compose_project == "acme-web"
    assert [(p.host_ip, p.host_port, p.container_port) for p in db.ports] == [
        ("0.0.0.0", 5432, 5432),
        ("::", 5432, 5432),
    ]
    assert redis.ports[0].host_port == 6380
    assert redis.short_id == "beef11111111"


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("2026-10-03T08:00:00Z", 1791014400.0),
        ("2026-10-03T08:00:00.123456789Z", 1791014400.123456),
        ("2026-10-03T10:00:00+02:00", 1791014400.0),
        ("0001-01-01T00:00:00Z", None),
        ("", None),
        ("yesterday", None),
    ],
)
def test_parse_timestamp(value: str, expected: float | None) -> None:
    result = parse_timestamp(value)
    if expected is None:
        assert result is None
    else:
        assert result == pytest.approx(expected)


def test_client_lists_and_inspects(daemon: FakeDaemon) -> None:
    client = DockerClient(daemon.server_address)

    status = client.status()
    containers = client.containers()
    details = client.inspect(containers[0].id)

    assert status.connected and status.version == "27.3.1"
    assert [c.name for c in containers] == ["acme-web-db-1", "redis"]
    assert details["State"]["Pid"] == 4321


def test_stop_container(daemon: FakeDaemon) -> None:
    client = DockerClient(daemon.server_address)

    client.stop("acme-web-db-1", timeout=7)

    assert ("POST", "/containers/acme-web-db-1/stop?t=7") in daemon.requests


def test_stop_missing_container_raises(daemon: FakeDaemon) -> None:
    client = DockerClient(daemon.server_address)

    with pytest.raises(docker.DockerError, match="No such container"):
        client.stop("missing")


def test_unreachable_daemon_reports_error(tmp_path: Path) -> None:
    client = DockerClient(str(tmp_path / "nothing.sock"))

    status = client.status()

    assert status.state == docker.ERROR
    assert not status.connected


def test_discover_prefers_docker_host(tmp_path: Path) -> None:
    sock = tmp_path / "custom.sock"
    sock.touch()

    client = DockerClient.discover(
        {"DOCKER_HOST": f"unix://{sock}", "XDG_RUNTIME_DIR": str(tmp_path)}
    )

    assert client is not None and client.socket_path == str(sock)


def test_discover_finds_rootless_podman(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(docker.os.path, "exists", lambda p: p.endswith("podman/podman.sock"))

    client = DockerClient.discover({"XDG_RUNTIME_DIR": str(tmp_path)})

    assert client is not None
    assert client.runtime == "podman"
    assert client.socket_path == f"{tmp_path}/podman/podman.sock"


def test_source_caches_until_invalidated(daemon: FakeDaemon) -> None:
    source = DockerSource(DockerClient(daemon.server_address), max_age=3600)

    status, containers = source.snapshot()
    source.snapshot()
    list_calls = daemon.requests.count(("GET", "/containers/json"))

    assert status.connected and len(containers) == 2
    assert containers[0].pid == 4321 and containers[0].started_at is not None
    assert list_calls == 1

    daemon.containers = daemon.containers[:1]
    source.invalidate()
    _, containers = source.snapshot()
    assert [c.name for c in containers] == ["acme-web-db-1"]
    # Containers are inspected once, not on every refresh.
    inspects = [r for r in daemon.requests if r[1].endswith("/json") and "containers/c0" in r[1]]
    assert len(inspects) == 1


def test_events_invalidate_the_cache(daemon: FakeDaemon) -> None:
    changed = threading.Event()
    source = DockerSource(DockerClient(daemon.server_address), on_change=changed.set, max_age=3600)
    source.snapshot()
    daemon.events = [
        {"Type": "container", "Action": "exec_start: sh", "id": "x"},  # ignored
        {"Type": "container", "Action": "start", "id": "abc"},
    ]

    source.start_events()
    daemon.events_ready.set()
    try:
        assert changed.wait(5)
    finally:
        source.stop_events()

    deadline = time.monotonic() + 5
    while daemon.requests.count(("GET", "/containers/json")) < 2:
        source.snapshot()
        assert time.monotonic() < deadline
        time.sleep(0.01)


def test_cgroup_memory(tmp_path: Path) -> None:
    proc = tmp_path / "proc" / "77"
    proc.mkdir(parents=True)
    (proc / "cgroup").write_text("0::/system.slice/docker-abc.scope\n")
    scope = tmp_path / "cgroup" / "system.slice" / "docker-abc.scope"
    scope.mkdir(parents=True)
    (scope / "memory.current").write_text("52428800\n")

    memory = docker.cgroup_memory(77, str(tmp_path / "proc"), str(tmp_path / "cgroup"))

    assert memory == 52_428_800
    assert docker.cgroup_memory(78, str(tmp_path / "proc"), str(tmp_path / "cgroup")) is None
