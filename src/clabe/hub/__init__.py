"""The clabe hub: an optional service (e.g. in Docker) that follows a fleet of rigs through their daemons, shows
which ones wait for an answer, and re-exposes each rig under ``/rigs/<id>/`` so the web UI works fleet-wide.

Each rig's daemon enrolls itself (its ``DAEMON_CLABE_HUBS``); from then on the hub dials in as an ordinary client of
the daemon. Rigs never depend on it: a rig whose hub is down runs as before."""

import importlib
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ._app import create_hub_app
    from ._fleet import EnrollBody, Excluded, Fleet, HubSettings, RigStatus, Waiting
    from ._registry import Exclusion, Registry, RigConfig
    from ._reports import InspectCache, ReportPage, ReportStore, StoredReport, ZipEntry

#: Where each public name lives; loaded on first use (its app needs the frontend extra).
_LAZY = {
    "EnrollBody": "._fleet",
    "Excluded": "._fleet",
    "Exclusion": "._registry",
    "Fleet": "._fleet",
    "HubSettings": "._fleet",
    "InspectCache": "._reports",
    "Registry": "._registry",
    "ReportPage": "._reports",
    "ReportStore": "._reports",
    "RigConfig": "._registry",
    "RigStatus": "._fleet",
    "StoredReport": "._reports",
    "Waiting": "._fleet",
    "ZipEntry": "._reports",
    "create_hub_app": "._app",
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
    "EnrollBody",
    "Excluded",
    "Exclusion",
    "Fleet",
    "HubSettings",
    "InspectCache",
    "Registry",
    "ReportPage",
    "ReportStore",
    "RigConfig",
    "RigStatus",
    "StoredReport",
    "Waiting",
    "ZipEntry",
    "create_hub_app",
]
