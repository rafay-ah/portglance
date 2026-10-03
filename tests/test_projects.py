"""Process-to-project mapping: working directory -> git root, with fallbacks."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest
from conftest import make_repo

from portglance.core.projects import ProjectResolver, read_branch


@pytest.fixture
def resolver(home: Path) -> ProjectResolver:
    return ProjectResolver(home=str(home))


# -- git repositories --------------------------------------------------------


def test_cwd_at_repo_root(resolver: ProjectResolver, home: Path) -> None:
    repo = make_repo(home / "code" / "acme-web")

    result = resolver.resolve(str(repo))

    assert result.project is not None
    assert result.project.name == "acme-web"
    assert result.project.root == str(repo)
    assert result.project.kind == "git"
    assert result.project.branch == "main"
    assert result.subpath is None


def test_cwd_inside_repo_walks_up_to_root(resolver: ProjectResolver, home: Path) -> None:
    repo = make_repo(home / "code" / "shop")
    nested = repo / "services" / "api" / "src"
    nested.mkdir(parents=True)

    result = resolver.resolve(str(nested))

    assert result.project is not None
    assert result.project.root == str(repo)
    assert result.subpath == os.path.join("services", "api", "src")


def test_monorepo_package_maps_to_repo_not_package(resolver: ProjectResolver, home: Path) -> None:
    repo = make_repo(home / "code" / "mono")
    web = repo / "apps" / "web"
    web.mkdir(parents=True)
    (web / "package.json").write_text("{}")

    result = resolver.resolve(str(web))

    assert result.project is not None
    assert result.project.root == str(repo)
    assert result.project.kind == "git"
    assert result.subpath == os.path.join("apps", "web")


def test_nested_repository_wins_over_outer_one(resolver: ProjectResolver, home: Path) -> None:
    outer = make_repo(home / "code" / "outer")
    inner = make_repo(outer / "vendor" / "inner", branch="develop")

    result = resolver.resolve(str(inner / "src"))

    assert result.project is not None
    assert result.project.root == str(inner)
    assert result.project.branch == "develop"


def test_worktree_with_dot_git_file(resolver: ProjectResolver, home: Path) -> None:
    main = make_repo(home / "code" / "app")
    gitdir = main / ".git" / "worktrees" / "feature"
    gitdir.mkdir(parents=True)
    (gitdir / "HEAD").write_text("ref: refs/heads/feature/login\n")
    worktree = home / "code" / "app-feature"
    worktree.mkdir(parents=True)
    (worktree / ".git").write_text(f"gitdir: {gitdir}\n")

    result = resolver.resolve(str(worktree))

    assert result.project is not None
    assert result.project.name == "app-feature"
    assert result.project.kind == "git"
    assert result.project.branch == "feature/login"


def test_submodule_with_relative_gitdir(resolver: ProjectResolver, home: Path) -> None:
    parent = make_repo(home / "code" / "parent")
    modules = parent / ".git" / "modules" / "lib"
    modules.mkdir(parents=True)
    (modules / "HEAD").write_text("ref: refs/heads/main\n")
    submodule = parent / "lib"
    submodule.mkdir()
    (submodule / ".git").write_text("gitdir: ../.git/modules/lib\n")

    result = resolver.resolve(str(submodule))

    assert result.project is not None
    assert result.project.root == str(submodule)
    assert result.project.branch == "main"


def test_detached_head_shows_short_commit(tmp_path: Path) -> None:
    head = tmp_path / "HEAD"
    head.write_text("3f2a9c1d8e7b6a5f4e3d2c1b0a9f8e7d6c5b4a3f\n")
    assert read_branch(str(head)) == "3f2a9c1"


def test_branch_follows_checkout(resolver: ProjectResolver, home: Path) -> None:
    repo = make_repo(home / "code" / "api")
    assert resolver.resolve(str(repo)).project.branch == "main"

    head = repo / ".git" / "HEAD"
    head.write_text("ref: refs/heads/fix/timeouts\n")
    stat = head.stat()
    os.utime(head, (stat.st_atime + 5, stat.st_mtime + 5))

    assert resolver.branch(str(repo)) == "fix/timeouts"


def test_real_git_repository(resolver: ProjectResolver, home: Path, git_available: bool) -> None:
    if not git_available:
        pytest.skip("git is not installed")
    repo = home / "code" / "real"
    (repo / "pkg").mkdir(parents=True)
    subprocess.run(
        ["git", "init", "-q", "-b", "trunk", str(repo)],
        check=True,
        capture_output=True,
    )

    result = resolver.resolve(str(repo / "pkg"))

    assert result.project is not None
    assert result.project.root == str(repo)
    assert result.project.branch == "trunk"
    assert result.subpath == "pkg"


# -- projects without git ----------------------------------------------------


@pytest.mark.parametrize(
    "marker", ["package.json", "pyproject.toml", "manage.py", "go.mod", "Cargo.toml"]
)
def test_marker_file_without_git(resolver: ProjectResolver, home: Path, marker: str) -> None:
    project = home / "scratch" / "prototype"
    (project / "src").mkdir(parents=True)
    (project / marker).write_text("")

    result = resolver.resolve(str(project / "src"))

    assert result.project is not None
    assert result.project.root == str(project)
    assert result.project.kind == "marker"
    assert result.project.branch is None
    assert result.subpath == "src"


def test_plain_directory_is_not_a_project(resolver: ProjectResolver, home: Path) -> None:
    downloads = home / "Downloads"
    downloads.mkdir()

    assert resolver.resolve(str(downloads)).project is None


def test_home_directory_repo_is_ignored(resolver: ProjectResolver, home: Path) -> None:
    """A dotfiles repository in $HOME must not swallow every process under ~."""
    make_repo(home)
    downloads = home / "Downloads"
    downloads.mkdir()

    assert resolver.resolve(str(downloads)).project is None
    assert resolver.resolve(str(home)).project is None


def test_projects_outside_home(resolver: ProjectResolver, tmp_path: Path) -> None:
    repo = make_repo(tmp_path / "srv" / "site")

    result = resolver.resolve(str(repo / "public"))

    assert result.project is not None
    assert result.project.root == str(repo)


def test_deleted_working_directory_still_maps_to_repo(
    resolver: ProjectResolver, home: Path
) -> None:
    repo = make_repo(home / "code" / "app")
    gone = repo / "build" / "tmp"  # removed after the server started

    result = resolver.resolve(str(gone))

    assert result.project is not None
    assert result.project.root == str(repo)


# -- command-line fallback ---------------------------------------------------


def test_root_cwd_falls_back_to_script_path(resolver: ProjectResolver, home: Path) -> None:
    repo = make_repo(home / "code" / "bot")
    (repo / "server.js").write_text("")

    result = resolver.resolve("/", ["node", str(repo / "server.js")])

    assert result.project is not None
    assert result.project.root == str(repo)
    assert result.subpath is None


def test_home_cwd_falls_back_to_virtualenv_interpreter(
    resolver: ProjectResolver, home: Path
) -> None:
    repo = make_repo(home / "code" / "api")
    bin_dir = repo / ".venv" / "bin"
    bin_dir.mkdir(parents=True)
    (bin_dir / "python").write_text("")
    (bin_dir / "uvicorn").write_text("")

    cmdline = [str(bin_dir / "python"), str(bin_dir / "uvicorn"), "main:app", "--reload"]
    result = resolver.resolve(str(home), cmdline)

    assert result.project is not None
    assert result.project.name == "api"


def test_relative_script_is_resolved_against_cwd(resolver: ProjectResolver, home: Path) -> None:
    repo = make_repo(home / "code" / "site")
    (repo / "bin").mkdir()
    (repo / "bin" / "serve.py").write_text("")
    scratch = home / "tmp"
    scratch.mkdir()

    result = resolver.resolve(str(scratch), ["python3", "../code/site/bin/serve.py"])

    assert result.project is not None
    assert result.project.root == str(repo)


def test_cwd_project_beats_command_line(resolver: ProjectResolver, home: Path) -> None:
    from_cwd = make_repo(home / "code" / "frontend")
    other = make_repo(home / "code" / "tools")
    (other / "serve.js").write_text("")

    result = resolver.resolve(str(from_cwd), ["node", str(other / "serve.js")])

    assert result.project is not None
    assert result.project.root == str(from_cwd)


def test_system_paths_in_command_line_are_ignored(resolver: ProjectResolver, home: Path) -> None:
    result = resolver.resolve("/", ["/usr/bin/python3", "-m", "http.server", "8000"])

    assert result.project is None


def test_rewritten_process_title(resolver: ProjectResolver, home: Path) -> None:
    repo = make_repo(home / "code" / "worker")
    (repo / "index.js").write_text("")

    result = resolver.resolve("/", [f"node {repo / 'index.js'} --port 4000"])

    assert result.project is not None
    assert result.project.root == str(repo)


def test_unknown_cwd_and_no_paths(resolver: ProjectResolver) -> None:
    assert resolver.resolve(None, ["redis-server", "*:6379"]).project is None


# -- compose projects and caching --------------------------------------------


def test_compose_directory(resolver: ProjectResolver, home: Path) -> None:
    repo = make_repo(home / "code" / "stack")
    (repo / "compose.yaml").write_text("services: {}\n")

    project = resolver.resolve_directory(str(repo), fallback_name="stack")

    assert project is not None
    assert project.kind == "git"
    assert project.root == str(repo)


def test_compose_directory_missing_uses_compose_name(resolver: ProjectResolver) -> None:
    project = resolver.resolve_directory("/nonexistent/stack", fallback_name="stack")

    assert project is not None
    assert project.name == "stack"
    assert project.kind == "compose"


def test_lookups_are_cached_for_a_while(home: Path) -> None:
    now = [1000.0]
    resolver = ProjectResolver(home=str(home), clock=lambda: now[0])
    folder = home / "code" / "later"
    folder.mkdir(parents=True)

    assert resolver.resolve(str(folder)).project is None
    make_repo(folder)
    assert resolver.resolve(str(folder)).project is None  # cached

    now[0] += 120
    assert resolver.resolve(str(folder)).project is not None
