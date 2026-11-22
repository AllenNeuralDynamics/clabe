"""The demo workflow, served over HTTP while it runs.

Answer its prompts from the terminal *or* from the browser that opens (any machine that can reach the port can join;
the first valid answer wins). ``uv run clabe run examples/behavior_launcher.py`` serves it the same way.
"""

from behavior_launcher import demo
from pydantic_settings import CliApp

from clabe.launcher import Launcher, LauncherCliArgs


def main():
    settings = CliApp.run(
        LauncherCliArgs,
        cli_args=["--allow-dirty", "--skip-hardware-validation", "--serve", "--open-browser"],
    )
    Launcher(settings=settings).run_experiment(demo)


if __name__ == "__main__":
    main()
