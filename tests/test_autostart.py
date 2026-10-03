from __future__ import annotations

from pathlib import Path

import pytest

from portglance import APP_ID
from portglance.core import autostart


@pytest.fixture(autouse=True)
def xdg_config(monkeypatch, tmp_path: Path) -> Path:
    path = tmp_path / "config"
    monkeypatch.setenv("XDG_CONFIG_HOME", str(path))
    monkeypatch.delenv("APPIMAGE", raising=False)
    return path


@pytest.mark.parametrize(
    ("arg", "quoted"),
    [
        ("portglance", "portglance"),
        ("/opt/apps/PortGlance.AppImage", "/opt/apps/PortGlance.AppImage"),
        ("/home/me/My Apps/PortGlance.AppImage", '"/home/me/My Apps/PortGlance.AppImage"'),
        ("100%", "100%%"),
        ('say "hi"', '"say \\"hi\\""'),
        ("$HOME", '"\\$HOME"'),
    ],
)
def test_quote_exec_arg(arg: str, quoted: str) -> None:
    assert autostart.quote_exec_arg(arg) == quoted


def test_exec_value_escapes_backslashes_for_string_values() -> None:
    assert autostart.exec_value(["/a b/c\\d"]) == '"/a b/c\\\\\\\\d"'


def test_enable_and_disable(xdg_config: Path) -> None:
    path = xdg_config / "autostart" / f"{APP_ID}.desktop"
    assert not autostart.is_enabled()

    autostart.set_enabled(True, ["/usr/bin/portglance"])

    assert autostart.is_enabled()
    text = path.read_text()
    assert "Exec=/usr/bin/portglance --background\n" in text
    assert f"Icon={APP_ID}\n" in text
    assert "X-GNOME-Autostart-enabled=true" in text

    autostart.set_enabled(False)
    assert not path.exists()
    assert not autostart.is_enabled()
    autostart.set_enabled(False)  # idempotent


@pytest.mark.parametrize("line", ["Hidden=true", "X-GNOME-Autostart-enabled=false"])
def test_entry_disabled_by_other_tools(xdg_config: Path, line: str) -> None:
    autostart.set_enabled(True, ["portglance"])
    path = autostart.autostart_path()
    path.write_text(path.read_text().replace("X-GNOME-Autostart-enabled=true", line))

    assert not autostart.is_enabled()


def test_refresh_follows_a_moved_appimage(xdg_config: Path) -> None:
    autostart.set_enabled(True, ["/old/PortGlance.AppImage"])

    autostart.refresh(["/new/PortGlance.AppImage"])

    assert "Exec=/new/PortGlance.AppImage --background" in autostart.autostart_path().read_text()


def test_entry_edited_into_another_encoding(xdg_config: Path) -> None:
    autostart.set_enabled(True, ["/old/PortGlance.AppImage"])
    path = autostart.autostart_path()
    path.write_bytes(path.read_bytes() + b"Comment[fr]=D\xe9marrage\n")  # Latin-1

    assert autostart.is_enabled()
    autostart.refresh(["/new/PortGlance.AppImage"])
    assert "Exec=/new/PortGlance.AppImage --background" in path.read_text()


def test_refresh_does_not_enable(xdg_config: Path) -> None:
    autostart.refresh(["/usr/bin/portglance"])
    assert not autostart.autostart_path().exists()


def test_launch_command_prefers_appimage(monkeypatch, tmp_path: Path) -> None:
    image = tmp_path / "PortGlance-x86_64.AppImage"
    image.write_text("")
    monkeypatch.setenv("APPIMAGE", str(image))

    assert autostart.launch_command() == [str(image)]


def test_launch_command_for_installed_script(tmp_path: Path) -> None:
    script = tmp_path / "bin" / "portglance"
    script.parent.mkdir()
    script.write_text("#!/bin/sh\n")
    script.chmod(0o755)

    assert autostart.launch_command(str(script)) == [str(script)]


def test_launch_command_from_source_checkout() -> None:
    command = autostart.launch_command("/somewhere/__main__.py")

    assert command[0] == "/usr/bin/env"
    assert command[1].startswith("PYTHONPATH=")
    assert command[-2:] == ["-m", "portglance"]
