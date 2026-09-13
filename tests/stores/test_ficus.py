import dataclasses
import json
import logging
from typing import Any

import pydantic
import pytest
import requests

from clabe import ui
from clabe.stores import CompositeStore, Kind
from clabe.stores.ficus import (
    COMPUTERS,
    DEFAULT_BASE_URL,
    DEFAULT_FILENAME,
    DEFAULT_STEM,
    SUBJECTS,
    SUPPORTED_EXTENSIONS,
    FicusClient,
    FicusSettings,
    FicusStore,
    Layer,
    LayerKey,
    MergePolicy,
    MergeResult,
    ScopeRef,
    WritePolicy,
    _stamp_origins,
    deep_merge,
    diff_leaves,
    iter_leaves,
    nest_leaves,
)


class Record(pydantic.BaseModel):
    """A minimal record type standing in for a real ficus-served model."""

    value: int


#: The canonical "rig" kind ``FicusStore`` treats as layered config by default. Named manually
#: (not via ``Kind.from_rig``) so tests don't need a real ``aind_behavior_services.Rig`` model,
#: or its default ``validate_rig_computer_name`` validator, which would itself read the
#: environment and interfere with the scope assertions below.
RIG = Kind(Record, "rig")

#: A flat-record kind: not in ``config_kinds``, so it lives at ``manipulator_position.json``.
POSITION = Kind(Record, "manipulator_position")


class Configured(pydantic.BaseModel):
    """A config model with defaults, and tolerant of keys it does not declare.

    Both traits matter to a write-back: a stored document omits fields the model defaults, and may
    carry legacy keys the model drops. Dumping the model re-introduces the former and loses the
    latter, so a diff taken against the raw document would report edits nobody made.
    """

    model_config = pydantic.ConfigDict(extra="ignore")

    value: int
    port: int = 3
    note: str | None = None


#: The same ``"rig"`` name as :data:`RIG`, so it too is layered config -- but with a model whose
#: dumped shape differs from what is stored.
CONFIGURED = Kind(Configured, "rig")

NAMESPACE = "vr_frg"


@pytest.fixture(autouse=True)
def rig_identity(monkeypatch):
    """Pins the machine name the rig scope is keyed on, so it does not vary with the test machine.

    Ficus is keyed on the machine name, *not* on the AIND rig name in ``aibs_comp_id`` -- the fleet
    was migrated under names like ``DT201256``. ``aibs_comp_id`` is cleared here to keep that
    distinction honest: nothing in this module may quietly start depending on it."""
    monkeypatch.delenv("aibs_comp_id", raising=False)
    monkeypatch.setenv("COMPUTERNAME", "RIG-01")
    monkeypatch.setenv("HOSTNAME", "RIG-01")


# --------------------------------------------------------------------------------------
# HTTP test doubles
# --------------------------------------------------------------------------------------


def _response(
    status_code: int,
    payload: Any = None,
    *,
    text: str | None = None,
) -> requests.Response:
    """Builds a real (offline) ``requests.Response`` with the given status and body."""
    response = requests.Response()
    response.status_code = status_code
    response.url = "http://fake-ficus"
    if text is not None:
        response._content = text.encode()
    elif payload is not None:
        response._content = json.dumps(payload).encode()
    else:
        response._content = b""
    request = requests.PreparedRequest()
    request.method = "GET"
    response.request = request
    return response


@dataclasses.dataclass
class _Request:
    """One recorded HTTP call made by :class:`FicusClient`."""

    method: str
    url: str
    params: dict[str, str] | None = None
    json: Any = None


class FakeSession:
    """Stands in for ``requests.Session``: records every call, answers from a responder.

    The responder is any callable taking the recorded :class:`_Request` and returning a
    ``requests.Response``. Nothing here touches the network.
    """

    def __init__(self, responder) -> None:
        self.requests: list[_Request] = []
        self._responder = responder

    def get(self, url, *, params=None, timeout=None):
        return self._record(_Request("GET", url, params=params))

    def post(self, url, *, json=None, timeout=None):
        return self._record(_Request("POST", url, json=json))

    def patch(self, url, *, json=None, timeout=None):
        return self._record(_Request("PATCH", url, json=json))

    def delete(self, url, *, timeout=None):
        return self._record(_Request("DELETE", url))

    def _record(self, request: _Request) -> requests.Response:
        self.requests.append(request)
        response = self._responder(request)
        response.request.method = request.method
        return response

    @property
    def last(self) -> _Request:
        return self.requests[-1]


def layer_key(params: dict[str, str] | None) -> str:
    """Names the layer a read addresses, e.g. ``computers/RIG-01/default.json``."""
    params = params or {}
    filename = params.get("filename", DEFAULT_FILENAME)
    if "hostname" in params:
        return f"computers/{params['hostname']}/{filename}"
    if "subject_id" in params:
        return f"subjects/{params['subject_id']}/{filename}"
    return f"defaults/{filename}"


def layer_key_from_url(url: str) -> str:
    """Names the layer a write addresses. Writes put the scope in the path, not the query."""
    parts = url.split("/v1/", 1)[1].split("/")
    if parts[0] in ("computers", "subjects"):
        return f"{parts[0]}/{parts[1]}/{parts[-1]}"
    return f"defaults/{parts[-1]}"


class FakeFicus:
    """A responder backed by a dict of ``layer key -> document``.

    Anything not in the dict answers with ficus' own "not found" 404 shape, so a merge chain
    can be described simply by listing the layers that happen to exist.

    Writes are served too, with ficus' own semantics: ``POST`` creates and 409s on an existing
    document, ``PATCH`` deep-merges into one. That is what lets a write-back be tested by writing
    and then reading the result back, rather than by asserting on the request that was sent.
    """

    def __init__(self, documents: dict[str, dict] | None = None) -> None:
        self.documents = dict(documents or {})

    def put(self, filename: str, data: dict, *, scope: ScopeRef | None = None) -> None:
        """Seeds one layer directly, bypassing HTTP."""
        self.documents[str(LayerKey(scope, filename))] = data

    def get(self, filename: str, *, scope: ScopeRef | None = None) -> dict | None:
        """Reads one layer directly, bypassing HTTP."""
        return self.documents.get(str(LayerKey(scope, filename)))

    def __call__(self, request: _Request) -> requests.Response:
        if request.method == "GET":
            return self._read(layer_key(request.params))
        return self._write(request)

    def _read(self, key: str) -> requests.Response:
        if key not in self.documents:
            return _response(404, {"detail": f"Config file not found at path: /scratch/{key}"})
        return _response(200, {"data": self.documents[key], "details": {"files": [key]}})

    def _write(self, request: _Request) -> requests.Response:
        key = layer_key_from_url(request.url)
        existing = self.documents.get(key)
        if request.method == "POST":
            if existing is not None:
                return _response(409, {"detail": f"Config file already exists at path: /scratch/{key}"})
            self.documents[key] = request.json
        elif request.method == "PATCH":
            if existing is None:
                return _response(404, {"detail": f"Config file not found at path: /scratch/{key}"})
            self.documents[key] = deep_merge(existing, request.json)
        elif request.method == "DELETE":
            if existing is None:
                return _response(404, {"detail": f"Config file not found at path: /scratch/{key}"})
            del self.documents[key]
        return _response(200, {"data": self.documents.get(key, {})})


def by_method(**responses: requests.Response):
    """A responder answering by HTTP verb, e.g. ``by_method(POST=..., PATCH=...)``."""
    return lambda request: responses[request.method]


def make_client(responder, **kwargs) -> tuple[FicusClient, FakeSession]:
    session = FakeSession(responder)
    return FicusClient("http://ficus.test", session=session, **kwargs), session


def requested_chain(session: FakeSession) -> list[str]:
    """The layer keys a client asked for, in the order it asked."""
    return [layer_key(r.params) for r in session.requests if r.method == "GET"]


# --------------------------------------------------------------------------------------
# Store test doubles
# --------------------------------------------------------------------------------------


@dataclasses.dataclass
class _MergedCall:
    namespace: str
    filename: str | None
    scopes: list[ScopeRef]
    policy: MergePolicy | None


@dataclasses.dataclass
class _LayerCall:
    namespace: str
    filename: str
    scope: ScopeRef | None


@dataclasses.dataclass
class _WriteCall:
    namespace: str
    data: dict
    filename: str
    scope: ScopeRef | None
    create_only: bool


class FakeClient:
    """Test double for :class:`FicusClient`, so store tests never build a URL or a response."""

    def __init__(
        self,
        *,
        merged: MergeResult | None = None,
        layers: dict[str, Layer] | None = None,
        default_extension: str = ".json",
    ) -> None:
        self.base_url = "http://fake-ficus"
        self.default_extension = default_extension
        self.merged = merged
        self.layers = dict(layers or {})
        self.merged_calls: list[_MergedCall] = []
        self.layer_calls: list[_LayerCall] = []
        self.write_calls: list[_WriteCall] = []

    def get_merged(self, namespace, *, filename=None, scopes=(), policy=None) -> MergeResult:
        self.merged_calls.append(_MergedCall(namespace, filename, list(scopes), policy))
        return self.merged if self.merged is not None else MergeResult(data={}, sources=[])

    def get_layer(self, namespace, *, filename=DEFAULT_FILENAME, scope=None) -> Layer | None:
        self.layer_calls.append(_LayerCall(namespace, filename, scope))
        return self.layers.get(filename)

    def write(self, namespace, data, *, filename=DEFAULT_FILENAME, scope=None, create_only=False) -> None:
        self.write_calls.append(_WriteCall(namespace, data, filename, scope, create_only))

    # The extension-resolving and write-back planning are the interesting behaviours, so the real
    # implementations are reused rather than faked; only the transport underneath them is a double.
    locate = FicusClient.locate
    plan_write_back = staticmethod(FicusClient.plan_write_back)
    write_back = FicusClient.write_back

    @property
    def last_layer_call(self) -> _LayerCall:
        return self.layer_calls[-1]

    @property
    def last_merged_call(self) -> _MergedCall:
        return self.merged_calls[-1]


def make_store(client: FakeClient, *, serves=None, scope=None, **settings_kwargs) -> FicusStore:
    settings = FicusSettings(namespace=NAMESPACE, **settings_kwargs)
    return FicusStore(settings, client=client, serves=serves, scope=scope)


def _chained(data: dict, *scopes: ScopeRef, filename: str = DEFAULT_FILENAME) -> MergeResult:
    """Builds a ``MergeResult`` shaped like a real one: every leaf owned by the ``defaults`` layer,
    with ``scopes`` stacked above it in the chain but holding nothing yet. That is the ordinary
    state of a rig that has no override -- and the one a write-back has to get right."""
    defaults = LayerKey(None, filename)
    origins: dict[tuple[str, ...], LayerKey] = {}
    _stamp_origins(data, defaults, origins)
    return MergeResult(
        data=data,
        sources=[str(defaults)],
        origins=origins,
        chain=[defaults, *(LayerKey(scope, filename) for scope in scopes)],
    )


# --------------------------------------------------------------------------------------
# deep_merge / MergePolicy
# --------------------------------------------------------------------------------------


class TestDeepMerge:
    def test_nested_dicts_recurse(self):
        base = {"a": {"x": 1, "y": 2}, "b": 1}
        override = {"a": {"y": 3, "z": 4}}
        assert deep_merge(base, override) == {"a": {"x": 1, "y": 3, "z": 4}, "b": 1}

    def test_non_dicts_are_replaced(self):
        assert deep_merge({"a": 1}, {"a": 2}) == {"a": 2}
        assert deep_merge({"a": "x"}, {"a": "y"}) == {"a": "y"}

    def test_keys_only_in_the_override_are_added(self):
        assert deep_merge({"a": 1}, {"b": 2}) == {"a": 1, "b": 2}

    def test_lists_are_replaced_wholesale_by_default(self):
        assert deep_merge({"a": [1, 2, 3]}, {"a": [4]}) == {"a": [4]}

    def test_lists_concat_when_asked(self):
        policy = MergePolicy(lists="concat")
        assert deep_merge({"a": [1, 2]}, {"a": [3]}, policy=policy) == {"a": [1, 2, 3]}

    def test_concat_still_replaces_when_the_base_is_not_a_list(self):
        policy = MergePolicy(lists="concat", on_type_conflict="override")
        assert deep_merge({"a": 1}, {"a": [3]}, policy=policy) == {"a": [3]}

    def test_null_on_the_override_side_is_a_value_not_a_deletion(self):
        """The load-bearing default: a layer must be able to blank a field inherited from below."""
        merged = deep_merge({"a": 1}, {"a": None})
        assert "a" in merged
        assert merged["a"] is None

    def test_null_blanks_a_nested_field_rather_than_dropping_it(self):
        merged = deep_merge({"a": {"x": 1, "y": 2}}, {"a": {"y": None}})
        assert merged == {"a": {"x": 1, "y": None}}

    def test_null_means_delete_removes_the_key(self):
        policy = MergePolicy(null_means="delete")
        assert deep_merge({"a": 1, "b": 2}, {"a": None}, policy=policy) == {"b": 2}

    def test_null_means_delete_on_an_absent_key_is_a_no_op(self):
        policy = MergePolicy(null_means="delete")
        assert deep_merge({"b": 2}, {"a": None}, policy=policy) == {"b": 2}

    def test_null_means_delete_reaches_nested_keys(self):
        policy = MergePolicy(null_means="delete")
        assert deep_merge({"a": {"x": 1, "y": 2}}, {"a": {"y": None}}, policy=policy) == {"a": {"x": 1}}

    def test_a_dict_replacing_a_scalar_raises_when_asked(self):
        policy = MergePolicy(on_type_conflict="raise")
        with pytest.raises(TypeError, match="Layer disagreement"):
            deep_merge({"a": 1}, {"a": {"x": 1}}, policy=policy)

    def test_a_scalar_replacing_a_dict_raises_when_asked(self):
        policy = MergePolicy(on_type_conflict="raise")
        with pytest.raises(TypeError, match="Layer disagreement"):
            deep_merge({"a": {"x": 1}}, {"a": 1}, policy=policy)

    def test_the_raised_conflict_names_the_dotted_path(self):
        policy = MergePolicy(on_type_conflict="raise")
        with pytest.raises(TypeError, match="'a.b'"):
            deep_merge({"a": {"b": {"c": 1}}}, {"a": {"b": 2}}, policy=policy)

    def test_a_type_conflict_warns_and_proceeds_by_default(self, caplog):
        with caplog.at_level(logging.WARNING, logger="clabe.stores.ficus"):
            merged = deep_merge({"a": {"x": 1}}, {"a": 2})
        assert merged == {"a": 2}
        assert "Layer disagreement" in caplog.text

    def test_on_type_conflict_override_is_silent(self, caplog):
        policy = MergePolicy(on_type_conflict="override")
        with caplog.at_level(logging.WARNING, logger="clabe.stores.ficus"):
            merged = deep_merge({"a": {"x": 1}}, {"a": 2}, policy=policy)
        assert merged == {"a": 2}
        assert caplog.text == ""

    def test_filling_in_a_null_with_a_dict_is_not_a_type_conflict(self, caplog):
        """An optional field populated by a more specific layer is the normal case, not a clash."""
        policy = MergePolicy(on_type_conflict="raise")
        with caplog.at_level(logging.WARNING, logger="clabe.stores.ficus"):
            merged = deep_merge({"a": None}, {"a": {"x": 1}}, policy=policy)
        assert merged == {"a": {"x": 1}}
        assert caplog.text == ""

    def test_blanking_a_dict_with_null_is_not_a_type_conflict(self):
        policy = MergePolicy(on_type_conflict="raise")
        assert deep_merge({"a": {"x": 1}}, {"a": None}, policy=policy) == {"a": None}

    def test_a_brand_new_dict_key_is_not_a_type_conflict(self):
        policy = MergePolicy(on_type_conflict="raise")
        assert deep_merge({}, {"a": {"x": 1}}, policy=policy) == {"a": {"x": 1}}

    def test_neither_input_is_mutated(self):
        base = {"a": {"x": 1}, "b": [1, 2]}
        override = {"a": {"y": 2}, "b": [3]}
        deep_merge(base, override)
        assert base == {"a": {"x": 1}, "b": [1, 2]}
        assert override == {"a": {"y": 2}, "b": [3]}

    def test_the_result_does_not_alias_the_base_at_any_depth(self):
        base = {"a": {"x": 1}}
        merged = deep_merge(base, {"a": {"y": 2}})
        merged["a"]["x"] = 99
        assert base["a"]["x"] == 1


# --------------------------------------------------------------------------------------
# FicusClient
# --------------------------------------------------------------------------------------


class TestGetLayer:
    def test_the_defaults_layer_is_read_with_merge_false_and_no_scope_key(self):
        client, session = make_client(FakeFicus({"defaults/default.json": {"value": 1}}))
        layer = client.get_layer(NAMESPACE)
        assert layer is not None
        assert layer.data == {"value": 1}
        assert session.last.params == {"filename": "default.json", "merge": "false"}
        assert session.last.url == "http://ficus.test/v1/namespaces/vr_frg/config"

    def test_a_computer_scope_reads_with_the_hostname_query_key(self):
        client, session = make_client(FakeFicus({"computers/RIG-01/default.json": {"value": 2}}))
        layer = client.get_layer(NAMESPACE, scope=COMPUTERS("RIG-01"))
        assert layer is not None
        assert layer.data == {"value": 2}
        assert session.last.params["hostname"] == "RIG-01"
        assert session.last.params["merge"] == "false"

    def test_a_subject_scope_reads_with_the_subject_id_query_key(self):
        client, session = make_client(FakeFicus({"subjects/789012/default.json": {"value": 3}}))
        assert client.get_layer(NAMESPACE, scope=SUBJECTS("789012")) is not None
        assert session.last.params["subject_id"] == "789012"

    def test_a_named_filename_is_forwarded(self):
        client, session = make_client(FakeFicus({"defaults/extra.json": {"value": 4}}))
        assert client.get_layer(NAMESPACE, filename="extra.json") is not None
        assert session.last.params["filename"] == "extra.json"

    def test_the_source_comes_from_the_first_reported_file(self):
        path = "/scratch/computers/DT201256/vr_frg/default.json"
        client, _ = make_client(lambda _: _response(200, {"data": {}, "details": {"files": [path, "/other.json"]}}))
        layer = client.get_layer(NAMESPACE)
        assert layer is not None
        assert layer.source == path

    def test_the_source_falls_back_to_the_filename_when_no_files_are_reported(self):
        client, _ = make_client(lambda _: _response(200, {"data": {}, "details": {}}))
        layer = client.get_layer(NAMESPACE, filename="extra.json")
        assert layer is not None
        assert layer.source == "extra.json"

    @pytest.mark.parametrize(
        "detail",
        [
            "Config file not found at path: /scratch/computers/RIG-01/vr_frg",
            "Subpath 'vr_frg' not found in path: /scratch/computers/RIG-01",
        ],
    )
    def test_a_404_that_reads_as_absence_yields_none(self, detail):
        client, _ = make_client(lambda _: _response(404, {"detail": detail}))
        assert client.get_layer(NAMESPACE) is None

    def test_a_404_with_a_non_json_body_mentioning_not_found_yields_none(self):
        client, _ = make_client(lambda _: _response(404, text="Not Found"))
        assert client.get_layer(NAMESPACE) is None

    def test_a_404_that_does_not_read_as_absence_raises(self):
        client, _ = make_client(lambda _: _response(404, {"detail": "Internal server error"}))
        with pytest.raises(requests.HTTPError):
            client.get_layer(NAMESPACE)

    def test_a_non_404_error_raises_even_when_it_mentions_not_found(self):
        client, _ = make_client(lambda _: _response(500, {"detail": "Config file not found"}))
        with pytest.raises(requests.HTTPError):
            client.get_layer(NAMESPACE)


def _all_layers_present(*keys: str) -> FakeFicus:
    """A ficus holding a document at every named layer, so each resolves on the first try.

    Chain-order assertions seed this rather than starting empty: an absent document is now probed
    across every supported extension, which is real behaviour but drowns the ordering being tested.
    """
    return FakeFicus({key: {"seeded": True} for key in keys})


class TestGetMerged:
    def test_the_chain_is_defaults_then_each_scope_in_order(self):
        client, session = make_client(
            _all_layers_present(
                "defaults/default.json",
                "computers/RIG-01/default.json",
                "subjects/789012/default.json",
            )
        )
        client.get_merged(NAMESPACE, scopes=[COMPUTERS("RIG-01"), SUBJECTS("789012")])
        assert requested_chain(session) == [
            "defaults/default.json",
            "computers/RIG-01/default.json",
            "subjects/789012/default.json",
        ]

    def test_an_extra_filename_is_layered_above_default_json_at_every_level(self):
        client, session = make_client(
            _all_layers_present(
                "defaults/default.json",
                "defaults/extra.json",
                "computers/RIG-01/default.json",
                "computers/RIG-01/extra.json",
            )
        )
        client.get_merged(NAMESPACE, filename="extra.json", scopes=[COMPUTERS("RIG-01")])
        assert requested_chain(session) == [
            "defaults/default.json",
            "defaults/extra.json",
            "computers/RIG-01/default.json",
            "computers/RIG-01/extra.json",
        ]

    def test_asking_for_default_json_explicitly_does_not_fetch_it_twice(self):
        client, session = make_client(_all_layers_present("defaults/default.json", "computers/RIG-01/default.json"))
        client.get_merged(NAMESPACE, filename=DEFAULT_FILENAME, scopes=[COMPUTERS("RIG-01")])
        assert requested_chain(session) == ["defaults/default.json", "computers/RIG-01/default.json"]

    def test_later_layers_win_and_deep_merge(self):
        client, _ = make_client(
            FakeFicus(
                {
                    "defaults/default.json": {"a": {"x": 1, "y": 2}, "shared": "base"},
                    "computers/RIG-01/default.json": {"a": {"y": 3}, "rig_only": True},
                }
            )
        )
        result = client.get_merged(NAMESPACE, scopes=[COMPUTERS("RIG-01")])
        assert result.data == {"a": {"x": 1, "y": 3}, "shared": "base", "rig_only": True}

    def test_missing_layers_are_skipped_and_only_present_ones_are_recorded_as_sources(self):
        client, _ = make_client(
            FakeFicus(
                {
                    "defaults/default.json": {"value": 1},
                    "subjects/789012/default.json": {"value": 3},
                }
            )
        )
        result = client.get_merged(NAMESPACE, scopes=[COMPUTERS("RIG-01"), SUBJECTS("789012")])
        assert result.data == {"value": 3}
        assert result.sources == ["defaults/default.json", "subjects/789012/default.json"]

    def test_a_missing_layer_raises_when_the_policy_demands_every_layer(self):
        client, _ = make_client(FakeFicus({"defaults/default.json": {"value": 1}}))
        with pytest.raises(LookupError):
            client.get_merged(
                NAMESPACE,
                scopes=[COMPUTERS("RIG-01")],
                policy=MergePolicy(on_missing_layer="raise"),
            )

    def test_nothing_stored_at_all_yields_empty_data_and_empty_sources(self):
        client, _ = make_client(FakeFicus())
        result = client.get_merged(NAMESPACE, scopes=[COMPUTERS("RIG-01")])
        assert result.data == {}
        assert result.sources == []

    def test_the_policy_reaches_the_merge_itself(self):
        client, _ = make_client(
            FakeFicus(
                {
                    "defaults/default.json": {"value": 1},
                    "computers/RIG-01/default.json": {"value": None},
                }
            )
        )
        assert client.get_merged(NAMESPACE, scopes=[COMPUTERS("RIG-01")]).data == {"value": None}
        deleted = client.get_merged(
            NAMESPACE,
            scopes=[COMPUTERS("RIG-01")],
            policy=MergePolicy(null_means="delete"),
        )
        assert deleted.data == {}


class TestLocate:
    """Ficus lets whatever created a document pick ``.json``, ``.yml`` or ``.yaml``, but serves
    reads by exact name -- so asking for the wrong extension is indistinguishable from the document
    not existing. These cover finding it anyway."""

    def test_the_preferred_extension_is_tried_first_and_costs_one_request(self):
        client, session = make_client(FakeFicus({"defaults/default.json": {"value": 1}}))
        filename, layer = client.locate(NAMESPACE, DEFAULT_STEM)
        assert (filename, layer.data) == ("default.json", {"value": 1})
        assert len(session.requests) == 1, "the ordinary case must not pay for the fallback"

    def test_a_document_stored_as_yml_is_still_found(self):
        client, _ = make_client(FakeFicus({"defaults/default.yml": {"value": 1}}))
        filename, layer = client.locate(NAMESPACE, DEFAULT_STEM)
        assert (filename, layer.data) == ("default.yml", {"value": 1})

    def test_a_document_stored_as_yaml_is_still_found(self):
        client, _ = make_client(FakeFicus({"defaults/default.yaml": {"value": 1}}))
        filename, _layer = client.locate(NAMESPACE, DEFAULT_STEM)
        assert filename == "default.yaml"

    def test_every_supported_extension_is_probed_before_giving_up(self):
        client, session = make_client(FakeFicus())
        filename, layer = client.locate(NAMESPACE, DEFAULT_STEM)
        assert layer is None
        assert filename == "default.json", "nothing exists, so the preferred extension names it"
        assert requested_chain(session) == [f"defaults/{DEFAULT_STEM}{ext}" for ext in SUPPORTED_EXTENSIONS]

    def test_the_clients_default_extension_reorders_the_probes_without_dropping_any(self):
        client, session = make_client(FakeFicus(), default_extension=".yml")
        client.locate(NAMESPACE, DEFAULT_STEM)
        assert requested_chain(session) == ["defaults/default.yml", "defaults/default.json", "defaults/default.yaml"]

    def test_the_clients_default_extension_names_the_document_when_nothing_exists(self):
        client, _ = make_client(FakeFicus(), default_extension=".yml")
        filename, layer = client.locate(NAMESPACE, DEFAULT_STEM)
        assert (filename, layer) == ("default.yml", None)

    def test_it_looks_inside_the_scope_it_is_given(self):
        client, _ = make_client(FakeFicus({"computers/RIG-01/manipulator_position.yml": {"x": 1}}))
        filename, layer = client.locate(NAMESPACE, "manipulator_position", scope=COMPUTERS("RIG-01"))
        assert (filename, layer.data) == ("manipulator_position.yml", {"x": 1})


class TestGetMergedAcrossExtensions:
    def test_a_layer_stored_under_another_extension_still_merges(self):
        """The failure this prevents: a rig whose config someone created as YAML reading as though
        it had no config at all, silently falling back to ``defaults``."""
        client, _ = make_client(
            FakeFicus(
                {
                    "defaults/default.json": {"value": 1, "shared": "base"},
                    "computers/RIG-01/default.yml": {"value": 2},
                }
            )
        )
        result = client.get_merged(NAMESPACE, scopes=[COMPUTERS("RIG-01")])
        assert result.data == {"value": 2, "shared": "base"}
        assert result.chain[-1] == LayerKey(COMPUTERS("RIG-01"), "default.yml")

    def test_layers_may_disagree_about_extension(self):
        client, _ = make_client(
            FakeFicus({"defaults/default.yaml": {"value": 1}, "computers/RIG-01/default.json": {"value": 2}})
        )
        result = client.get_merged(NAMESPACE, scopes=[COMPUTERS("RIG-01")])
        assert result.data == {"value": 2}
        assert [key.filename for key in result.chain] == ["default.yaml", "default.json"]

    def test_an_empty_layer_is_named_by_the_clients_default_extension(self):
        """Nothing is there to dictate an extension, so the write that eventually fills it decides."""
        client, _ = make_client(FakeFicus({"defaults/default.json": {"value": 1}}), default_extension=".yml")
        result = client.get_merged(NAMESPACE, scopes=[COMPUTERS("RIG-01")])
        assert result.chain[-1] == LayerKey(COMPUTERS("RIG-01"), "default.yml")

    def test_an_extra_filename_is_taken_literally(self):
        """A caller-chosen name has no guarantee that a same-stem sibling means the same thing, so
        only ficus' own ``default`` document gets its extension resolved."""
        client, session = make_client(FakeFicus({"defaults/extra.yml": {"value": 1}}))
        result = client.get_merged(NAMESPACE, filename="extra.json")
        assert result.sources == []
        assert "defaults/extra.json" in requested_chain(session)
        assert "defaults/extra.yaml" not in requested_chain(session)


class TestWrite:
    def test_a_write_posts_first(self):
        client, session = make_client(by_method(POST=_response(201, {"ok": True})))
        client.write(NAMESPACE, {"value": 1})
        assert [r.method for r in session.requests] == ["POST"]
        assert session.last.json == {"value": 1}

    def test_a_conflict_falls_back_to_patch(self):
        client, session = make_client(by_method(POST=_response(409, {"detail": "exists"}), PATCH=_response(200, {})))
        client.write(NAMESPACE, {"value": 1})
        assert [r.method for r in session.requests] == ["POST", "PATCH"]
        assert session.last.json == {"value": 1}

    def test_create_only_does_not_fall_back(self):
        client, session = make_client(by_method(POST=_response(409, {"detail": "exists"})))
        with pytest.raises(requests.HTTPError):
            client.write(NAMESPACE, {"value": 1}, create_only=True)
        assert [r.method for r in session.requests] == ["POST"]

    def test_a_scoped_write_puts_the_route_segment_in_the_path(self):
        client, session = make_client(by_method(POST=_response(201, {})))
        client.write(NAMESPACE, {"value": 1}, filename="manipulator_position.json", scope=COMPUTERS("RIG-01"))
        assert session.last.url == (
            "http://ficus.test/v1/computers/RIG-01/namespaces/vr_frg/config/manipulator_position.json"
        )

    def test_a_subject_scoped_write_uses_the_subjects_segment(self):
        client, session = make_client(by_method(POST=_response(201, {})))
        client.write(NAMESPACE, {"value": 1}, scope=SUBJECTS("789012"))
        assert session.last.url == "http://ficus.test/v1/subjects/789012/namespaces/vr_frg/config/default.json"

    def test_an_unscoped_write_targets_the_defaults_layer(self):
        client, session = make_client(by_method(POST=_response(201, {})))
        client.write(NAMESPACE, {"value": 1})
        assert session.last.url == "http://ficus.test/v1/namespaces/vr_frg/config/default.json"

    def test_a_failed_write_raises(self):
        client, _ = make_client(by_method(POST=_response(500, {"detail": "boom"})))
        with pytest.raises(requests.HTTPError):
            client.write(NAMESPACE, {"value": 1})


class TestDelete:
    def test_deleting_something_absent_returns_false(self):
        client, _ = make_client(lambda _: _response(404, {"detail": "Config file not found at path: /x"}))
        assert client.delete(NAMESPACE, scope=COMPUTERS("RIG-01")) is False

    def test_a_successful_delete_returns_true(self):
        client, session = make_client(lambda _: _response(200, {"ok": True}))
        assert client.delete(NAMESPACE, filename="manipulator_position.json", scope=SUBJECTS("789012")) is True
        assert session.last.url == (
            "http://ficus.test/v1/subjects/789012/namespaces/vr_frg/config/manipulator_position.json"
        )

    def test_a_404_that_does_not_read_as_absence_raises(self):
        client, _ = make_client(lambda _: _response(404, {"detail": "Internal server error"}))
        with pytest.raises(requests.HTTPError):
            client.delete(NAMESPACE)


class TestListFiles:
    def test_an_absent_scope_lists_nothing(self):
        client, _ = make_client(lambda _: _response(404, {"detail": "Subpath 'vr_frg' not found in path: /scratch"}))
        assert client.list_files(NAMESPACE, scope=COMPUTERS("RIG-01")) == []

    def test_files_are_returned_from_the_data_key(self):
        client, session = make_client(lambda _: _response(200, {"data": ["default.json", "extra.json"]}))
        assert client.list_files(NAMESPACE, scope=COMPUTERS("RIG-01")) == ["default.json", "extra.json"]
        assert session.last.url == "http://ficus.test/v1/list_files/vr_frg/configs"
        assert session.last.params == {"hostname": "RIG-01"}

    def test_an_unscoped_listing_sends_no_scope_params(self):
        client, session = make_client(lambda _: _response(200, {"data": []}))
        client.list_files(NAMESPACE)
        assert session.last.params == {}


class TestClientConstruction:
    def test_the_base_url_defaults_to_the_deployment(self):
        assert FicusClient(session=FakeSession(lambda _: _response(200, {}))).base_url == DEFAULT_BASE_URL

    def test_a_trailing_slash_is_stripped(self):
        assert FicusClient("http://ficus.test/", session=FakeSession(lambda _: _response(200, {}))).base_url == (
            "http://ficus.test"
        )

    def test_str_names_the_backend(self):
        client, _ = make_client(FakeFicus())
        assert str(client) == "FicusClient(http://ficus.test)"


# --------------------------------------------------------------------------------------
# Leaves, provenance and write-back
# --------------------------------------------------------------------------------------

DEFAULTS = LayerKey(None, DEFAULT_FILENAME)
RIG_LAYER = LayerKey(COMPUTERS("RIG-01"), DEFAULT_FILENAME)


class TestIterLeaves:
    def test_nested_keys_become_paths(self):
        assert dict(iter_leaves({"a": 1, "m": {"x": 2}})) == {("a",): 1, ("m", "x"): 2}

    def test_a_key_containing_a_dot_stays_one_path_segment(self):
        """The reason paths are tuples rather than dotted strings."""
        assert dict(iter_leaves({"a.b": 1})) == {("a.b",): 1}

    def test_lists_and_none_are_leaves_because_a_merge_replaces_them_whole(self):
        assert dict(iter_leaves({"xs": [1, 2], "n": None})) == {("xs",): [1, 2], ("n",): None}

    def test_an_empty_dict_is_a_leaf(self):
        assert dict(iter_leaves({"a": {}})) == {("a",): {}}


class TestDiffLeaves:
    def test_only_changed_leaves_are_reported(self):
        changed, removed = diff_leaves({"a": 1, "b": 2}, {"a": 1, "b": 3})
        assert changed == {("b",): 3}
        assert removed == []

    def test_a_new_leaf_counts_as_changed(self):
        changed, _ = diff_leaves({"a": 1}, {"a": 1, "b": 2})
        assert changed == {("b",): 2}

    def test_setting_a_leaf_to_null_is_a_change_not_a_removal(self):
        """Null is a value here, so blanking a field is expressible -- which is the whole reason
        the merge does not follow RFC 7386."""
        changed, removed = diff_leaves({"a": 1}, {"a": None})
        assert changed == {("a",): None}
        assert removed == []

    def test_a_dropped_leaf_is_reported_as_removed(self):
        changed, removed = diff_leaves({"a": 1, "b": 2}, {"a": 1})
        assert changed == {}
        assert removed == [("b",)]

    def test_a_leaf_replaced_by_a_subtree_is_a_change_only(self):
        changed, removed = diff_leaves({"a": 1}, {"a": {"x": 2}})
        assert changed == {("a", "x"): 2}
        assert removed == []

    def test_a_subtree_replaced_by_a_leaf_is_a_change_only(self):
        changed, removed = diff_leaves({"a": {"x": 2}}, {"a": 1})
        assert changed == {("a",): 1}
        assert removed == []


class TestNestLeaves:
    def test_paths_are_rebuilt_into_nested_dicts(self):
        assert nest_leaves({("m", "c", "x"): 1, ("a",): 2}) == {"m": {"c": {"x": 1}}, "a": 2}

    def test_it_round_trips_with_iter_leaves(self):
        data = {"a": 1, "m": {"x": None, "y": [1, 2]}}
        assert nest_leaves(dict(iter_leaves(data))) == data


class TestStampOrigins:
    def test_a_later_layer_takes_ownership(self):
        origins: dict = {}
        _stamp_origins({"a": 1, "b": 2}, DEFAULTS, origins)
        _stamp_origins({"b": 3}, RIG_LAYER, origins)
        assert origins == {("a",): DEFAULTS, ("b",): RIG_LAYER}

    def test_replacing_a_subtree_with_a_leaf_drops_the_subtrees_owners(self):
        origins: dict = {}
        _stamp_origins({"m": {"x": 1, "y": 2}}, DEFAULTS, origins)
        _stamp_origins({"m": 5}, RIG_LAYER, origins)
        assert origins == {("m",): RIG_LAYER}

    def test_replacing_a_leaf_with_a_subtree_drops_the_leafs_owner(self):
        origins: dict = {}
        _stamp_origins({"m": 5}, DEFAULTS, origins)
        _stamp_origins({"m": {"x": 1}}, RIG_LAYER, origins)
        assert origins == {("m", "x"): RIG_LAYER}


class TestOriginsFromARealMerge:
    def test_each_leaf_is_attributed_to_the_layer_that_won_it(self):
        ficus = FakeFicus()
        ficus.put(DEFAULT_FILENAME, {"a": 1, "m": {"x": 1, "y": 2}})
        ficus.put(DEFAULT_FILENAME, {"m": {"y": 9}}, scope=COMPUTERS("RIG-01"))
        client, _ = make_client(ficus)

        result = client.get_merged(NAMESPACE, scopes=[COMPUTERS("RIG-01")])
        assert result.data == {"a": 1, "m": {"x": 1, "y": 9}}
        assert result.origin(("a",)) == DEFAULTS
        assert result.origin(("m", "x")) == DEFAULTS
        assert result.origin(("m", "y")) == RIG_LAYER

    def test_the_chain_lists_layers_that_hold_nothing_yet(self):
        """A rig with no override still has a layer to write to, so it must stay in the chain."""
        ficus = FakeFicus()
        ficus.put(DEFAULT_FILENAME, {"a": 1})
        client, _ = make_client(ficus)

        result = client.get_merged(NAMESPACE, scopes=[COMPUTERS("RIG-01")])
        assert result.sources == ["defaults/default.json"]
        assert result.chain == [DEFAULTS, RIG_LAYER]


class TestPlanWriteBack:
    @staticmethod
    def _current(data: dict) -> MergeResult:
        origins: dict = {}
        _stamp_origins(data, DEFAULTS, origins)
        return MergeResult(data=data, sources=[str(DEFAULTS)], origins=origins, chain=[DEFAULTS, RIG_LAYER])

    def test_only_the_delta_is_written_and_it_goes_to_the_top_of_the_chain(self):
        """The point of the whole exercise: the rig override gains one key, not a copy of
        ``defaults`` that then stops tracking it."""
        plan = FicusClient.plan_write_back(self._current({"a": 1, "b": 2}), {"a": 1, "b": 3})
        assert plan is not None
        assert plan.key == RIG_LAYER
        assert plan.data == {"b": 3}
        assert plan.paths == [("b",)]

    def test_nesting_is_preserved_in_the_payload(self):
        current = self._current({"m": {"c": {"x": 1, "y": 2}}})
        plan = FicusClient.plan_write_back(current, {"m": {"c": {"x": 1, "y": 9}}})
        assert plan is not None
        assert plan.data == {"m": {"c": {"y": 9}}}

    def test_no_change_plans_nothing(self):
        assert FicusClient.plan_write_back(self._current({"a": 1}), {"a": 1}) is None

    def test_a_removal_is_refused_because_patch_cannot_express_it(self):
        with pytest.raises(LookupError, match="cannot remove a key"):
            FicusClient.plan_write_back(self._current({"a": 1, "b": 2}), {"a": 1})

    def test_a_removal_can_be_skipped_instead(self, caplog):
        policy = WritePolicy(on_key_removal="skip")
        with caplog.at_level(logging.WARNING):
            plan = FicusClient.plan_write_back(self._current({"a": 1, "b": 2}), {"a": 9}, policy=policy)
        assert plan is not None
        assert plan.data == {"a": 9}
        assert "cannot remove keys" in caplog.text

    def test_skipping_a_removal_that_is_the_only_change_plans_nothing(self):
        policy = WritePolicy(on_key_removal="skip")
        assert FicusClient.plan_write_back(self._current({"a": 1, "b": 2}), {"a": 1}, policy=policy) is None

    def test_a_shadowed_write_is_refused(self):
        """Writing to ``defaults`` a value the rig layer already overrides would return 200 and
        change nothing on the next read."""
        current = MergeResult(
            data={"a": 5},
            sources=[str(DEFAULTS), str(RIG_LAYER)],
            origins={("a",): RIG_LAYER},
            chain=[DEFAULTS, RIG_LAYER],
        )
        with pytest.raises(PermissionError, match="would keep winning"):
            FicusClient.plan_write_back(current, {"a": 6}, target=DEFAULTS)

    def test_a_shadowed_write_can_be_downgraded_to_a_warning(self, caplog):
        current = MergeResult(
            data={"a": 5},
            sources=[str(RIG_LAYER)],
            origins={("a",): RIG_LAYER},
            chain=[DEFAULTS, RIG_LAYER],
        )
        policy = WritePolicy(on_shadowed_write="warn")
        with caplog.at_level(logging.WARNING):
            plan = FicusClient.plan_write_back(current, {"a": 6}, target=DEFAULTS, policy=policy)
        assert plan is not None and plan.key == DEFAULTS
        assert "would keep winning" in caplog.text

    def test_a_shadowed_write_can_be_allowed_silently(self, caplog):
        current = MergeResult(
            data={"a": 5},
            sources=[str(RIG_LAYER)],
            origins={("a",): RIG_LAYER},
            chain=[DEFAULTS, RIG_LAYER],
        )
        policy = WritePolicy(on_shadowed_write="allow")
        with caplog.at_level(logging.WARNING):
            assert FicusClient.plan_write_back(current, {"a": 6}, target=DEFAULTS, policy=policy) is not None
        assert caplog.text == ""

    def test_writing_down_to_a_layer_nothing_overrides_is_fine(self):
        """``defaults`` owns the value, so writing it there is effective, not shadowed."""
        plan = FicusClient.plan_write_back(self._current({"a": 1}), {"a": 2}, target=DEFAULTS)
        assert plan is not None and plan.key == DEFAULTS

    def test_a_target_outside_the_chain_cannot_be_ranked_and_is_allowed(self):
        other = LayerKey(COMPUTERS("SOME-OTHER-RIG"), DEFAULT_FILENAME)
        plan = FicusClient.plan_write_back(self._current({"a": 1}), {"a": 2}, target=other)
        assert plan is not None and plan.key == other

    def test_an_empty_chain_has_nowhere_to_write(self):
        current = MergeResult(data={}, sources=[])
        with pytest.raises(ValueError, match="no layer to write back to"):
            FicusClient.plan_write_back(current, {"a": 1})

    def test_an_owner_outside_the_chain_is_not_treated_as_shadowing(self):
        stray = LayerKey(SUBJECTS("000000"), DEFAULT_FILENAME)
        current = MergeResult(data={"a": 5}, sources=[], origins={("a",): stray}, chain=[DEFAULTS, RIG_LAYER])
        plan = FicusClient.plan_write_back(current, {"a": 6}, target=DEFAULTS)
        assert plan is not None and plan.key == DEFAULTS


class TestWriteBack:
    def test_the_plan_is_sent_to_the_layer_it_names(self):
        ficus = FakeFicus()
        ficus.put(DEFAULT_FILENAME, {"a": 1, "b": 2})
        client, _ = make_client(ficus)

        current = client.get_merged(NAMESPACE, scopes=[COMPUTERS("RIG-01")])
        plan = client.write_back(NAMESPACE, current, {"a": 1, "b": 3})

        assert plan is not None and plan.key == RIG_LAYER
        assert ficus.get(DEFAULT_FILENAME, scope=COMPUTERS("RIG-01")) == {"b": 3}
        assert ficus.get(DEFAULT_FILENAME) == {"a": 1, "b": 2}, "defaults must be left alone"

    def test_the_result_resolves_to_what_was_asked_for(self):
        """The round trip that matters: write back, read again, get the value you wrote."""
        ficus = FakeFicus()
        ficus.put(DEFAULT_FILENAME, {"a": 1, "m": {"x": 1, "y": 2}})
        client, _ = make_client(ficus)

        current = client.get_merged(NAMESPACE, scopes=[COMPUTERS("RIG-01")])
        client.write_back(NAMESPACE, current, {"a": 1, "m": {"x": 1, "y": 9}})

        assert client.get_merged(NAMESPACE, scopes=[COMPUTERS("RIG-01")]).data == {"a": 1, "m": {"x": 1, "y": 9}}

    def test_nothing_is_sent_when_nothing_changed(self):
        ficus = FakeFicus()
        ficus.put(DEFAULT_FILENAME, {"a": 1})
        client, session = make_client(ficus)

        current = client.get_merged(NAMESPACE, scopes=[COMPUTERS("RIG-01")])
        before = len(session.requests)
        assert client.write_back(NAMESPACE, current, {"a": 1}) is None
        assert len(session.requests) == before


# --------------------------------------------------------------------------------------
# FicusStore
# --------------------------------------------------------------------------------------


class TestConfigKinds:
    def test_a_config_kind_is_read_from_the_merge_chain(self):
        client = FakeClient(merged=MergeResult({"value": 7}, ["defaults/default.json"]))
        store = make_store(client)
        assert store.list(RIG) == [Record(value=7)]
        assert client.merged_calls[-1].namespace == NAMESPACE

    def test_resolving_a_config_kind_does_not_prompt(self, mock_frontend):
        ui.set_current_frontend(mock_frontend)
        client = FakeClient(merged=MergeResult({"value": 7}, ["defaults/default.json"]))
        assert make_store(client).resolve(RIG) == Record(value=7)
        mock_frontend._ask_pick_mock.assert_not_called()

    def test_the_merge_chain_uses_the_machine_name(self, monkeypatch):
        monkeypatch.setenv("COMPUTERNAME", "RIG-77")
        client = FakeClient(merged=MergeResult({"value": 1}, ["defaults/default.json"]))
        make_store(client).list(RIG)
        assert client.last_merged_call.scopes == [COMPUTERS("RIG-77")]

    def test_the_merge_chain_ignores_the_aind_rig_name(self, monkeypatch):
        """Ficus is keyed on the machine name. ``aibs_comp_id`` holds the AIND rig name
        (``FRG.4A``), which is a different identifier and would address a layer that does not
        exist."""
        monkeypatch.setenv("aibs_comp_id", "FRG.4A")
        client = FakeClient(merged=MergeResult({"value": 1}, ["defaults/default.json"]))
        make_store(client).list(RIG)
        assert client.last_merged_call.scopes == [COMPUTERS("RIG-01")]

    def test_another_machine_is_reached_by_narrowing_computer_name(self):
        """How you read a rig's config from somewhere that is not that rig -- the same narrowing
        LocalFileStore takes, rather than a setting only this backend understands."""
        client = FakeClient(merged=MergeResult({"value": 1}, ["defaults/default.json"]))
        make_store(client).scoped(computer_name="DT201256").list(RIG)
        assert client.last_merged_call.scopes == [COMPUTERS("DT201256")]

    def test_a_call_level_computer_name_also_redirects_the_rig_layer(self):
        client = FakeClient(merged=MergeResult({"value": 1}, ["defaults/default.json"]))
        make_store(client).list(RIG, scope={"computer_name": "DT300043"})
        assert client.last_merged_call.scopes == [COMPUTERS("DT300043")]

    def test_the_merge_chain_excludes_the_subject_even_when_the_store_is_scoped_to_one(self):
        """Deliberate: per-animal values reach the rig via ByAnimalModifier, so layering the
        subject scope here too would apply them twice."""
        client = FakeClient(merged=MergeResult({"value": 1}, ["defaults/default.json"]))
        store = make_store(client).scoped(subject="789012")
        store.list(RIG)
        assert client.last_merged_call.scopes == [COMPUTERS("RIG-01")]

    def test_the_subject_scope_is_layered_only_when_configured_explicitly(self):
        client = FakeClient(merged=MergeResult({"value": 1}, ["defaults/default.json"]))
        store = make_store(client, config_scopes=["computers", "subjects"]).scoped(subject="789012")
        store.list(RIG)
        assert client.last_merged_call.scopes == [COMPUTERS("RIG-01"), SUBJECTS("789012")]

    def test_a_configured_subject_scope_is_skipped_when_no_subject_is_in_scope(self):
        client = FakeClient(merged=MergeResult({"value": 1}, ["defaults/default.json"]))
        make_store(client, config_scopes=["computers", "subjects"]).list(RIG)
        assert client.last_merged_call.scopes == [COMPUTERS("RIG-01")]

    def test_an_empty_config_scopes_drops_every_layer_but_defaults(self):
        """The one way to say "no rig layer" now that ``rig_scope`` is gone."""
        client = FakeClient(merged=MergeResult({"value": 1}, ["defaults/default.json"]))
        make_store(client, config_scopes=[]).list(RIG)
        assert client.last_merged_call.scopes == []

    def test_the_configured_filename_and_policy_are_forwarded(self):
        policy = MergePolicy(lists="concat", on_type_conflict="raise")
        client = FakeClient(merged=MergeResult({"value": 1}, ["defaults/default.json"]))
        make_store(client, filename="extra.json", policy=policy).list(RIG)
        assert client.last_merged_call.filename == "extra.json"
        assert client.last_merged_call.policy == policy

    def test_the_candidate_label_names_every_contributing_document(self, mock_frontend):
        ui.set_current_frontend(mock_frontend)
        sources = ["defaults/default.json", "computers/RIG-01/default.json"]
        client = FakeClient(merged=MergeResult({"value": 1}, sources))
        make_store(client).resolve(RIG)
        message = mock_frontend._render_mock.call_args[0][0]
        assert "defaults/default.json" in message
        assert "computers/RIG-01/default.json" in message

    def test_an_empty_merge_chain_yields_no_candidates(self):
        client = FakeClient(merged=MergeResult({}, []))
        store = make_store(client)
        assert store.list(RIG) == []
        with pytest.raises(LookupError):
            store.resolve(RIG)

    def test_a_merged_document_that_does_not_satisfy_the_model_raises(self):
        client = FakeClient(merged=MergeResult({}, ["defaults/default.json"]))
        with pytest.raises(pydantic.ValidationError):
            make_store(client).list(RIG)


class TestFlatRecords:
    def test_a_record_is_read_from_a_document_named_after_its_kind(self):
        client = FakeClient(layers={"manipulator_position.json": Layer({"value": 5}, "/scratch/x.json")})
        assert make_store(client).list(POSITION) == [Record(value=5)]
        assert client.last_layer_call.filename == "manipulator_position.json"

    def test_a_record_is_read_from_the_subject_scope_when_a_subject_is_in_scope(self):
        client = FakeClient(layers={"manipulator_position.json": Layer({"value": 5}, "/scratch/x.json")})
        make_store(client).scoped(subject="789012").list(POSITION)
        assert client.last_layer_call.scope == SUBJECTS("789012")

    def test_a_record_falls_back_to_the_rig_scope_without_a_subject(self):
        client = FakeClient(layers={"manipulator_position.json": Layer({"value": 5}, "/scratch/x.json")})
        make_store(client).list(POSITION)
        assert client.last_layer_call.scope == COMPUTERS("RIG-01")

    def test_a_record_follows_a_narrowed_computer_name(self):
        client = FakeClient(layers={"manipulator_position.json": Layer({"value": 5}, "/x.json")})
        make_store(client).scoped(computer_name="DT201256").list(POSITION)
        assert client.last_layer_call.scope == COMPUTERS("DT201256")

    def test_a_record_stored_under_another_extension_is_still_read(self):
        client = FakeClient(layers={"manipulator_position.yml": Layer({"value": 5}, "/x.yml")})
        assert make_store(client).list(POSITION) == [Record(value=5)]

    def test_a_record_never_joins_a_merge_chain(self):
        client = FakeClient(layers={"manipulator_position.json": Layer({"value": 5}, "/scratch/x.json")})
        make_store(client).list(POSITION)
        assert client.merged_calls == []

    def test_a_missing_record_yields_no_candidates(self):
        client = FakeClient(layers={})
        store = make_store(client)
        assert store.list(POSITION) == []
        with pytest.raises(LookupError):
            store.resolve(POSITION)

    def test_the_candidate_is_labelled_with_the_document_path(self, mock_frontend):
        ui.set_current_frontend(mock_frontend)
        client = FakeClient(layers={"manipulator_position.json": Layer({"value": 5}, "/scratch/subjects/1/mp.json")})
        make_store(client).resolve(POSITION)
        assert "/scratch/subjects/1/mp.json" in mock_frontend._render_mock.call_args[0][0]

    def test_a_record_always_has_somewhere_to_live(self):
        """``computer_name`` is always in scope, so a subject-less record falls back to this
        machine rather than having nowhere to go."""
        client = FakeClient(layers={"manipulator_position.json": Layer({"value": 1}, "/x.json")})
        assert make_store(client).list(POSITION) == [Record(value=1)]
        assert client.last_layer_call.scope == COMPUTERS("RIG-01")

    def test_a_record_is_unaffected_by_config_scopes(self):
        """A flat record never joins a merge chain, so the chain's shape has no say in where it
        lives -- only whether a subject is in scope does."""
        layer = Layer({"value": 1}, "/scratch/subjects/789907/vr_frg/manipulator_position.json")
        client = FakeClient(layers={"manipulator_position.json": layer})
        store = make_store(client, config_scopes=[]).scoped(subject="789907")
        assert store.list(POSITION) == [Record(value=1)]
        assert client.layer_calls[-1].scope == SUBJECTS("789907")

    def test_config_kinds_can_be_reconfigured(self):
        """``config_kinds`` decides the shape, so a store can serve "rig" as a flat record too."""
        client = FakeClient(layers={"rig.json": Layer({"value": 5}, "/scratch/rig.json")})
        store = make_store(client, config_kinds=set())
        assert store.list(RIG) == [Record(value=5)]
        assert client.last_layer_call.filename == "rig.json"
        assert client.merged_calls == []


class TestStoreWrite:
    def test_writing_a_record_targets_its_own_document_in_the_subject_scope(self):
        client = FakeClient()
        make_store(client).scoped(subject="789012").write(POSITION, Record(value=9))
        call = client.write_calls[-1]
        assert call.namespace == NAMESPACE
        assert call.filename == "manipulator_position.json"
        assert call.scope == SUBJECTS("789012")
        assert call.data == {"value": 9}

    def test_writing_a_record_without_a_subject_targets_the_rig_scope(self):
        client = FakeClient()
        make_store(client).write(POSITION, Record(value=9))
        assert client.write_calls[-1].scope == COMPUTERS("RIG-01")

    def test_a_call_level_scope_is_layered_over_the_stores_own(self):
        client = FakeClient()
        make_store(client).write(POSITION, Record(value=9), scope={"subject": "789012"})
        assert client.write_calls[-1].scope == SUBJECTS("789012")

    def test_a_bare_model_is_accepted_as_a_kind(self):
        client = FakeClient()
        make_store(client).write(Record, Record(value=9))
        assert client.write_calls[-1].filename == "record.json"

    def test_writing_layered_config_sends_only_the_change_to_the_rig_layer(self):
        client = FakeClient(merged=_chained({"value": 1}, COMPUTERS("RIG-01")))
        make_store(client).write(RIG, Record(value=2))
        call = client.write_calls[-1]
        assert call.data == {"value": 2}
        assert call.scope == COMPUTERS("RIG-01")
        assert call.filename == DEFAULT_FILENAME

    def test_writing_unchanged_layered_config_writes_nothing(self):
        """The common case after a resolve-then-save round trip. Writing anyway would copy every
        inherited value down into the rig's override."""
        client = FakeClient(merged=_chained({"value": 1}, COMPUTERS("RIG-01")))
        make_store(client).write(RIG, Record(value=1))
        assert client.write_calls == []

    def test_writing_layered_config_with_nowhere_but_defaults_is_refused(self):
        """``defaults`` is read by every machine in the namespace, so landing there has to be
        deliberate rather than the consequence of an unset scope."""
        client = FakeClient(merged=_chained({"value": 1}))
        with pytest.raises(LookupError, match="'defaults'"):
            make_store(client, config_scopes=[]).write(RIG, Record(value=2))
        assert client.write_calls == []

    def test_a_round_trip_with_no_edits_writes_nothing(self):
        """The document omits ``port`` and ``note``; the model supplies them. Writing them back
        would pin those defaults into the override, so this rig would stop tracking ``defaults``
        for them forever -- the exact flattening a diffed write exists to avoid."""
        client = FakeClient(merged=_chained({"value": 1}, COMPUTERS("RIG-01")))
        make_store(client).write(CONFIGURED, Configured(value=1))
        assert client.write_calls == []

    def test_a_key_the_model_does_not_declare_is_not_a_removal(self):
        """The model drops ``legacy`` on dump. Diffed against the raw document that reads as a
        deletion, which would fail the write outright."""
        client = FakeClient(merged=_chained({"value": 1, "legacy": "x"}, COMPUTERS("RIG-01")))
        make_store(client).write(CONFIGURED, Configured(value=1))
        assert client.write_calls == []

    def test_only_the_edited_leaf_survives_normalisation(self):
        client = FakeClient(merged=_chained({"value": 1, "legacy": "x"}, COMPUTERS("RIG-01")))
        make_store(client).write(CONFIGURED, Configured(value=2))
        assert client.write_calls[-1].data == {"value": 2}

    def test_a_namespace_with_no_config_yet_writes_the_whole_value(self):
        """Nothing resolved, so there is no baseline to normalise against and every field is
        genuinely new."""
        chain = [LayerKey(None, DEFAULT_FILENAME), LayerKey(COMPUTERS("RIG-01"), DEFAULT_FILENAME)]
        client = FakeClient(merged=MergeResult(data={}, sources=[], chain=chain))
        make_store(client).write(CONFIGURED, Configured(value=2))
        assert client.write_calls[-1].data == {"value": 2, "port": 3, "note": None}

    def test_a_flat_record_already_stored_as_yml_is_updated_in_place(self):
        """Not rewritten as ``.json``: ficus allows a stem only one extension, so a write to the
        preferred one would be refused outright."""
        client = FakeClient(layers={"manipulator_position.yml": Layer({"value": 1}, "/x.yml")})
        make_store(client).scoped(subject="789012").write(POSITION, Record(value=9))
        assert client.write_calls[-1].filename == "manipulator_position.yml"

    def test_a_flat_record_that_does_not_exist_yet_uses_the_clients_default_extension(self):
        client = FakeClient(default_extension=".yml")
        make_store(client).scoped(subject="789012").write(POSITION, Record(value=9))
        assert client.write_calls[-1].filename == "manipulator_position.yml"

    def test_the_configured_extension_reaches_the_client(self):
        """``FicusSettings.extension`` is the store's way of setting it on the client it builds."""
        store = FicusStore(FicusSettings(namespace=NAMESPACE, extension=".yaml"))
        assert store.client.default_extension == ".yaml"

    def test_writing_layered_config_to_a_subject_scope_targets_that_subject(self):
        client = FakeClient(merged=_chained({"value": 1}, COMPUTERS("RIG-01"), SUBJECTS("789012")))
        store = make_store(client, config_scopes=["computers", "subjects"]).scoped(subject="789012")
        store.write(RIG, Record(value=3))
        assert client.write_calls[-1].scope == SUBJECTS("789012")


class TestKindGuard:
    def test_an_unserved_kind_raises_on_read(self):
        client = FakeClient(merged=MergeResult({"value": 1}, ["defaults/default.json"]))
        store = make_store(client, serves="rig")
        with pytest.raises(LookupError, match="manipulator_position"):
            store.list(POSITION)

    def test_an_unserved_kind_raises_on_write(self):
        store = make_store(FakeClient(), serves="rig")
        with pytest.raises(LookupError, match="manipulator_position"):
            store.write(POSITION, Record(value=1))

    def test_the_served_kind_works(self):
        client = FakeClient(merged=MergeResult({"value": 1}, ["defaults/default.json"]))
        assert make_store(client, serves="rig").list(RIG) == [Record(value=1)]

    def test_serves_accepts_a_set_of_names(self):
        client = FakeClient(
            merged=MergeResult({"value": 1}, ["defaults/default.json"]),
            layers={"manipulator_position.json": Layer({"value": 2}, "/x.json")},
        )
        store = make_store(client, serves={"rig", "manipulator_position"})
        assert store.list(RIG) == [Record(value=1)]
        assert store.list(POSITION) == [Record(value=2)]

    def test_serves_none_means_any_kind(self):
        client = FakeClient(
            merged=MergeResult({"value": 1}, ["defaults/default.json"]),
            layers={"manipulator_position.json": Layer({"value": 2}, "/x.json")},
        )
        store = make_store(client, serves=None)
        assert store.list(RIG) == [Record(value=1)]
        assert store.list(POSITION) == [Record(value=2)]


class TestComputerNameIsAnOrdinaryScopeKey:
    """The trap this closes: ``computer_name`` used to reach LocalFileStore through the scope but
    FicusStore through a setting, so narrowing a CompositeStore moved one backend and not the
    other."""

    def test_it_is_seeded_at_construction_like_localfilestore_does(self):
        assert make_store(FakeClient()).scope == {"computer_name": "RIG-01"}

    def test_narrowing_it_moves_the_rig_layer_for_config_and_records_alike(self):
        client = FakeClient(
            merged=_chained({"value": 1}, COMPUTERS("DT201256")),
            layers={"manipulator_position.json": Layer({"value": 5}, "/x.json")},
        )
        store = make_store(client).scoped(computer_name="DT201256")
        store.list(RIG)
        store.list(POSITION)
        assert client.last_merged_call.scopes == [COMPUTERS("DT201256")]
        assert client.last_layer_call.scope == COMPUTERS("DT201256")

    def test_one_narrowing_moves_every_backend_in_a_composite(self):
        ficus = FakeClient(merged=_chained({"value": 1}, COMPUTERS("DT201256")))
        composite = CompositeStore(routes={"rig": make_store(ficus)}).scoped(computer_name="DT201256")
        composite.list(RIG)
        assert ficus.last_merged_call.scopes == [COMPUTERS("DT201256")]


class TestScoping:
    def test_scoped_returns_a_narrowed_store_without_mutating_the_original(self):
        client = FakeClient(layers={"manipulator_position.json": Layer({"value": 1}, "/x.json")})
        store = make_store(client)
        scoped = store.scoped(subject="789012")
        assert store.scope == {"computer_name": "RIG-01"}
        assert scoped.scope == {"computer_name": "RIG-01", "subject": "789012"}

        scoped.list(POSITION)
        assert client.last_layer_call.scope == SUBJECTS("789012")
        store.list(POSITION)
        assert client.last_layer_call.scope == COMPUTERS("RIG-01")

    def test_an_initial_scope_is_honoured(self):
        client = FakeClient(layers={"manipulator_position.json": Layer({"value": 1}, "/x.json")})
        make_store(client, scope={"subject": "789012"}).list(POSITION)
        assert client.last_layer_call.scope == SUBJECTS("789012")

    def test_a_composite_store_can_narrow_a_routed_ficus_store(self, mock_frontend):
        ui.set_current_frontend(mock_frontend)
        client = FakeClient(merged=MergeResult({"value": 1}, ["defaults/default.json"]))
        composite = CompositeStore(routes={"rig": make_store(client)})

        assert composite.scoped(subject="789012").resolve(RIG) == Record(value=1)
        assert client.last_merged_call.scopes == [COMPUTERS("RIG-01")]


class TestValidation:
    def test_kind_validators_still_run_on_the_resolved_value(self, mock_frontend):
        ui.set_current_frontend(mock_frontend)
        client = FakeClient(merged=MergeResult({"value": 5}, ["defaults/default.json"]))
        doubled = Kind(Record, "rig", validators=lambda r: Record(value=r.value * 2))
        assert make_store(client).resolve(doubled) == Record(value=10)

    def test_kind_validators_run_on_flat_records_too(self):
        client = FakeClient(layers={"manipulator_position.json": Layer({"value": 5}, "/x.json")})
        doubled = Kind(Record, "manipulator_position", validators=lambda r: Record(value=r.value * 2))
        assert make_store(client).list(doubled) == [Record(value=10)]


class TestMisc:
    def test_str_names_the_namespace_and_the_backend(self):
        assert str(make_store(FakeClient())) == "FicusStore(vr_frg @ http://fake-ficus)"

    def test_the_client_and_namespace_are_exposed(self):
        client = FakeClient()
        store = make_store(client)
        assert store.client is client
        assert store.namespace == NAMESPACE

    def test_a_store_built_without_a_client_does_not_touch_the_network(self):
        store = FicusStore(FicusSettings(namespace=NAMESPACE, base_url="http://ficus.test"))
        assert isinstance(store.client, FicusClient)
        assert store.client.base_url == "http://ficus.test"
