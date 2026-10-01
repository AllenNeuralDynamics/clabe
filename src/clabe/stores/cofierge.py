import dataclasses
import functools
import logging
from typing import Literal, TypeVar

from confierge import Confierge
from pydantic import Field, TypeAdapter

from clabe.services import ServiceSettings
from clabe.stores import Candidate, Kind, Scope, StoreBase

DEFAULT_BASE_URL = "http://eng-tools/ficus-dev"
T = TypeVar("T")
logger = logging.getLogger(__name__)

Segment = Literal["computer", "subject"]


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
COMPUTER = functools.partial(ScopeRef, "computer", "hostname")

#: The per-animal scope, bound to a subject id: ``SUBJECTS("789907")``.
SUBJECT = functools.partial(ScopeRef, "subject", "subject_id")


class ConfiergeSettings(ServiceSettings):
    """Settings for :class:`ConfiergeStore`, read from the ``confierge`` section of clabe.yml."""

    namespace: str = Field(
        description="The ficus namespace this store reads, e.g. 'aind-behavior-vr-foraging'. Required.",
    )
    base_url: str = Field(
        default=DEFAULT_BASE_URL,
        description="Ficus' root URL.",
    )
    mode: str | None = Field(
        default=None,
        description=("An extra document layered above 'default' for config kinds. None uses 'default' alone."),
    )
    overwrite_defaults: bool = Field(
        default=False,
        description="Whether to overwrite the 'defaults' layer when writing config documents.",
    )
    create_if_missing: bool = Field(
        default=True,
        description="Whether to create the config layer if it does not exist.",
    )
    append_new_fields_to_last_scope: bool = Field(
        default=True,
        description="Whether to append new fields to the last scope when writing config documents.",
    )


class ConfiergeStore(StoreBase):
    """Reads and writes config records through the Concierge backend."""

    def __init__(
        self,
        *,
        settings: ConfiergeSettings,
        scope: Scope | None = None,
    ) -> None:
        super().__init__(scope=scope)

        self._client = Confierge(base_url=settings.base_url)
        self._settings = settings
        self._namespace = settings.namespace
        self._mode = settings.mode

    def _candidates(self, kind: Kind, scope: Scope) -> list[Candidate[Kind]]:
        """Fetch the current config record for a scope and package it as a candidate.

        Args:
            kind: The config kind to deserialize from the returned JSON.
            scope: The active experiment scope used to resolve the config.

        Returns:
            A single candidate containing the validated config model for this namespace.
        """
        data = self._client.get_config(
            namespace=self._namespace,
            mode=self._mode,
            scopes=self._config_scopes(scope),
        )
        config = TypeAdapter(kind.model).validate_python(data)

        return [Candidate(label=self._namespace, value=config)]

    def write(self, value: dict, *, scope: Scope) -> None:
        """Write a config document to the current namespace and scope.

        Args:
            value: The config payload to persist as JSON.
            scope: The experiment scope that determines the target machine or subject.
        """
        self._client.post_config_file(
            namespace=self._namespace,
            config_data=value,
            mode=self._mode,
            scopes=self._config_scopes(scope),
            overwrite_defaults=self._settings.overwrite_defaults,
            create_if_missing=self._settings.create_if_missing,
            append_new_fields_to_last_scope=self._settings.append_new_fields_to_last_scope,
        )

    def _config_scopes(self, scope: Scope) -> dict[str, str]:
        """Builds the merge chain's scopes, lowest precedence first.

        Args:
            scope: The merged scope for this call.

        Returns:
            dict[str, str]: The bound scopes, skipping any this store cannot resolve an identifier
                for.
        """
        refs: list[ScopeRef] = []
        if computer := scope.get("computer"):
            refs.append(COMPUTER(computer))
        if subject := scope.get("subject"):
            refs.append(SUBJECT(subject))

        scopes = {value.param: value.identifier for value in refs}
        ignored_scopes = [key for key in scope if key not in {"computer", "subject"}]
        for key in ignored_scopes:
            logger.warning("Ignoring scope '%s' because it is not included in the configuration scope mapping.", key)

        return scopes


if __name__ == "__main__":
    from aind_behavior_services import Task

    from clabe import ui

    ui.set_current_frontend(ui.make_frontend("console"))

    rig_settings = ConfiergeSettings(
        namespace="clabe-example",
    )
    rig_store = ConfiergeStore(
        settings=rig_settings,
        scope={"computer": "SIPE-Micah", "subject": "test", "task_name": "AindDynamicForaging"},
    )  # extra scopes not found in confierge are ignored
    task = rig_store.resolve(Task)

    # push back subject-specific configuration
    task.stage_name = "dummy_stage_name"
    rig_store.write(value=task.model_dump(include={"stage_name": True}), scope={"subject": "test"})
