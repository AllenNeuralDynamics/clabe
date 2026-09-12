"""A read-only store backend over ficus, via the ``confierge`` client.

Ficus is a config service: a config is assembled from a ``defaults`` layer plus one layer per
scope identifier, deep-merged **server-side** into exactly one config. This module adapts that
one-config-per-request model onto :class:`~clabe.stores._base.StoreBase`.
"""

import importlib.util

if importlib.util.find_spec("confierge") is None:
    raise ImportError(
        "The 'confierge' package is required to use this module. "
        "Install the optional dependencies by running `pip install .[aind-services]`"
    )

import logging
from collections.abc import Sequence
from pathlib import Path
from typing import ClassVar, TypeVar

import pydantic
import requests
from confierge import Confierge

from ..services import ServiceSettings
from ..utils.aind_validators import get_aind_rig_name
from ._base import Candidate, Kind, KindLike, Scope, StoreBase

logger = logging.getLogger(__name__)

T = TypeVar("T")

#: Reserved clabe scope key selecting a ficus *mode* (a variant within every scope, e.g.
#: ``"default"`` vs. ``"high-freq"``). Ficus forbids ``mode`` as a scope name, so this key is
#: never itself sent to ficus as a scope -- it is consumed before scope translation.
MODE_SCOPE_KEY = "mode"


class ConfiergeSettings(ServiceSettings):
    """
    Settings for :class:`ConfiergeStore`.

    Configuration for the ficus namespace this store reads, the underlying ``confierge`` client,
    and the translation from clabe scope keys onto ficus scopes.

    Attributes:
        namespace: The ficus namespace to read, e.g. ``"vr_frg"``. No sensible default.
        base_url: The ficus base URL. Defaults to ``None``, in which case ``confierge.Confierge``
            falls back to the ``FICUS_BASE_URL`` environment variable, then a hardcoded default.
        cache_dir: Directory for ``confierge``'s offline disk cache. Defaults to ``None``, in
            which case ``confierge.Confierge`` picks a platform-appropriate cache directory.
        raise_connection_errors: Whether ``confierge.Confierge`` should raise if it cannot reach
            ficus at construction time. Defaults to ``False``, matching the client's own default.
        mode: The ficus mode to request when a call's scope does not supply one. Defaults to
            ``None``, in which case ficus applies its own default (``"default"``).
        scope_map: Maps a clabe scope key to its ficus scope name. Keys not present here are
            passed through unchanged (identity mapping). Defaults to ``{"subject": "subject_id"}``.
        scope_order: The ficus scopes this store knows about, lowest priority first. Also acts as
            the allow-list a translated scope key must appear in to survive filtering. Defaults to
            ``["hostname", "subject_id"]``.
        rig_scope: The ficus scope under which this store injects the rig identity from
            :func:`~clabe.utils.aind_validators.get_aind_rig_name`. ``None`` disables rig
            injection. Defaults to ``"hostname"``.
    """

    __yml_section__: ClassVar[str | None] = "confierge"

    namespace: str
    base_url: str | None = None
    cache_dir: Path | None = None
    raise_connection_errors: bool = False
    mode: str | None = None
    scope_map: dict[str, str] = pydantic.Field(default_factory=lambda: {"subject": "subject_id"})
    scope_order: list[str] = pydantic.Field(default_factory=lambda: ["hostname", "subject_id"])
    rig_scope: str | None = "hostname"


class ConfiergeStore(StoreBase):
    """
    A read-only store over a single ficus namespace, via the ``confierge`` client.

    Ficus deep-merges a config's layers server-side and returns exactly one result, so
    :meth:`_candidates` yields at most one :class:`Candidate` and the default
    :meth:`~clabe.stores._base.StoreBase.resolve` auto-selects it without prompting. This store
    routes one namespace; a deployment that needs more than one should route each through its own
    ``ConfiergeStore`` with :class:`~clabe.stores._base.CompositeStore`.

    Writing is not supported: see :meth:`write`.

    Example:
        ```python
        store = CompositeStore(
            default=LocalFileStore(root=VR_LIB),
            routes={"rig": ConfiergeStore(ConfiergeSettings(namespace="vr_frg"))},
        )
        ```
    """

    def __init__(
        self,
        settings: ConfiergeSettings | None = None,
        *,
        client: Confierge | None = None,
        serves: str | set[str] | None = "rig",
        scope: Scope | None = None,
    ) -> None:
        """
        Args:
            settings: Settings for this store. Defaults to ``None``, in which case
                :class:`ConfiergeSettings` is built from the ``confierge`` YAML section (and the
                environment), and construction fails if ``namespace`` cannot be resolved that way.
            client: The ``confierge`` client. Defaults to ``None``, in which case one is built from
                ``settings``. Injectable so tests never touch the network.
            serves: The kind name(s) this store serves, or ``None`` to serve any kind. Defaults to
                ``"rig"``.
            scope: Initial scope.
        """
        super().__init__(scope=scope)
        self._settings = settings or ConfiergeSettings()
        self._client = client or Confierge(
            base_url=self._settings.base_url,
            cache_dir=self._settings.cache_dir,
            raise_connection_errors=self._settings.raise_connection_errors,
        )
        self._serves: set[str] | None = (
            None if serves is None else ({serves} if isinstance(serves, str) else set(serves))
        )

    @property
    def client(self) -> Confierge:
        """The underlying ``confierge`` client."""
        return self._client

    @property
    def namespace(self) -> str:
        """The ficus namespace this store reads."""
        return self._settings.namespace

    def __str__(self) -> str:
        return f"{type(self).__name__}({self.namespace} @ {self._client.base_url})"

    def _require_supported(self, kind: Kind[T]) -> None:
        """
        Raises unless this store is configured to serve the given kind.

        Args:
            kind: The kind requested.

        Raises:
            LookupError: If ``serves`` was given at construction and does not include this kind's
                name.
        """
        if self._serves is not None and kind.name not in self._serves:
            raise LookupError(f"ConfiergeStore only serves {sorted(self._serves)!r}, not {kind.name!r}.")

    def _translate_scope(self, scope: Scope) -> dict[str, str]:
        """
        Translates a clabe scope into a ficus scopes dict: translated, filtered and ordered.

        ``CompositeStore.scoped()`` narrows every routed backend at once, so this store will
        receive clabe scope keys ficus has never heard of (e.g. ``subject``, ``computer_name``).
        Passing them through would make ``Confierge.validate_scopes`` raise, so each key is:

        1. Dropped, if it is the reserved :data:`MODE_SCOPE_KEY` (consumed by the caller instead).
        2. Renamed through ``scope_map``, defaulting to identity for unmapped keys.
        3. Dropped, at ``logger.debug``, if its ficus name is not in the configured known set
           (``scope_order`` union ``{rig_scope}``). Filtering is against this configured set, not
           a network call to ``client.get_ficus_scopes()``, so translation never touches ficus.

        The rig identity this store owns is then injected under ``rig_scope`` (if set), from
        :func:`~clabe.utils.aind_validators.get_aind_rig_name`, overriding any value already
        present under that key -- clabe's own ``computer_name`` is deliberately never mapped onto
        it, since ``LocalFileStore`` fills ``computer_name`` from ``COMPUTERNAME``/
        ``platform.node()`` while ficus keys rigs on ``aibs_comp_id``, a different string for the
        same machine.

        Finally the result is emitted in ``scope_order`` (lowest priority first), then any
        remaining keys. This ordering is load-bearing: ficus derives merge precedence from the
        insertion order of the scopes dict on the request, not from clabe's own scope order.

        Args:
            scope: The merged clabe scope for this call.

        Returns:
            dict[str, str]: The ficus scopes dict, ready to pass to ``Confierge.get_config_safe``.
        """
        known = set(self._settings.scope_order)
        if self._settings.rig_scope is not None:
            known.add(self._settings.rig_scope)

        translated: dict[str, str] = {}
        for key, value in scope.items():
            if key == MODE_SCOPE_KEY:
                continue
            ficus_key = self._settings.scope_map.get(key, key)
            if ficus_key not in known:
                logger.debug("Dropping scope key %r (as %r): not a known ficus scope for this store.", key, ficus_key)
                continue
            translated[ficus_key] = value

        if self._settings.rig_scope is not None:
            translated[self._settings.rig_scope] = get_aind_rig_name(required=True)

        ordered: dict[str, str] = {}
        for key in self._settings.scope_order:
            if key in translated:
                ordered[key] = translated[key]
        for key, value in translated.items():
            if key not in ordered:
                ordered[key] = value
        return ordered

    @staticmethod
    def _is_config_not_found(exc: requests.HTTPError) -> bool:
        """
        Returns whether an ``HTTPError`` indicates a missing ficus config, not a genuine failure.

        Every ``/configs`` handler on ficus wraps *any* exception in a 404, so a blanket
        404-means-empty rule would turn a server outage into a silent "no records found" from
        :meth:`~clabe.stores._base.StoreBase.resolve`. This inspects the response body instead of
        trusting the status code alone.

        Args:
            exc: The HTTP error raised by the underlying ``requests`` call.

        Returns:
            bool: True if the status is 404 and its body indicates a missing config (a
                case-insensitive match on ``"confignotfound"`` with spaces stripped, or on
                ``"not found"``); False otherwise, in which case the caller should re-raise.
        """
        response = exc.response
        if response is None or response.status_code != 404:
            return False
        try:
            detail = response.json().get("detail", "")
        except ValueError:
            detail = response.text
        detail = str(detail).lower()
        return "confignotfound" in detail.replace(" ", "") or "not found" in detail

    def _candidates(self, kind: Kind[T], scope: Scope) -> Sequence[Candidate[T]]:
        """
        Returns the single ficus config for this namespace/mode/scope, if one exists.

        Args:
            kind: The kind of record to fetch. Must be one this store serves.
            scope: The merged scope for this call.

        Returns:
            Sequence[Candidate[T]]: Empty if ficus reports no config for this namespace/scope;
                otherwise exactly one candidate, since ficus merges layers server-side.

        Raises:
            LookupError: If this store does not serve ``kind``.
            requests.HTTPError: If the underlying request fails for any reason other than a
                missing config.
        """
        self._require_supported(kind)
        mode = scope.get(MODE_SCOPE_KEY) or self._settings.mode
        ficus_scopes = self._translate_scope(scope)
        try:
            data = self._client.get_config_safe(
                namespace=self.namespace,
                mode=mode,
                scopes=ficus_scopes or None,
                model=None,
            )
        except requests.HTTPError as exc:
            if self._is_config_not_found(exc):
                logger.debug(
                    "No ficus config found for namespace %r, mode %r, scopes %r.", self.namespace, mode, ficus_scopes
                )
                return []
            raise
        value = kind.adapter.validate_python(data)
        scopes_repr = " ".join(f"{k}={v}" for k, v in ficus_scopes.items())
        label = f"{self.namespace} [mode={mode or 'default'}]" + (f" {scopes_repr}" if scopes_repr else "")
        return [Candidate(label, value)]

    def write(self, kind: KindLike[T], value: T, *, scope: Scope | None = None) -> None:
        """
        Always raises: ``ConfiergeStore`` is read-only.

        Ficus' ``POST /v1/configs`` performs a "deep save" that scatters keys back into their
        origin layers and rejects genuinely-new fields (the ``confierge`` client never sets
        ``append_new_fields_to_last_scope``), and its semantics are still unsettled upstream (see
        ficus#43 and ficus#47). A future write should target the planned ``/v1/leaves`` endpoint
        instead.

        Args:
            kind: Unused; accepted to match :class:`~clabe.stores._base.Store`.
            value: Unused; accepted to match :class:`~clabe.stores._base.Store`.
            scope: Unused; accepted to match :class:`~clabe.stores._base.Store`.

        Raises:
            NotImplementedError: Always.
        """
        raise NotImplementedError(
            "ConfiergeStore is read-only. Ficus' POST /v1/configs performs a 'deep save' that scatters "
            "keys back into their origin layers and rejects genuinely-new fields, and its semantics are "
            "still unsettled upstream (see ficus#43 and ficus#47). A future write should target the "
            "planned /v1/leaves endpoint instead."
        )
