"""Start-on-login through an XDG autostart entry.

The entry lives in ``$XDG_CONFIG_HOME/autostart`` and starts PortGlance with
``--background``, so only the top-panel indicator appears at login. The
file itself is the source of truth, so disabling the app in GNOME Tweaks'
"Startup Applications" is respected too.
"""

from __future__ import annotations

import configparser
import os
import shutil
import sys
from collections.abc import Sequence
from pathlib import Path

from .. import APP_ID, APP_NAME

DESKTOP_FILE = f"{APP_ID}.desktop"
BACKGROUND_FLAG = "--background"

_RESERVED = frozenset(" \t\n\"'\\><~|&;$*?#()`")


def autostart_dir() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.join(os.path.expanduser("~"), ".config")
    return Path(base) / "autostart"


def autostart_path() -> Path:
    return autostart_dir() / DESKTOP_FILE


def quote_exec_arg(arg: str) -> str:
    """Quote one argument for an ``Exec=`` key (Desktop Entry spec, "Exec key")."""
    arg = arg.replace("%", "%%")
    if arg and not any(ch in _RESERVED for ch in arg):
        return arg
    escaped = "".join("\\" + ch if ch in '"`$\\' else ch for ch in arg)
    return f'"{escaped}"'


def exec_value(args: Sequence[str]) -> str:
    line = " ".join(quote_exec_arg(a) for a in args)
    # String values get one more level of backslash escaping.
    return line.replace("\\", "\\\\")


def launch_command(argv0: str | None = None) -> list[str]:
    """The command that starts the PortGlance installation that is running now."""
    appimage = os.environ.get("APPIMAGE")
    if appimage and os.path.isfile(appimage):
        return [appimage]
    argv0 = sys.argv[0] if argv0 is None else argv0
    if os.path.basename(argv0) == "portglance":
        resolved = argv0 if os.sep in argv0 else shutil.which(argv0)
        if resolved and os.access(resolved, os.X_OK):
            return [os.path.abspath(resolved)]
    package_parent = str(Path(__file__).resolve().parents[2])
    return ["/usr/bin/env", f"PYTHONPATH={package_parent}", sys.executable, "-m", "portglance"]


def render_entry(command: Sequence[str]) -> str:
    return "\n".join(
        [
            "[Desktop Entry]",
            "Type=Application",
            f"Name={APP_NAME}",
            "Comment=See which dev servers are listening, at a glance",
            f"Exec={exec_value([*command, BACKGROUND_FLAG])}",
            f"Icon={APP_ID}",
            "Terminal=false",
            "X-GNOME-Autostart-enabled=true",
            "",
        ]
    )


def _read_entry(path: Path) -> configparser.SectionProxy | None:
    parser = configparser.ConfigParser(interpolation=None, strict=False)
    parser.optionxform = str  # keys are case-sensitive
    try:
        parser.read_string(path.read_text(encoding="utf-8", errors="replace"), str(path))
    except (OSError, configparser.Error):
        return None
    if not parser.has_section("Desktop Entry"):
        return None
    return parser["Desktop Entry"]


def is_enabled(path: Path | None = None) -> bool:
    entry = _read_entry(path or autostart_path())
    if entry is None:
        return False
    if entry.get("Hidden", "false").strip().lower() == "true":
        return False
    return entry.get("X-GNOME-Autostart-enabled", "true").strip().lower() != "false"


def set_enabled(
    enabled: bool, command: Sequence[str] | None = None, path: Path | None = None
) -> None:
    path = path or autostart_path()
    if not enabled:
        try:
            path.unlink()
        except FileNotFoundError:
            pass
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_text(render_entry(command or launch_command()), encoding="utf-8")
    os.replace(tmp, path)


def refresh(command: Sequence[str] | None = None, path: Path | None = None) -> None:
    """Rewrite an enabled entry so ``Exec=`` follows a moved AppImage or reinstall."""
    path = path or autostart_path()
    if not is_enabled(path):
        return
    desired = render_entry(command or launch_command())
    try:
        current = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        current = ""
    if current != desired:
        set_enabled(True, command, path)
