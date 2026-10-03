from __future__ import annotations

import json
import socket
import subprocess
import sys
import time
from pathlib import Path

from conftest import make_repo

from portglance import cli
from portglance.core.model import PortEntry, ProcessInfo, Project
from portglance.core.scanner import Snapshot

SERVER = """
import socket, sys, time
sock = socket.socket()
sock.bind(("127.0.0.1", 0))
sock.listen()
print(sock.getsockname()[1], flush=True)
while True:
    time.sleep(0.1)
"""


def fake_snapshot(*entries: PortEntry) -> Snapshot:
    return Snapshot(list(entries), time.time())


def vite_entry() -> PortEntry:
    process = ProcessInfo(
        pid=4100,
        ppid=1,
        name="node",
        cmdline=("node", "node_modules/.bin/vite"),
        exe="/usr/bin/node",
        cwd="/home/dev/code/acme-web",
        uid=1000,
        start_time=time.time() - 600,
        start_ticks=1,
        rss=150_000_000,
    )
    return PortEntry(
        proto="tcp",
        port=5173,
        addresses=["127.0.0.1"],
        uid=1000,
        pid=4100,
        pids=[4100],
        process=process,
        project=Project("acme-web", "/home/dev/code/acme-web", "git", "main"),
        framework="Vite",
        is_dev=True,
        http=True,
    )


def test_list_json(monkeypatch, capsys) -> None:
    system = PortEntry(proto="tcp", port=631, addresses=["127.0.0.1"], uid=0)
    monkeypatch.setattr(cli, "take_snapshot", lambda **_: fake_snapshot(vite_entry(), system))

    assert cli.main(["list", "--json"]) == 0

    data = json.loads(capsys.readouterr().out)
    assert [d["port"] for d in data] == [5173]  # system port hidden without --all
    assert data[0]["project"]["name"] == "acme-web"
    assert data[0]["url"] == "http://localhost:5173"
    assert 590 < data[0]["uptime_seconds"] < 700


def test_list_table(monkeypatch, capsys) -> None:
    monkeypatch.setattr(cli, "take_snapshot", lambda **_: fake_snapshot(vite_entry()))

    assert cli.main(["list"]) == 0

    out = capsys.readouterr().out
    assert "PORT" in out and "5173" in out and "acme-web (main)" in out and "Vite" in out


def test_kill_nothing_listening(monkeypatch, capsys) -> None:
    monkeypatch.setattr(cli, "take_snapshot", lambda **_: fake_snapshot())

    assert cli.main(["kill", "9", "--yes"]) == 1
    assert "Nothing is listening on port 9" in capsys.readouterr().out


def test_kill_real_process(tmp_path: Path, monkeypatch, capsys) -> None:
    repo = make_repo(tmp_path / "project")
    server = subprocess.Popen(
        [sys.executable, "-c", SERVER], cwd=repo, stdout=subprocess.PIPE, text=True
    )
    try:
        assert server.stdout is not None
        port = int(server.stdout.readline())

        assert cli.main(["kill", str(port), "--yes", "--timeout", "2", "--no-docker"]) == 0
        server.wait(timeout=5)
    finally:
        server.kill()
        server.wait()

    out = capsys.readouterr().out
    assert f"PID {server.pid}" in out
    assert "Stopped" in out
    with socket.socket() as probe:
        assert probe.connect_ex(("127.0.0.1", port)) != 0
