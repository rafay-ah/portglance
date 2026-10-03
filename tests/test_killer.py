from __future__ import annotations

import os
import subprocess
import sys
import time

import pytest

from portglance.core import killer
from portglance.core.procfs import read_stat

GRACEFUL = "import time\nwhile True: time.sleep(0.1)\n"
STUBBORN = (
    "import signal, sys, time\n"
    "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
    "sys.stdout.write('ready\\n'); sys.stdout.flush()\n"
    "while True: time.sleep(0.1)\n"
)


def spawn(code: str) -> subprocess.Popen:
    proc = subprocess.Popen([sys.executable, "-c", code], stdout=subprocess.PIPE, text=True)
    if "ready" in code:
        assert proc.stdout is not None
        assert proc.stdout.readline().strip() == "ready"
    else:
        time.sleep(0.2)
    return proc


def start_ticks(pid: int) -> int:
    stat = read_stat(pid)
    assert stat is not None
    return stat[2]


def test_sigterm_is_enough_for_well_behaved_processes() -> None:
    proc = spawn(GRACEFUL)
    try:
        result = killer.terminate(proc.pid, timeout=5, start_ticks=start_ticks(proc.pid))
    finally:
        proc.kill()
        proc.wait()

    assert result.outcome == killer.TERMINATED
    assert result.ok
    assert result.elapsed < 5


def test_escalates_to_sigkill_after_timeout() -> None:
    proc = spawn(STUBBORN)
    escalated = []
    try:
        result = killer.terminate(
            proc.pid,
            timeout=0.4,
            start_ticks=start_ticks(proc.pid),
            on_escalate=lambda: escalated.append(True),
        )
    finally:
        proc.kill()
        proc.wait()

    assert result.outcome == killer.KILLED
    assert escalated == [True]
    assert result.elapsed >= 0.4
    assert proc.returncode == -9


def test_companion_workers_are_killed_too() -> None:
    main = spawn(GRACEFUL)
    worker = spawn(STUBBORN)
    try:
        result = killer.terminate(
            main.pid,
            timeout=0.4,
            start_ticks=start_ticks(main.pid),
            companions=[(worker.pid, start_ticks(worker.pid))],
        )
    finally:
        for proc in (main, worker):
            proc.kill()
            proc.wait()

    assert result.outcome == killer.KILLED
    assert worker.returncode == -9


def test_mismatched_start_time_means_the_process_is_gone() -> None:
    """A recycled PID must never be signalled."""
    proc = spawn(GRACEFUL)
    try:
        result = killer.terminate(proc.pid, timeout=1, start_ticks=start_ticks(proc.pid) + 1)
        assert proc.poll() is None  # untouched
    finally:
        proc.kill()
        proc.wait()

    assert result.outcome == killer.GONE


def test_exited_process_is_gone() -> None:
    proc = spawn(GRACEFUL)
    pid = proc.pid
    proc.kill()
    proc.wait()

    assert killer.terminate(pid, timeout=1).outcome == killer.GONE


@pytest.mark.skipif(os.geteuid() == 0, reason="root may signal any process")
def test_other_users_process_is_denied() -> None:
    result = killer.terminate(1, timeout=0.1)

    assert result.outcome == killer.DENIED
    assert not result.ok
