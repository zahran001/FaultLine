"""Phase 6 — Checkpoint 2 (C3): instrumented ENGINE.run() p99 vs the 200 ms target.

Counterpart to scripts/phase6_checkpoint1.py Section A (which measured the BARE engine).
Here the engine call goes through the SAME OpenTelemetry histogram.record() path the live
backend uses, timed end-to-end (run + record), so the number answers C3b directly:
"after instrumenting, confirm the instrumented p99 still clears 200 ms" — and quantifies
the overhead honestly rather than asserting it away.

Uses an in-memory metric reader so it needs NO collector (the record()/aggregation cost is
identical to production; only the export transport differs, and export is on a background
thread off the hot path).

Run (from repo root):  .venv/Scripts/python.exe scripts/phase6_checkpoint2_p99.py
"""

import os
import sys
import time

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from diagnostic_engine import RuleBasedDiagnostics  # noqa: E402
from simulator import VehicleSimulator  # noqa: E402
from telemetry import M_ENGINE_DURATION, _ENGINE_MS_BUCKETS  # noqa: E402

from opentelemetry.sdk.metrics import MeterProvider  # noqa: E402
from opentelemetry.sdk.metrics.export import InMemoryMetricReader  # noqa: E402
from opentelemetry.sdk.metrics.view import (  # noqa: E402
    ExplicitBucketHistogramAggregation,
    View,
)

LATENCY_TARGET_MS = 200.0
N = 200_000


def measure_instrumented_p99():
    reader = InMemoryMetricReader()
    provider = MeterProvider(
        metric_readers=[reader],
        views=[View(instrument_name=M_ENGINE_DURATION,
                    aggregation=ExplicitBucketHistogramAggregation(_ENGINE_MS_BUCKETS))],
    )
    meter = provider.get_meter("faultline.bench")
    hist = meter.create_histogram(M_ENGINE_DURATION)

    engine = RuleBasedDiagnostics()
    healthy = VehicleSimulator("BENCH-H", seed=0).tick()
    faulted = dict(healthy)
    faulted.update({"pack_voltage": 300.0, "coolant_flow_rate": 2.0,
                    "inverter_efficiency": 0.5, "bms_heartbeat": None})
    readings = [healthy, faulted]

    # This is exactly telemetry._timed_run's body (run + record), timed end-to-end.
    def timed_run(reading):
        start = time.perf_counter()
        engine.run(reading)
        elapsed_ms = (time.perf_counter() - start) * 1000.0
        hist.record(elapsed_ms, {"vehicle": reading["vehicle_id"]})

    for r in readings * 1000:
        timed_run(r)

    total = np.empty(N, dtype=np.float64)
    for i in range(N):
        r = readings[i & 1]
        t0 = time.perf_counter()
        timed_run(r)
        total[i] = (time.perf_counter() - t0) * 1000.0  # full instrumented per-call cost

    return total


if __name__ == "__main__":
    total = measure_instrumented_p99()
    p50, p99, p999, mx = np.percentile(total, [50, 99, 99.9, 100])
    print("=" * 70)
    print("C3 — INSTRUMENTED  ENGINE.run() + histogram.record()  per-call cost")
    print("=" * 70)
    print(f"  samples            : {N:,}")
    print(f"  mean               : {total.mean():.5f} ms")
    print(f"  p50                : {p50:.5f} ms")
    print(f"  p99                : {p99:.5f} ms")
    print(f"  p99.9              : {p999:.5f} ms")
    print(f"  max                : {mx:.5f} ms")
    print(f"  target             : < {LATENCY_TARGET_MS:.0f} ms")
    print(f"  baseline p99 (C1 A): 0.00830 ms  (bare engine, no telemetry)")
    print(f"  instrumented p99   : {p99:.5f} ms")
    clears = p99 < LATENCY_TARGET_MS
    print(f"  VERDICT            : {'CLEARS' if clears else 'EXCEEDS'} the 200 ms target "
          f"({LATENCY_TARGET_MS / p99:,.0f}x headroom)")
    print(f"  overhead finding   : instrumentation adds ~{p99 - 0.0083:.4f} ms at p99 "
          f"(span/record cost) — absolute p99 stays ~{LATENCY_TARGET_MS / p99:,.0f}x under target.")
