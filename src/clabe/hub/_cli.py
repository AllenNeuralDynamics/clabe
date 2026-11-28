"""``clabe hub``: follow a fleet of rigs from one page."""

import logging
import sys
import time

from pydantic_settings import BaseSettings, CliApp, CliSubCommand

from .. import __version__
from ..logging import rich_handler
from ._fleet import HubSettings

logger = logging.getLogger(__name__)


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

        _log_to_stdout(self.log_level)
        token = (self.token.get_secret_value() if self.token else None) or None
        reports = ReportStore(self.data_dir)
        fleet = Fleet(self, reports=reports, registry=Registry(self.data_dir))
        logger.info(
            "clabe hub %s on port %s (token %s), polling every %g s; data in %s",
            __version__,
            self.port,
            "required" if token else "not set",
            self.poll_interval,
            self.data_dir.resolve(),
        )
        logger.info("Following %s rig(s): %s", len(fleet.rigs), ", ".join(sorted(fleet.rigs)) or "none yet")
        app = create_hub_app(fleet, token=token, self_url=f"http://127.0.0.1:{self.port}")
        uvicorn.run(
            app,
            host=self.host,
            port=self.port,
            log_config=None,
            log_level=self.log_level.lower(),
            access_log=self.access_log,
        )


def _log_to_stdout(level: str) -> None:
    """Sends the hub's logs (and uvicorn's) to stdout, with UTC times: ``2026-11-23T00:39:17.867Z INFO ...``."""
    handler = logging.StreamHandler(sys.stdout)
    formatter = logging.Formatter(
        "%(asctime)s.%(msecs)03dZ %(levelname)s %(name)s: %(message)s", datefmt="%Y-%m-%dT%H:%M:%S"
    )
    formatter.converter = time.gmtime
    handler.setFormatter(formatter)
    root = logging.getLogger()
    root.removeHandler(rich_handler)  # clabe's terminal handler, installed on import
    root.addHandler(handler)
    root.setLevel(level)
    logging.getLogger("httpx").setLevel(logging.WARNING)  # one line per poll otherwise


class HubCli(BaseSettings, cli_kebab_case=True):
    """The clabe hub: a fleet view and an inbox of the rigs waiting for an answer."""

    run: CliSubCommand[_RunHubCli]

    def cli_cmd(self):
        """Run the selected subcommand."""
        CliApp.run_subcommand(self)
