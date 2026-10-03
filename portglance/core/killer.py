"""Stop processes politely: SIGTERM first, SIGKILL if they outlive a timeout.

Signals are sent through pidfds where the kernel supports them (Linux 5.3+).
A pidfd refers to one specific process, so a PID that gets recycled between
"user clicked Stop" and "signal sent" can never hit an unrelated process.
"""

from __future__ import annotations

import math
import os
import select
import signal
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass

from .procfs import read_stat

TERMINATED = "terminated"  # exited after SIGTERM
KILLED = "killed"  # needed SIGKILL
GONE = "gone"  # had already exited
DENIED = "denied"  # not allowed to signal it (another user's process)
FAILED = "failed"  # still running even after SIGKILL

KILL_GRACE = 2.0  # how long to wait for the process to vanish after SIGKILL


@dataclass(frozen=True, slots=True)
class KillResult:
    pid: int
    outcome: str
    elapsed: float
    message: str = ""

    @property
    def ok(self) -> bool:
        return self.outcome in (TERMINATED, KILLED, GONE)


class _Target:
    """A process pinned by a pidfd (or, on old kernels, by PID and start time)."""

    def __init__(self, pid: int, start_ticks: int | None, proc_root: str) -> None:
        self.pid = pid
        self.start_ticks = start_ticks
        self.proc_root = proc_root
        self.fd: int | None = None
        try:
            self.fd = os.pidfd_open(pid)
        except (AttributeError, OSError):
            self.fd = None

    def close(self) -> None:
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None

    def same_process(self) -> bool:
        stat = read_stat(self.pid, self.proc_root)
        if stat is None:
            return False
        return self.start_ticks is None or stat[2] == self.start_ticks

    def alive(self) -> bool:
        if self.fd is not None:
            poller = select.poll()
            poller.register(self.fd, select.POLLIN)
            return not poller.poll(0)
        stat_path = f"{self.proc_root}/{self.pid}/stat"
        try:
            with open(stat_path, encoding="utf-8", errors="replace") as fh:
                text = fh.read()
        except OSError:
            return False
        state = text[text.rindex(")") + 2 :].split(" ", 1)[0]
        return state not in ("Z", "X") and self.same_process()

    def signal(self, sig: int) -> None:
        if self.fd is not None and hasattr(signal, "pidfd_send_signal"):
            signal.pidfd_send_signal(self.fd, sig)
        else:
            os.kill(self.pid, sig)

    def wait(self, timeout: float) -> bool:
        """Wait up to ``timeout`` seconds for the process to exit."""
        deadline = time.monotonic() + timeout
        if self.fd is not None:
            poller = select.poll()
            poller.register(self.fd, select.POLLIN)
            remaining = max(0.0, deadline - time.monotonic())
            # Round up: never give up (and escalate to SIGKILL) before the deadline.
            return bool(poller.poll(math.ceil(remaining * 1000)))
        while time.monotonic() < deadline:
            if not self.alive():
                return True
            time.sleep(0.05)
        return not self.alive()


def terminate(
    pid: int,
    *,
    timeout: float = 5.0,
    start_ticks: int | None = None,
    companions: Iterable[tuple[int, int | None]] = (),
    proc_root: str = "/proc",
    on_escalate: Callable[[], None] | None = None,
) -> KillResult:
    """Send SIGTERM to ``pid``; send SIGKILL if it is still alive after ``timeout``.

    ``start_ticks`` is the process start time as shown to the user. When it
    no longer matches, the process has exited and its PID may have been
    reused, so nothing is signalled. ``companions`` are other ``(pid,
    start_ticks)`` processes holding the same socket (pre-fork workers); they
    are given the same deadline and force-killed along with the main process.
    """
    started = time.monotonic()

    def result(outcome: str, message: str = "") -> KillResult:
        return KillResult(pid, outcome, time.monotonic() - started, message)

    main = _Target(pid, start_ticks, proc_root)
    others = [_Target(p, t, proc_root) for p, t in companions if p != pid]
    targets = [main, *others]
    try:
        if not main.same_process():
            return result(GONE, "The process had already exited.")
        try:
            main.signal(signal.SIGTERM)
        except ProcessLookupError:
            return result(GONE, "The process had already exited.")
        except PermissionError:
            return result(DENIED, "Permission denied: the process belongs to another user.")

        deadline = started + timeout
        for target in targets:
            target.wait(max(0.0, deadline - time.monotonic()))
        survivors = [t for t in targets if t.alive()]
        if not survivors:
            return result(TERMINATED)

        if on_escalate is not None:
            on_escalate()
        for target in survivors:
            try:
                target.signal(signal.SIGKILL)
            except ProcessLookupError:
                pass
            except PermissionError:
                return result(DENIED, "Permission denied while force-killing the process.")
        for target in survivors:
            target.wait(KILL_GRACE)
        if any(t.alive() for t in survivors):
            return result(FAILED, "The process is still running after SIGKILL.")
        return result(KILLED)
    finally:
        for target in targets:
            target.close()
