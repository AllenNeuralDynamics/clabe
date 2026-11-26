"""The clabe daemon: one per PC, it lists experiment repositories, runs an experiment in its repository's own
environment, and serves it (proxied under ``/run/``) so each rig has one stable URL."""

import importlib
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ._app import create_daemon, create_daemon_app
    from ._host import AttachBody, Host, HostError, HostInfo, LaunchBody, RepositoryInfo, RunInfo
    from ._settings import DaemonSettings, RepositoryConfig
    from ._worker import Worker

#: Where each public name lives, loaded on first use: the app needs the frontend extra, the settings do not.
_LAZY = {
    "AttachBody": "._host",
    "DaemonSettings": "._settings",
    "Host": "._host",
    "HostError": "._host",
    "HostInfo": "._host",
    "LaunchBody": "._host",
    "RepositoryConfig": "._settings",
    "RepositoryInfo": "._host",
    "RunInfo": "._host",
    "Worker": "._worker",
    "create_daemon": "._app",
    "create_daemon_app": "._app",
}


def __getattr__(name: str):
    """Imports a public name from its module on first use."""
    module = _LAZY.get(name)
    if module is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(importlib.import_module(module, __name__), name)
    globals()[name] = value
    return value


__all__ = [
    "AttachBody",
    "DaemonSettings",
    "Host",
    "HostError",
    "HostInfo",
    "LaunchBody",
    "RepositoryConfig",
    "RepositoryInfo",
    "RunInfo",
    "Worker",
    "create_daemon",
    "create_daemon_app",
]
