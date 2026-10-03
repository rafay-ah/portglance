"""Builds the demo projects and runs their fake servers."""

from __future__ import annotations

import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..core.docker import DockerSource, DockerStatus
from ..core.model import ContainerInfo, PublishedPort

FAKE_SERVER = Path(__file__).with_name("fake_server.py")
BANNER = "Demo mode — these servers are fake"


@dataclass
class DemoServer:
    label: str
    port: int
    argv: list[str]
    cwd: Path
    env: dict[str, str]
    process: subprocess.Popen | None = None
    container: ContainerInfo | None = None


@dataclass
class _Spec:
    label: str
    preferred: int
    host: str = "127.0.0.1"
    kind: str = "http"
    stubborn: bool = False
    subtitle: str = ""
    container: dict[str, Any] = field(default_factory=dict)


def _port_free(port: int) -> bool:
    for family, host in ((socket.AF_INET, "0.0.0.0"), (socket.AF_INET, "127.0.0.1")):
        with socket.socket(family, socket.SOCK_STREAM) as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                sock.bind((host, port))
            except OSError:
                return False
    return True


def _free_port(preferred: int, taken: set[int]) -> int:
    for port in range(preferred, preferred + 50):
        if port not in taken and _port_free(port):
            return port
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _git_init(path: Path, branch: str) -> None:
    path.mkdir(parents=True, exist_ok=True)
    if shutil.which("git"):
        result = subprocess.run(
            ["git", "init", "-q", "-b", branch, str(path)], capture_output=True, check=False
        )
        if result.returncode == 0:
            return
    (path / ".git" / "refs" / "heads").mkdir(parents=True, exist_ok=True)
    (path / ".git" / "HEAD").write_text(f"ref: refs/heads/{branch}\n")


def _write(path: Path, text: str, executable: bool = False) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    if executable:
        path.chmod(0o755)
    return path


class DemoFleet:
    banner = BANNER

    def __init__(self, root: Path) -> None:
        self.root = root
        self.servers: list[DemoServer] = []
        self._docker = DemoDockerClient(self)

    # -- setup ----------------------------------------------------------------------

    def start(self) -> None:
        root = self.root
        code = root / "code"
        bin_dir = root / "bin"
        bin_dir.mkdir(parents=True, exist_ok=True)
        node = bin_dir / "node"
        node.symlink_to(sys.executable)
        server_code = FAKE_SERVER.read_text()
        shebang = f"#!{sys.executable}\n"
        docker_proxy = _write(bin_dir / "docker-proxy", shebang + server_code, True)

        # acme-web: a Vite app with Storybook, plus a Compose stack with PostgreSQL.
        web = code / "acme-web"
        _git_init(web, "main")
        _write(web / "package.json", json.dumps({"name": "acme-web", "private": True}, indent=2))
        _write(web / "compose.yaml", "services:\n  db:\n    image: postgres:16\n")
        vite = _write(web / "node_modules" / ".bin" / "vite", server_code)
        storybook = _write(web / "node_modules" / ".bin" / "storybook", server_code)

        # api-service: FastAPI under uvicorn, listening on all interfaces.
        api = code / "api-service"
        _git_init(api, "feat/auth")
        _write(api / "pyproject.toml", '[project]\nname = "api-service"\n')
        uvicorn = _write(api / ".venv" / "bin" / "uvicorn", shebang + server_code, True)

        # ml-playground: JupyterLab.
        ml = code / "ml-playground"
        _git_init(ml, "main")
        jupyter = _write(bin_dir / "jupyter-lab", shebang + server_code, True)

        # legacy-dashboard: an old Express server that ignores SIGTERM.
        legacy = code / "legacy-dashboard"
        _git_init(legacy, "master")
        _write(legacy / "package.json", '{"name": "legacy-dashboard"}\n')
        _write(legacy / "server.js", server_code)

        # docs-site: no git repository, just a package.json.
        docs = code / "docs-site"
        _write(docs / "package.json", '{"name": "docs-site"}\n')
        astro = _write(docs / "node_modules" / ".bin" / "astro", server_code)

        downloads = root / "Downloads"
        downloads.mkdir(parents=True, exist_ok=True)
        _write(downloads / "index.html", "<h1>Downloads</h1>\n")

        specs: list[tuple[_Spec, list[str], Path]] = [
            (
                _Spec("Vite dev server", 5173, subtitle="acme-web"),
                [str(node), str(vite), "--port", "{port}"],
                web,
            ),
            (
                _Spec("Storybook", 6006, subtitle="acme-web"),
                [str(node), str(storybook), "dev", "-p", "{port}"],
                web,
            ),
            (
                _Spec("api-service (FastAPI)", 8000, host="0.0.0.0", subtitle="uvicorn main:app"),
                [str(uvicorn), "main:app", "--reload", "--host", "0.0.0.0", "--port", "{port}"],
                api,
            ),
            (
                _Spec("JupyterLab", 8888, subtitle="ml-playground"),
                [str(jupyter), "--no-browser", "--port", "{port}"],
                ml,
            ),
            (
                _Spec("legacy-dashboard", 3000, stubborn=True, subtitle="ignores SIGTERM"),
                [str(node), "server.js", "--port", "{port}"],
                legacy,
            ),
            (
                _Spec("Astro docs", 4321, subtitle="docs-site"),
                [str(node), str(astro), "dev", "--port", "{port}"],
                docs,
            ),
            (
                _Spec("Static files", 8080, subtitle="python3 -m http.server"),
                [sys.executable, "-m", "http.server", "{port}", "--bind", "127.0.0.1"],
                downloads,
            ),
            (
                _Spec(
                    "acme-web-db-1",
                    5432,
                    host="0.0.0.0",
                    kind="tcp",
                    container={
                        "image": "postgres:16",
                        "private": 5432,
                        "labels": {
                            "com.docker.compose.project": "acme-web",
                            "com.docker.compose.project.working_dir": str(web),
                            "com.docker.compose.service": "db",
                        },
                    },
                ),
                [str(docker_proxy), "-proto", "tcp", "-host-ip", "0.0.0.0",
                 "-host-port", "{port}", "-container-ip", "172.18.0.2", "-container-port", "5432"],
                root,
            ),
            (
                _Spec(
                    "redis-cache",
                    6379,
                    kind="tcp",
                    container={"image": "redis:7-alpine", "private": 6379, "labels": {}},
                ),
                [str(docker_proxy), "-proto", "tcp", "-host-ip", "127.0.0.1",
                 "-host-port", "{port}", "-container-ip", "172.17.0.3", "-container-port", "6379"],
                root,
            ),
        ]  # fmt: skip

        taken: set[int] = set()
        for spec, argv, cwd in specs:
            port = _free_port(spec.preferred, taken)
            taken.add(port)
            env = {
                "PORTGLANCE_DEMO_PORT": str(port),
                "PORTGLANCE_DEMO_HOST": spec.host,
                "PORTGLANCE_DEMO_KIND": spec.kind,
                "PORTGLANCE_DEMO_TITLE": spec.label,
                "PORTGLANCE_DEMO_SUBTITLE": spec.subtitle,
            }
            if spec.stubborn:
                env["PORTGLANCE_DEMO_STUBBORN"] = "1"
            server = DemoServer(
                spec.label, port, [a.replace("{port}", str(port)) for a in argv], cwd, env
            )
            if spec.container:
                server.container = ContainerInfo(
                    id=os.urandom(32).hex(),
                    name=spec.label,
                    image=spec.container["image"],
                    state="running",
                    status="Up",
                    labels=spec.container["labels"],
                    ports=[PublishedPort(spec.host, port, spec.container["private"], "tcp")],
                )
            self.servers.append(server)

        self._write_settings(pinned=[3000, 9000])
        for server in self.servers:
            self._spawn(server)
        self._wait_until_listening()

    def _write_settings(self, pinned: list[int]) -> None:
        config = Path(os.environ.get("PORTGLANCE_CONFIG_DIR", self.root / "config"))
        config.mkdir(parents=True, exist_ok=True)
        settings = {"pinned_ports": pinned, "kill_timeout": 3.0, "widget_pinned": True}
        (config / "settings.json").write_text(json.dumps(settings, indent=2))

    def _spawn(self, server: DemoServer) -> None:
        env = {**os.environ, **server.env}
        server.process = subprocess.Popen(
            server.argv,
            cwd=server.cwd,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )

    def _wait_until_listening(self, timeout: float = 10.0) -> None:
        deadline = time.monotonic() + timeout
        pending = list(self.servers)
        while pending and time.monotonic() < deadline:
            still = []
            for server in pending:
                with socket.socket() as probe:
                    probe.settimeout(0.2)
                    if probe.connect_ex(("127.0.0.1", server.port)) != 0:
                        still.append(server)
            pending = still
            if pending:
                time.sleep(0.05)

    def stop(self) -> None:
        for server in self.servers:
            process = server.process
            if process is not None and process.poll() is None:
                process.terminate()
        deadline = time.monotonic() + 2
        for server in self.servers:
            process = server.process
            if process is None:
                continue
            try:
                process.wait(max(0.0, deadline - time.monotonic()))
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
        self._docker.close_events()

    # -- simulated Docker -------------------------------------------------------------------

    def docker_source(self) -> DockerSource:
        return DockerSource(self._docker, discover=lambda: None, memory_reader=_rss)

    def containers(self) -> list[DemoServer]:
        return [
            s
            for s in self.servers
            if s.container is not None and s.process is not None and s.process.poll() is None
        ]


def _rss(pid: int) -> int | None:
    try:
        with open(f"/proc/{pid}/statm", encoding="ascii") as fh:
            return int(fh.read().split()[1]) * os.sysconf("SC_PAGE_SIZE")
    except (OSError, ValueError, IndexError):
        return None


class DemoDockerClient:
    """Pretends to be a Docker daemon whose containers are the demo's proxies."""

    socket_path = "demo"
    runtime = "docker"

    def __init__(self, fleet: DemoFleet) -> None:
        self.fleet = fleet
        self._on_event: Callable[[dict[str, Any]], None] | None = None
        self._closed = threading.Event()

    def status(self) -> DockerStatus:
        return DockerStatus("connected", "demo", "docker", "27.3.1 (demo)")

    def containers(self) -> list[ContainerInfo]:
        return [s.container for s in self.fleet.containers() if s.container is not None]

    def inspect(self, container_id: str) -> dict[str, Any]:
        for server in self.fleet.containers():
            if server.container is not None and server.container.id == container_id:
                process = server.process
                assert process is not None
                started = datetime.fromtimestamp(_start_time(process.pid), timezone.utc)
                return {"State": {"StartedAt": started.isoformat(), "Pid": process.pid}}
        return {}

    def stop(self, container_id: str, timeout: float = 10.0) -> None:
        for server in self.fleet.containers():
            if server.container is not None and server.container.id == container_id:
                process = server.process
                assert process is not None
                process.send_signal(signal.SIGTERM)
                try:
                    process.wait(timeout)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
                if self._on_event is not None:
                    self._on_event({"Type": "container", "Action": "die", "id": container_id})
                return

    def watch_events(
        self,
        on_event: Callable[[dict[str, Any]], None],
        *,
        on_connect: Callable[[], None] | None = None,
        stop: threading.Event | None = None,
    ) -> None:
        # Block until the source stops watching; close_events() ends it too.
        self._closed = stop if stop is not None else threading.Event()
        self._on_event = on_event
        if on_connect is not None:
            on_connect()
        self._closed.wait()

    def close_events(self) -> None:
        self._closed.set()


def _start_time(pid: int) -> float:
    try:
        with open(f"/proc/{pid}/stat", encoding="utf-8") as fh:
            ticks = int(fh.read().rsplit(")", 1)[1].split()[19])
        with open("/proc/stat", encoding="ascii") as fh:
            boot = next(float(line.split()[1]) for line in fh if line.startswith("btime"))
        return boot + ticks / os.sysconf("SC_CLK_TCK")
    except (OSError, ValueError, IndexError, StopIteration):
        return time.time()
