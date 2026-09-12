import dataclasses
import json
from typing import Any

import pydantic
import pytest
import requests

from clabe import ui
from clabe.stores import CompositeStore, Kind
from clabe.stores.confierge import ConfiergeSettings, ConfiergeStore


class Record(pydantic.BaseModel):
    """A minimal record type standing in for a real ficus-served model."""

    value: int


#: The canonical "rig" kind ``ConfiergeStore`` serves by default. Named manually (not via
#: ``Kind.from_rig``) so tests don't need a real ``aind_behavior_services.Rig`` model, or its
#: default ``validate_rig_computer_name`` validator, which would itself read ``aibs_comp_id``
#: and interfere with the rig-injection assertions below.
RIG = Kind(Record, "rig")
TASK = Kind(Record, "task")


@dataclasses.dataclass
class _Call:
    """One recorded call to :meth:`FakeConfierge.get_config_safe`."""

    namespace: str
    mode: str | None
    scopes: dict[str, str] | None
    model: Any


class FakeConfierge:
    """Test double for ``confierge.Confierge``: records calls, never touches the network.

    Configured with either a ``config`` dict to return, or an ``error`` to raise, from
    ``get_config_safe``. Every call is appended to :attr:`calls` so tests can assert exactly
    what the store sent.
    """

    def __init__(self, *, config: dict | None = None, error: Exception | None = None) -> None:
        self.base_url = "http://fake-ficus"
        self.calls: list[_Call] = []
        self.config = config
        self.error = error

    def get_config_safe(
        self,
        *,
        namespace: str,
        mode: str | None = None,
        scopes: dict[str, str] | None = None,
        model: Any = None,
    ) -> dict:
        self.calls.append(_Call(namespace=namespace, mode=mode, scopes=scopes, model=model))
        if self.error is not None:
            raise self.error
        return self.config if self.config is not None else {}

    @property
    def last_call(self) -> _Call:
        return self.calls[-1]


def _http_error(status_code: int, *, detail: str | None = None, text: str | None = None) -> requests.HTTPError:
    """Builds an ``HTTPError`` with a real (offline) ``Response`` body, as ``_is_config_not_found`` expects."""
    response = requests.Response()
    response.status_code = status_code
    if detail is not None:
        response._content = json.dumps({"detail": detail}).encode()
    elif text is not None:
        response._content = text.encode()
    else:
        response._content = b""
    return requests.HTTPError(response=response)


@pytest.fixture(autouse=True)
def rig_identity(monkeypatch):
    """Sets the ``aibs_comp_id`` env var ``get_aind_rig_name`` reads, since the default
    ``rig_scope="hostname"`` requires it. Individual tests may override or delete it."""
    monkeypatch.setenv("aibs_comp_id", "RIG-01")


def make_store(fake: FakeConfierge, *, serves: str | set[str] | None = "rig", **settings_kwargs) -> ConfiergeStore:
    settings = ConfiergeSettings(namespace="vr_frg", **settings_kwargs)
    return ConfiergeStore(settings, client=fake, serves=serves)


class TestScopeTranslation:
    def test_mapped_keys_are_renamed(self):
        fake = FakeConfierge(config={"value": 1})
        store = make_store(fake)
        store.list(RIG, scope={"subject": "789012"})
        assert fake.last_call.scopes["subject_id"] == "789012"
        assert "subject" not in fake.last_call.scopes

    def test_keys_ficus_does_not_define_are_dropped_not_raised(self):
        fake = FakeConfierge(config={"value": 1})
        store = make_store(fake)
        # task_name and computer_name are neither mapped nor in scope_order/rig_scope.
        store.list(RIG, scope={"task_name": "foraging", "computer_name": "SOME-PC", "subject": "789012"})
        sent = fake.last_call.scopes
        assert "task_name" not in sent
        assert "computer_name" not in sent
        assert sent["subject_id"] == "789012"

    def test_rig_identity_is_injected_from_env_not_from_computer_name_scope(self, monkeypatch):
        monkeypatch.setenv("aibs_comp_id", "RIG-77")
        fake = FakeConfierge(config={"value": 1})
        store = make_store(fake)
        # A conflicting computer_name is present in scope but must never reach ficus, let alone
        # win over aibs_comp_id: LocalFileStore's computer_name (COMPUTERNAME/platform.node())
        # and ficus's rig identity (aibs_comp_id) are different strings for the same machine.
        store.list(RIG, scope={"computer_name": "OTHER-RIG"})
        assert fake.last_call.scopes["hostname"] == "RIG-77"

    def test_rig_identity_overrides_a_same_named_scope_key(self, monkeypatch):
        monkeypatch.setenv("aibs_comp_id", "RIG-77")
        fake = FakeConfierge(config={"value": 1})
        store = make_store(fake)
        # "hostname" is itself a known (identity-mapped) scope key; the rig injection must still
        # win over any value supplied for it directly.
        store.list(RIG, scope={"hostname": "SOMETHING-ELSE"})
        assert fake.last_call.scopes["hostname"] == "RIG-77"

    def test_ordering_matches_configured_scope_order(self):
        # Ficus derives merge precedence from the *insertion order* of the scopes dict on the
        # request, not from clabe's own scope order -- so the store must emit keys in exactly
        # `scope_order`, lowest priority first, regardless of the order scope was supplied in.
        fake = FakeConfierge(config={"value": 1})
        store = make_store(fake, scope_order=["subject_id", "hostname"], rig_scope="hostname")
        store.list(RIG, scope={"subject": "789012"})
        assert list(fake.last_call.scopes) == ["subject_id", "hostname"]

    def test_rig_scope_none_disables_rig_injection(self, monkeypatch):
        monkeypatch.delenv("aibs_comp_id", raising=False)
        fake = FakeConfierge(config={"value": 1})
        store = make_store(fake, rig_scope=None)
        # Must not raise even though aibs_comp_id is unset: rig injection is fully disabled.
        store.list(RIG, scope={"subject": "789012"})
        assert fake.last_call.scopes == {"subject_id": "789012"}


class TestMode:
    def test_mode_in_scope_is_consumed_and_not_forwarded_as_a_scope_key(self):
        fake = FakeConfierge(config={"value": 1})
        store = make_store(fake)
        store.list(RIG, scope={"mode": "high-freq", "subject": "789012"})
        assert fake.last_call.mode == "high-freq"
        assert "mode" not in fake.last_call.scopes

    def test_mode_falls_back_to_settings_level_mode(self):
        fake = FakeConfierge(config={"value": 1})
        store = make_store(fake, mode="from-settings")
        store.list(RIG)
        assert fake.last_call.mode == "from-settings"

    def test_mode_in_scope_wins_over_settings_level_mode(self):
        fake = FakeConfierge(config={"value": 1})
        store = make_store(fake, mode="from-settings")
        store.list(RIG, scope={"mode": "from-scope"})
        assert fake.last_call.mode == "from-scope"


class TestComposition:
    def test_composite_store_routes_scoped_rig_reads_through(self, mock_frontend):
        """The regression this design exists to prevent: CompositeStore.scoped() must be able to
        narrow a routed ConfiergeStore with clabe scope keys (like "subject") without the
        translation raising or leaking stray keys to ficus."""
        ui.set_current_frontend(mock_frontend)
        fake = FakeConfierge(config={"value": 1})
        confierge_store = make_store(fake)
        composite = CompositeStore(routes={"rig": confierge_store})

        result = composite.scoped(subject="789012").resolve(RIG)

        assert result == Record(value=1)
        assert fake.last_call.scopes["subject_id"] == "789012"
        assert set(fake.last_call.scopes) <= {"hostname", "subject_id"}


class TestKindGuard:
    def test_an_unserved_kind_raises(self):
        fake = FakeConfierge(config={"value": 1})
        store = make_store(fake, serves="rig")
        with pytest.raises(LookupError, match="task"):
            store.list(TASK)

    def test_the_served_kind_works(self):
        fake = FakeConfierge(config={"value": 1})
        store = make_store(fake, serves="rig")
        assert store.list(RIG) == [Record(value=1)]

    def test_serves_none_means_any_kind(self):
        fake = FakeConfierge(config={"value": 1})
        store = make_store(fake, serves=None)
        assert store.list(RIG) == [Record(value=1)]
        assert store.list(TASK) == [Record(value=1)]


class TestErrorMapping:
    def test_a_missing_config_404_yields_no_candidates(self):
        fake = FakeConfierge(error=_http_error(404, detail="ConfigNotFound: no such path"))
        store = make_store(fake)
        assert store.list(RIG) == []
        with pytest.raises(LookupError):
            store.resolve(RIG)

    def test_a_404_with_unrelated_detail_propagates(self):
        fake = FakeConfierge(error=_http_error(404, detail="Internal server error"))
        store = make_store(fake)
        with pytest.raises(requests.HTTPError):
            store.list(RIG)

    def test_a_non_404_http_error_propagates(self):
        fake = FakeConfierge(error=_http_error(500, detail="ConfigNotFound"))
        store = make_store(fake)
        with pytest.raises(requests.HTTPError):
            store.list(RIG)


class TestReadsAndValidation:
    def test_a_config_dict_is_validated_through_the_kind_adapter(self):
        fake = FakeConfierge(config={"value": 42})
        store = make_store(fake)
        assert store.list(RIG) == [Record(value=42)]

    def test_a_config_that_does_not_satisfy_the_model_raises_validation_error(self):
        fake = FakeConfierge(config={})  # missing required "value"
        store = make_store(fake)
        with pytest.raises(pydantic.ValidationError):
            store.list(RIG)

    def test_kind_validators_still_run_on_the_resolved_value(self, mock_frontend):
        ui.set_current_frontend(mock_frontend)
        fake = FakeConfierge(config={"value": 5})
        doubled = Kind(Record, "rig", validators=lambda r: Record(value=r.value * 2))
        store = make_store(fake)
        assert store.resolve(doubled) == Record(value=10)

    def test_the_client_is_always_called_with_model_none(self):
        fake = FakeConfierge(config={"value": 1})
        store = make_store(fake)
        store.list(RIG)
        assert fake.last_call.model is None


class TestWrite:
    def test_write_raises_not_implemented(self):
        fake = FakeConfierge(config={"value": 1})
        store = make_store(fake)
        with pytest.raises(NotImplementedError):
            store.write(RIG, Record(value=1))


class TestMisc:
    def test_resolve_on_a_single_candidate_does_not_prompt(self, mock_frontend):
        ui.set_current_frontend(mock_frontend)
        fake = FakeConfierge(config={"value": 1})
        store = make_store(fake)
        assert store.resolve(RIG) == Record(value=1)
        mock_frontend._ask_pick_mock.assert_not_called()

    def test_scoped_returns_a_narrowed_store_without_mutating_the_original(self):
        fake = FakeConfierge(config={"value": 1})
        store = make_store(fake)
        scoped = store.scoped(subject="789012")
        assert store.scope == {}
        assert scoped.scope == {"subject": "789012"}

        scoped.list(RIG)
        assert fake.last_call.scopes["subject_id"] == "789012"
