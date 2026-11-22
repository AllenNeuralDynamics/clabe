"""Serve a run over HTTP: a snapshot, a resumable Server-Sent Events stream, answers and commands, described by
OpenAPI (``/openapi.json``); plus a thin Python client."""

import importlib
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ._app import create_app
    from ._client import RunClient
    from ._models import (
        AbortBody,
        AnswerBody,
        ClientBody,
        CommandResult,
        DirEntry,
        DirListing,
        ListDirBody,
        ServerInfo,
        SessionInfo,
    )
    from ._server import RunServer, free_port

#: Where each public name lives; loaded on first use (the server needs the frontend extra; its models and client do not).
_LAZY = {
    "AbortBody": "._models",
    "AnswerBody": "._models",
    "ClientBody": "._models",
    "CommandResult": "._models",
    "DirEntry": "._models",
    "DirListing": "._models",
    "ListDirBody": "._models",
    "RunClient": "._client",
    "RunServer": "._server",
    "ServerInfo": "._models",
    "SessionInfo": "._models",
    "create_app": "._app",
    "free_port": "._server",
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
    "AbortBody",
    "AnswerBody",
    "ClientBody",
    "CommandResult",
    "DirEntry",
    "DirListing",
    "ListDirBody",
    "RunClient",
    "RunServer",
    "ServerInfo",
    "SessionInfo",
    "create_app",
    "free_port",
]
