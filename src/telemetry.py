"""Phase 6 — OpenTelemetry instrumentation (a LAYER ON TOP of the frozen engine).

This module instruments the live FaultLine backend WITHOUT modifying any Phase 0-5
code. It is split deliberately into two parts:

  1. PURE MEASUREMENT FOUNDATION (importable with no side effects) — the
     profile->designed-DTC map, the false_positive vs incidental_dtcs classifier, and
     the detection-latency READ helper. These have no OpenTelemetry dependency and run
     in the Phase 6 checkpoint-1 script and the metric tests. Importing this module
     does NOT start an exporter or touch global state.

  2. OTEL WIRING (functions, called only by the api_telemetry entrypoint) —
     setup_metrics() builds a MeterProvider + OTLP exporter; instrument_fleet() wraps
     the existing rule engine + tick loop and registers an observable gauge. All
     wrapping; zero edits to diagnostic_engine.py / fleet_manager.py / api.py.

THE THREE MEASUREMENT HAZARDS THIS PHASE FENCES OFF (see docs/README_PROJECT_RECORD.md
"Phase 6 metric foundation" and CLAUDE.md):

  C1 — Detection latency is READ, never recomputed. Phase 5 established
       `raw_first_fire_at` (the detector's true first crossing) decoupled from the
       flickering `opened_at`, and stored `detection_latency_ticks = raw_first_fire_at
       - injected_at` ON the event. We read that field. Recomputing a fresh latency in
       the telemetry layer would reintroduce the ~89-tick bug Phase 5 fixed to ~2.
       `detection_latency_from_event` only reads; `verify_latency_is_read` proves the
       stored value equals raw_first_fire_at - injected_at (used by the checkpoint/tests,
       NOT inlined in the hot path).

  C2 — false_positive and incidental_dtcs are DISTINCT and never conflated. Per the
       recorded metric foundation (invariant 9):
         false_positive : a rule-based DTC fired on a vehicle with NO injected fault.
                          EV-0006 (CellImbalance) contributes ZERO — it is faulted.
         incidental_dtc : a rule-based DTC on a genuinely-FAULTED vehicle that is NOT
                          that vehicle's designed code. EV-0006's secondary P0A1B lives
                          here; expected and healthy, NEVER a false positive.
       `classify_rule_event` returns exactly one of {"designed","incidental",
       "false_positive"} and the two are emitted as two separately-named counters.

Flat imports (no package): resolved via pythonpath = ["src"] in pyproject.toml.
"""

import time

import fault_profiles
from dtc_registry import DTC_REGISTRY


# ─────────────────────────────────────────────────────────────────────────────
#  PART 1 — PURE MEASUREMENT FOUNDATION  (no OpenTelemetry; no side effects)
# ─────────────────────────────────────────────────────────────────────────────

# Profile class name -> the rule-based DTC code(s) that profile is DESIGNED to trip.
# PROVENANCE: transcribed from the fault_profiles.py docstrings (their "its designed
# code" statements), cross-checked against dtc_registry trigger fields. This is the
# single piece of profile->DTC metadata the metric layer needs and it lives here, with
# the consumer, validated against the registry at import so a typo can't silently
# misclassify (mirrors the registry's own self-validation convention).
#
#   CoolantBlockage        -> P0C73  (coolant_flow_rate < 4.0)
#   CellImbalance          -> P1A15  (cell_voltage_delta > 0.05); its later P0A1B is
#                                     INCIDENTAL by design (multi-DTC; see the record)
#   HVIsolationFault       -> P0AA6  (isolation_resistance < 500)
#   SensorDropout          -> U0100  (bms_heartbeat == None)
#   ChargePortOverheat     -> P0C2E  (charge_port_temp > 85)
#   InverterDegradation    -> P0A78  (inverter_efficiency < 0.88)
#   ThermalRunawayPrecursor-> (none) slope-detector target; deliberately NO rule DTC to
#                                     escalate into, so ANY rule DTC on it is incidental
DESIGNED_DTCS = {
    "CoolantBlockage": frozenset({"P0C73"}),
    "CellImbalance": frozenset({"P1A15"}),
    "HVIsolationFault": frozenset({"P0AA6"}),
    "SensorDropout": frozenset({"U0100"}),
    "ChargePortOverheat": frozenset({"P0C2E"}),
    "InverterDegradation": frozenset({"P0A78"}),
    "ThermalRunawayPrecursor": frozenset(),
}

# Classification outcomes for a rule-based DTC firing on a vehicle.
CORRECT_DETECTION = "designed"   # the vehicle's own injected fault's DTC — neither metric
INCIDENTAL = "incidental"        # non-injected DTC on a FAULTED vehicle (incidental_dtcs)
FALSE_POSITIVE = "false_positive"  # any rule DTC on an UN-injected (healthy) vehicle


def _validate_designed_dtcs():
    """Fail loudly at import if the profile->DTC map drifts from the real code.

    Every key must be a real fault_profiles class; every code a real registry DTC.
    Keeps this metric-layer metadata honest against the single sources of truth.
    """
    for profile_name, codes in DESIGNED_DTCS.items():
        assert hasattr(fault_profiles, profile_name), (
            f"DESIGNED_DTCS references unknown profile {profile_name!r}"
        )
        for code in codes:
            assert code in DTC_REGISTRY, (
                f"DESIGNED_DTCS[{profile_name!r}] references unknown DTC {code!r}"
            )


_validate_designed_dtcs()


def classify_rule_event(pending_fault_name, code):
    """Classify one rule-based DTC firing as designed / incidental / false_positive.

    pending_fault_name : the vehicle's injected fault profile name, or None if healthy
                         (this is exactly FleetManager's per-vehicle `pending_fault_name`).
    code               : the rule-based DTC code that fired (e.g. "P0A1B").

    STRICT split (C2 / invariant 9):
      - healthy vehicle (pending_fault_name is None)  -> FALSE_POSITIVE  (always)
      - faulted vehicle, code is its designed DTC      -> CORRECT_DETECTION
      - faulted vehicle, code is NOT its designed DTC  -> INCIDENTAL
    EV-0006 (CellImbalance): P1A15 -> CORRECT_DETECTION, P0A1B -> INCIDENTAL,
    contributing ZERO to false_positive — the property this split exists to protect.
    """
    if pending_fault_name is None:
        return FALSE_POSITIVE
    if code in DESIGNED_DTCS.get(pending_fault_name, frozenset()):
        return CORRECT_DETECTION
    return INCIDENTAL


def detection_latency_from_event(event):
    """READ the detector's true detection latency off an event record (C1).

    Returns the stored `detection_latency_ticks` (== raw_first_fire_at - injected_at,
    computed once by the DTCEventTracker at open time from the RAW first-fire tick) or
    None if the fault's injection tick was unknown. This NEVER recomputes a latency —
    recomputation is precisely the ~89-tick bug Phase 5 fixed. Smoothing/hysteresis can
    therefore never widen or narrow what this reports.
    """
    return event.get("detection_latency_ticks")


def verify_latency_is_read(event):
    """Prove the stored latency IS raw_first_fire_at - injected_at (not a recomputation).

    Returns (stored, recomputed_from_raw) so a caller (the checkpoint script / a test)
    can assert they are equal — demonstrating the metric reads the existing field rather
    than deriving a fresh, drift-prone number. Returns (None, None) when injected_at is
    unknown (healthy vehicles), where latency is undefined by design.
    """
    injected_at = event.get("injected_at")
    raw_first = event.get("raw_first_fire_at")
    stored = event.get("detection_latency_ticks")
    if injected_at is None or raw_first is None:
        return (stored, None)
    return (stored, raw_first - injected_at)


# ─────────────────────────────────────────────────────────────────────────────
#  PART 2 — OPENTELEMETRY WIRING  (called only by the api_telemetry entrypoint)
# ─────────────────────────────────────────────────────────────────────────────

# Instrument names. Chosen with explicit unit suffixes and NO OTel `unit=` param so the
# collector's prometheus exporter maps them to predictable series (verified at
# checkpoint 2 by curling the collector /metrics — names below are confirmed there):
#   faultline_engine_run_duration_ms      -> _bucket/_sum/_count   (Metric 1)
#   faultline_false_positive_dtc          -> _total                (Metric 2, STRICT)
#   faultline_incidental_dtc              -> _total                (distinct series, C2)
#   faultline_detection_latency_ticks     -> _bucket/_sum/_count   (Metric 3)
#   faultline_active_fault_count          -> gauge                 (Metric 4)
M_ENGINE_DURATION = "faultline_engine_run_duration_ms"
M_FALSE_POSITIVE = "faultline_false_positive_dtc"
M_INCIDENTAL = "faultline_incidental_dtc"
M_DETECTION_LATENCY = "faultline_detection_latency_ticks"
M_ACTIVE_FAULTS = "faultline_active_fault_count"

SERVICE_NAME = "faultline-backend"

# Fine-grained buckets (ms) for the engine-run histogram. RuleBasedDiagnostics.run() is
# a tiny loop over 8 DTCs (~microseconds), so the default OTel buckets (first edge 5 ms)
# would collapse every sample into one bucket and report a meaningless p99. These edges
# give real resolution around the measured latency AND straddle the 200 ms target so the
# p99-vs-200ms claim is honestly visible.
_ENGINE_MS_BUCKETS = [0.02, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 25, 50, 100, 200, 500]


def setup_metrics(endpoint=None, interval_ms=2000):
    """Build and install a MeterProvider that exports OTLP/HTTP to the collector.

    endpoint    : OTLP/HTTP metrics endpoint. Defaults to the standard
                  OTEL_EXPORTER_OTLP_ENDPOINT env wiring (collector on localhost:4318
                  when the docker-compose stack is up). Exporter import is lazy so the
                  PURE part of this module (Part 1) imports with neither the exporter
                  package nor a running collector.
    interval_ms : periodic export interval (2 s — a responsive live demo cadence).

    Returns the Meter. Counters/histograms use the SDK default CUMULATIVE temporality,
    which is what the prometheus exporter expects.
    """
    from opentelemetry import metrics
    from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
    from opentelemetry.sdk.metrics import MeterProvider
    from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
    from opentelemetry.sdk.metrics.view import (
        ExplicitBucketHistogramAggregation,
        View,
    )
    from opentelemetry.sdk.resources import SERVICE_NAME as RES_SERVICE_NAME, Resource

    exporter = OTLPMetricExporter(endpoint=endpoint) if endpoint else OTLPMetricExporter()
    reader = PeriodicExportingMetricReader(exporter, export_interval_millis=interval_ms)
    provider = MeterProvider(
        resource=Resource.create({RES_SERVICE_NAME: SERVICE_NAME}),
        metric_readers=[reader],
        views=[
            View(
                instrument_name=M_ENGINE_DURATION,
                aggregation=ExplicitBucketHistogramAggregation(_ENGINE_MS_BUCKETS),
            )
        ],
    )
    metrics.set_meter_provider(provider)
    return metrics.get_meter("faultline.phase6")


def instrument_fleet(fleet, meter):
    """Wrap a FleetManager's engine + tick loop and register live-state gauges.

    Pure WRAPPING (C4): replaces the bound `rule_engine.run` and `tick_all` attributes
    on the live instances with timing/recording wrappers, and registers an observable
    gauge that READS tracker.open_events() each collection cycle (C5). diagnostic_engine.py,
    fleet_manager.py and api.py are not edited at all. Idempotent.
    """
    if getattr(fleet, "_faultline_instrumented", False):
        return
    fleet._faultline_instrumented = True

    engine_duration = meter.create_histogram(
        M_ENGINE_DURATION,
        description="RuleBasedDiagnostics.run() wall-clock duration per vehicle",
    )
    false_positive = meter.create_counter(
        M_FALSE_POSITIVE,
        description="Rule-based DTCs fired on a vehicle with NO injected fault (strict)",
    )
    incidental = meter.create_counter(
        M_INCIDENTAL,
        description="Non-injected rule-based DTCs on a genuinely-faulted vehicle",
    )
    detection_latency = meter.create_histogram(
        M_DETECTION_LATENCY,
        description="injection->first-fire latency, READ from raw_first_fire_at - injected_at",
    )

    # — Metric 1: wrap rule_engine.run() to time each per-vehicle diagnostic run ——————
    # The reading carries vehicle_id, so the wrapper can label without any engine change.
    _orig_run = fleet.rule_engine.run

    def _timed_run(reading):
        start = time.perf_counter()
        result = _orig_run(reading)
        elapsed_ms = (time.perf_counter() - start) * 1000.0
        engine_duration.record(
            elapsed_ms, {"vehicle": reading.get("vehicle_id", "unknown")}
        )
        return result

    fleet.rule_engine.run = _timed_run

    # — Metrics 2 & 3: edge-record new events after each tick ————————————————————————
    # tracker.events is append-only; we remember how many we've recorded per vehicle and
    # process only the newly-appended ones. An event already carries its final
    # raw_first_fire_at / injected_at / detection_latency_ticks the instant it opens, so
    # reading them here is correct and final (we never recompute — C1).
    #
    # DEDUPE BY FIRST OCCURRENCE. Over a long run a detection legitimately FLICKERS —
    # the slope detector on an ever-rising ramp dips below threshold on I²R noise, the
    # event closes, and a later crossing re-opens a fresh event (with its own, much
    # larger, raw_first_fire_at - injected_at). Those re-opens are RE-detections, not the
    # fault's detection latency, and counting each one would skew the latency histogram
    # and inflate the FP/incidental counters. We record each metric ONCE per its natural
    # key — exactly the distinct-occurrence semantics the checkpoint-1 Section C used:
    #   detection latency : first event per (vehicle, source, code-or-field)
    #   FP / incidental   : first rule DTC per (vehicle, dtc)
    _recorded = {vid: 0 for vid in fleet.vehicles}
    _latency_seen = set()   # (vehicle, source, key)
    _class_seen = set()     # (vehicle, dtc)
    _orig_tick_all = fleet.tick_all

    def _instrumented_tick_all(*args, **kwargs):
        _orig_tick_all(*args, **kwargs)
        for vid, state in fleet.vehicles.items():
            events = state.tracker.events
            for event in events[_recorded.get(vid, 0):]:
                _record_new_event(
                    vid, state.pending_fault_name, event,
                    false_positive, incidental, detection_latency,
                    _latency_seen, _class_seen,
                )
            _recorded[vid] = len(events)

    fleet.tick_all = _instrumented_tick_all

    # — Metric 4: observable gauge reading the tracker open-event SSoT (C5) ———————————
    from opentelemetry.metrics import Observation

    def _active_faults(_options):
        return [
            Observation(len(state.tracker.open_events()), {"vehicle": vid})
            for vid, state in fleet.vehicles.items()
        ]

    meter.create_observable_gauge(
        M_ACTIVE_FAULTS,
        callbacks=[_active_faults],
        description="Open DTC events per vehicle (tracker open_events — the Phase 5 SSoT)",
    )


def _record_new_event(
    vehicle_id, pending_fault_name, event,
    false_positive, incidental, detection_latency,
    latency_seen, class_seen,
):
    """Record Metric 2/3 contributions for ONE newly-opened event, deduped by first
    occurrence (re-opens after a flicker gap are ignored — see _instrumented_tick_all).

    Metric 3 (detection latency) applies to any detection with a known injection tick,
    tagged by source so rule-based and slope latencies stay distinguishable. Metric 2
    (FP) and the distinct incidental series apply only to rule-based DTC events (a slope
    trend is a detection, not a "DTC"); they are routed by `classify_rule_event` so the
    two are never folded into one number (C2).
    """
    source = event.get("source")
    key = event.get("code") or event.get("field") or "unknown"

    latency = detection_latency_from_event(event)
    lat_key = (vehicle_id, source, key)
    if latency is not None and lat_key not in latency_seen:
        latency_seen.add(lat_key)
        detection_latency.record(
            latency, {"vehicle": vehicle_id, "source": source, "dtc": key}
        )

    if source != "rule_based":
        return
    code = event.get("code")
    cls_key = (vehicle_id, code)
    if cls_key in class_seen:
        return
    class_seen.add(cls_key)
    kind = classify_rule_event(pending_fault_name, code)
    attrs = {"vehicle": vehicle_id, "dtc": code}
    if kind == FALSE_POSITIVE:
        false_positive.add(1, attrs)
    elif kind == INCIDENTAL:
        incidental.add(1, attrs)
    # CORRECT_DETECTION: the vehicle's own designed DTC — neither metric counts it.
