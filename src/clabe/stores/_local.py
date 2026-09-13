import logging
import os
import platform
from collections.abc import Sequence
from pathlib import Path
from typing import Protocol, TypeVar

import pydantic

from .. import ui
from ._base import Candidate, Kind, KindLike, Scope, StoreBase, as_kind

logger = logging.getLogger(__name__)

T = TypeVar("T")

RIG_DIR = "Rig"
TASK_DIR = "Task"
SUBJECT_DIR = "Subjects"


class Layout(Protocol):
    """Maps a record kind and scope onto paths within a config library."""

    def read(self, kind_name: str, scope: Scope) -> Sequence[str]:
        """Returns library-relative glob patterns to read from, highest priority first."""
        ...

    def write(self, kind_name: str, scope: Scope) -> str:
        """Returns the library-relative path to write to."""
        ...


class DefaultLayout:
    """
    The config library tree as it exists on the shared drive today.

    ``Rig/<computer_name>/*.json``, ``Task/*.json`` and
    ``Subjects/<subject>/<kind>.json``. Tasks read from the subject folder first
    and fall back to the shared library, which is the ordered scope chain that
    replaces the nested lookup the picker used to do inline.

    Kinds other than ``rig`` and ``task`` are per-subject state, living beside
    the subject's task; with no subject in scope they sit at the library root,
    which is what makes a one-off store pointed at a session directory work.
    """

    def read(self, kind_name: str, scope: Scope) -> Sequence[str]:
        """Returns the glob patterns for this kind, subject folder before shared library."""
        subject = scope.get("subject")
        if kind_name == "rig":
            return [f"{RIG_DIR}/{scope['computer_name']}/*.json"]
        if kind_name == "task":
            library = [f"{TASK_DIR}/*.json"]
            return [f"{SUBJECT_DIR}/{subject}/task.json", *library] if subject else library
        return [self._subject_scoped(kind_name, subject)]

    def write(self, kind_name: str, scope: Scope) -> str:
        """Returns the single path this kind is written to."""
        if kind_name == "rig":
            return f"{RIG_DIR}/{scope['computer_name']}/rig.json"
        return self._subject_scoped(kind_name, scope.get("subject"))

    @staticmethod
    def _subject_scoped(kind_name: str, subject: str | None) -> str:
        """Places a record in the subject folder, or at the library root when unscoped."""
        return f"{SUBJECT_DIR}/{subject}/{kind_name}.json" if subject else f"{kind_name}.json"


class LocalFileStore(StoreBase):
    """
    A store over a directory of JSON files, i.e. the config library.

    Writes overwrite, and create the directories they need. Files that fail to
    parse are skipped with a warning rather than failing the whole listing.

    Example:
        ```python
        store = LocalFileStore(root=r"\\\\allen\\aind\\scratch\\AindBehavior.db\\AindVrForaging")
        rig = store.resolve(Kind.from_rig(AindVrForagingRig))
        ```
    """

    def __init__(self, root: os.PathLike | str, *, layout: Layout | None = None, scope: Scope | None = None) -> None:
        """
        Args:
            root: The config library directory.
            layout: Maps kinds onto paths within ``root``. Defaults to :class:`DefaultLayout`.
            scope: Initial scope. ``computer_name`` defaults to this machine's.
        """
        super().__init__(scope={"computer_name": os.environ.get("COMPUTERNAME", platform.node()), **(scope or {})})
        self._root = Path(root)
        self._layout = layout or DefaultLayout()

    @property
    def root(self) -> Path:
        """The config library directory."""
        return self._root

    def __str__(self) -> str:
        return f"{type(self).__name__}({self._root})"

    def _candidates(self, kind: Kind[T], scope: Scope) -> Sequence[Candidate[T]]:
        """Reads every matching file, labelled by path, in layout priority order."""
        candidates: list[Candidate[T]] = []
        seen: set[Path] = set()
        for pattern in self._layout.read(kind.name, scope):
            for path in sorted(self._root.glob(pattern)):
                if path in seen or not path.is_file():
                    continue
                seen.add(path)
                value = self._load(path, kind)
                if value is not None:
                    candidates.append(Candidate(str(path), value))
        return candidates

    def write(self, kind: KindLike[T], value: T, *, scope: Scope | None = None) -> None:
        """Overwrites the file this kind maps to, creating parent directories."""
        _kind = as_kind(kind)
        path = self._root / self._layout.write(_kind.name, self._merge_scope(scope))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(_kind.adapter.dump_json(value, indent=2))
        ui.notify(f"Saved {_kind.name} to {path}", ui.MessageLevel.SUCCESS)

    @staticmethod
    def _load(path: Path, kind: Kind[T]) -> T | None:
        """Parses one file, returning ``None`` and warning if it does not validate."""
        try:
            return kind.adapter.validate_json(path.read_text(encoding="utf-8"))
        except (pydantic.ValidationError, ValueError) as e:
            ui.notify(f"Skipping {path}: it is not a valid {kind.model.__name__}.", ui.MessageLevel.WARNING)
            logger.warning("Failed to parse %s as %s: %s", path, kind.model.__name__, e)
            return None
