"""``clabe mcp``: serve a run, a daemon or a hub to an MCP client (over stdio)."""

import os
import sys

from pydantic import Field
from pydantic_settings import BaseSettings


class McpCli(BaseSettings, cli_kebab_case=True):
    """Serve clabe to an MCP client (e.g. Claude) over stdio: read runs and answer their prompts."""

    url: str = Field(description="A run server, a rig's daemon, or a hub, e.g. http://rig-3:8070")
    token: str | None = Field(
        default=None, description="Its token (defaults to $DAEMON_CLABE_TOKEN, then $CLABE_TOKEN)"
    )

    def cli_cmd(self):
        """Serve over stdio until the client disconnects."""
        try:
            from ._mcp import ClabeTarget, create_mcp_server
        except ImportError as exc:  # the agents extra is missing
            print(f"clabe mcp needs the agents extra (pip install 'aind-clabe[agents]'): {exc}", file=sys.stderr)
            raise SystemExit(1) from exc
        token = self.token or os.environ.get("DAEMON_CLABE_TOKEN") or os.environ.get("CLABE_TOKEN") or None
        create_mcp_server(ClabeTarget(self.url, token)).run("stdio")
