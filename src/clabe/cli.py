from pydantic_settings import BaseSettings, CliApp, CliSubCommand

from .rpc._server import _RpcServerStartCli


class CliAppSettings(BaseSettings, cli_prog_name="clabe", cli_kebab_case=True):
    rpc_server: CliSubCommand[_RpcServerStartCli]

    def cli_cmd(self):
        CliApp.run_subcommand(self)


def main():
    CliApp.run(CliAppSettings)


if __name__ == "__main__":
    main()
