"""Keeps the port snapshot fresh without heavy polling.

* Scans run in a worker thread, so a slow socket-owner walk never blocks the UI.
* Every listening process is watched through a pidfd: when one exits, the
  list updates immediately instead of on the next tick.
* Docker/Podman changes arrive through the Engine API events stream.
* The periodic tick, which only re-reads the kernel's socket tables, slows
  down while no window is visible.
"""

from __future__ import annotations

import os
import threading
from collections.abc import Callable

from gi.repository import GLib, GObject

from ..core.docker import DockerSource
from ..core.scanner import Scanner, Snapshot
from ..core.settings import SettingsStore

#: Interval multiplier and floor while only the panel indicator is visible.
BACKGROUND_FACTOR = 3
BACKGROUND_MIN = 6.0


class PortMonitor(GObject.Object):
    __gsignals__ = {
        "updated": (GObject.SignalFlags.RUN_FIRST, None, (object,)),
    }

    def __init__(
        self,
        settings: SettingsStore,
        *,
        docker: DockerSource | None = None,
        scanner_factory: Callable[[DockerSource | None], Scanner] | None = None,
    ) -> None:
        super().__init__()
        self.settings = settings
        self.docker = docker if docker is not None else DockerSource()
        self.docker.on_change = self.refresh_soon
        factory = scanner_factory or (lambda d: Scanner(docker=d))
        self._scanner = factory(self.docker)
        self._docker_enabled = settings.get("docker_enabled")
        self._scanner.docker = self.docker if self._docker_enabled else None
        self.snapshot: Snapshot | None = None

        self._lock = threading.Lock()
        self._busy = False
        self._again = False
        self._timer_id = 0
        self._soon_id = 0
        self._foreground = False
        self._watches: dict[int, tuple[int, int]] = {}  # pid -> (pidfd, source id)
        self._running = False
        settings.connect(self._on_setting_changed)

    # -- lifecycle -------------------------------------------------------------

    def start(self) -> None:
        self._running = True
        if self._docker_enabled:
            self.docker.start_events()
        self.refresh()
        self._schedule_tick()

    def stop(self) -> None:
        self._running = False
        if self._timer_id:
            GLib.source_remove(self._timer_id)
            self._timer_id = 0
        self.docker.stop_events()
        for pid in list(self._watches):
            self._unwatch(pid)

    def set_foreground(self, foreground: bool) -> None:
        """Refresh faster while a window showing ports is visible."""
        if foreground == self._foreground:
            return
        self._foreground = foreground
        if foreground:
            self.refresh()
        self._schedule_tick()

    # -- refreshing ------------------------------------------------------------

    def refresh(self) -> None:
        """Scan now (or right after the scan that is already running)."""
        if not self._running:
            return
        with self._lock:
            if self._busy:
                self._again = True
                return
            self._busy = True
        pinned = tuple(self.settings.get("pinned_ports"))
        include_udp = self.settings.get("include_udp")
        thread = threading.Thread(target=self._scan_worker, args=(pinned, include_udp), daemon=True)
        thread.start()

    def refresh_soon(self, delay_ms: int = 150) -> None:
        """Thread-safe, debounced refresh request."""

        def schedule() -> bool:
            if not self._soon_id:
                self._soon_id = GLib.timeout_add(delay_ms, self._on_soon)
            return GLib.SOURCE_REMOVE

        GLib.idle_add(schedule)

    def _on_soon(self) -> bool:
        self._soon_id = 0
        self.refresh()
        return GLib.SOURCE_REMOVE

    def _scan_worker(self, pinned: tuple[int, ...], include_udp: bool) -> None:
        try:
            snapshot = self._scanner.scan(pinned, include_udp=include_udp)
        except Exception as exc:  # never let a scan error kill the monitor
            snapshot = None
            GLib.idle_add(_log_error, exc)
        GLib.idle_add(self._deliver, snapshot)

    def _deliver(self, snapshot: Snapshot | None) -> bool:
        with self._lock:
            self._busy = False
            again = self._again
            self._again = False
        if snapshot is not None and self._running:
            self.snapshot = snapshot
            self._sync_watches(snapshot)
            self.emit("updated", snapshot)
        if again:
            self.refresh()
        return GLib.SOURCE_REMOVE

    def _schedule_tick(self) -> None:
        if self._timer_id:
            GLib.source_remove(self._timer_id)
            self._timer_id = 0
        if not self._running:
            return
        interval = float(self.settings.get("refresh_interval"))
        if not self._foreground:
            interval = max(interval * BACKGROUND_FACTOR, BACKGROUND_MIN)
        self._timer_id = GLib.timeout_add(int(interval * 1000), self._on_tick)

    def _on_tick(self) -> bool:
        self.refresh()
        return GLib.SOURCE_CONTINUE

    def _on_setting_changed(self, key: str, value) -> None:
        if key == "refresh_interval":
            self._schedule_tick()
        elif key == "docker_enabled":
            self._docker_enabled = bool(value)
            self._scanner.docker = self.docker if value else None
            if value and self._running:
                self.docker.invalidate()
                self.docker.start_events()
            else:
                self.docker.stop_events()
            self.refresh()
        elif key in ("pinned_ports", "include_udp"):
            self.refresh()

    # -- process exit watches ----------------------------------------------------

    def _sync_watches(self, snapshot: Snapshot) -> None:
        wanted = {pid for entry in snapshot.entries for pid in entry.pids}
        for pid in list(self._watches):
            if pid not in wanted:
                self._unwatch(pid)
        for pid in wanted - set(self._watches):
            self._watch(pid)

    def _watch(self, pid: int) -> None:
        try:
            fd = os.pidfd_open(pid)
        except (AttributeError, OSError):
            return
        source = GLib.unix_fd_add_full(
            GLib.PRIORITY_DEFAULT, fd, GLib.IOCondition.IN, self._on_exit, pid
        )
        self._watches[pid] = (fd, source)

    def _unwatch(self, pid: int) -> None:
        fd, source = self._watches.pop(pid)
        GLib.source_remove(source)
        os.close(fd)

    def _on_exit(self, _fd: int, _condition, pid: int) -> bool:
        fd, _source = self._watches.pop(pid, (None, None))
        if fd is not None:
            os.close(fd)
        self.refresh_soon(60)
        return GLib.SOURCE_REMOVE


def _log_error(exc: Exception) -> bool:
    import traceback

    traceback.print_exception(exc)
    return GLib.SOURCE_REMOVE
