"""``clabe hub``: follow a fleet of rigs from one page."""

import os

from pydantic_settings import BaseSettings, CliApp, CliSubCommand

from ._fleet import HubSettings


class _RunHubCli(HubSettings, cli_kebab_case=True):
    """Run the hub in the foreground (what its Docker image starts)."""

    def cli_cmd(self):
        """Serve until interrupted."""
        import uvicorn

        from ._app import create_hub_app
        from ._fleet import Fleet

        token = self.token.get_secret_value() if self.token else os.environ.get("CLABE_HUB_TOKEN") or None
        print(f"clabe hub on port {self.port}, following {len(self.rigs)} rig(s)", flush=True)
        uvicorn.run(create_hub_app(Fleet(self), token=token), host=self.host, port=self.port, log_level="warning")


class HubCli(BaseSettings, cli_kebab_case=True):
    """The clabe hub: a fleet view and an inbox of the rigs waiting for an answer."""

    run: CliSubCommand[_RunHubCli]

    def cli_cmd(self):
        """Run the selected subcommand."""
        CliApp.run_subcommand(self)
