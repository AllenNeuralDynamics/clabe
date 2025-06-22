#!/usr/bin/env uv run
"""
Script to regenerate the API documentation structure in mkdocs.yml
based on the current state of the library.
"""

import logging
import shutil
from pathlib import Path
from typing import Any, Dict, List, Tuple, Union

import yaml

# Constants
ROOT_DIR: Path = Path(__file__).parent.parent
PACKAGE_NAME: str = "clabe"
SRC_DIR: Path = ROOT_DIR / "src" / f"{PACKAGE_NAME}"
DOCS_DIR: Path = ROOT_DIR / "docs"
API_DIR: Path = DOCS_DIR / "api"
MKDOCS_YML: Path = ROOT_DIR / "mkdocs.yml"
API_LABEL: str = "API Reference"

# Leaving this manual for now.
DOCUMENTED_MODULES: List[str] = ["apps", "behavior_launcher", "logging_helper"]
TO_COPY: List[str] = ["assets", "examples", "LICENSE"]
log: logging.Logger = logging.getLogger("mkdocs")


def on_pre_build(config: Dict[str, Any]) -> None:
    """Mkdocs pre-build hook."""
    for file_or_dir in TO_COPY:
        src: Path = ROOT_DIR / file_or_dir
        dest: Path = DOCS_DIR / file_or_dir
        
        if src.exists():
            log.info(f"Copying {file_or_dir} to docs...")

            if src.is_file():
                print(f"Copying file {src} to {dest}")
                shutil.copy(src, dest)
            else:
                if dest.exists():
                    shutil.rmtree(dest)
                shutil.copytree(src, dest)
            log.info(f"{file_or_dir} copied successfully.")
        else:
            log.warning(f"Source: {file_or_dir} not found, skipping.")

    main()


def find_modules(base_dir: Path, module_name: str) -> List[Tuple[str, str]]:
    """Find all Python modules in the given directory."""
    modules: List[Tuple[str, str]] = []
    dir_path: Path = base_dir / module_name

    if not dir_path.is_dir():
        return modules

    if (dir_path / "__init__.py").exists():
        modules.append(("core", f"{module_name}"))

    for item in dir_path.iterdir():
        if item.is_file() and item.suffix == ".py" and not item.name.startswith("_"):
            modules.append((item.stem, f"{module_name}.{item.stem}"))

    modules.sort(key=lambda x: x[0])
    return modules


def generate_api_structure() -> Dict[str, List[Dict[str, str]]]:
    """Generate the API documentation structure."""
    api_structure: Dict[str, List[Dict[str, str]]] = {}

    for module_name in DOCUMENTED_MODULES:
        module_structure: List[Dict[str, str]] = []
        modules: List[Tuple[str, str]] = find_modules(SRC_DIR, module_name)

        for name, import_path in modules:
            md_file: str = f"api/{module_name}/{name}.md"

            (API_DIR / module_name).mkdir(parents=True, exist_ok=True)

            with open(DOCS_DIR / md_file, "w") as f:
                f.write(f"# {import_path}\n\n")
                f.write(f"::: {PACKAGE_NAME}.{import_path}\n")

            module_structure.append({name: md_file})

        api_structure[module_name] = module_structure

    return api_structure


def update_mkdocs_yml(api_structure: Dict[str, List[Dict[str, str]]]) -> None:
    """Rewrite the mkdocs.yml overriding the API Reference section only!."""
    with open(MKDOCS_YML, "r") as f:
        config: Dict[str, Any] = yaml.safe_load(f)

    nav: List[Union[str, Dict[str, Any]]] = config.get("nav", [])
    
    for entry in nav:
        if isinstance(entry, dict) and API_LABEL in entry:
            api_ref: List[Union[str, Dict[str, List[Dict[str, str]]]]] = ["api/index.md"]
            
            for module_name, module_content in api_structure.items():
                api_ref.append({module_name.capitalize(): module_content})
            
            entry[API_LABEL] = api_ref

    with open(MKDOCS_YML, "w") as f:
        yaml.dump(config, f, sort_keys=False, default_flow_style=False)


def main() -> None:
    """Main function."""
    log.info("Regenerating API documentation...")

    # Generate API structure
    api_structure: Dict[str, List[Dict[str, str]]] = generate_api_structure()

    # Update mkdocs.yml
    update_mkdocs_yml(api_structure)

    log.info("API documentation regenerated successfully.")


if __name__ == "__main__":
    main()