from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from fakeproc import FakeProc  # noqa: E402


@pytest.fixture
def fake_proc(tmp_path: Path) -> FakeProc:
    return FakeProc(tmp_path / "proc")


@pytest.fixture
def home(tmp_path: Path) -> Path:
    path = tmp_path / "home" / "dev"
    path.mkdir(parents=True)
    return path


def make_repo(path: Path, branch: str = "main") -> Path:
    """Create a minimal git repository layout (no git binary needed)."""
    (path / ".git" / "refs" / "heads").mkdir(parents=True)
    (path / ".git" / "HEAD").write_text(f"ref: refs/heads/{branch}\n")
    return path


@pytest.fixture
def repo_factory():
    return make_repo


@pytest.fixture
def git_available() -> bool:
    try:
        subprocess.run(["git", "--version"], check=True, capture_output=True)
    except (OSError, subprocess.CalledProcessError):
        return False
    return True
