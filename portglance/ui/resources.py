"""Locations of bundled data files."""

from __future__ import annotations

from pathlib import Path

PACKAGE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = PACKAGE_DIR / "data"
ICONS_DIR = DATA_DIR / "icons"
UI_DIR = Path(__file__).resolve().parent
SHELL_EXTENSION_DIR = PACKAGE_DIR / "shell-extension"
