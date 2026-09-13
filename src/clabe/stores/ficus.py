"""A store backend over ficus, a layered configuration service.

A config is assembled from a ``defaults`` document plus one document per scope (a computer, a
subject), deep-merged in precedence order. Two shapes of document are served:

* **Config** -- layered, merged from ficus' conventional ``default`` document at each scope. See
  :meth:`FicusClient.get_merged` and :class:`MergePolicy`.
* **Records** -- flat, standalone documents named after their kind (``manipulator_position.json``),
  belonging to exactly one scope, read and written whole. Applied to a model by
  :class:`~clabe.modifiers.ByAnimalModifier`.

Layers are fetched individually with ``merge=false`` and merged client-side; ficus' own
``merge=true`` returns 404 for the whole request when any requested scope has no document. The
merge reproduces ficus' ``_deep_update``: dicts recurse, everything else is replaced, and ``null``
is a value rather than a deletion.

Writing a config back sends only the leaves that differ from the current resolution, to a single
layer. See :meth:`FicusClient.plan_write_back`.
"""

import dataclasses
import functools
import logging
from collections.abc import Iterable, Iterator, Sequence
from typing import Any, ClassVar, Literal, TypeVar

import pydantic
import requests
from typing_extensions import Sentinel

from ..services import ServiceSettings
from ..utils import get_computer_name
from ._base import Candidate, Kind, KindLike, Scope, StoreBase, as_kind

logger = logging.getLogger(__name__)

T = TypeVar("T")

#: Where ficus lives when nothing says otherwise. Matches the published client's own fallback.
DEFAULT_BASE_URL = "http://eng-tools/ficus-dev"

#: Extensions ficus accepts. A read matches the extension exactly, and ficus allows a stem only
#: one extension at a time. See :meth:`FicusClient.locate`.
SUPPORTED_EXTENSIONS = (".json", ".yml", ".yaml")

#: Ficus serves the document with this stem when a read names no filename. Layered config uses it;
#: flat records do not.
DEFAULT_STEM = "default"

#: Default for the exact-match calls: :meth:`FicusClient.get_layer`, :meth:`~FicusClient.write` and
#: :meth:`~FicusClient.delete`.
DEFAULT_FILENAME = "default.json"

Segment = Literal["computers", "subjects"]


@dataclasses.dataclass(frozen=True)
class ScopeRef:
    """One ficus scope, narrowed to a single identifier.

    A scope has a collection name and a parameter name. Ficus' routes use them differently::

        POST|PATCH|DELETE  /v1/computers/{hostname}/namespaces/{namespace}/config/{filename}
        POST|PATCH|DELETE  /v1/subjects/{subject_id}/namespaces/{namespace}/config/{filename}
        GET                /v1/namespaces/{namespace}/config?hostname=&subject_id=&filename=&merge=

    Writes address a scope by path, under the collection. There is no scoped ``GET`` route -- a
    ``GET`` on a write path returns 405 -- so reads address it by query parameter on the unscoped
    route. An unrecognised query parameter is ignored, and such a read returns the ``defaults``
    layer.

    Attributes:
        segment: The collection a write addresses this scope under, e.g. ``"computers"``.
        param: The identifier's parameter name, e.g. ``"hostname"`` -- the path variable when
            writing, the query parameter when reading.
        identifier: The machine name or subject id.
    """

    segment: Segment
    param: str
    identifier: str


#: The rig scope, bound to a machine name: ``COMPUTERS("DT201256")``.
COMPUTERS = functools.partial(ScopeRef, "computers", "hostname")

#: The per-animal scope, bound to a subject id: ``SUBJECTS("789907")``.
SUBJECTS = functools.partial(ScopeRef, "subjects", "subject_id")


@dataclasses.dataclass(frozen=True)
class LayerKey:
    """Names a layer, whether or not a document exists there yet.

    Attributes:
        scope: The scope the document lives in. ``None`` is the ``defaults`` layer.
        filename: The document name within that scope.
    """

    scope: ScopeRef | None
    filename: str

    def __str__(self) -> str:
        if self.scope is None:
            return f"defaults/{self.filename}"
        return f"{self.scope.segment}/{self.scope.identifier}/{self.filename}"


#: A path to a leaf within a document, as the sequence of keys leading to it. A tuple rather than a
#: dotted string, so a key containing a dot stays one segment.
LeafPath = tuple[str, ...]


def _dotted(path: LeafPath) -> str:
    """Renders a leaf path for a message. Never parsed back."""
    return ".".join(path)


@dataclasses.dataclass(frozen=True)
class Layer:
    """One document fetched from ficus, with the znode path it came from.

    Attributes:
        data: The document's contents.
        source: The path ficus reports for it, e.g.
            ``/scratch/computers/DT201256/aind-behavior-vr-foraging/default.json``.
        key: The layer as addressed, rather than as reported. ``None`` when not recorded.
    """

    data: dict[str, Any]
    source: str
    key: LayerKey | None = None


@dataclasses.dataclass(frozen=True)
class MergeResult:
    """A merged config together with the documents that produced it.

    Attributes:
        data: The merged document.
        sources: The contributing paths, in the order they were applied (lowest precedence
            first). Empty when no layer existed at all.
        origins: For each leaf in :attr:`data`, the layer whose value won it.
        chain: Every layer consulted, in precedence order, including those that held no document.
    """

    data: dict[str, Any]
    sources: list[str]
    origins: dict[LeafPath, LayerKey] = dataclasses.field(default_factory=dict)
    chain: list[LayerKey] = dataclasses.field(default_factory=list)

    def origin(self, path: LeafPath) -> LayerKey | None:
        """Returns the layer a leaf's value came from.

        Args:
            path: The leaf to look up.

        Returns:
            LayerKey | None: The winning layer, or ``None`` if nothing recorded one.
        """
        return self.origins.get(path)


@dataclasses.dataclass(frozen=True)
class MergePolicy:
    """How layers combine. The defaults reproduce ficus' ``_deep_update``.

    Attributes:
        null_means: ``"value"`` (default) treats ``null`` as a value, so a layer can blank out a
            field inherited from below. ``"delete"`` applies RFC 7386 semantics and removes the key.
        lists: ``"replace"`` (default) lets a later list win outright. ``"concat"`` appends.
        on_type_conflict: What to do when a layer replaces a dict with a non-dict or vice versa.
            ``"warn"`` (default) logs and takes the override; ``"override"`` is silent; ``"raise"``
            refuses.
        on_missing_layer: ``"skip"`` (default) skips an absent document. ``"raise"`` requires every
            requested layer to exist.
    """

    null_means: Literal["value", "delete"] = "value"
    lists: Literal["replace", "concat"] = "replace"
    on_type_conflict: Literal["override", "warn", "raise"] = "warn"
    on_missing_layer: Literal["skip", "raise"] = "skip"


@dataclasses.dataclass(frozen=True)
class WritePolicy:
    """How a layered-config write-back behaves.

    Attributes:
        on_key_removal: What to do when a key present in the resolved config is absent from the
            value being written back. Ficus' ``PATCH`` merges and cannot remove a key. ``"raise"``
            (default) refuses; ``"skip"`` leaves the key in place and writes the rest.
        on_shadowed_write: What to do when a changed leaf's current value comes from a layer above
            the one being written to, so the next read would return the old value. ``"raise"``
            (default) refuses, ``"warn"`` logs and writes anyway, ``"allow"`` is silent. Cannot
            arise when writing to the top of the chain, which is the default target.
    """

    on_key_removal: Literal["raise", "skip"] = "raise"
    on_shadowed_write: Literal["raise", "warn", "allow"] = "raise"


@dataclasses.dataclass(frozen=True)
class LayerWrite:
    """One document a write-back will ``PATCH``.

    Attributes:
        key: The layer being written.
        data: The partial document to send: the changed leaves, nested back into shape.
        paths: The leaves this write carries.
    """

    key: LayerKey
    data: dict[str, Any]
    paths: list[LeafPath]


#: Distinguishes an absent key from one present and set to ``null``.
MISSING = Sentinel("MISSING")


def _note_type_conflict(path: str, current: Any, value: Any, policy: MergePolicy) -> None:
    """Reports a dict/non-dict disagreement between two layers.

    Args:
        path: Dotted path to the conflicting key, for the message.
        current: The value from the lower-precedence layer.
        value: The value from the higher-precedence layer.
        policy: Decides whether to log or raise.

    Raises:
        TypeError: If ``policy.on_type_conflict`` is ``"raise"``.
    """
    message = (
        f"Layer disagreement at {path!r}: {type(current).__name__} is being replaced by "
        f"{type(value).__name__}. This usually means two layers were written against different "
        f"schema versions."
    )
    if policy.on_type_conflict == "raise":
        raise TypeError(message)
    if policy.on_type_conflict == "warn":
        logger.warning(message)


def deep_merge(
    base: dict[str, Any],
    override: dict[str, Any],
    *,
    policy: MergePolicy | None = None,
    _path: str = "",
) -> dict[str, Any]:
    """Merges ``override`` onto ``base``, mirroring ficus' own ``_deep_update``.

    Dicts present on both sides recurse; everything else is replaced outright, lists included.
    ``null`` on the overriding side is a value, not a deletion, unless ``policy`` says otherwise.

    Args:
        base: The lower-precedence document.
        override: The higher-precedence document, layered on top.
        policy: How to combine. ``on_missing_layer`` is not read here; it governs fetching, and is
            applied by :meth:`FicusClient.get_merged`.
        _path: Internal. Dotted path to the current level, used only in messages.

    Returns:
        dict[str, Any]: A new merged document. Neither argument is modified.
    """
    policy = policy or MergePolicy()
    result = dict(base)
    for key, value in override.items():
        here = f"{_path}.{key}" if _path else key

        if value is None and policy.null_means == "delete":
            result.pop(key, None)
            continue

        current = result.get(key, MISSING)

        if isinstance(current, dict) and isinstance(value, dict):
            result[key] = deep_merge(current, value, policy=policy, _path=here)
            continue

        if isinstance(current, list) and isinstance(value, list) and policy.lists == "concat":
            result[key] = [*current, *value]
            continue

        # A dict replacing a scalar, or the reverse. Filling in a null is not a conflict.
        if (
            current is not MISSING
            and current is not None
            and value is not None
            and isinstance(current, dict) != isinstance(value, dict)
        ):
            _note_type_conflict(here, current, value, policy)

        result[key] = value
    return result


def iter_leaves(data: dict[str, Any], _prefix: LeafPath = ()) -> Iterator[tuple[LeafPath, Any]]:
    """Walks a document, yielding every leaf and the path of keys that reaches it.

    A leaf is anything :func:`deep_merge` replaces wholesale: scalars, ``None``, lists and empty
    dicts. Non-empty dicts are recursed into.

    Args:
        data: The document to walk.
        _prefix: Internal. The path to the current level.

    Yields:
        tuple[LeafPath, Any]: Each leaf's path and value.
    """
    for key, value in data.items():
        here = (*_prefix, key)
        if isinstance(value, dict) and value:
            yield from iter_leaves(value, here)
        else:
            yield here, value


def diff_leaves(old: dict[str, Any], new: dict[str, Any]) -> tuple[dict[LeafPath, Any], list[LeafPath]]:
    """Finds the leaves that differ between two documents.

    Args:
        old: The document as it currently resolves.
        new: The document as it should resolve.

    Returns:
        tuple[dict[LeafPath, Any], list[LeafPath]]: The changed leaves with their new values, and
            the paths present only in ``old``.
    """
    old_leaves = dict(iter_leaves(old))
    new_leaves = dict(iter_leaves(new))

    changed = {path: value for path, value in new_leaves.items() if path not in old_leaves or old_leaves[path] != value}

    # A leaf that turned into a subtree, or the reverse, appears as both a change and a removal.
    # The change already replaces everything underneath it, so it is not also a removal.
    def _related_to_a_change(path: LeafPath) -> bool:
        return any(path[: len(c)] == c or c[: len(path)] == path for c in changed)

    removed = [path for path in old_leaves if path not in new_leaves and not _related_to_a_change(path)]
    return changed, removed


def nest_leaves(leaves: dict[LeafPath, Any]) -> dict[str, Any]:
    """Rebuilds a nested document from flat leaf paths.

    Args:
        leaves: Leaf paths and their values.

    Returns:
        dict[str, Any]: The nested document.
    """
    out: dict[str, Any] = {}
    for path, value in leaves.items():
        node = out
        for part in path[:-1]:
            node = node.setdefault(part, {})
        node[path[-1]] = value
    return out


def _stamp_origins(data: dict[str, Any], key: LayerKey, into: dict[LeafPath, LayerKey]) -> None:
    """Records this layer as the owner of every leaf it defines.

    Called in precedence order, so the last layer to define a leaf owns it. Ancestors and subtrees
    the new leaf replaces are dropped from the map.

    Args:
        data: The layer's own document.
        key: The layer defining these leaves.
        into: The origin map being built, modified in place.
    """
    for path, _ in iter_leaves(data):
        for stale in [p for p in into if p[: len(path)] == path or path[: len(p)] == p]:
            del into[stale]
        into[path] = key


class FicusClient:
    """An HTTP client for ficus, independent of clabe's store vocabulary.

    Example:
        ```python
        client = FicusClient("http://eng-tools/ficus-dev")
        result = client.get_merged("aind-behavior-vr-foraging", scopes=[COMPUTERS("DT201256")])
        print(result.sources)  # contributing documents, lowest precedence first
        ```
    """

    def __init__(
        self,
        base_url: str = DEFAULT_BASE_URL,
        *,
        default_extension: str = ".json",
        timeout: float = 10.0,
        session: requests.Session | None = None,
    ) -> None:
        """
        Args:
            base_url: Ficus' root URL.
            default_extension: Names a document ficus does not have yet, and is tried first when
                reading. Reads fall back across :data:`SUPPORTED_EXTENSIONS`, and an existing
                document keeps the extension it has.
            timeout: Seconds to wait on any single request.
            session: A ``requests`` session to reuse. Defaults to ``None``, in which case one is
                created.
        """
        self.base_url = base_url.rstrip("/")
        self.default_extension = default_extension
        self.timeout = timeout
        self._session = session or requests.Session()

    def __str__(self) -> str:
        return f"{type(self).__name__}({self.base_url})"

    @staticmethod
    def _is_absent(response: requests.Response) -> bool:
        """Returns whether a 404 means "no such document" rather than a genuine failure.

        Ficus returns 404 for a missing document, a missing scope directory and some real errors,
        with different messages, so the body is inspected rather than the status alone.

        Args:
            response: The 404 response to classify.

        Returns:
            bool: True if this reads as an absent document.
        """
        try:
            detail = str(response.json().get("detail", ""))
        except ValueError:
            detail = response.text
        return "not found" in detail.lower()

    def _read_url(self, namespace: str) -> str:
        """Builds the URL reads go to. The scope is a query parameter, not part of the path.

        Args:
            namespace: The ficus namespace.

        Returns:
            str: The full URL.
        """
        return f"{self.base_url}/v1/namespaces/{namespace}/config"

    def _write_url(self, namespace: str, *, filename: str, scope: ScopeRef | None) -> str:
        """Builds the URL writes and deletes go to, with the scope in the path.

        Args:
            namespace: The ficus namespace.
            filename: The document name.
            scope: The scope to address. ``None`` targets the ``defaults`` layer.

        Returns:
            str: The full URL.
        """
        prefix = f"{self.base_url}/v1"
        if scope is not None:
            prefix = f"{prefix}/{scope.segment}/{scope.identifier}"
        return f"{prefix}/namespaces/{namespace}/config/{filename}"

    def get_layer(
        self,
        namespace: str,
        *,
        filename: str = DEFAULT_FILENAME,
        scope: ScopeRef | None = None,
    ) -> Layer | None:
        """Fetches exactly one document, without any server-side merging.

        Args:
            namespace: The ficus namespace.
            filename: The document to fetch.
            scope: The scope to read from. ``None`` reads the ``defaults`` layer.

        Returns:
            Layer | None: The document, or ``None`` if it does not exist.

        Raises:
            requests.HTTPError: For any failure other than the document being absent.
        """
        params: dict[str, str] = {"filename": filename, "merge": "false"}
        if scope is not None:
            params[scope.param] = scope.identifier

        response = self._session.get(self._read_url(namespace), params=params, timeout=self.timeout)
        if response.status_code == 404 and self._is_absent(response):
            logger.debug("No ficus document at namespace=%r filename=%r scope=%r", namespace, filename, scope)
            return None
        response.raise_for_status()

        payload = response.json()
        files = payload.get("details", {}).get("files") or []
        return Layer(
            data=payload["data"],
            source=files[0] if files else filename,
            key=LayerKey(scope=scope, filename=filename),
        )

    def locate(self, namespace: str, stem: str, *, scope: ScopeRef | None = None) -> tuple[str, Layer | None]:
        """Finds which extension a document is stored under, and fetches it.

        A read matches the extension exactly, so a document stored as ``rig.yml`` is absent to a
        request for ``rig.json``. :attr:`default_extension` is tried first, then the rest of
        :data:`SUPPORTED_EXTENSIONS`. Ficus allows a stem only one extension at a time, so at most
        one can exist.

        Args:
            namespace: The ficus namespace.
            stem: The document name without its extension, e.g. ``"default"``.
            scope: The scope to look in. ``None`` is the ``defaults`` layer.

        Returns:
            tuple[str, Layer | None]: The filename to address -- the extension found, or ``stem``
                plus :attr:`default_extension` when none was -- and the layer, if it exists.

        Raises:
            requests.HTTPError: For any failure other than a document being absent.
        """
        for extension in dict.fromkeys((self.default_extension, *SUPPORTED_EXTENSIONS)):
            filename = f"{stem}{extension}"
            layer = self.get_layer(namespace, filename=filename, scope=scope)
            if layer is not None:
                return filename, layer
        return f"{stem}{self.default_extension}", None

    def get_merged(
        self,
        namespace: str,
        *,
        filename: str | None = None,
        scopes: Iterable[ScopeRef] = (),
        policy: MergePolicy | None = None,
    ) -> MergeResult:
        """Fetches every layer in the chain and merges them.

        The chain runs ``defaults`` first, then each scope in the order given, and within each
        layer the ``default`` document before any extra ``filename``. This is ficus' documented
        precedence.

        Args:
            namespace: The ficus namespace.
            filename: An extra document to layer on top of ``default`` at every level. ``None``
                uses ``default`` alone. Taken literally, extension included; the ``default``
                document's extension is resolved per layer by :meth:`locate`.
            scopes: Scopes to layer, lowest precedence first.
            policy: How to combine layers, and what to do about missing ones.

        Returns:
            MergeResult: The merged document and the paths that produced it.

        Raises:
            LookupError: If ``policy.on_missing_layer`` is ``"raise"`` and a layer is absent.
        """
        policy = policy or MergePolicy()
        scopes = list(scopes)  # consumed more than once below, so never leave it a generator
        merged: dict[str, Any] = {}
        sources: list[str] = []
        origins: dict[LeafPath, LayerKey] = {}
        chain: list[LayerKey] = []

        for key, layer in self._fetch_chain(namespace, filename, scopes):
            chain.append(key)
            if layer is None:
                if policy.on_missing_layer == "raise":
                    raise LookupError(f"No ficus document at {key} in namespace {namespace!r}")
                continue
            merged = deep_merge(merged, layer.data, policy=policy)
            sources.append(layer.source)
            _stamp_origins(layer.data, key, origins)

        if not sources:
            # An unknown namespace returns the same "not found" as a namespace with no documents.
            logger.warning(
                "No ficus document found at any layer for namespace=%r (scopes=%r). If this is "
                "unexpected, check the namespace spelling -- an unknown namespace looks exactly "
                "like an empty one.",
                namespace,
                [s.identifier for s in scopes],
            )

        return MergeResult(data=merged, sources=sources, origins=origins, chain=chain)

    def _fetch_chain(
        self,
        namespace: str,
        filename: str | None,
        scopes: Iterable[ScopeRef],
    ) -> Iterator[tuple[LayerKey, Layer | None]]:
        """Fetches every layer of a merge chain, lowest precedence first.

        Each scope contributes the ``default`` document, whose extension is resolved by
        :meth:`locate`, then any extra ``filename``, which is fetched by exact name.

        Args:
            namespace: The ficus namespace.
            filename: An extra document layered above ``default`` at each level, or ``None``.
            scopes: The scopes to include, lowest precedence first. ``defaults`` is prepended.

        Yields:
            tuple[LayerKey, Layer | None]: Each layer, and its document if it has one.
        """
        extra = None if filename is None or filename.rsplit(".", 1)[0] == DEFAULT_STEM else filename
        for scope in (None, *scopes):
            name, layer = self.locate(namespace, DEFAULT_STEM, scope=scope)
            yield LayerKey(scope, name), layer
            if extra is not None:
                yield LayerKey(scope, extra), self.get_layer(namespace, filename=extra, scope=scope)

    def write(
        self,
        namespace: str,
        data: dict[str, Any],
        *,
        filename: str = DEFAULT_FILENAME,
        scope: ScopeRef | None = None,
        create_only: bool = False,
    ) -> None:
        """Writes one document, to exactly one layer.

        ``POST`` is tried first and falls back to ``PATCH`` on a 409. Ficus' ``PATCH`` deep-merges
        the payload into the existing document server-side, so a partial write needs no
        read-modify-write here; it cannot remove a key, which takes a delete and a rewrite.

        Args:
            namespace: The ficus namespace.
            data: The document, or the part of it being changed.
            filename: The document to write.
            scope: The layer to write to. ``None`` writes the ``defaults`` layer, which every
                consumer of the namespace reads.
            create_only: Whether to fail rather than update an existing document.

        Raises:
            requests.HTTPError: If the write fails.
        """
        url = self._write_url(namespace, filename=filename, scope=scope)
        verb = "POST"
        response = self._session.post(url, json=data, timeout=self.timeout)
        if response.status_code == 409 and not create_only:
            verb = "PATCH"
            response = self._session.patch(url, json=data, timeout=self.timeout)
        response.raise_for_status()
        logger.info("Wrote ficus document %s (%s)", url, verb)

    @staticmethod
    def plan_write_back(
        current: MergeResult,
        new_data: dict[str, Any],
        *,
        target: LayerKey | None = None,
        policy: WritePolicy | None = None,
    ) -> LayerWrite | None:
        """Works out what a write-back would do, without doing it.

        Only the leaves that differ are written, to a single layer. The default target is the top
        of the merge chain, which no layer can shadow. A lower target is allowed, and
        ``policy.on_shadowed_write`` then governs any leaf whose current value comes from above it.

        Args:
            current: The config as it resolves now, from :meth:`get_merged`. Its ``origins`` and
                ``chain`` drive the shadow check.
            new_data: The config as it should resolve.
            target: The layer to write to. ``None`` (default) means the top of ``current.chain``.
            policy: How to handle removals and shadowed writes.

        Returns:
            LayerWrite | None: The write to perform, or ``None`` if nothing changed.

        Raises:
            ValueError: If there is no chain to write to.
            LookupError: If a key would have to be removed and ``policy.on_key_removal`` is
                ``"raise"``.
            PermissionError: If the write would be shadowed and ``policy.on_shadowed_write`` is
                ``"raise"``.
        """
        policy = policy or WritePolicy()
        if not current.chain:
            raise ValueError("This config resolved from no chain at all, so there is no layer to write back to.")
        key = target if target is not None else current.chain[-1]

        changed, removed = diff_leaves(current.data, new_data)
        if removed and policy.on_key_removal == "raise":
            raise LookupError(
                f"Writing back would have to remove {[_dotted(p) for p in removed]!r}, but ficus' PATCH "
                f"merges and cannot remove a key. Delete and rewrite {key}, or set "
                f"WritePolicy(on_key_removal='skip') to leave these keys in place."
            )
        if removed:
            logger.warning("Leaving %r in place: a partial write cannot remove keys.", [_dotted(p) for p in removed])

        if not changed:
            logger.debug("Write-back to %s is a no-op: nothing changed.", key)
            return None

        FicusClient._check_not_shadowed(current, changed, key, policy)
        return LayerWrite(key=key, data=nest_leaves(changed), paths=sorted(changed))

    @staticmethod
    def _check_not_shadowed(
        current: MergeResult,
        changed: dict[LeafPath, Any],
        key: LayerKey,
        policy: WritePolicy,
    ) -> None:
        """Applies ``policy.on_shadowed_write`` to leaves a higher layer in the chain owns.

        Args:
            current: The config as it resolves now.
            changed: The leaves about to be written.
            key: The layer being written to.
            policy: Decides whether to log or raise.

        Raises:
            PermissionError: If any leaf is shadowed and ``policy.on_shadowed_write`` is ``"raise"``.
        """
        if policy.on_shadowed_write == "allow":
            return
        if key not in current.chain:
            # Outside the chain there is nothing to rank the target against.
            logger.debug("Target %s is outside the merge chain, so it cannot be ranked against it.", key)
            return

        rank = current.chain.index(key)
        shadowed: dict[LeafPath, LayerKey] = {}
        for path in changed:
            owner = current.origin(path)
            if owner is not None and owner in current.chain and current.chain.index(owner) > rank:
                shadowed[path] = owner
        if not shadowed:
            return

        detail = ", ".join(f"{_dotted(p)} (owned by {owner})" for p, owner in sorted(shadowed.items()))
        message = (
            f"Writing to {key} would not change {len(shadowed)} value(s), because a higher layer in the "
            f"chain already sets them and would keep winning: {detail}. Write to that layer instead, or "
            f"set WritePolicy(on_shadowed_write='warn')."
        )
        if policy.on_shadowed_write == "raise":
            raise PermissionError(message)
        logger.warning(message)

    def write_back(
        self,
        namespace: str,
        current: MergeResult,
        new_data: dict[str, Any],
        *,
        target: LayerKey | None = None,
        policy: WritePolicy | None = None,
    ) -> LayerWrite | None:
        """Writes the difference between a resolved config and a new one back to a single layer.

        See :meth:`plan_write_back` for how the layer and the payload are chosen; this runs that
        plan.

        Args:
            namespace: The ficus namespace.
            current: The config as it resolves now, from :meth:`get_merged`.
            new_data: The config as it should resolve.
            target: The layer to write to. ``None`` (default) means the top of the merge chain.
            policy: How to handle removals and shadowed writes.

        Returns:
            LayerWrite | None: What was written, or ``None`` if nothing changed.

        Raises:
            requests.HTTPError: If the write fails.
        """
        plan = self.plan_write_back(current, new_data, target=target, policy=policy)
        if plan is None:
            return None
        self.write(namespace, plan.data, filename=plan.key.filename, scope=plan.key.scope)
        logger.info("Wrote %d changed value(s) back to %s", len(plan.paths), plan.key)
        return plan

    def delete(self, namespace: str, *, filename: str = DEFAULT_FILENAME, scope: ScopeRef | None = None) -> bool:
        """Deletes one document.

        Args:
            namespace: The ficus namespace.
            filename: The document to delete.
            scope: The layer to delete from. ``None`` is the ``defaults`` layer.

        Returns:
            bool: True if a document was deleted, False if there was nothing there.

        Raises:
            requests.HTTPError: If the delete fails for any reason other than absence.
        """
        response = self._session.delete(
            self._write_url(namespace, filename=filename, scope=scope), timeout=self.timeout
        )
        if response.status_code == 404 and self._is_absent(response):
            return False
        response.raise_for_status()
        return True

    def list_files(self, namespace: str, *, scope: ScopeRef | None = None) -> list[str]:
        """Lists the documents visible for a namespace, optionally within one scope.

        Args:
            namespace: The ficus namespace.
            scope: Restrict to this scope. ``None`` lists the ``defaults`` layer only.

        Returns:
            list[str]: The document paths, or an empty list if the scope holds nothing.

        Raises:
            requests.HTTPError: For any failure other than the scope being absent.
        """
        params = {} if scope is None else {scope.param: scope.identifier}
        url = f"{self.base_url}/v1/list_files/{namespace}/configs"
        response = self._session.get(url, params=params, timeout=self.timeout)
        if response.status_code == 404 and self._is_absent(response):
            return []
        response.raise_for_status()
        return response.json()["data"]


class FicusSettings(ServiceSettings):
    """Settings for :class:`FicusStore`, read from the ``ficus`` section of clabe.yml."""

    __yml_section__: ClassVar[str | None] = "ficus"

    namespace: str = pydantic.Field(
        description="The ficus namespace this store reads, e.g. 'aind-behavior-vr-foraging'. Required.",
    )
    base_url: str = pydantic.Field(
        default=DEFAULT_BASE_URL,
        description="Ficus' root URL.",
    )
    filename: str | None = pydantic.Field(
        default=None,
        description=(
            "An extra document layered above 'default' for config kinds. Taken literally, "
            "extension included. None uses 'default' alone."
        ),
    )
    extension: Literal[".json", ".yml", ".yaml"] = pydantic.Field(
        default=".json",
        description=(
            "Names a document ficus does not have yet, and is tried first when reading. Reads fall "
            "back across the other supported extensions; an existing document keeps the one it has."
        ),
    )
    config_kinds: set[str] = pydantic.Field(
        default_factory=lambda: {"rig"},
        description=(
            "Kind names served as layered config, merged across scopes from ficus' 'default' "
            "document. Every other kind is a flat record in its own document, named after the kind."
        ),
    )
    config_scopes: list[Segment] = pydantic.Field(
        default_factory=lambda: ["computers"],
        description=(
            "Which scopes make up the config merge chain, lowest precedence first. Empty leaves "
            "the chain at 'defaults' alone."
        ),
    )
    timeout: float = pydantic.Field(
        default=10.0,
        description="Seconds to wait on any single request.",
    )
    policy: MergePolicy = pydantic.Field(
        default_factory=MergePolicy,
        description="How layers combine. The defaults reproduce ficus' own merge.",
    )
    write_policy: WritePolicy = pydantic.Field(
        default_factory=WritePolicy,
        description="How a layered-config write-back handles key removals and shadowed writes.",
    )


class FicusStore(StoreBase):
    """A store over a single ficus namespace.

    Serves two shapes of document, told apart by kind name:

    * A kind in ``config_kinds`` (``"rig"`` by default) is **layered config**: merged across
      ``defaults`` and the configured scopes, from ficus' ``default`` document. The merge yields
      exactly one document, so :meth:`resolve` never prompts.
    * Any other kind is a **flat record**: a standalone document named after the kind, living in one
      scope, read and written whole. :class:`~clabe.modifiers.ByAnimalModifier` uses this shape.

    Example:
        ```python
        store = FicusStore(FicusSettings(namespace="aind-behavior-vr-foraging"))
        rig = store.resolve(Kind.from_rig(AindVrForagingRig))
        position = store.scoped(subject="789907").resolve(Kind(ManipulatorPosition))
        ```
    """

    def __init__(
        self,
        settings: FicusSettings | None = None,
        *,
        client: FicusClient | None = None,
        serves: str | set[str] | None = None,
        scope: Scope | None = None,
    ) -> None:
        """
        Args:
            settings: Settings for this store. Defaults to ``None``, in which case
                :class:`FicusSettings` is built from the ``ficus`` YAML section and the
                environment, and construction fails if ``namespace`` cannot be resolved that way.
            client: The ficus client. Defaults to ``None``, in which case one is built from
                ``settings``.
            serves: Kind name(s) this store will answer for, or ``None`` (default) for any kind.
            scope: Initial scope. ``computer_name`` defaults to this machine's and selects the rig
                layer; narrow it with ``scoped(computer_name=...)`` to address another machine.
        """
        super().__init__(scope={"computer_name": get_computer_name(), **(scope or {})})
        self._settings = settings or FicusSettings()
        self._client = client or FicusClient(
            self._settings.base_url,
            default_extension=self._settings.extension,
            timeout=self._settings.timeout,
        )
        self._serves: set[str] | None = (
            None if serves is None else ({serves} if isinstance(serves, str) else set(serves))
        )

    @property
    def client(self) -> FicusClient:
        """The underlying ficus client."""
        return self._client

    @property
    def namespace(self) -> str:
        """The ficus namespace this store reads."""
        return self._settings.namespace

    def __str__(self) -> str:
        return f"{type(self).__name__}({self.namespace} @ {self._client.base_url})"

    def _require_supported(self, kind: Kind[T]) -> None:
        """Raises unless this store is configured to serve ``kind``.

        Args:
            kind: The kind requested.

        Raises:
            LookupError: If ``serves`` was given and does not include this kind's name.
        """
        if self._serves is not None and kind.name not in self._serves:
            raise LookupError(f"FicusStore only serves {sorted(self._serves)!r}, not {kind.name!r}.")

    def _is_config(self, kind: Kind[T]) -> bool:
        """Returns whether this kind is layered config rather than a flat record."""
        return kind.name in self._settings.config_kinds

    def _rig_ref(self, scope: Scope) -> ScopeRef:
        """Binds the rig layer to the ``computer_name`` in scope, or to this machine.

        Args:
            scope: The merged scope for this call.

        Returns:
            ScopeRef: The rig scope to read and write under.
        """
        return COMPUTERS(scope.get("computer_name") or get_computer_name())

    def _config_scopes(self, scope: Scope) -> list[ScopeRef]:
        """Builds the merge chain's scopes, lowest precedence first.

        Args:
            scope: The merged scope for this call.

        Returns:
            list[ScopeRef]: The bound scopes, skipping any this store cannot resolve an identifier
                for.
        """
        refs: list[ScopeRef] = []
        for segment in self._settings.config_scopes:
            if segment == "computers":
                refs.append(self._rig_ref(scope))
            elif subject := scope.get("subject"):
                refs.append(SUBJECTS(subject))
        return refs

    def _record_scope(self, scope: Scope) -> ScopeRef:
        """Picks the scope a flat record belongs to: the subject when one is in scope, else the rig.

        Args:
            scope: The merged scope for this call.

        Returns:
            ScopeRef: The scope to read from or write to.
        """
        return SUBJECTS(subject) if (subject := scope.get("subject")) else self._rig_ref(scope)

    def _candidates(self, kind: Kind[T], scope: Scope) -> Sequence[Candidate[T]]:
        """Returns the single document for this kind, if one exists.

        Args:
            kind: The kind to fetch.
            scope: The merged scope for this call.

        Returns:
            Sequence[Candidate[T]]: Empty if nothing is stored; otherwise exactly one candidate,
                since both a merged chain and a flat record yield one document.

        Raises:
            LookupError: If this store does not serve ``kind``.
            requests.HTTPError: If a request fails for any reason other than absence.
        """
        self._require_supported(kind)

        if self._is_config(kind):
            result = self._client.get_merged(
                self.namespace,
                filename=self._settings.filename,
                scopes=self._config_scopes(scope),
                policy=self._settings.policy,
            )
            if not result.sources:
                return []
            logger.debug("Resolved %s from %s", kind.name, " <- ".join(result.sources))
            label = f"{self.namespace} [{', '.join(result.sources)}]"
            data, source = result.data, label
        else:
            target = self._record_scope(scope)
            _, layer = self._client.locate(self.namespace, kind.name, scope=target)
            if layer is None:
                return []
            data, source = layer.data, layer.source

        return [Candidate(source, kind.adapter.validate_python(data))]

    def write(self, kind: KindLike[T], value: T, *, scope: Scope | None = None) -> None:
        """Persists a value.

        A flat record is written whole, to the subject's scope when one is in scope and the rig's
        otherwise. Layered config is written as a diff against its current resolution: only the
        leaves that differ are sent, to the top of the merge chain. See
        :meth:`FicusClient.plan_write_back`.

        Args:
            kind: The kind being written.
            value: The value.
            scope: Extra scope for this call, layered over the store's own.

        Raises:
            LookupError: If this store does not serve ``kind``, if the chain has no scope to write
                to, or if writing back would have to remove a key.
            PermissionError: If a layered write would land below a layer that overrides it.
            requests.HTTPError: If the write fails.
        """
        _kind = as_kind(kind)
        self._require_supported(_kind)
        merged = self._merge_scope(scope)
        payload = _kind.adapter.dump_python(value, mode="json")

        if self._is_config(_kind):
            self._write_config(_kind, payload, merged)
            return

        target = self._record_scope(merged)
        # Ficus allows a stem only one extension, so an existing document is the one to update.
        filename, _ = self._client.locate(self.namespace, _kind.name, scope=target)
        self._client.write(self.namespace, payload, filename=filename, scope=target)

    def _write_config(self, kind: Kind[T], payload: dict[str, Any], scope: Scope) -> None:
        """Writes layered config back as a diff against its current resolution.

        Args:
            kind: The config kind, used to put both sides of the diff through the same model.
            payload: The config as it should resolve, already dumped to JSON types.
            scope: The merged scope for this call.

        Raises:
            LookupError: If the chain holds nothing but the shared ``defaults`` layer.
        """
        scopes = self._config_scopes(scope)
        if not scopes:
            raise LookupError(
                "config_scopes is empty, so the only layer left is 'defaults', which every machine "
                "in the namespace reads. Add a scope to config_scopes, or call "
                "FicusClient.write_back(target=LayerKey(None, ...)) to write defaults explicitly."
            )

        current = self._client.get_merged(
            self.namespace,
            filename=self._settings.filename,
            scopes=scopes,
            policy=self._settings.policy,
        )
        current = dataclasses.replace(current, data=self._as_model_shape(kind, current))
        self._client.write_back(self.namespace, current, payload, policy=self._settings.write_policy)

    @staticmethod
    def _as_model_shape(kind: Kind[T], current: MergeResult) -> dict[str, Any]:
        """Re-expresses the resolved document the way the model would dump it.

        A model round trip materialises every default the document omitted and drops every key the
        model does not declare. Both sides of the diff go through the model so that those
        differences cancel and only the edit remains.

        Args:
            kind: The kind whose model defines the shape.
            current: The resolved config.

        Returns:
            dict[str, Any]: The resolved document in model shape, or unchanged when nothing
                resolved, in which case there is no baseline to normalise against.
        """
        if not current.sources:
            return current.data
        return kind.adapter.dump_python(kind.adapter.validate_python(current.data), mode="json")
