import logging

from clabe.stores.cofierge import ConfiergeSettings, ConfiergeStore


class FakeConfierge:
    def __init__(self, *, base_url: str):
        self.base_url = base_url

    def get_config(self, *, namespace: str, mode: str | None, scopes: dict[str, str]):
        return {"value": 7}

    def post_config_file(
        self,
        *,
        namespace: str,
        config_data: dict,
        mode: str | None,
        scopes: dict[str, str],
        overwrite_defaults: bool,
        create_if_missing: bool,
        append_new_fields_to_last_scope: bool,
    ) -> None:
        return None


def test_scope_resolution_maps_supported_scopes_and_ignores_unsupported_ones(caplog, monkeypatch):
    monkeypatch.setattr("clabe.stores.cofierge.Confierge", FakeConfierge)

    store = ConfiergeStore(
        settings=ConfiergeSettings(namespace="demo"),
        scope={"computer": "RIG-01", "subject": "123", "task_name": "a_task"},
    )

    with caplog.at_level(logging.WARNING):
        scopes = store._config_scopes({"computer": "RIG-01", "subject": "123", "task_name": "a_task"})

    assert scopes == {"hostname": "RIG-01", "subject_id": "123"}
    assert "Ignoring scope 'task_name'" in caplog.text