"""In-process Prometheus metrics (REQUIREMENTS.md OBS-05).

The OBS-05 metric set is recorded into a small thread-safe registry when
``observability.prometheus.enabled`` is true and served at the configured
path (default ``/metrics``) in the Prometheus text exposition format
(version 0.0.4). Labels are low-cardinality by construction: each metric
caps its distinct label sets (default 128) and drops beyond the cap with a
single warning — request/session/run/principal IDs are prohibited labels
per OBS-05.

When OTel is ALSO enabled the same instruments record to both sinks via
the ``Observability`` facade; when both are disabled the call sites hold
null instruments and per-request cost stays O(1) (OBS-06).
"""

from __future__ import annotations

import logging
import threading
from collections import defaultdict
from typing import Any

logger = logging.getLogger(__name__)

# OBS-05 latency buckets, extended past the default set to cover
# engine.timeoutSeconds (up to 3600 s).
DEFAULT_BUCKETS = [
    0.005,
    0.01,
    0.025,
    0.05,
    0.1,
    0.25,
    0.5,
    1,
    2.5,
    5,
    10,
    30,
    60,
    120,
    300,
    600,
    1800,
    3600,
]


class _CardinalityGuard:
    """Drop label sets beyond the cap per metric (OBS-05 low-cardinality)."""

    def __init__(self, cap: int = 128) -> None:
        self._cap = cap
        self._seen: set[Any] = set()
        self._warned: set[str] = set()

    def admit(self, metric: str, key: Any) -> bool:
        if key in self._seen:
            return True
        if len(self._seen) >= self._cap:
            if metric not in self._warned:
                self._warned.add(metric)
                logger.warning(
                    "prometheus metric %s exceeded %d label sets; further sets dropped",
                    metric,
                    self._cap,
                )
            return False
        self._seen.add(key)
        return True

    @property
    def seen(self) -> set[Any]:
        return self._seen


class MetricsRegistry:
    """Counters, gauges, and bounded histograms keyed by (name, labels)."""

    def __init__(self, max_label_sets: int = 128) -> None:
        self._lock = threading.Lock()
        self._counters: dict[str, dict[tuple[tuple[str, str], ...], int | float]] = defaultdict(
            dict
        )
        self._gauges: dict[str, dict[tuple[tuple[str, str], ...], int | float]] = defaultdict(dict)
        self._hist_buckets: dict[str, dict[tuple[tuple[str, str], ...], list[int]]] = defaultdict(
            dict
        )
        self._hist_sums: dict[str, dict[tuple[tuple[str, str], ...], float]] = defaultdict(dict)
        self._hist_counts: dict[str, dict[tuple[tuple[str, str], ...], int]] = defaultdict(dict)
        self._buckets = DEFAULT_BUCKETS
        self._guard = _CardinalityGuard(max_label_sets)

    @staticmethod
    def _labels(attributes: dict[str, Any] | None) -> tuple[tuple[str, str], ...]:
        return tuple(sorted((str(k), str(v)) for k, v in (attributes or {}).items()))

    def add(self, name: str, amount: int | float, attributes: dict[str, Any] | None = None) -> None:
        key = self._labels(attributes)
        with self._lock:
            if not self._guard.admit(name, key):
                return
            self._counters[name][key] = self._counters[name].get(key, 0) + amount

    def set_gauge(
        self, name: str, value: int | float, attributes: dict[str, Any] | None = None
    ) -> None:
        key = self._labels(attributes)
        with self._lock:
            if not self._guard.admit(name, key):
                return
            self._gauges[name][key] = value

    def inc_gauge(self, name: str, attributes: dict[str, Any] | None = None) -> None:
        key = self._labels(attributes)
        with self._lock:
            if not self._guard.admit(name, key):
                return
            self._gauges[name][key] = self._gauges[name].get(key, 0) + 1

    def dec_gauge(self, name: str, attributes: dict[str, Any] | None = None) -> None:
        key = self._labels(attributes)
        with self._lock:
            if not self._guard.admit(name, key):
                return
            self._gauges[name][key] = self._gauges[name].get(key, 0) - 1

    def record(self, name: str, amount: float, attributes: dict[str, Any] | None = None) -> None:
        key = self._labels(attributes)
        with self._lock:
            if not self._guard.admit(name, key):
                return
            buckets = self._hist_buckets[name].setdefault(key, [0] * len(self._buckets))
            for i, bound in enumerate(self._buckets):
                if amount <= bound:
                    buckets[i] += 1
            self._hist_sums[name][key] = self._hist_sums[name].get(key, 0.0) + amount
            self._hist_counts[name][key] = self._hist_counts[name].get(key, 0) + 1

    def render(self) -> str:
        """Prometheus text exposition format 0.0.4."""
        lines: list[str] = []
        with self._lock:
            for name in sorted(set(self._counters) | set(self._gauges) | set(self._hist_counts)):
                if name in self._counters:
                    lines.append(f"# TYPE {name} counter")
                    for key, value in sorted(self._counters[name].items()):
                        lines.append(_sample(name, key, value))
                if name in self._gauges:
                    lines.append(f"# TYPE {name} gauge")
                    for key, value in sorted(self._gauges[name].items()):
                        lines.append(_sample(name, key, value))
                if name in self._hist_counts:
                    lines.append(f"# TYPE {name} histogram")
                    for key in sorted(self._hist_counts[name]):
                        labels = _labels_suffix(key)
                        for i, bound in enumerate(self._buckets):
                            lines.append(
                                f'{name}_bucket{labels}{{le="{bound}"}} '
                                f"{self._hist_buckets[name][key][i]}"
                            )
                        lines.append(
                            f'{name}_bucket{labels}{{le="+Inf"}} {self._hist_counts[name][key]}'
                        )
                        lines.append(f"{name}_sum{labels} {self._hist_sums[name][key]:g}")
                        lines.append(f"{name}_count{labels} {self._hist_counts[name][key]}")
        return "\n".join(lines) + "\n"


def _labels_suffix(key: tuple[tuple[str, str], ...]) -> str:
    if not key:
        return ""
    return "{" + ",".join(f'{k}="{_escape(v)}"' for k, v in key) + "}"


def _sample(name: str, key: tuple[tuple[str, str], ...], value: int | float) -> str:
    return f"{name}{_labels_suffix(key)} {value:g}"


def _escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


class MetricBundle:
    """The OBS-05 instrument set, created once per component build.

    Each instrument records into the Prometheus registry (when enabled)
    and the OTel meter (when enabled) through the Observability facade.
    """

    def __init__(self, observability: Any) -> None:
        self.runs_admitted = observability.counter(
            "agentbase_runs_admitted_total", "Runs admitted (ENG-03 step 7)"
        )
        self.runs_completed = observability.counter(
            "agentbase_runs_completed_total",
            "Runs reaching a terminal commit, by status",
        )
        self.runs_failed = observability.counter(
            "agentbase_runs_failed_total", "Failed runs, by public error code"
        )
        self.active_runs = observability.gauge("agentbase_active_runs", "In-flight runs")
        self.run_duration = observability.histogram(
            "agentbase_run_duration_seconds", "Admit-to-terminal run duration"
        )
        self.llm_calls = observability.counter(
            "agentbase_llm_calls_total", "Root LLM invocations, by model"
        )
        self.tool_calls = observability.counter(
            "agentbase_tool_calls_total", "Tool invocations, by tool"
        )
        self.tokens = observability.counter(
            "agentbase_tokens_total", "Billed tokens, by kind (input|output)"
        )
        self.denials = observability.counter(
            "agentbase_denials_total", "Admission denials, by reason"
        )
        self.reloads = observability.counter(
            "agentbase_reloads_total", "Live-reload attempts, by outcome"
        )
        self.cost_usd = observability.counter(
            "agentbase_cost_usd_total", "Accumulated USD cost, by model (COST-01)"
        )
        self.queue_cancellations = observability.counter(
            "agentbase_output_queue_cancellations_total",
            "Runs cancelled for a full output queue (slow consumer)",
        )
        self.dependency_healthy = observability.gauge(
            "agentbase_dependency_healthy", "Dependency health (1 healthy, 0 not), by dependency"
        )
