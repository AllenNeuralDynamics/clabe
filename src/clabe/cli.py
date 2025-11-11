from pydantic_settings import BaseSettings, CliApp, CliSubCommand

from .xml_rpc._server import _XmlRpcServerStartCli


class CliAppSettings(BaseSettings, cli_prog_name="clabe", cli_kebab_case=True):
    """CLI application settings."""

    rpc_server: CliSubCommand[_XmlRpcServerStartCli]

    def cli_cmd(self):
        """Run the selected subcommand."""
        CliApp.run_subcommand(self)


def main():
    """Main entry point for the CLI application."""
    CliApp.run(CliAppSettings)


if __name__ == "__main__":
    main()
