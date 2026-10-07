import pydantic
import pytest

from clabe.stores import CompositeStore, Kind, MemoryStore, Store
from clabe.stores.confierge import DEFAULT_SCOPE_MAPPING, ConfiergeSettings, ConfiergeStore


class Widget(pydantic.BaseModel):
    value: int = 0


class FakeConfierge:
    """Stands in for ``confierge.Confierge``, recording every call."""

    def __init__(self, data: dict | None = None):
        self.data = {"value": 7} if data is None else data
        self.gets: list[dict] = []
        self.posts: list[dict] = []

    def get_config_safe(self, **kwargs):
        self.gets.append(kwargs)
        return self.data

    def post_config_file(self, **kwargs) -> None:
        self.posts.append(kwargs)


@pytest.fixture
def client() -> FakeConfierge:
    return FakeConfierge()


def make_store(client: FakeConfierge, **kwargs) -> ConfiergeStore:
    settings = kwargs.pop("settings", ConfiergeSettings(namespace="demo"))
    return ConfiergeStore(settings, client=client, **kwargs)  # type: ignore[arg-type]


class TestScopeMapping:
    def test_translates_known_keys_and_drops_the_rest(self, client):
        store = make_store(client, scope={"computer_name": "RIG-01", "subject": "123", "task_name": "a_task"})
        store.resolve(Widget)
        assert client.gets[0]["scopes"] == {"hostname": "RIG-01", "subject_id": "123"}

    def test_computer_name_defaults_to_this_machine(self, client, monkeypatch):
        monkeypatch.setattr("clabe.stores.confierge.get_computer_name", lambda: "HERE")
        make_store(client).resolve(Widget)
        assert client.gets[0]["scopes"] == {"hostname": "HERE"}

    def test_empty_values_are_not_sent(self, client):
        make_store(client, scope={"computer_name": "RIG-01", "subject": ""}).resolve(Widget)
        assert client.gets[0]["scopes"] == {"hostname": "RIG-01"}

    def test_custom_mapping_extends_the_default(self, client):
        mapping = {**DEFAULT_SCOPE_MAPPING, "rig": "rig_id"}
        store = make_store(client, scope={"computer_name": "RIG-01", "rig": "FRG.4A"}, scope_mapping=mapping)
        store.resolve(Widget)
        assert client.gets[0]["scopes"] == {"hostname": "RIG-01", "rig_id": "FRG.4A"}

    def test_default_mapping_is_read_only(self):
        with pytest.raises(TypeError):
            DEFAULT_SCOPE_MAPPING["x"] = "y"  # type: ignore[index]


class TestRead:
    def test_resolve_validates_into_the_kind_model(self, client):
        widget = make_store(client).resolve(Widget)
        assert widget == Widget(value=7)

    def test_resolve_applies_the_kind_validators(self, client):
        def bump(w: Widget) -> Widget:
            return Widget(value=w.value + 1)

        assert make_store(client).resolve(Kind(Widget, validators=bump)).value == 8

    def test_list_returns_the_single_document(self, client):
        assert make_store(client).list(Widget) == [Widget(value=7)]

    def test_invalid_document_raises(self):
        with pytest.raises(pydantic.ValidationError):
            make_store(FakeConfierge({"value": "not an int"})).resolve(Widget)

    def test_namespace_and_mode_are_forwarded(self, client):
        make_store(client, settings=ConfiergeSettings(namespace="ns", mode="fast")).resolve(Widget)
        assert (client.gets[0]["namespace"], client.gets[0]["mode"]) == ("ns", "fast")

    def test_scoped_narrows_the_view_without_touching_the_original(self, client):
        store = make_store(client, scope={"computer_name": "RIG-01"})
        store.scoped(subject="123").resolve(Widget)
        store.resolve(Widget)
        assert client.gets[0]["scopes"] == {"hostname": "RIG-01", "subject_id": "123"}
        assert client.gets[1]["scopes"] == {"hostname": "RIG-01"}

    def test_call_scope_overrides_store_scope(self, client):
        make_store(client, scope={"computer_name": "RIG-01"}).resolve(Widget, scope={"computer_name": "RIG-02"})
        assert client.gets[0]["scopes"] == {"hostname": "RIG-02"}


class TestWrite:
    def test_posts_the_serialized_record_under_the_merged_scope(self, client):
        store = make_store(client, scope={"computer_name": "RIG-01"})
        store.write(Widget, Widget(value=3), scope={"subject": "123"})
        post = client.posts[0]
        assert post["config_data"] == {"value": 3}
        assert post["scopes"] == {"hostname": "RIG-01", "subject_id": "123"}
        assert post["namespace"] == "demo"

    def test_write_flags_come_from_the_settings(self, client):
        settings = ConfiergeSettings(
            namespace="demo", overwrite_defaults=True, create_if_missing=False, append_new_fields_to_last_scope=False
        )
        make_store(client, settings=settings).write(Widget, Widget())
        post = client.posts[0]
        assert post["overwrite_defaults"] is True
        assert post["create_if_missing"] is False
        assert post["append_new_fields_to_last_scope"] is False

    def test_non_object_records_are_rejected(self, client):
        with pytest.raises(TypeError):
            make_store(client).write(Kind(list[int], "numbers"), [1, 2])
        assert client.posts == []


class TestInterop:
    def test_is_a_store(self, client):
        assert isinstance(make_store(client), Store)

    def test_composite_routes_a_kind_to_it(self, client):
        composite = CompositeStore(default=MemoryStore(), routes={"widget": make_store(client)})
        assert composite.resolve(Widget) == Widget(value=7)
        composite.write(Widget, Widget(value=9))
        assert client.posts[0]["config_data"] == {"value": 9}

    def test_composite_scoped_narrows_it(self, client):
        composite = CompositeStore(default=MemoryStore(), routes={"widget": make_store(client)})
        composite.scoped(subject="123").resolve(Widget)
        assert client.gets[0]["scopes"]["subject_id"] == "123"


class TestSettings:
    def test_reads_the_confierge_section(self):
        assert ConfiergeSettings.__yml_section__ == "confierge"

    def test_base_url_defers_to_confierge_by_default(self):
        assert ConfiergeSettings(namespace="demo").base_url is None

    def test_builds_a_confierge_client_from_the_base_url(self, monkeypatch):
        seen = {}

        class Recorder(FakeConfierge):
            def __init__(self, *, base_url):
                super().__init__()
                seen["base_url"] = base_url

        monkeypatch.setattr("clabe.stores.confierge.Confierge", Recorder)
        ConfiergeStore(ConfiergeSettings(namespace="demo", base_url="http://ficus.test"))
        assert seen["base_url"] == "http://ficus.test"
