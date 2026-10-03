from __future__ import annotations

import pytest

from portglance.core.classify import is_dev_runtime, is_not_dev_app
from portglance.core.formatting import (
    describe_addresses,
    format_bytes,
    format_duration,
    shorten_path,
)
from portglance.core.frameworks import detect_framework, looks_like_http


@pytest.mark.parametrize(
    ("name", "cmdline", "expected"),
    [
        ("node", ["node", "/w/app/node_modules/.bin/vite", "--port", "5173"], "Vite"),
        ("node", ["node", "/w/app/node_modules/.bin/next", "dev"], "Next.js"),
        ("next-server (v1", ["next-server (v14.2.3)"], "Next.js"),
        (
            "node",
            ["node", "/w/app/node_modules/react-scripts/scripts/start.js"],
            "Create React App",
        ),
        ("node", ["node", "/w/app/node_modules/.bin/webpack", "serve"], "webpack"),
        ("node", ["node", "/w/app/node_modules/.bin/astro", "dev"], "Astro"),
        ("node", ["node", "/w/app/node_modules/.bin/ng", "serve"], "Angular"),
        ("node", ["node", "/w/app/node_modules/.bin/storybook", "dev", "-p", "6006"], "Storybook"),
        ("node", ["node", "server.js"], "Node.js"),
        ("bun", ["bun", "run", "dev"], "Bun"),
        ("python3", ["python3", "manage.py", "runserver"], "Django"),
        ("uvicorn", ["/w/api/.venv/bin/python", "/w/api/.venv/bin/uvicorn", "main:app"], "Uvicorn"),
        ("python3", ["python3", "-m", "fastapi", "dev", "main.py"], "FastAPI"),
        ("flask", ["/usr/bin/python3", "/usr/bin/flask", "run"], "Flask"),
        ("python3", ["python3", "-m", "flask", "run"], "Flask"),
        ("jupyter-lab", ["/usr/bin/python3", "/usr/bin/jupyter-lab"], "JupyterLab"),
        ("python3", ["python3", "-m", "ipykernel_launcher", "-f", "k.json"], "Jupyter kernel"),
        ("python3", ["python3", "-m", "http.server", "8000"], "http.server"),
        ("python3", ["python3", "app.py"], "Python"),
        ("ruby", ["ruby", "bin/rails", "server"], "Rails"),
        ("postgres", ["/usr/lib/postgresql/16/bin/postgres", "-D", "/var/lib/pg"], "PostgreSQL"),
        ("redis-server", ["redis-server *:6379"], "Redis"),
        ("docker-proxy", ["/usr/bin/docker-proxy", "-proto", "tcp"], "Docker"),
        ("ssh", ["ssh", "-L", "5432:db:5432", "bastion"], "SSH tunnel"),
        ("spotify", ["/usr/share/spotify/spotify"], None),
    ],
)
def test_detect_framework(name: str, cmdline: list[str], expected: str | None) -> None:
    assert detect_framework(name, cmdline) == expected


@pytest.mark.parametrize(
    ("kwargs", "expected"),
    [
        ({"port": 5173, "proto": "tcp", "framework": "Vite"}, True),
        ({"port": 5432, "proto": "tcp", "framework": "PostgreSQL"}, False),
        ({"port": 6379, "proto": "tcp"}, False),
        ({"port": 5353, "proto": "udp"}, False),
        ({"port": 15432, "proto": "tcp", "image": "postgres:16", "container_port": 5432}, False),
        ({"port": 8081, "proto": "tcp", "image": "adminer", "container_port": 8080}, True),
        ({"port": 8000, "proto": "tcp", "framework": "Jupyter kernel"}, False),
    ],
)
def test_looks_like_http(kwargs: dict, expected: bool) -> None:
    assert looks_like_http(**kwargs) is expected


@pytest.mark.parametrize("name", ["node", "python3", "python3.12", "uvicorn", "php8.3", "ruby"])
def test_dev_runtimes(name: str) -> None:
    assert is_dev_runtime(name)


@pytest.mark.parametrize("name", ["spotify", "Discord", "gsd-sharing", "steam", "cupsd"])
def test_desktop_apps_are_not_dev(name: str) -> None:
    assert is_not_dev_app(name)


@pytest.mark.parametrize(
    ("seconds", "text"),
    [
        (None, "—"),
        (5, "5s"),
        (125, "2m"),
        (3600, "1h"),
        (8040, "2h 14m"),
        (90000, "1d 1h"),
        (172800, "2d"),
    ],
)
def test_format_duration(seconds, text: str) -> None:
    assert format_duration(seconds) == text


@pytest.mark.parametrize(
    ("size", "text"),
    [
        (None, "—"),
        (512, "512 B"),
        (980_000, "980 kB"),
        (182_400_000, "182 MB"),
        (1_400_000_000, "1.4 GB"),
    ],
)
def test_format_bytes(size, text: str) -> None:
    assert format_bytes(size) == text


@pytest.mark.parametrize(
    ("addresses", "text"),
    [
        (["127.0.0.1", "::1"], "localhost"),
        (["0.0.0.0", "::"], "all interfaces"),
        (["192.168.1.20"], "192.168.1.20"),
        ([], "all interfaces"),
    ],
)
def test_describe_addresses(addresses: list[str], text: str) -> None:
    assert describe_addresses(addresses) == text


def test_shorten_path() -> None:
    assert shorten_path("/home/dev/code/app", "/home/dev") == "~/code/app"
    assert shorten_path("/home/dev", "/home/dev") == "~"
    assert shorten_path("/home/developer/x", "/home/dev") == "/home/developer/x"
    assert shorten_path(None, "/home/dev") == ""
