"""Builds the web UI (ui-web/, Blazor WebAssembly) into src/clabe/server/static, where the run server serves it.

Needs the .NET SDK; users of the wheel do not. Run it before ``uv build`` (CI does):

    uv run python scripts/build_web.py
"""

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROJECT = ROOT / "ui-web" / "Clabe.Web.csproj"
STATIC = ROOT / "src" / "clabe" / "server" / "static"


def main() -> int:
    """Publishes the app and replaces the packaged static files with its output."""
    dotnet = shutil.which("dotnet")
    if dotnet is None:
        print("The .NET SDK (dotnet) is required to build the web UI.", file=sys.stderr)
        return 1
    with tempfile.TemporaryDirectory() as out:
        subprocess.run(
            [dotnet, "publish", str(PROJECT), "-c", "Release", "-o", out, "-nologo", "-v", "q"],
            check=True,
        )
        wwwroot = Path(out) / "wwwroot"
        if not (wwwroot / "index.html").is_file():
            print(f"dotnet publish produced no {wwwroot / 'index.html'}", file=sys.stderr)
            return 1
        if STATIC.exists():
            shutil.rmtree(STATIC)
        shutil.copytree(wwwroot, STATIC)
    size = sum(f.stat().st_size for f in STATIC.rglob("*") if f.is_file())
    print(f"Web UI built into {STATIC.relative_to(ROOT)} ({size / 1e6:.1f} MB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
