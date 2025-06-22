import contextlib
import importlib.util
import io
import logging
import os
import sys
from pathlib import Path
from types import ModuleType

import git

REPO_ROOT = Path(__file__).parents[1]

TESTS_ASSETS = REPO_ROOT / "tests" / "assets"

logger = logging.getLogger(__name__)
logger.addHandler(logging.NullHandler())
logging.disable(logging.CRITICAL)
os.environ["WATCHDOG_EXE"] = "watchdog.exe"
os.environ["WATCHDOG_CONFIG"] = "watchdog_config.yml"


def build_example(script_path: str) -> ModuleType:
    module_name = Path(script_path).stem
    spec = importlib.util.spec_from_file_location(module_name, script_path)
    if spec is None:
        raise ImportError(f"Can't find {script_path}")
    module = importlib.util.module_from_spec(spec)
    if spec.loader is None:
        raise ImportError(f"Can't load {script_path}")
    spec.loader.exec_module(module)
    return module


@contextlib.contextmanager
def suppress_stdout():
    original_stdout = sys.stdout
    sys.stdout = io.StringIO()
    try:
        yield
    finally:
        sys.stdout = original_stdout


class SubmoduleManager:
    _instance = None
    initialized = False

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(SubmoduleManager, cls).__new__(cls)
            cls.initialized = False
        return cls._instance

    @classmethod
    def initialize_submodules(cls, force: bool = False) -> None:
        if force or not cls.initialized:
            cls._initialize_submodules()
            cls.initialized = True

    @staticmethod
    def _initialize_submodules() -> None:
        root_repo = git.Repo(REPO_ROOT)
        root_repo.git.submodule("update", "--init", "--recursive")


SubmoduleManager.initialize_submodules()
