from portglance.core.hostenv import host_environ, host_overrides

APPIMAGE_ENV = {
    "PATH": "/usr/bin",
    "LD_LIBRARY_PATH": "/tmp/.mount_x/usr/lib",
    "PORTGLANCE_HOST_LD_LIBRARY_PATH": "__unset__",
    "XDG_DATA_DIRS": "/tmp/.mount_x/usr/share:/usr/share",
    "PORTGLANCE_HOST_XDG_DATA_DIRS": "/usr/local/share:/usr/share",
    "PYTHONHOME": "/tmp/.mount_x/usr",
    "PORTGLANCE_HOST_PYTHONHOME": "__unset__",
}


def test_outside_the_appimage_nothing_changes() -> None:
    env = {"PATH": "/usr/bin", "LD_LIBRARY_PATH": "/opt/lib"}

    assert host_overrides(env) == {}
    assert host_environ(env) == env


def test_appimage_variables_are_restored() -> None:
    assert host_overrides(APPIMAGE_ENV) == {
        "LD_LIBRARY_PATH": None,
        "PYTHONHOME": None,
        "XDG_DATA_DIRS": "/usr/local/share:/usr/share",
    }
    assert host_environ(APPIMAGE_ENV) == {
        "PATH": "/usr/bin",
        "XDG_DATA_DIRS": "/usr/local/share:/usr/share",
    }
