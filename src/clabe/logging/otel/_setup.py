import logging
from typing import TYPE_CHECKING, Any

from opentelemetry.util.types import AttributeValue

from ._settings import OtelSettings

if TYPE_CHECKING:
    from opentelemetry.sdk.resources import Resource

_INTERNAL_LOGGERS = ("opentelemetry", "urllib3")

# The single attribute bag for a run. Every stage of population (config defaults, session
# registration, ad-hoc enrichment) writes here; the span and log enrichers replay it onto
# every span and log. This is what makes the four write points behave consistently — see
# :class:`clabe.logging.otel._settings.OtelSettings`.
_attributes: dict[str, AttributeValue] = {}

# The providers built by :func:`configure`, kept so :func:`flush` can drain them on demand.
# Typed loosely because the SDK is an optional dependency and is imported lazily.
_tracer_provider: Any | None = None
_logger_provider: Any | None = None


class _ExcludeInternalLogs(logging.Filter):
    """Keeps the exporter's own logs out of the OTLP pipeline to avoid a feedback loop."""

    def filter(self, record: logging.LogRecord) -> bool:
        """Drop records emitted by the OTLP exporter and its HTTP stack."""
        return not record.name.startswith(_INTERNAL_LOGGERS)


class _AttributeLogEnricher(logging.Filter):
    """Stamps the run's attribute bag onto each exported log record."""

    def filter(self, record: logging.LogRecord) -> bool:
        """Add the current attributes so the OTLP exporter carries them."""
        for key, value in _attributes.items():
            attribute = key.replace(".", "_")
            if not hasattr(record, attribute):
                setattr(record, attribute, value)
        return True


def set_attributes(attributes: dict[str, AttributeValue]) -> None:
    """Replace the run's attribute bag (used to seed at run start and clear at run end)."""
    _attributes.clear()
    _attributes.update(attributes)


def merge_attributes(attributes: dict[str, AttributeValue]) -> None:
    """Merge attributes into the run's bag, overriding any same-named values."""
    _attributes.update(attributes)


def flush(timeout_millis: int = 5_000) -> None:
    """Force-export any spans and logs still sitting in the batch processors.

    The batch processors hold a span for up to their schedule delay before shipping it. A
    run's root span is by construction the last span to end, so without an explicit drain it
    is the one most likely to be lost when the process goes away before the next batch — and
    a run whose root span never arrives is indistinguishable, downstream, from one that never
    finished. Best effort: failures are logged and swallowed, as observability must never
    break a run.

    Args:
        timeout_millis: Budget granted to each provider's flush.
    """
    for provider in (_tracer_provider, _logger_provider):
        if provider is None:
            continue
        try:
            if provider.force_flush(timeout_millis) is False:
                logging.getLogger(__name__).warning("Timed out while flushing telemetry")
        except Exception:  # observability must never break a run
            logging.getLogger(__name__).warning("Failed to flush telemetry", exc_info=True)


def _build_resource(settings: OtelSettings) -> "Resource":
    """Build the resource shared by every span and log of the run.

    The resource holds only the service identity. Runs are correlated by trace/span id, and
    all log-schema identity travels in the attribute bag instead (see :data:`_attributes`),
    so it can be populated before or after the resource is frozen.
    """
    from opentelemetry.sdk.resources import Resource

    return Resource.create({"service.name": settings.resolved_service_name()})


def configure(settings: OtelSettings) -> None:
    """Install the trace and log SDKs, exporting OTLP to the configured endpoint.

    The OpenTelemetry SDK is imported lazily so base clabe does not require it (only the
    optional ``otel`` extra). Spans and logs share one resource (the service identity). A
    logging handler bridges stdlib ``logging`` to OTLP with the active trace context and the
    run's resource attached; it rides alongside clabe's existing console/file handlers, which
    are untouched. Both spans and logs are enriched from the run's attribute bag (see
    :data:`_attributes`) so backend telemetry is filterable by subject, rig, session, etc.

    ``settings.protocol`` selects the wire protocol (see
    :attr:`~clabe.logging.otel._settings.OtelSettings.protocol`); the matching exporter package
    (``opentelemetry-exporter-otlp-proto-http`` or ``-grpc``) must be installed.

    Args:
        settings: The resolved :class:`~clabe.logging.otel._settings.OtelSettings`.
    """
    global _tracer_provider, _logger_provider

    # OTel's global providers can only be installed once. Reuse the providers that own the
    # active tracers and loggers rather than constructing an unregistered provider that a
    # later flush would drain instead.
    if _tracer_provider is not None and _logger_provider is not None:
        return

    from opentelemetry import trace
    from opentelemetry._logs import set_logger_provider
    from opentelemetry.sdk._logs import LoggerProvider, LoggingHandler
    from opentelemetry.sdk._logs.export import BatchLogRecordProcessor
    from opentelemetry.sdk.trace import SpanProcessor, TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor

    class _AttributeSpanEnricher(SpanProcessor):
        """Stamps the run's attribute bag onto every span as it starts."""

        def on_start(self, span, parent_context=None):
            """Add the current attributes to the starting span."""
            for key, value in _attributes.items():
                span.set_attribute(key, value)

    headers = settings.headers or None  # None lets the exporter fall back to OTEL_* env headers

    match settings.protocol:
        case "grpc":
            from opentelemetry.exporter.otlp.proto.grpc._log_exporter import OTLPLogExporter
            from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter

            traces_endpoint = settings.endpoint
            logs_endpoint = settings.endpoint
            # Passed explicitly rather than inferred from endpoint's scheme — see the ``insecure``
            # field docstring on :class:`~clabe.logging.otel._settings.OtelSettings`.
            exporter_kwargs = {"insecure": settings.insecure}
        case "http":
            from opentelemetry.exporter.otlp.proto.http._log_exporter import OTLPLogExporter
            from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

            traces_endpoint = f"{settings.endpoint}/v1/traces"
            logs_endpoint = f"{settings.endpoint}/v1/logs"
            exporter_kwargs = {}

    resource = _build_resource(settings)

    tracer_provider = TracerProvider(resource=resource)
    tracer_provider.add_span_processor(_AttributeSpanEnricher())
    tracer_provider.add_span_processor(
        BatchSpanProcessor(OTLPSpanExporter(endpoint=traces_endpoint, headers=headers, **exporter_kwargs))
    )
    trace.set_tracer_provider(tracer_provider)
    _tracer_provider = tracer_provider

    logger_provider = LoggerProvider(resource=resource)
    logger_provider.add_log_record_processor(
        BatchLogRecordProcessor(OTLPLogExporter(endpoint=logs_endpoint, headers=headers, **exporter_kwargs))
    )
    set_logger_provider(logger_provider)
    _logger_provider = logger_provider
    handler = LoggingHandler(logger_provider=logger_provider)
    handler.addFilter(_ExcludeInternalLogs())
    handler.addFilter(_AttributeLogEnricher())
    logging.getLogger().addHandler(handler)
