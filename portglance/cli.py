"""Command-line entry point.

``portglance`` starts the app (or raises the instance that is already
running). The ``list`` and ``kill`` subcommands work in a plain terminal and
never load GTK.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import time
from collections.abc import Sequence

from . import APP_NAME, __version__
from .core import killer
from .core.docker import DockerError, DockerSource
from .core.formatting import (
    describe_addresses,
    format_bytes,
    format_duration,
    shorten_path,
    user_name,
)
from .core.model import PortEntry
from .core.scanner import Scanner, Snapshot

CLI_COMMANDS = ("list", "ls", "kill", "doctor")


def gui_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="portglance",
        description="See which dev servers are listening on your machine, at a glance.",
        epilog=(
            "terminal commands:\n"
            "  portglance list [--all] [--json]   print listening ports\n"
            "  portglance kill PORT [--yes]       stop whatever listens on PORT\n"
            "  portglance doctor                  check the environment, for bug reports\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("-V", "--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument(
        "--background",
        action="store_true",
        help="start with only the top-panel indicator (used at login)",
    )
    parser.add_argument("--widget", action="store_true", help="show the desktop widget")
    parser.add_argument(
        "--demo",
        action="store_true",
        help="start a few fake dev servers in throwaway projects and show them",
    )
    parser.add_argument("--quit", action="store_true", help="quit the running instance")
    return parser


def cli_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="portglance")
    sub = parser.add_subparsers(dest="command", required=True)

    list_cmd = sub.add_parser("list", aliases=["ls"], help="print listening ports")
    list_cmd.add_argument("-a", "--all", action="store_true", help="include system ports")
    list_cmd.add_argument("-u", "--udp", action="store_true", help="include UDP sockets")
    list_cmd.add_argument("--json", action="store_true", help="machine-readable output")
    list_cmd.add_argument("--no-docker", action="store_true", help="skip Docker/Podman")

    kill_cmd = sub.add_parser("kill", help="stop the process or container on a port")
    kill_cmd.add_argument("port", type=int)
    kill_cmd.add_argument("-y", "--yes", action="store_true", help="do not ask for confirmation")
    kill_cmd.add_argument(
        "-t",
        "--timeout",
        type=float,
        default=5.0,
        help="seconds to wait after SIGTERM before sending SIGKILL (default: 5)",
    )
    kill_cmd.add_argument("-f", "--force", action="store_true", help="send SIGKILL straight away")
    kill_cmd.add_argument("--no-docker", action="store_true", help="skip Docker/Podman")

    sub.add_parser("doctor", help="check the environment PortGlance runs in")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if args and args[0] in CLI_COMMANDS:
        options = cli_parser().parse_args(args)
        try:
            if options.command in ("list", "ls"):
                return cmd_list(options)
            if options.command == "doctor":
                from .doctor import run_doctor

                return run_doctor()
            return cmd_kill(options)
        except BrokenPipeError:
            # Output piped into something like `head` that stopped reading.
            os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())
            return 0
        except KeyboardInterrupt:
            return 130

    options = gui_parser().parse_args(args)
    if options.demo:
        from .demo import run_demo

        return run_demo([sys.argv[0], *[a for a in args if a != "--demo"]])
    from .ui.app import run

    return run([sys.argv[0], *args])


# --------------------------------------------------------------------------
# list
# --------------------------------------------------------------------------


def take_snapshot(include_udp: bool = False, docker: bool = True) -> Snapshot:
    scanner = Scanner(docker=DockerSource() if docker else None)
    return scanner.scan(_pinned_ports(), include_udp=include_udp)


def _pinned_ports() -> list[int]:
    try:
        from .core.settings import SettingsStore

        return list(SettingsStore().settings.pinned_ports)
    except Exception:  # settings are a nicety for the CLI, never a reason to fail
        return []


def entry_to_dict(entry: PortEntry, now: float) -> dict:
    data = {
        "port": entry.port,
        "proto": entry.proto,
        "addresses": entry.addresses,
        "scope": entry.scope,
        "pid": entry.pid,
        "pids": entry.pids,
        "name": entry.name,
        "framework": entry.framework,
        "command": entry.process.command if entry.process else None,
        "cwd": entry.process.cwd if entry.process else None,
        "uid": entry.uid,
        "project": None,
        "uptime_seconds": round(now - entry.start_time, 1) if entry.start_time else None,
        "memory_bytes": entry.memory,
        "dev": entry.is_dev,
        "pinned": entry.pinned,
        "url": entry.url if entry.http else None,
        "container": None,
    }
    if entry.project is not None:
        data["project"] = {
            "name": entry.project.name,
            "root": entry.project.root,
            "kind": entry.project.kind,
            "branch": entry.project.branch,
            "subpath": entry.subpath,
        }
    if entry.container is not None:
        data["container"] = {
            "id": entry.container.id,
            "name": entry.container.name,
            "image": entry.container.image,
            "runtime": entry.container.runtime,
            "container_port": entry.container_port,
            "status": entry.container.status,
        }
    return data


class _Style:
    def __init__(self, enabled: bool) -> None:
        self.enabled = enabled

    def __call__(self, text: str, code: str) -> str:
        return f"\033[{code}m{text}\033[0m" if self.enabled and text else text


def _use_color(stream) -> bool:
    return stream.isatty() and "NO_COLOR" not in os.environ and os.environ.get("TERM") != "dumb"


def cmd_list(options: argparse.Namespace) -> int:
    snapshot = take_snapshot(include_udp=options.udp, docker=not options.no_docker)
    entries = snapshot.entries if options.all else snapshot.dev_entries
    now = time.time()
    if options.json:
        json.dump([entry_to_dict(e, now) for e in entries], sys.stdout, indent=2)
        sys.stdout.write("\n")
        return 0
    if not entries:
        hint = "" if options.all else "  (try --all)"
        print(f"No {'listening' if options.all else 'dev'} ports.{hint}")
        return 0

    style = _Style(_use_color(sys.stdout))
    home = os.path.expanduser("~")
    rows = []
    for entry in entries:
        project = ""
        if entry.project is not None:
            project = entry.project.name
            if entry.subpath:
                project += f"/{entry.subpath}"
            if entry.project.branch:
                project += f" ({entry.project.branch})"
        elif entry.process is not None and entry.process.cwd:
            project = shorten_path(entry.process.cwd, home)
        name = entry.name
        if entry.container is not None:
            name = f"{entry.container.name} [{entry.container.runtime}]"
        elif entry.process is None:
            name = f"? ({user_name(entry.uid)})"
        rows.append(
            [
                str(entry.port) + ("/udp" if entry.proto == "udp" else ""),
                str(entry.pid) if entry.pid else "-",
                name,
                entry.framework or "",
                project,
                format_duration(now - entry.start_time) if entry.start_time else "-",
                format_bytes(entry.memory) if entry.memory is not None else "-",
                describe_addresses(entry.addresses),
            ]
        )
    headers = ["PORT", "PID", "PROCESS", "KIND", "PROJECT", "UPTIME", "MEMORY", "LISTENING ON"]
    widths = [max(len(h), *(len(r[i]) for r in rows)) for i, h in enumerate(headers)]
    max_width = shutil.get_terminal_size((120, 20)).columns

    def fmt(cells: list[str]) -> str:
        line = "  ".join(c.ljust(w) for c, w in zip(cells, widths, strict=True)).rstrip()
        return line[:max_width] if not style.enabled else line

    print(style(fmt(headers), "1"))
    for row, entry in zip(rows, entries, strict=True):
        line = fmt(row)
        if style.enabled:
            port = row[0]
            line = line.replace(port, style(port, "1;36"), 1)
            if entry.scope == "network":
                line = line.replace(row[7], style(row[7], "33"))
        print(line)
    if not options.all:
        hidden = len(snapshot.entries) - len(entries)
        if hidden:
            print(style(f"\n{hidden} system port(s) hidden. Use --all to show them.", "2"))
    return 0


# --------------------------------------------------------------------------
# kill
# --------------------------------------------------------------------------


def _describe(entry: PortEntry) -> str:
    if entry.container is not None:
        return f"container {entry.container.name} ({entry.container.image})"
    parts = [entry.name]
    if entry.pid:
        parts.append(f"PID {entry.pid}")
    if entry.project is not None:
        parts.append(entry.project.name)
    return f"{parts[0]} ({', '.join(parts[1:])})" if len(parts) > 1 else parts[0]


def _confirm(question: str) -> bool:
    if not sys.stdin.isatty():
        print(f"{question} [y/N] refusing without a terminal; pass --yes", file=sys.stderr)
        return False
    try:
        answer = input(f"{question} [y/N] ")
    except EOFError:
        return False
    return answer.strip().lower() in ("y", "yes")


def cmd_kill(options: argparse.Namespace) -> int:
    snapshot = take_snapshot(include_udp=True, docker=not options.no_docker)
    entries = snapshot.find(options.port)
    if not entries:
        print(f"Nothing is listening on port {options.port}.")
        return 1

    status = 0
    for entry in entries:
        description = _describe(entry)
        if not entry.killable:
            owner = f"user {user_name(entry.uid)}" if entry.uid is not None else "another user"
            print(
                f"Port {entry.port}/{entry.proto} belongs to a process of {owner} that "
                f"{APP_NAME} cannot see or stop.\n"
                f"Try: sudo fuser -k {entry.port}/{entry.proto}"
            )
            status = 1
            continue
        if not options.yes and not _confirm(f"Stop {description} on port {entry.port}?"):
            print("Cancelled.")
            status = 1
            continue
        if entry.container is not None:
            status |= _stop_container(entry, options)
        else:
            status |= _stop_process(entry, options)
    return status


def _stop_container(entry: PortEntry, options: argparse.Namespace) -> int:
    from .core.docker import DockerClient

    assert entry.container is not None
    client = DockerClient.discover()
    if client is None:
        print("Cannot reach Docker/Podman.", file=sys.stderr)
        return 1
    timeout = 0 if options.force else options.timeout
    print(f"Stopping container {entry.container.name}…", flush=True)
    try:
        client.stop(entry.container.id, timeout)
    except DockerError as exc:
        print(f"Failed: {exc}", file=sys.stderr)
        return 1
    print("Stopped.")
    return 0


def _stop_process(entry: PortEntry, options: argparse.Namespace) -> int:
    assert entry.pid is not None
    process = entry.process
    timeout = 0.0 if options.force else options.timeout
    signal_name = "SIGKILL" if options.force else "SIGTERM"
    print(f"Sending {signal_name} to {entry.name} (PID {entry.pid})…", flush=True)
    companions = [(pid, None) for pid in entry.pids if pid != entry.pid]
    result = killer.terminate(
        entry.pid,
        timeout=timeout,
        start_ticks=process.start_ticks if process else None,
        companions=companions,
        on_escalate=(
            None
            if options.force
            else lambda: print(f"Still running after {timeout:g}s, sending SIGKILL…", flush=True)
        ),
    )
    messages = {
        killer.TERMINATED: f"Stopped in {result.elapsed:.1f}s.",
        killer.KILLED: f"Killed after {result.elapsed:.1f}s.",
        killer.GONE: "It had already exited.",
    }
    if result.ok:
        print(messages[result.outcome])
        return 0
    print(f"Failed: {result.message}", file=sys.stderr)
    if result.outcome == killer.DENIED:
        print(f"Try: sudo kill {entry.pid}", file=sys.stderr)
    return 1
