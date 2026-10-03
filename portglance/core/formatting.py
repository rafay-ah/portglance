"""Human-friendly formatting shared by the CLI and the UI."""

from __future__ import annotations

import os
import pwd
from functools import lru_cache

from .model import is_loopback

_WILDCARDS = {"0.0.0.0", "::", "*"}


def format_duration(seconds: float | None) -> str:
    if seconds is None:
        return "—"
    total = int(max(0, seconds))
    if total < 60:
        return f"{total}s"
    minutes, _ = divmod(total, 60)
    if minutes < 60:
        return f"{minutes}m"
    hours, minutes = divmod(minutes, 60)
    if hours < 24:
        return f"{hours}h {minutes}m" if minutes else f"{hours}h"
    days, hours = divmod(hours, 24)
    return f"{days}d {hours}h" if hours else f"{days}d"


def format_bytes(size: int | None) -> str:
    """SI units, like GNOME's ``g_format_size()``: ``"182 MB"``, ``"1.4 GB"``."""
    if size is None:
        return "—"
    value = float(size)
    for unit in ("B", "kB", "MB", "GB", "TB"):
        if value < 1000 or unit == "TB":
            if unit == "B":
                return f"{int(value)} B"
            return f"{value:.1f} {unit}" if value < 10 else f"{value:.0f} {unit}"
        value /= 1000
    raise AssertionError("unreachable")


def describe_addresses(addresses: list[str]) -> str:
    """``"localhost"``, ``"all interfaces"`` or the specific address."""
    if not addresses:
        return "all interfaces"
    if any(a in _WILDCARDS for a in addresses):
        return "all interfaces"
    if all(is_loopback(a) for a in addresses):
        return "localhost"
    specific = [a for a in addresses if not is_loopback(a)]
    return ", ".join(dict.fromkeys(specific))


def shorten_path(path: str | None, home: str | None = None) -> str:
    if not path:
        return ""
    home = home or os.environ.get("PORTGLANCE_DISPLAY_HOME") or os.path.expanduser("~")
    if path == home:
        return "~"
    if path.startswith(home.rstrip("/") + "/"):
        return "~" + path[len(home.rstrip("/")) :]
    return path


@lru_cache(maxsize=64)
def user_name(uid: int | None) -> str:
    if uid is None:
        return "unknown"
    try:
        return pwd.getpwuid(uid).pw_name
    except KeyError:
        return str(uid)
