from __future__ import annotations

import json
from pathlib import Path

from portglance.core.settings import Settings, SettingsStore, config_dir


def test_defaults_on_first_run(tmp_path: Path) -> None:
    store = SettingsStore(tmp_path)

    assert store.is_first_run
    assert store.settings == Settings()
    assert store.get("confirm_kill") is True
    assert store.get("kill_timeout") == 5.0


def test_changes_are_persisted_and_broadcast(tmp_path: Path) -> None:
    store = SettingsStore(tmp_path)
    seen = []
    store.connect(lambda key, value: seen.append((key, value)))

    store.set("show_all", True)
    store.set("show_all", True)  # no-op: unchanged
    store.set("kill_timeout", 8)

    assert seen == [("show_all", True), ("kill_timeout", 8.0)]
    reloaded = SettingsStore(tmp_path)
    assert not reloaded.is_first_run
    assert reloaded.get("show_all") is True
    assert reloaded.get("kill_timeout") == 8.0


def test_values_are_normalised(tmp_path: Path) -> None:
    store = SettingsStore(tmp_path)

    store.set("pinned_ports", [8000, 3000, 3000, 70000, 0])
    store.set("kill_timeout", 999)
    store.set("refresh_interval", 0)

    assert store.get("pinned_ports") == [3000, 8000]
    assert store.get("kill_timeout") == 60.0
    assert store.get("refresh_interval") == 1.0


def test_toggle_pinned(tmp_path: Path) -> None:
    store = SettingsStore(tmp_path)

    assert store.toggle_pinned(5173) is True
    assert store.toggle_pinned(3000) is True
    assert store.get("pinned_ports") == [3000, 5173]
    assert store.toggle_pinned(5173) is False
    assert store.get("pinned_ports") == [3000]


def test_corrupt_or_wrongly_typed_file_falls_back_to_defaults(tmp_path: Path) -> None:
    (tmp_path / "settings.json").write_text("{not json")
    assert SettingsStore(tmp_path).settings == Settings()

    (tmp_path / "settings.json").write_text(
        json.dumps({"show_all": "yes", "pinned_ports": "3000", "kill_timeout": 2, "extra": 1})
    )
    settings = SettingsStore(tmp_path).settings
    assert settings.show_all is False
    assert settings.pinned_ports == []
    assert settings.kill_timeout == 2.0


def test_unknown_key_is_rejected(tmp_path: Path) -> None:
    store = SettingsStore(tmp_path)
    try:
        store.set("nope", 1)
    except KeyError:
        pass
    else:
        raise AssertionError("expected KeyError")


def test_config_dir_honours_xdg_and_override(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.delenv("PORTGLANCE_CONFIG_DIR", raising=False)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    assert config_dir() == tmp_path / "xdg" / "portglance"

    monkeypatch.setenv("PORTGLANCE_CONFIG_DIR", str(tmp_path / "custom"))
    assert config_dir() == tmp_path / "custom"
