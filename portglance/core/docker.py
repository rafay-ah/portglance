"""Docker and Podman containers through the Engine API on a unix socket.

No Docker SDK is needed: the Engine API is plain HTTP over a unix socket.
Podman's Docker-compatible API works the same way. Changes are picked up from
the ``/events`` stream instead of polling.
"""

from __future__ import annotations

import http.client
import json
import os
import re
import socket
import threading
import time
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from urllib.parse import quote

from .model import ContainerInfo, PublishedPort

CONNECTED = "connected"
UNAVAILABLE = "unavailable"
PERMISSION_DENIED = "permission-denied"
ERROR = "error"
DISABLED = "disabled"

#: Event actions after which the container list is re-read.
REFRESH_ACTIONS = frozenset(
    {"create", "start", "restart", "stop", "die", "kill", "pause", "unpause", "destroy", "rename"}
)


class DockerError(Exception):
    pass


@dataclass(frozen=True, slots=True)
class DockerStatus:
    state: str
    endpoint: str | None = None
    runtime: str | None = None
    version: str | None = None
    message: str | None = None

    @property
    def connected(self) -> bool:
        return self.state == CONNECTED


class UnixHTTPConnection(http.client.HTTPConnection):
    def __init__(self, path: str, timeout: float | None = 5.0) -> None:
        super().__init__("localhost", timeout=timeout)
        self.socket_path = path

    def connect(self) -> None:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(self.timeout)
        try:
            sock.connect(self.socket_path)
        except OSError:
            sock.close()
            raise
        self.sock = sock


def candidate_sockets(env: Mapping[str, str] | None = None) -> Iterator[tuple[str, str]]:
    """Yield ``(socket_path, runtime)`` pairs in order of preference."""
    env = os.environ if env is None else env
    host = env.get("DOCKER_HOST", "")
    if host.startswith("unix://"):
        yield host[len("unix://") :], "podman" if "podman" in host else "docker"
    yield "/var/run/docker.sock", "docker"
    yield "/run/docker.sock", "docker"
    runtime_dir = env.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}"
    yield f"{runtime_dir}/docker.sock", "docker"  # rootless Docker
    yield f"{runtime_dir}/podman/podman.sock", "podman"
    yield "/run/podman/podman.sock", "podman"


class DockerClient:
    def __init__(self, socket_path: str, runtime: str = "docker", timeout: float = 4.0) -> None:
        self.socket_path = socket_path
        self.runtime = runtime
        self.timeout = timeout
        self._events_conn: UnixHTTPConnection | None = None

    @classmethod
    def discover(cls, env: Mapping[str, str] | None = None) -> DockerClient | None:
        for path, runtime in candidate_sockets(env):
            if os.path.exists(path):
                return cls(path, runtime)
        return None

    # -- HTTP ---------------------------------------------------------------

    def request(
        self, method: str, path: str, *, timeout: float | None = None, expect_body: bool = True
    ) -> Any:
        conn = UnixHTTPConnection(self.socket_path, timeout or self.timeout)
        try:
            conn.request(method, path, headers={"Host": "docker"})
            response = conn.getresponse()
            body = response.read()
        except PermissionError as exc:
            raise DockerError(f"permission denied on {self.socket_path}") from exc
        except OSError as exc:
            raise DockerError(f"cannot reach {self.socket_path}: {exc}") from exc
        finally:
            conn.close()
        if response.status >= 400:
            try:
                message = json.loads(body).get("message", "")
            except (ValueError, AttributeError):
                message = body.decode(errors="replace").strip()
            raise DockerError(f"{method} {path}: HTTP {response.status} {message}".strip())
        if not expect_body or not body:
            return None
        try:
            return json.loads(body)
        except ValueError as exc:
            raise DockerError(f"{method} {path}: invalid JSON") from exc

    # -- API ----------------------------------------------------------------

    def status(self) -> DockerStatus:
        try:
            info = self.request("GET", "/version") or {}
        except DockerError as exc:
            state = PERMISSION_DENIED if "permission denied" in str(exc) else ERROR
            return DockerStatus(state, self.socket_path, self.runtime, message=str(exc))
        runtime = self.runtime
        for component in info.get("Components") or []:
            if "podman" in str(component.get("Name", "")).lower():
                runtime = "podman"
        self.runtime = runtime
        return DockerStatus(CONNECTED, self.socket_path, runtime, info.get("Version"))

    def containers(self) -> list[ContainerInfo]:
        items = self.request("GET", "/containers/json") or []
        return parse_containers(items, self.runtime)

    def inspect(self, container_id: str) -> dict[str, Any]:
        return self.request("GET", f"/containers/{quote(container_id)}/json") or {}

    def stop(self, container_id: str, timeout: float = 10.0) -> None:
        seconds = max(0, int(round(timeout)))
        self.request(
            "POST",
            f"/containers/{quote(container_id)}/stop?t={seconds}",
            timeout=seconds + 30,
            expect_body=False,
        )

    def watch_events(self, on_event: Callable[[dict[str, Any]], None]) -> None:
        """Block, calling ``on_event`` for each container event, until closed."""
        filters = quote(json.dumps({"type": ["container"]}))
        conn = UnixHTTPConnection(self.socket_path, timeout=None)
        self._events_conn = conn
        try:
            conn.request("GET", f"/events?filters={filters}", headers={"Host": "docker"})
            response = conn.getresponse()
            if response.status != 200:
                raise DockerError(f"events: HTTP {response.status}")
            while True:
                line = response.readline()
                if not line:
                    break
                try:
                    event = json.loads(line)
                except ValueError:
                    continue
                if isinstance(event, dict):
                    on_event(event)
        except OSError as exc:
            raise DockerError(f"events stream closed: {exc}") from exc
        finally:
            self._events_conn = None
            conn.close()

    def close_events(self) -> None:
        conn = self._events_conn
        if conn is not None and conn.sock is not None:
            try:
                conn.sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass


# --------------------------------------------------------------------------
# Parsing
# --------------------------------------------------------------------------


def parse_containers(items: list[dict[str, Any]], runtime: str = "docker") -> list[ContainerInfo]:
    containers = []
    for item in items:
        container_id = str(item.get("Id") or "")
        if not container_id:
            continue
        names = item.get("Names") or []
        name = str(names[0]).lstrip("/") if names else container_id[:12]
        ports = []
        seen = set()
        for port in item.get("Ports") or []:
            public = port.get("PublicPort")
            if not public:
                continue
            published = PublishedPort(
                host_ip=port.get("IP") or "0.0.0.0",
                host_port=int(public),
                container_port=int(port.get("PrivatePort") or 0),
                proto=str(port.get("Type") or "tcp").lower(),
            )
            if published not in seen:
                seen.add(published)
                ports.append(published)
        containers.append(
            ContainerInfo(
                id=container_id,
                name=name,
                image=str(item.get("Image") or ""),
                state=str(item.get("State") or ""),
                status=str(item.get("Status") or ""),
                runtime=runtime,
                labels=dict(item.get("Labels") or {}),
                ports=ports,
            )
        )
    return containers


_TIMESTAMP_RE = re.compile(r"^(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d)(?:\.(\d+))?(Z|[+-]\d\d:?\d\d)?$")


def parse_timestamp(value: str | None) -> float | None:
    """Parse Docker's RFC 3339 timestamps (with nanoseconds) to a Unix time."""
    if not value:
        return None
    match = _TIMESTAMP_RE.match(value.strip())
    if not match:
        return None
    base, fraction, zone = match.groups()
    if base.startswith("0001-"):
        return None  # Go's zero time: never started
    text = base + (f".{fraction[:6].ljust(6, '0')}" if fraction else "")
    if not zone or zone == "Z":
        zone = "+00:00"
    elif ":" not in zone:
        zone = f"{zone[:3]}:{zone[3:]}"
    try:
        return datetime.fromisoformat(text + zone).timestamp()
    except ValueError:
        return None


def apply_inspect(container: ContainerInfo, details: Mapping[str, Any]) -> None:
    state = details.get("State") or {}
    container.started_at = parse_timestamp(state.get("StartedAt"))
    pid = state.get("Pid")
    container.pid = int(pid) if pid else None


def cgroup_memory(
    pid: int, proc_root: str = "/proc", cgroup_root: str = "/sys/fs/cgroup"
) -> int | None:
    """Memory usage of the cgroup ``pid`` lives in (cgroup v2), in bytes."""
    try:
        with open(f"{proc_root}/{pid}/cgroup", encoding="utf-8") as fh:
            lines = fh.read().splitlines()
    except OSError:
        return None
    for line in lines:
        if line.startswith("0::"):
            path = line[3:].strip()
            try:
                with open(f"{cgroup_root}{path}/memory.current", encoding="ascii") as fh:
                    return int(fh.read().strip())
            except (OSError, ValueError):
                return None
    return None


# --------------------------------------------------------------------------
# Cached, event-driven source used by the scanner
# --------------------------------------------------------------------------


class DockerSource:
    """A cached view of running containers that refreshes on Docker events.

    ``snapshot()`` is cheap while nothing changes: the container list is only
    re-read after an event (or, as a safety net, every ``max_age`` seconds).
    """

    def __init__(
        self,
        client: DockerClient | None = None,
        *,
        discover: Callable[[], DockerClient | None] = DockerClient.discover,
        on_change: Callable[[], None] | None = None,
        max_age: float = 60.0,
        rediscover_every: float = 30.0,
        proc_root: str = "/proc",
    ) -> None:
        self._client = client
        self._discover = discover
        self.on_change = on_change
        self.max_age = max_age
        self.rediscover_every = rediscover_every
        self.proc_root = proc_root
        self._lock = threading.Lock()
        self._containers: list[ContainerInfo] = []
        self._inspected: dict[str, tuple[float | None, int | None]] = {}
        self._status = DockerStatus(UNAVAILABLE)
        self._dirty = True
        self._loaded_at = 0.0
        self._discovered_at = 0.0
        self._stop = threading.Event()
        self._events_thread: threading.Thread | None = None

    @property
    def client(self) -> DockerClient | None:
        return self._client

    @property
    def status(self) -> DockerStatus:
        return self._status

    def invalidate(self) -> None:
        with self._lock:
            self._dirty = True

    def start_events(self) -> None:
        """Watch the events stream in a daemon thread (reconnecting with backoff)."""
        if self._events_thread is not None:
            return
        self._stop.clear()
        self._events_thread = threading.Thread(
            target=self._events_loop, name="portglance-docker-events", daemon=True
        )
        self._events_thread.start()

    def stop_events(self) -> None:
        self._stop.set()
        if self._client is not None:
            self._client.close_events()
        self._events_thread = None

    def snapshot(self) -> tuple[DockerStatus, list[ContainerInfo]]:
        now = time.monotonic()
        with self._lock:
            needs_reload = self._dirty or now - self._loaded_at > self.max_age
        if self._client is None and now - self._discovered_at > self.rediscover_every:
            self._discovered_at = now
            self._client = self._discover()
            needs_reload = True
        if self._client is None:
            self._status = DockerStatus(UNAVAILABLE)
            return self._status, []
        if needs_reload:
            self._reload(now)
        for container in self._containers:
            if container.pid:
                container.memory = cgroup_memory(container.pid, self.proc_root)
        return self._status, list(self._containers)

    def _reload(self, now: float) -> None:
        client = self._client
        assert client is not None
        status = client.status()
        containers: list[ContainerInfo] = []
        if status.connected:
            try:
                containers = client.containers()
            except DockerError as exc:
                status = DockerStatus(ERROR, client.socket_path, client.runtime, message=str(exc))
        for container in containers:
            cached = self._inspected.get(container.id)
            if cached is None:
                try:
                    apply_inspect(container, client.inspect(container.id))
                except DockerError:
                    pass
                else:
                    self._inspected[container.id] = (container.started_at, container.pid)
            else:
                container.started_at, container.pid = cached
        live = {c.id for c in containers}
        for container_id in list(self._inspected):
            if container_id not in live:
                del self._inspected[container_id]
        with self._lock:
            self._status = status
            self._containers = containers
            self._dirty = False
            self._loaded_at = now

    def _events_loop(self) -> None:
        delay = 2.0
        while not self._stop.is_set():
            client = self._client
            if client is None:
                self._stop.wait(self.rediscover_every)
                continue
            started = time.monotonic()
            try:
                client.watch_events(self._on_event)
            except DockerError:
                pass
            if self._stop.is_set():
                break
            # The stream ended (daemon restarted, socket went away...).
            self.invalidate()
            if self.on_change:
                self.on_change()
            delay = 2.0 if time.monotonic() - started > 60 else min(delay * 2, 60.0)
            self._stop.wait(delay)

    def _on_event(self, event: dict[str, Any]) -> None:
        action = str(event.get("Action") or event.get("status") or "").split(":")[0]
        if action not in REFRESH_ACTIONS:
            return
        if action in ("start", "restart", "die", "stop"):
            container_id = str(event.get("id") or (event.get("Actor") or {}).get("ID") or "")
            self._inspected.pop(container_id, None)
        self.invalidate()
        if self.on_change:
            self.on_change()
