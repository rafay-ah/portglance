"""Decide which listening ports are "dev ports" and which are system noise.

A port counts as a dev port when any of these holds:

* it is pinned by the user;
* it is a port published by a Docker/Podman container;
* it is TCP, owned by the current user, outside the kernel's ephemeral port
  range, not a known desktop app, and the process either belongs to a project
  or is a known dev runtime or framework.

Everything else (system daemons, other users' processes, desktop apps such as
Spotify or Steam, and random high ports used by language servers and Jupyter
kernels) is only shown with "Show all".
"""

from __future__ import annotations

from .model import TCP, PortEntry

#: Process names (``comm``) of runtimes and servers people use for development.
DEV_RUNTIMES = frozenset(
    {
        "air", "beam.smp", "bun", "caddy", "cloud-sql-proxy", "cloud_sql_proxy",
        "cloudflared", "daphne", "deno", "docker-proxy", "dotnet", "elixir", "erl",
        "esbuild", "etcd", "flask", "go", "granian", "gunicorn", "httpd", "hugo",
        "hypercorn", "java", "jekyll", "kubectl", "mailhog", "mailpit", "mariadbd",
        "memcached", "minio", "mongod", "mysqld", "next-server", "nginx", "node",
        "nodejs", "ollama", "perl", "php", "postgres", "puma", "python", "python3",
        "rails", "redis-server", "rootlesskit", "rootlessport", "ruby", "socat",
        "ssh", "streamlit", "traefik", "trunk", "tsx", "uvicorn", "valkey-server",
        "vite", "workerd", "wrangler",
    }
)  # fmt: skip

#: Desktop apps and system services that listen on ports but are not dev servers.
NOT_DEV = frozenset(
    {
        "1password", "avahi-daemon", "bitwarden", "brave", "chrome", "chromium",
        "containerd", "cupsd", "discord", "dnsmasq", "dockerd", "dropbox", "evolution",
        "firefox", "kdeconnectd", "keepassxc", "megasync", "msedge", "nextcloud",
        "obs", "opera", "pipewire", "qbittorrent", "rpcbind", "signal-desktop",
        "skypeforlinux", "slack", "smbd", "snapd", "spotify", "sshd", "steam",
        "steamwebhelper", "syncthing", "systemd", "systemd-resolve", "teams",
        "telegram-desktop", "thunderbird", "transmission-gtk", "vivaldi", "xwayland",
        "zoom",
    }
)  # fmt: skip

#: Frameworks that are internal plumbing rather than something you browse to.
NOT_DEV_FRAMEWORKS = frozenset({"Jupyter kernel"})


def is_dev_runtime(name: str) -> bool:
    lowered = name.lower()
    if lowered in DEV_RUNTIMES:
        return True
    # python3.12, php8.3, ruby3.2, node20 ...
    return lowered.startswith(("python3.", "python2.", "php", "ruby", "node"))


def is_not_dev_app(name: str) -> bool:
    lowered = name.lower()
    return lowered in NOT_DEV or lowered.startswith(("gsd-", "gvfs", "ibus-", "tracker-"))


def is_dev_port(entry: PortEntry, *, uid: int, ephemeral: tuple[int, int]) -> bool:
    if entry.pinned or entry.container is not None:
        return True
    if entry.proto != TCP or entry.uid != uid or entry.process is None:
        return False
    if ephemeral[0] <= entry.port <= ephemeral[1]:
        return False
    if is_not_dev_app(entry.process.name) or entry.framework in NOT_DEV_FRAMEWORKS:
        return False
    return (
        entry.project is not None
        or entry.framework is not None
        or is_dev_runtime(entry.process.name)
    )
