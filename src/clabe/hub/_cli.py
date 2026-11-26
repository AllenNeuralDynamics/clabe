"""``clabe hub``: follow a fleet of rigs from one page."""

from pydantic_settings import BaseSettings, CliApp, CliSubCommand

from ._fleet import HubSettings


class _RunHubCli(HubSettings, cli_kebab_case=True):
    """Run the hub in the foreground (what its Docker image starts)."""

    def cli_cmd(self):
        """Serve until interrupted."""
        from .._extras import require_web

        try:
            require_web("The hub")
        except ImportError as exc:
            raise SystemExit(str(exc)) from None
        import uvicorn

        from ._app import create_hub_app
        from ._fleet import Fleet
        from ._registry import Registry
        from ._reports import ReportStore

        token = (self.token.get_secret_value() if self.token else None) or None
        enroll_token = (self.enroll_token.get_secret_value() if self.enroll_token else None) or None
        reports = ReportStore(self.data_dir)
        fleet = Fleet(self, reports=reports, registry=Registry(self.data_dir))
        print(f"clabe hub on port {self.port}, following {len(fleet.rigs)} enrolled rig(s)", flush=True)
        print(f"Enrolled rigs and issue reports are kept in {self.data_dir.resolve()}", flush=True)
        if enroll_token is None:
            print("ENROLL_TOKEN is not set: any rig that reaches this hub can enroll", flush=True)
        app = create_hub_app(fleet, token=token, self_url=f"http://127.0.0.1:{self.port}", enroll_token=enroll_token)
        uvicorn.run(app, host=self.host, port=self.port, log_level="warning")


class HubCli(BaseSettings, cli_kebab_case=True):
    """The clabe hub: a fleet view and an inbox of the rigs waiting for an answer."""

    run: CliSubCommand[_RunHubCli]

    def cli_cmd(self):
        """Run the selected subcommand."""
        CliApp.run_subcommand(self)
