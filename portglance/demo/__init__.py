"""Demo mode: real listening sockets from fake dev servers in throwaway projects.

``portglance --demo`` creates a few small projects under a temporary
directory (git repositories, a plain folder, a Compose stack), starts fake
servers in them that look like Vite, Uvicorn, JupyterLab and friends to
PortGlance, and opens the app against them. Docker containers are simulated
so the container features can be tried without Docker. Everything is
cleaned up on exit; your real settings and autostart entry are not touched.
"""

from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path

from .fleet import DemoFleet

__all__ = ["DemoFleet", "run_demo"]


def run_demo(argv: list[str]) -> int:
    root = Path(tempfile.mkdtemp(prefix="portglance-demo-"))
    os.environ["PORTGLANCE_CONFIG_DIR"] = str(root / "config")
    # Show the throwaway projects the way real ones look: ~/code/<project>.
    os.environ["PORTGLANCE_DISPLAY_HOME"] = str(root)
    fleet = DemoFleet(root)
    try:
        fleet.start()
        print(f"PortGlance demo: {len(fleet.servers)} fake servers under {root}", flush=True)
        for server in fleet.servers:
            print(f"  :{server.port:<5} {server.label}", flush=True)
        from ..ui.app import run

        return run(argv, demo=fleet)
    finally:
        fleet.stop()
        shutil.rmtree(root, ignore_errors=True)
