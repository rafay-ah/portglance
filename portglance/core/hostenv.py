"""The host's environment, as it was before the AppImage's ``AppRun`` changed it.

Inside the AppImage, variables such as ``LD_LIBRARY_PATH`` and ``PYTHONHOME``
point into the image. Programs PortGlance launches must not inherit them,
or they would load the bundled libraries. ``AppRun`` saves the original
values as ``PORTGLANCE_HOST_<NAME>`` (``__unset__`` when a variable was not
set); outside the AppImage nothing changes.
"""

from __future__ import annotations

import os
from collections.abc import Mapping

BUNDLE_VARIABLES = (
    "LD_LIBRARY_PATH",
    "PYTHONHOME",
    "PYTHONPATH",
    "PYTHONDONTWRITEBYTECODE",
    "PYTHONNOUSERSITE",
    "GI_TYPELIB_PATH",
    "GIO_MODULE_DIR",
    "GSETTINGS_SCHEMA_DIR",
    "GDK_PIXBUF_MODULE_FILE",
    "XDG_DATA_DIRS",
)
PREFIX = "PORTGLANCE_HOST_"
UNSET = "__unset__"


def host_overrides(environ: Mapping[str, str] | None = None) -> dict[str, str | None]:
    """Variables to restore for child processes: name -> value (None = unset)."""
    environ = os.environ if environ is None else environ
    overrides: dict[str, str | None] = {}
    for name in BUNDLE_VARIABLES:
        saved = environ.get(PREFIX + name)
        if saved is not None:
            overrides[name] = None if saved == UNSET else saved
    return overrides


def host_environ(environ: Mapping[str, str] | None = None) -> dict[str, str]:
    """A copy of the environment suitable for ``subprocess``."""
    result = dict(os.environ if environ is None else environ)
    for name, value in host_overrides(result).items():
        if value is None:
            result.pop(name, None)
        else:
            result[name] = value
    for name in list(result):
        if name.startswith(PREFIX):
            del result[name]
    return result
