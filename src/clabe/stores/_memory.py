from collections.abc import Sequence
from typing import Any, TypeVar

from ._base import Candidate, Kind, KindLike, Scope, StoreBase, as_kind

T = TypeVar("T")


class MemoryStore(StoreBase):
    """
    A store holding records in memory, for tests and for assembling records in-process.

    Writes append, as Dataverse does, so several records of one kind can be
    offered as candidates. A record is visible to a read whose scope agrees with
    every narrowing the record was written under, so a record written unscoped
    is library-wide.
    """

    def __init__(self, *, scope: Scope | None = None) -> None:
        super().__init__(scope=scope)
        self._records: list[tuple[str, dict[str, str], Any]] = []

    def __str__(self) -> str:
        return f"{type(self).__name__}({len(self._records)} records)"

    def _candidates(self, kind: Kind[T], scope: Scope) -> Sequence[Candidate[T]]:
        """Returns the records whose write-time narrowings the read scope satisfies."""
        return [
            Candidate(f"{kind.name}[{i}]", value)
            for i, (name, written, value) in enumerate(self._records)
            if name == kind.name and all(scope.get(k) == v for k, v in written.items())
        ]

    def write(self, kind: KindLike[T], value: T, *, scope: Scope | None = None) -> None:
        """Appends a record, so repeated writes accumulate as candidates."""
        self._records.append((as_kind(kind).name, self._merge_scope(scope), value))
