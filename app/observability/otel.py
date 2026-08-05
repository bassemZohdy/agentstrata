"""OpenTelemetry wiring (REQUIREMENTS.md OBS-04, OBS-05, OBS-06).

OBS-06: with OTel disabled, opentelemetry packages MUST NOT be imported and
no span/metric objects may be allocated per request — all imports happen
inside ``if enabled`` branches. When enabled, standard OTEL_EXPORTER_OTLP_*
env vars configure the exporter with lazy imports and bounded queues;
export failure is nonfatal and marks health degraded.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


# Zero-cost stub used when OTel is disabled (OBS-06).
class _NullTracer:
    def start_as_current_span(self, name: str, **kwargs: Any):
        return _NullSpanContext()

    def start_span(self, name: str, **kwargs: Any):
        return _NullSpanContext()


class _NullSpanContext:
    def __enter__(self) -> _NullSpanContext:
        return self

    def __exit__(self, *exc: Any) -> None:
        return None

    def set_attribute(self, key: str, value: Any) -> None:
        return None

    def add_event(self, name: str, attributes: dict[str, Any] | None = None) -> None:
        return None

    def record_exception(self, exc: BaseException) -> None:
        return None


class NullMeter:
    def create_counter(self, name: str, **kwargs: Any):
        return _NullCounter()

    def create_histogram(self, name: str, **kwargs: Any):
        return _NullCounter()

    def create_up_down_counter(self, name: str, **kwargs: Any):
        return _NullCounter()


class _NullCounter:
    def add(self, amount: int, attributes: dict[str, Any] | None = None) -> None:
        return None

    def record(self, amount: float, attributes: dict[str, Any] | None = None) -> None:
        return None


class _DualCounter:
    """Records to OTel (when enabled) AND the Prometheus registry (when
    enabled); both sinks are optional, so this is a no-op object when
    neither is active (OBS-06)."""

    def __init__(self, otel_instrument: Any, registry: Any, name: str) -> None:
        self._otel = otel_instrument
        self._registry = registry
        self._name = name

    def add(self, amount: int, attributes: dict[str, Any] | None = None) -> None:
        if self._registry is not None:
            self._registry.add(self._name, amount, attributes)
        if self._otel is not None:
            self._otel.add(amount, attributes or {})


class _DualHistogram:
    def __init__(self, otel_instrument: Any, registry: Any, name: str) -> None:
        self._otel = otel_instrument
        self._registry = registry
        self._name = name

    def record(self, amount: float, attributes: dict[str, Any] | None = None) -> None:
        if self._registry is not None:
            self._registry.record(self._name, amount, attributes)
        if self._otel is not None:
            self._otel.record(amount, attributes or {})


class _DualGauge:
    """Active-count style gauge: OTel UpDownCounter + registry gauge."""

    def __init__(self, otel_instrument: Any, registry: Any, name: str) -> None:
        self._otel = otel_instrument
        self._registry = registry
        self._name = name

    def inc(self, attributes: dict[str, Any] | None = None) -> None:
        if self._registry is not None:
            self._registry.inc_gauge(self._name, attributes)
        if self._otel is not None:
            self._otel.add(1, attributes or {})

    def dec(self, attributes: dict[str, Any] | None = None) -> None:
        if self._registry is not None:
            self._registry.dec_gauge(self._name, attributes)
        if self._otel is not None:
            self._otel.add(-1, attributes or {})


class Observability:
    """Facade: real OTel when enabled, null objects otherwise (OBS-06)."""

    def __init__(self, config: Any) -> None:
        self._enabled = bool(config.observability.otel.enabled)
        self._tracer: Any = _NullTracer()
        self._meter: Any = NullMeter()
        self._provider: Any = None
        self._export_failed = False
        self._prometheus_enabled = bool(
            getattr(config.observability, "prometheus", None) is not None
            and config.observability.prometheus.enabled
        )
        self._registry: Any = None
        if self._prometheus_enabled:
            from .metrics import MetricsRegistry

            self._registry = MetricsRegistry()
        if self._enabled:
            self._initialize(config)

    @property
    def prometheus_enabled(self) -> bool:
        return self._prometheus_enabled

    @property
    def registry(self) -> Any:
        return self._registry

    def prometheus_path(self) -> str:
        return "/metrics"

    @property
    def enabled(self) -> bool:
        return self._enabled

    @property
    def export_failed(self) -> bool:
        return self._export_failed

    def _initialize(self, config: Any) -> None:
        # OBS-04/05: lazy imports only on the enabled path.
        try:
            from opentelemetry import trace
            from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
                OTLPSpanExporter,
            )
            from opentelemetry.sdk.resources import SERVICE_NAME, Resource
            from opentelemetry.sdk.trace import TracerProvider
            from opentelemetry.sdk.trace.export import BatchSpanProcessor

            resource = Resource.create(
                {SERVICE_NAME: config.observability.otel.serviceName or config.name}
            )
            provider = TracerProvider(resource=resource)
            provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
            trace.set_tracer_provider(provider)
            self._tracer = trace.get_tracer("agentbase")
            self._provider = provider
        except Exception as exc:  # noqa: BLE001
            # OBS-04: export failure is nonfatal and marks health degraded.
            logger.warning("OTel initialization failed: %s", exc)
            self._export_failed = True
            self._enabled = False
            self._tracer = _NullTracer()
            self._meter = NullMeter()

    def start_span(self, name: str, attributes: dict[str, Any] | None = None):
        """Start a span with optional attributes (set inside the context)."""
        if attributes and self._enabled:
            span: Any = self._tracer.start_as_current_span(name)
            span.__enter__()
            try:
                for k, v in attributes.items():
                    span.set_attribute(k, v)
            finally:
                span.__exit__(None, None, None)
            return span
        return self._tracer.start_as_current_span(name)

    def counter(self, name: str, description: str = ""):
        otel_instrument = self._meter.create_counter(name, description=description)
        return _DualCounter(otel_instrument, self._registry, name)

    def histogram(self, name: str, description: str = ""):
        otel_instrument = self._meter.create_histogram(name, description=description)
        return _DualHistogram(otel_instrument, self._registry, name)

    def gauge(self, name: str, description: str = ""):
        otel_instrument = self._meter.create_up_down_counter(name, description=description)
        return _DualGauge(otel_instrument, self._registry, name)

    def shutdown(self) -> None:
        if self._provider is not None:
            try:
                self._provider.shutdown()
            except Exception:  # noqa: BLE001
                logger.warning("OTel shutdown failed")
