"""Map a process to the project it came from.

The process' working directory is the primary signal: walk up from it to the
nearest git repository root. When there is no repository, the nearest
directory containing a well-known project file (``package.json``,
``pyproject.toml``, ...) is used instead. If the working directory says
nothing useful (``/`` or ``$HOME``, as for services started by systemd or a
desktop launcher), script and interpreter paths in the command line are tried,
e.g. ``~/code/api/.venv/bin/python`` or ``node ~/code/web/server.js``.
"""

from __future__ import annotations

import os
import re
import time
from collections.abc import Iterator, Sequence
from dataclasses import dataclass

from .model import Project

#: Files that mark the root of a project when there is no git repository.
PROJECT_MARKERS = (
    "package.json",
    "pyproject.toml",
    "setup.py",
    "setup.cfg",
    "requirements.txt",
    "Pipfile",
    "manage.py",
    "Cargo.toml",
    "go.mod",
    "Gemfile",
    "composer.json",
    "pom.xml",
    "build.gradle",
    "build.gradle.kts",
    "mix.exs",
    "deno.json",
    "deno.jsonc",
    "compose.yaml",
    "compose.yml",
    "docker-compose.yaml",
    "docker-compose.yml",
)

#: Command-line paths under these prefixes are never used to find a project.
SYSTEM_PREFIXES = (
    "/bin/",
    "/boot/",
    "/dev/",
    "/etc/",
    "/gnu/",
    "/lib/",
    "/lib32/",
    "/lib64/",
    "/libx32/",
    "/nix/",
    "/opt/",
    "/proc/",
    "/run/",
    "/sbin/",
    "/snap/",
    "/sys/",
    "/usr/",
    "/var/lib/",
)

_SHA_RE = re.compile(r"[0-9a-f]{7,64}")
_CACHE_TTL = 60.0


@dataclass(frozen=True, slots=True)
class Resolution:
    project: Project | None
    #: Working directory relative to the project root, when it is a subdirectory.
    subpath: str | None = None


class ProjectResolver:
    def __init__(self, home: str | None = None, clock=time.monotonic) -> None:
        self.home = os.path.normpath(home or os.path.expanduser("~"))
        self._clock = clock
        self._roots: dict[str, tuple[float, tuple[str, str] | None]] = {}
        self._branches: dict[str, tuple[float, str | None]] = {}

    # -- public API ---------------------------------------------------------

    def resolve(self, cwd: str | None, cmdline: Sequence[str] = ()) -> Resolution:
        """Find the project of a process from its working directory and command line."""
        start = self._usable_dir(cwd)
        if start is not None:
            found = self.find_root(start)
            if found is not None:
                root, kind = found
                return Resolution(self._project(root, kind), _subpath(start, root))

        for directory in self._command_dirs(cmdline, cwd):
            found = self.find_root(directory)
            if found is not None:
                return Resolution(self._project(*found))
        return Resolution(None)

    def resolve_directory(self, directory: str, fallback_name: str | None = None) -> Project | None:
        """Resolve a directory such as a Compose project's working directory."""
        found = self.find_root(os.path.normpath(directory))
        if found is not None:
            return self._project(*found)
        if fallback_name:
            return Project(fallback_name, os.path.normpath(directory), "compose")
        return None

    def find_root(self, directory: str) -> tuple[str, str] | None:
        """Return ``(root, kind)`` for the project containing ``directory``.

        ``kind`` is ``"git"`` for a repository root (a ``.git`` directory, or
        a ``.git`` file as used by worktrees and submodules) and ``"marker"``
        for the nearest directory holding a project file. A repository root
        further up wins over a nearer marker, so a package inside a monorepo
        maps to the monorepo.
        """
        now = self._clock()
        cached = self._roots.get(directory)
        if cached is not None and now - cached[0] < _CACHE_TTL:
            return cached[1]

        result: tuple[str, str] | None = None
        marker_root: str | None = None
        for current in self._ancestors(directory):
            if os.path.lexists(os.path.join(current, ".git")):
                result = (current, "git")
                break
            if marker_root is None and any(
                os.path.isfile(os.path.join(current, marker)) for marker in PROJECT_MARKERS
            ):
                marker_root = current
        if result is None and marker_root is not None:
            result = (marker_root, "marker")

        self._roots[directory] = (now, result)
        return result

    def branch(self, root: str) -> str | None:
        """Current branch (or short commit when detached) of the repository at ``root``."""
        head = _head_file(root)
        if head is None:
            return None
        try:
            mtime = os.stat(head).st_mtime
        except OSError:
            return None
        cached = self._branches.get(head)
        if cached is not None and cached[0] == mtime:
            return cached[1]
        branch = read_branch(head)
        self._branches[head] = (mtime, branch)
        return branch

    # -- helpers ------------------------------------------------------------

    def _project(self, root: str, kind: str) -> Project:
        branch = self.branch(root) if kind == "git" else None
        return Project(os.path.basename(root) or root, root, kind, branch)

    def _usable_dir(self, cwd: str | None) -> str | None:
        if not cwd or not os.path.isabs(cwd):
            return None
        path = os.path.normpath(cwd)
        if path in ("/", self.home):
            return None
        return path

    def _ancestors(self, directory: str) -> Iterator[str]:
        """Yield ``directory`` and its parents, never ``$HOME`` or ``/``."""
        current = os.path.normpath(directory)
        while current not in ("/", self.home, ""):
            yield current
            parent = os.path.dirname(current)
            if parent == current:
                break
            current = parent

    def _command_dirs(self, cmdline: Sequence[str], cwd: str | None) -> Iterator[str]:
        args = list(cmdline)
        if len(args) == 1 and " " in args[0]:
            args = args[0].split()  # a process that rewrote its title
        seen: set[str] = set()
        for arg in args[:8]:
            if not arg or arg.startswith("-") or "/" not in arg:
                continue
            if os.path.isabs(arg):
                path = arg
            elif cwd and os.path.isabs(cwd):
                path = os.path.join(cwd, arg)
            else:
                continue
            path = os.path.normpath(path)
            if path.startswith(SYSTEM_PREFIXES) or not os.path.exists(path):
                continue
            directory = path if os.path.isdir(path) else os.path.dirname(path)
            if directory not in seen:
                seen.add(directory)
                yield directory


def _subpath(start: str, root: str) -> str | None:
    if start == root:
        return None
    rel = os.path.relpath(start, root)
    return None if rel.startswith("..") else rel


def _head_file(root: str) -> str | None:
    dotgit = os.path.join(root, ".git")
    if os.path.isdir(dotgit):
        return os.path.join(dotgit, "HEAD")
    try:
        # surrogateescape keeps a gitdir path that is not UTF-8 usable.
        with open(dotgit, encoding="utf-8", errors="surrogateescape") as fh:
            content = fh.read().strip()
    except OSError:
        return None
    if not content.startswith("gitdir:"):
        return None
    gitdir = content[len("gitdir:") :].strip()
    if not os.path.isabs(gitdir):
        gitdir = os.path.normpath(os.path.join(root, gitdir))
    return os.path.join(gitdir, "HEAD")


def read_branch(head_file: str) -> str | None:
    try:
        with open(head_file, encoding="utf-8", errors="replace") as fh:
            head = fh.read().strip()
    except OSError:
        return None
    if head.startswith("ref:"):
        ref = head[len("ref:") :].strip()
        return ref.removeprefix("refs/heads/")
    if _SHA_RE.fullmatch(head):
        return head[:7]
    return None
