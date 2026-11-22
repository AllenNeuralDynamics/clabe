"""The clabe hub: an optional service (e.g. in Docker) that follows a fleet of rigs through their daemons, shows
which ones wait for an answer, and re-exposes each rig under ``/rigs/<id>/`` so the web UI works fleet-wide.

Rigs never depend on it: the hub dials in as an ordinary client of their daemons."""

from ._app import create_hub_app
from ._fleet import Fleet, HubSettings, RigConfig, RigStatus, Waiting

__all__ = ["Fleet", "HubSettings", "RigConfig", "RigStatus", "Waiting", "create_hub_app"]
