"""The clabe hub: an optional service (e.g. in Docker) that follows a fleet of rigs through their daemons, shows
which ones wait for an answer, and re-exposes each rig under ``/rigs/<id>/`` so the web UI works fleet-wide.

Rigs never depend on it: the hub dials in as an ordinary client of their daemons."""

import importlib
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ._app import create_hub_app
    from ._fleet import Fleet, HubSettings, RigConfig, RigStatus, Waiting
    from ._reports import InspectCache, ReportPage, ReportStore, StoredReport, ZipEntry

#: Where each public name lives; loaded on first use (its app needs the frontend extra).
_LAZY = {
    "Fleet": "._fleet",
    "HubSettings": "._fleet",
    "InspectCache": "._reports",
    "ReportPage": "._reports",
    "ReportStore": "._reports",
    "RigConfig": "._fleet",
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
    "Fleet",
    "HubSettings",
    "InspectCache",
    "ReportPage",
    "ReportStore",
    "RigConfig",
    "RigStatus",
    "StoredReport",
    "Waiting",
    "ZipEntry",
    "create_hub_app",
]
