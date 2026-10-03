# Changelog

All notable changes to PortGlance are listed here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[semantic versioning](https://semver.org/).

## [0.1.0] - 2026-10-03

First release.

### Added

- Top-panel indicator (StatusNotifierItem) with the number of dev ports in use and a menu
  to open, reveal or stop each one.
- Main window listing dev ports grouped by project (git repository and branch), with an
  *All* page for system ports, search, pinned ports, and a LAN badge for ports reachable
  from the network.
- Docker and Podman containers with their published ports; Compose services are grouped
  with their project.
- Stopping processes with SIGTERM, then SIGKILL after a configurable timeout, through
  pidfds; stopping containers through the Engine API.
- Pinnable desktop widget, with an optional GNOME Shell helper extension to keep it above
  other windows on Wayland.
- Start on login, enabled on first run.
- `portglance list`, `portglance kill` and `portglance doctor` terminal commands.
- Demo mode (`portglance --demo`) with fake servers in throwaway projects.
- `.deb` and AppImage packages built by GitHub Actions on release.

[0.1.0]: https://github.com/rafay-ah/portglance/releases/tag/v0.1.0
