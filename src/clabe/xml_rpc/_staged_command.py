"""Runs a command whose file inputs were staged by the client.

The client sends the command together with the contents of its input files (``Command.inputs``,
e.g. the rig, session and task a Bonsai workflow reads). On the server, this module writes each
input to a temporary file, replaces its ``{name}`` placeholder in the command with that file's
path, and runs the command. The inputs therefore never need to be uploaded and referenced one by one.

Invoked on the server as ``python -m clabe.xml_rpc._staged_command <spec.json>``.
"""

import subprocess
import sys
import tempfile
from pathlib import Path

from pydantic import BaseModel, Field


class StagedFile(BaseModel):
    """An input file of a staged command."""

    filename: str = Field(description="Name to write the file as")
    content: str = Field(description="Text content of the file")


class StagedCommand(BaseModel):
    """A command together with the input files it needs on the machine that runs it."""

    cmd: list[str] = Field(description="Command to run. '{name}' is replaced by the path of input file 'name'")
    files: dict[str, StagedFile] = Field(default_factory=dict, description="Input files, keyed by placeholder name")


def _substitute(arg: str, paths: dict[str, str]) -> str:
    """Replaces the placeholders of known input files in a command argument."""
    for name, path in paths.items():
        arg = arg.replace(f"{{{name}}}", path)
    return arg


def run_staged(spec: StagedCommand) -> int:
    """Writes the inputs to a temporary directory and runs the command.

    The input files are removed once the command exits.

    Args:
        spec: The command and its inputs.

    Returns:
        The command's exit code.
    """
    with tempfile.TemporaryDirectory(prefix="clabe_staged_") as tmp:
        paths = {}
        for name, file in spec.files.items():
            # One directory per input, so inputs sharing a filename cannot overwrite each other
            path = Path(tmp) / name / Path(file.filename).name
            path.parent.mkdir()
            path.write_text(file.content, encoding="utf-8")
            paths[name] = str(path)
        cmd = [_substitute(arg, paths) for arg in spec.cmd]
        # Output is inherited, so the server captures it as this job's stdout/stderr
        return subprocess.run(cmd, check=False).returncode


def main(argv: list[str]) -> int:
    """Runs the staged command described by the spec file, deleting the spec afterwards."""
    spec_path = Path(argv[1])
    try:
        return run_staged(StagedCommand.model_validate_json(spec_path.read_text(encoding="utf-8")))
    finally:
        spec_path.unlink(missing_ok=True)


if __name__ == "__main__":
    sys.exit(main(sys.argv))
