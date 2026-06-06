import enum
import logging
from dataclasses import dataclass, replace
from typing import ClassVar, Optional

from pydantic import field_validator

from ..services import ServiceSettings

#: Granular reporting flags that a tier expands into and that any layer may override.
_FLAGS = ("show_activity", "notify_start", "notify_success", "notify_fail")


class ReportTier(enum.IntEnum):
    """How much a runnable reports while it runs, from quiet to loud.

    A tier is the ergonomic dial; it expands into the granular flags on
    :class:`ResolvedSpec`. Higher tiers are strictly more verbose.
    """

    SILENT = 0  # spinner + debug log only
    FAILURES = 10  # + notify on failure (default)
    LIFECYCLE = 20  # + notify on start and success
    VERBOSE = 30  # + timing in the success message


class RunnableSettings(ServiceSettings):
    """Process-wide defaults for the ``runnable`` decorator (the house style).

    Loaded from the ``runnable`` section of the known config files (and env),
    like every other :class:`ServiceSettings`. ``None`` flags inherit from the
    tier; set one to override the tier for that single concern.
    """

    __yml_section__: ClassVar[Optional[str]] = "runnable"

    tier: ReportTier = ReportTier.FAILURES
    show_activity: Optional[bool] = None
    notify_start: Optional[bool] = None
    notify_success: Optional[bool] = None
    notify_fail: Optional[bool] = None
    emit_span: bool = True  # OTEL; reserved for when tracing is wired in

    @field_validator("tier", mode="before")
    @classmethod
    def _coerce_tier(cls, value: object) -> object:
        """Accept tier by name (e.g. ``LIFECYCLE``) in YAML/env, not just its int."""
        if isinstance(value, str) and not value.isdigit():
            try:
                return ReportTier[value.strip().upper()]
            except KeyError:
                names = ", ".join(t.name for t in ReportTier)
                raise ValueError(f"Unknown report tier {value!r}; expected one of: {names}.") from None
        return value


@dataclass(frozen=True)
class RunnableSpec:
    """A partial set of overrides; ``None`` fields inherit from a lower layer.

    Used identically for the decorator's baked-in spec and a call-site rewrap;
    :meth:`merge` applies the more specific layer on top.
    """

    name: Optional[str] = None
    tier: Optional[ReportTier] = None
    notify: Optional[str] = None  # start message; its presence implies notify_start
    show_activity: Optional[bool] = None
    notify_start: Optional[bool] = None
    notify_success: Optional[bool] = None
    notify_fail: Optional[bool] = None

    def merge(self, other: "RunnableSpec") -> "RunnableSpec":
        """Return a copy where ``other``'s set (non-``None``) fields win."""
        return replace(self, **{k: v for k, v in vars(other).items() if v is not None})


@dataclass(frozen=True)
class ResolvedSpec:
    """The concrete behavior for a single runnable invocation."""

    show_activity: bool
    notify_start: bool
    notify_success: bool
    notify_fail: bool
    include_timing: bool
    log_level: int


def resolve(spec: RunnableSpec, settings: RunnableSettings) -> ResolvedSpec:
    """Flatten a spec against the global settings into concrete behavior.

    Precedence (most specific wins): built-in tier defaults ◁ settings flags ◁
    spec flags. A start message (``spec.notify``) implies ``notify_start``.
    """
    tier = spec.tier if spec.tier is not None else settings.tier
    flags = {
        "show_activity": True,
        "notify_fail": tier >= ReportTier.FAILURES,
        "notify_start": tier >= ReportTier.LIFECYCLE,
        "notify_success": tier >= ReportTier.LIFECYCLE,
        "include_timing": tier >= ReportTier.VERBOSE,
        "log_level": logging.DEBUG if tier <= ReportTier.SILENT else logging.INFO,
    }
    for layer in (settings, spec):
        for key in _FLAGS:
            value = getattr(layer, key)
            if value is not None:
                flags[key] = value
    if spec.notify is not None:
        flags["notify_start"] = True
    return ResolvedSpec(**flags)
