import logging
import shutil
from pathlib import Path
from typing import Any, Dict, List, Union

import yaml

ROOT_DIR = Path(__file__).parent.parent
PACKAGE_NAME = "clabe"
SRC_DIR = ROOT_DIR / "src" / PACKAGE_NAME
DOCS_DIR = ROOT_DIR / "docs"
API_DIR = DOCS_DIR / "api"
MKDOCS_YML = ROOT_DIR / "mkdocs.yml"
API_LABEL = "API Reference"
INCLUDE_PRIVATE_MODULES = False

TO_COPY = ["examples", "LICENSE"]
log = logging.getLogger("mkdocs")


def discover_python_modules(package_root: Path, include_private: bool = False) -> List[str]:
    modules = []

    def _find_modules(current_path: Path, prefix: str = "") -> None:
        if not current_path.exists() or not current_path.is_dir():
            return

        for item in current_path.iterdir():
            if not item.is_dir():
                continue
            if not (item / "__init__.py").exists():
                continue

            if item.name.startswith("_") and not include_private:
                continue

            module_name = f"{prefix}{item.name}" if prefix else item.name
            modules.append(module_name)

            # Recursively search subdirectories for nested modules
            _find_modules(item, f"{module_name}.")

    _find_modules(package_root)
    return sorted(modules)


def discover_module_files(module_path: Path, include_private: bool = False) -> List[str]:
    files = []

    def _find_files(current_path: Path, prefix: str = "") -> None:
        if not current_path.exists() or not current_path.is_dir():
            return

        for item in current_path.iterdir():
            if item.is_file() and item.suffix == ".py":
                if item.name.startswith("_") and not include_private:
                    continue
                file_name = f"{prefix}{item.stem}" if prefix else item.stem
                files.append(file_name)
            elif item.is_dir() and not item.name.startswith("__"):
                if item.name.startswith("_") and not include_private:
                    continue
                # Recursively search subdirectories
                _find_files(item, f"{prefix}{item.name}." if prefix else f"{item.name}.")

    _find_files(module_path)
    return sorted(files)


def generate_api_structure() -> Dict[str, List[Dict[str, str]]]:
    api_structure: Dict[str, List[Dict[str, str]]] = {}
    modules = discover_python_modules(SRC_DIR, INCLUDE_PRIVATE_MODULES)

    API_DIR.mkdir(parents=True, exist_ok=True)

    for item in SRC_DIR.iterdir():
        if item.is_file() and item.suffix == ".py":
            if item.name.startswith("_") and not INCLUDE_PRIVATE_MODULES:
                continue
            file_name = item.stem.replace("-", "_").replace(" ", "_")
            safe_file_name = item.stem.replace(".", "_")
            api_structure[file_name] = [{file_name: f"api/{safe_file_name}.md"}]

            with open(DOCS_DIR / f"api/{safe_file_name}.md", "w") as f:
                f.write(f"# {file_name}\n\n")
                f.write(f"::: {PACKAGE_NAME}.{file_name}\n")

    for module_name in modules:
        module_structure: List[Dict[str, str]] = []
        module_path = SRC_DIR / module_name.replace(".", "/")

        # Add the module's __init__.py as the main module entry
        safe_module_name = module_name.replace(".", "_")
        module_structure.append({module_name: f"api/{safe_module_name}/{safe_module_name}.md"})

        module_files = discover_module_files(module_path, INCLUDE_PRIVATE_MODULES)
        for file_name in module_files:
            safe_file_name = file_name.replace(".", "_")
            module_structure.append({file_name: f"api/{safe_module_name}/{safe_file_name}.md"})

        (API_DIR / safe_module_name).mkdir(parents=True, exist_ok=True)

        with open(DOCS_DIR / f"api/{safe_module_name}/{safe_module_name}.md", "w") as f:
            f.write(f"# {module_name}\n\n")
            f.write(f"::: {PACKAGE_NAME}.{module_name}\n")

        for file_name in module_files:
            safe_file_name = file_name.replace(".", "_")
            with open(DOCS_DIR / f"api/{safe_module_name}/{safe_file_name}.md", "w") as f:
                f.write(f"# {module_name}.{file_name}\n\n")
                f.write(f"::: {PACKAGE_NAME}.{module_name}.{file_name}\n")

        api_structure[module_name] = module_structure
    return api_structure


def update_mkdocs_yml(api_structure: Dict[str, List[Dict[str, str]]]) -> None:
    with open(MKDOCS_YML, "r") as f:
        config: Dict[str, Any] = yaml.safe_load(f)

    nav: List[Union[str, Dict[str, Any]]] = config.get("nav", [])

    for entry in nav:
        if isinstance(entry, dict) and API_LABEL in entry:
            api_ref: List[Union[str, Dict[str, List[Dict[str, str]]]]] = []
            for module_name, module_content in api_structure.items():
                display_name = module_name.replace("_", " ").title()
                api_ref.append({display_name: module_content})

            entry[API_LABEL] = api_ref

    with open(MKDOCS_YML, "w") as f:
        yaml.dump(config, f, sort_keys=False, default_flow_style=False)


def copy_assets() -> None:
    for file_or_dir in TO_COPY:
        src: Path = ROOT_DIR / file_or_dir
        dest: Path = DOCS_DIR / file_or_dir

        if src.exists():
            log.info(f"Copying {file_or_dir} to docs...")

            if src.is_file():
                log.info(f"Copying file {src} to {dest}")
                shutil.copy(src, dest)
            else:
                if dest.exists():
                    shutil.rmtree(dest)
                shutil.copytree(src, dest)
            log.info(f"{file_or_dir} copied successfully.")
        else:
            log.warning(f"Source: {file_or_dir} not found, skipping.")


def main() -> None:
    log.info("Starting API documentation regeneration...")
    copy_assets()
    log.info("Regenerating API documentation...")
    # Generate API structure
    api_structure: Dict[str, List[Dict[str, str]]] = generate_api_structure()

    # Update mkdocs.yml
    update_mkdocs_yml(api_structure)

    log.info("API documentation regenerated successfully.")


if __name__ == "__main__":
    main()
