"""The APIs match the OpenAPI documents committed in ui-web/openapi/ (the web UI's contract).

When this fails after an intended API change, regenerate them: ``uv run python scripts/openapi.py``.
"""

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def _script():
    spec = importlib.util.spec_from_file_location("openapi_script", ROOT / "scripts" / "openapi.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("name", ["run", "daemon", "hub"])
def test_the_api_matches_its_committed_openapi_document(name):
    script = _script()
    committed = (ROOT / "ui-web" / "openapi" / f"{name}.json").read_text(encoding="utf-8")
    assert script.render(script.documents()[name]) == committed, (
        f"The {name} API changed: run `uv run python scripts/openapi.py` and review the diff of "
        f"ui-web/openapi/{name}.json (the web UI's models are generated from it)."
    )


@pytest.mark.parametrize("name", ["run", "daemon", "hub"])
def test_the_web_ui_models_are_generated_from_the_current_api(name):
    script = _script()
    committed = (script.MODELS / f"{script._pascal(name)}.g.cs").read_text(encoding="utf-8")
    assert script.csharp(name, script.documents()[name]) == committed, (
        f"The web UI's {name} models are stale: run `uv run python scripts/openapi.py`."
    )
