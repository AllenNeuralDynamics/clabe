from typing import TYPE_CHECKING, ClassVar, Literal

from opentelemetry.util.types import AttributeValue
from pydantic import Field

from ...services import ServiceSettings

if TYPE_CHECKING:
    from aind_behavior_services.session import Session


class OtelSettings(ServiceSettings):
    """OpenTelemetry transport settings read from the ``otel`` section of clabe.yml.

    The base class is backend-agnostic: it configures how telemetry is shipped and tags a
    run with only its name. A profile enriches the run with domain identity by overriding
    the two population hooks:

    * :meth:`initial_attributes` — values known at process start (config + auto-defaults).
    * :meth:`session_attributes` — values derived from the session, once it is registered.

    Both feed a single attribute bag that is stamped on every span and log. Attributes may
    also be set at any time during a run via :func:`clabe.logging.otel.enrich_attribute`.
    """

    __yml_section__: ClassVar[str] = "otel"

    enabled: bool = Field(default=False, description="Whether telemetry is installed for the run.")
    protocol: Literal["grpc", "http"] = Field(
        default="grpc",
        description="OTLP wire protocol. 'grpc' (default) uses endpoint as-is; 'http' appends per-signal paths.",
    )
    endpoint: str = Field(
        default="localhost:4317",
        description="OTLP base endpoint, e.g. 'localhost:4317' (grpc) or 'http://localhost:4318' (http).",
    )
    insecure: bool = Field(
        default=True,
        description="protocol='grpc' only: use a plaintext channel instead of TLS. Ignored for protocol='http'.",
    )
    headers: dict[str, str] = Field(
        default_factory=dict,
        description=(
            "OTLP export headers, e.g. 'Authorization' for a backend reached without a collector. "
            "Usually empty: the collector (Alloy) adds auth when forwarding."
        ),
    )
    service_name: str = Field(
        default="clabe",
        description='"service.name" resource attribute — the "Service" every span and log is tagged with.',
    )
    run_name: str | None = Field(
        default=None,
        description=(
            "Operation name of the run's root span. "
            "When set, takes precedence over the experiment callable's name. "
            "Defaults to the experiment name when absent."
        ),
    )

    def resolved_service_name(self) -> str:
        """The ``service.name`` resource attribute — the "Service" every span and log is tagged with."""
        return self.service_name

    def initial_attributes(self) -> dict[str, AttributeValue]:
        """Attributes known at process start: config values plus auto-resolved defaults.

        This is stages 1 and 2 of population. Auto-defaults fill only fields left ``None`` in
        config, so an explicit clabe.yml value is never overwritten. The base profile adds
        nothing; keys whose resolved value is ``None`` are omitted.
        """
        return {}

    def session_attributes(self, session: "Session") -> dict[str, AttributeValue]:
        """Attributes derived from the session, bound once the launcher registers it.

        This is stage 3 of population and overrides any same-named value set earlier.
        """
        attributes: dict[str, AttributeValue] = {}
        if session.session_name:
            attributes["session_name"] = session.session_name
        return attributes


class AindOtelSettings(OtelSettings):
    """AIND log-schema profile: tags runs with the fields from log-schema.

    Every field is optional and settable in clabe.yml. Fields left unset are auto-populated
    at creation time where a default exists (``hostname``, ``rig_id``, ``software_version``);
    ``subject_id`` and ``user_id`` stay ``None`` until the session is registered. Any field
    can also be overridden at any time via :func:`clabe.logging.otel.enrich_attribute` (this is also
    how one-off attributes such as ``instrument_id`` are set — they have no dedicated field).

    See https://github.com/AllenNeuralDynamics/log-schema for the field definitions.
    """

    hostname: str | None = Field(
        default=None,
        description="Machine name. Default: env COMPUTERNAME / HOSTNAME / the socket hostname.",
    )
    software_name: str | None = Field(
        default=None,
        description="The producing application. clabe orchestrates many, so it has no default — set per rig.",
    )
    software_version: str | None = Field(
        default=None,
        description="Version of software_name. Default: that distribution's installed package version.",
    )
    rig_id: str | None = Field(
        default=None,
        description="Rig identifier. Default: env aibs_comp_id; None on rigs that do not export it.",
    )
    subject_id: str | None = Field(
        default=None,
        description="Subject under test. Left None in config; filled from the session when registered.",
    )
    user_id: str | None = Field(
        default=None,
        description="Experimenter(s). Left None in config; filled from the session when registered.",
    )

    def resolved_service_name(self) -> str:
        """Use the log-schema ``software_name`` as the "Service", falling back to ``service_name``.

        ``software_name`` is the producing application, which is exactly what ``service.name``
        should identify. It is also emitted as the explicit ``software_name`` attribute (see
        :meth:`initial_attributes`) for log-schema consumers that key on that field by name.
        """
        return self.software_name or self.service_name

    def initial_attributes(self) -> dict[str, AttributeValue]:
        """Resolve the log-schema fields, applying auto-defaults to any left unset in config."""
        from ...utils.aind_validators import get_aind_rig_name

        resolved: dict[str, str | None] = {
            "hostname": self.hostname or _default_hostname(),
            "software_name": self.software_name,
            "software_version": self.software_version
            or (_distribution_version(self.software_name) if self.software_name else None),
            "rig_id": self.rig_id or get_aind_rig_name(),
            "subject_id": self.subject_id,
            "user_id": self.user_id,
        }
        return {key: value for key, value in resolved.items() if value is not None}

    def session_attributes(self, session: "Session") -> dict[str, AttributeValue]:
        """Fill ``subject_id`` and ``user_id`` from the session (plus the base session name)."""
        attributes = super().session_attributes(session)
        if session.subject:
            attributes["subject_id"] = session.subject
        if session.experimenter:
            attributes["user_id"] = ", ".join(session.experimenter)
        return attributes


def _default_hostname() -> str:
    """Return the machine name from the environment, falling back to the socket hostname."""
    from ...utils import get_computer_name

    return get_computer_name()


def _distribution_version(distribution: str) -> str | None:
    """Return an installed distribution's version, or ``None`` if it is not installed."""
    try:
        from importlib.metadata import PackageNotFoundError, version

        return version(distribution)
    except PackageNotFoundError:
        return None
