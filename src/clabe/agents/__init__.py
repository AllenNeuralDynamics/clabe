"""Agents and clabe runs: an MCP server (``clabe mcp``, needs the ``agents`` extra). The AG-UI projection of a run
is served by the run itself (``POST /api/agui``, see :mod:`clabe.server`)."""

from ._mcp import ClabeTarget, create_mcp_server

__all__ = ["ClabeTarget", "create_mcp_server"]
