"""User preferences, stored as JSON in ``$XDG_CONFIG_HOME/portglance``."""

from __future__ import annotations

import json
import os
import tempfile
from collections.abc import Callable
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Any


def config_dir() -> Path:
    override = os.environ.get("PORTGLANCE_CONFIG_DIR")
    if override:
        return Path(override)
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.join(os.path.expanduser("~"), ".config")
    return Path(base) / "portglance"


@dataclass(slots=True)
class Settings:
    show_all: bool = False
    include_udp: bool = False
    pinned_ports: list[int] = field(default_factory=list)
    confirm_kill: bool = True
    kill_timeout: float = 5.0
    docker_enabled: bool = True
    refresh_interval: float = 3.0
    widget_visible: bool = False
    widget_pinned: bool = True

    def normalized(self) -> Settings:
        ports = sorted({int(p) for p in self.pinned_ports if 0 < int(p) < 65536})
        return Settings(
            show_all=bool(self.show_all),
            include_udp=bool(self.include_udp),
            pinned_ports=ports,
            confirm_kill=bool(self.confirm_kill),
            kill_timeout=min(max(float(self.kill_timeout), 0.5), 60.0),
            docker_enabled=bool(self.docker_enabled),
            refresh_interval=min(max(float(self.refresh_interval), 1.0), 60.0),
            widget_visible=bool(self.widget_visible),
            widget_pinned=bool(self.widget_pinned),
        )


Listener = Callable[[str, Any], None]


class SettingsStore:
    """Loads, saves and broadcasts changes to :class:`Settings`."""

    FILENAME = "settings.json"

    def __init__(self, directory: Path | None = None) -> None:
        self.path = (directory or config_dir()) / self.FILENAME
        self.is_first_run = not self.path.exists()
        self.settings = self._load()
        self._listeners: dict[int, Listener] = {}
        self._next_id = 1

    def get(self, key: str) -> Any:
        return getattr(self.settings, key)

    def set(self, key: str, value: Any) -> None:
        if key not in {f.name for f in fields(Settings)}:
            raise KeyError(key)
        candidate = Settings(**{**asdict(self.settings), key: value}).normalized()
        new_value = getattr(candidate, key)
        if new_value == getattr(self.settings, key):
            return
        self.settings = candidate
        self.save()
        for listener in list(self._listeners.values()):
            listener(key, new_value)

    def toggle_pinned(self, port: int) -> bool:
        """Pin ``port`` if it is not pinned yet, unpin it otherwise. Returns the new state."""
        ports = set(self.settings.pinned_ports)
        pinned = port not in ports
        ports.symmetric_difference_update({port})
        self.set("pinned_ports", sorted(ports))
        return pinned

    def connect(self, listener: Listener) -> int:
        handler_id = self._next_id
        self._next_id += 1
        self._listeners[handler_id] = listener
        return handler_id

    def disconnect(self, handler_id: int) -> None:
        self._listeners.pop(handler_id, None)

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        data = json.dumps(asdict(self.settings), indent=2, sort_keys=True) + "\n"
        fd, tmp = tempfile.mkstemp(prefix=".settings-", dir=self.path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(data)
            os.replace(tmp, self.path)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
        self.is_first_run = False

    def _load(self) -> Settings:
        try:
            with open(self.path, encoding="utf-8") as fh:
                raw = json.load(fh)
        except FileNotFoundError:
            return Settings()
        except (OSError, ValueError):
            return Settings()
        if not isinstance(raw, dict):
            return Settings()
        defaults = Settings()
        values: dict[str, Any] = {}
        for f in fields(Settings):
            default = getattr(defaults, f.name)
            value = raw.get(f.name, default)
            if isinstance(default, (bool, list)) and not isinstance(value, type(default)):
                value = default  # wrong type in the file: fall back to the default
            values[f.name] = value
        try:
            return Settings(**values).normalized()
        except (TypeError, ValueError):
            return Settings()
