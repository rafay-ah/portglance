# Contributing to PortGlance

Thanks for helping out! Bug reports, ideas and pull requests are all welcome.

## Reporting a bug

Please include the output of `portglance doctor`, your distribution and desktop, and
what you expected to see. If a port is missing or in the wrong group, the matching line
of `portglance list --all --json` helps a lot.

## Development setup

```sh
python3 -m venv --system-site-packages .venv   # uses the system's PyGObject
. .venv/bin/activate
pip install -e '.[dev]'
python -m portglance --demo                    # the app against fake servers
```

You need `python3-gi`, `gir1.2-gtk-4.0` and `gir1.2-adw-1` (libadwaita 1.5 or newer)
for the app; the core library and its tests only need Python 3.10+.

## Before you open a pull request

```sh
ruff check . && ruff format --check .
pytest
```

The GTK smoke test runs when a display is available; CI runs it under Xvfb.

- Keep `portglance/core` free of GTK imports. It is shared by the app, the terminal
  commands and the tests.
- New process-to-project rules need a test in `tests/test_projects.py` or
  `tests/test_scanner.py`; `tests/fakeproc.py` builds a fake `/proc` for that.
- New framework labels go in `portglance/core/frameworks.py` with a case in
  `tests/test_frameworks.py`.
- Write commit messages in the imperative mood, with a body that explains why.

## Releasing

1. Update `__version__` in `portglance/__init__.py`, the `<releases>` entry in
   `data/io.github.rafay_ah.PortGlance.metainfo.xml`, the man page header and
   `CHANGELOG.md`.
2. Tag the commit `vX.Y.Z` and publish a GitHub release for the tag. The *Release*
   workflow tests, builds the `.deb` and the AppImage, and attaches them with checksums.
