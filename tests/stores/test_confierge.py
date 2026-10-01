import logging

from clabe.stores.cofierge import ConfiergeSettings, ConfiergeStore


def test_scope_resolution_maps_supported_scopes_and_ignores_unsupported_ones(caplog):
    store = ConfiergeStore(
        settings=ConfiergeSettings(namespace="demo"),
        scope={"computer": "RIG-01", "subject": "123", "task_name": "a_task"},
    )

    with caplog.at_level(logging.WARNING):
        scopes = store._config_scopes({"computer": "RIG-01", "subject": "123", "task_name": "a_task"})

    assert scopes == {"hostname": "RIG-01", "subject_id": "123"}
    assert "Ignoring scope 'task_name'" in caplog.text