"""Phase 6 — Checkpoint 1: prove the three measurement hazards on the running system
BEFORE any Grafana/collector work is built on top.

Run (from repo root):
    .venv/Scripts/python.exe scripts/phase6_checkpoint1.py

Produces three sections, each a load-bearing pre-condition for Phase 6:

  A. BASELINE ENGINE.run() p99 — measured on the real RuleBasedDiagnostics, with NO
     OpenTelemetry in the path. Tells us whether the < 200 ms target is comfortable or
     tight before instrumentation overhead is added (C3a).

  B. DETECTION LATENCY IS READ, NOT RECOMPUTED — ticks the real DEMO_FLEET and shows,
     per detection, that the telemetry layer's latency equals the event's stored
     `detection_latency_ticks`, which equals `raw_first_fire_at - injected_at`. No fresh
     latency is computed anywhere (C1).

  C. false_positive vs incidental_dtcs ARE DISTINCT — classifies every rule-based DTC
     the demo fleet fires and shows EV-0006 (CellImbalance) contributing to INCIDENTAL
     (its secondary P0A1B) and ZERO to false_positive, while the four healthy vehicles
     contribute ZERO to both (C2 / invariant 9).

Flat imports resolved by adding src/ to sys.path (this script lives outside pytest).
"""

import os
import sys
import time

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from dashboard_config import DEMO_FLEET  # noqa: E402
from diagnostic_engine import RuleBasedDiagnostics  # noqa: E402
from fleet_manager import FleetManager  # noqa: E402
from simulator import VehicleSimulator  # noqa: E402
from telemetry import (  # noqa: E402
    classify_rule_event,
    detection_latency_from_event,
    verify_latency_is_read,
    FALSE_POSITIVE,
    INCIDENTAL,
    CORRECT_DETECTION,
)

LATENCY_TARGET_MS = 200.0


# ── A. Baseline ENGINE.run() p99 ─────────────────────────────────────────────────
def section_a_baseline_p99(n=200_000):
    print("=" * 78)
    print("A. BASELINE  RuleBasedDiagnostics.run()  LATENCY  (no OpenTelemetry)")
    print("=" * 78)

    engine = RuleBasedDiagnostics()
    # A realistic mix of readings: a healthy one (no DTCs match) and a faulted one
    # (several conditions evaluated/append). run() iterates all 8 DTCs either way, so the
    # cost is essentially value-independent — we cycle a few readings to be representative.
    healthy = VehicleSimulator("BENCH-H", seed=0).tick()
    faulted = dict(healthy)
    faulted.update({"pack_voltage": 300.0, "coolant_flow_rate": 2.0,
                    "inverter_efficiency": 0.5, "bms_heartbeat": None})
    readings = [healthy, faulted]

    # Warm up (JIT-free Python, but warms caches/branch predictors and the numpy import).
    for r in readings * 1000:
        engine.run(r)

    samples = np.empty(n, dtype=np.float64)
    for i in range(n):
        r = readings[i & 1]
        t0 = time.perf_counter()
        engine.run(r)
        samples[i] = (time.perf_counter() - t0) * 1000.0  # ms

    p50, p90, p99, p999, mx = np.percentile(samples, [50, 90, 99, 99.9, 100])
    print(f"  samples         : {n:,}")
    print(f"  mean            : {samples.mean():.5f} ms")
    print(f"  p50             : {p50:.5f} ms")
    print(f"  p90             : {p90:.5f} ms")
    print(f"  p99             : {p99:.5f} ms")
    print(f"  p99.9           : {p999:.5f} ms")
    print(f"  max             : {mx:.5f} ms")
    headroom = LATENCY_TARGET_MS / p99 if p99 > 0 else float("inf")
    verdict = "COMFORTABLE" if p99 < LATENCY_TARGET_MS * 0.5 else "TIGHT"
    print(f"  target          : < {LATENCY_TARGET_MS:.0f} ms")
    print(f"  p99 vs target   : {verdict}  (~{headroom:,.0f}x headroom)")
    print()
    return p99


# ── Shared: tick the real demo fleet ─────────────────────────────────────────────
def _run_demo_fleet(n_ticks):
    fleet = FleetManager()  # default roster=DEMO_FLEET, soc_floor=DEMO_SOC_FLOOR
    for _ in range(n_ticks):
        fleet.tick_all()
    return fleet


def _fault_name(fleet, vid):
    return fleet.vehicles[vid].pending_fault_name


# ── B. Detection latency is READ, not recomputed ─────────────────────────────────
def section_b_latency_is_read(fleet):
    print("=" * 78)
    print("B. DETECTION LATENCY IS READ  (raw_first_fire_at - injected_at), NOT RECOMPUTED")
    print("=" * 78)
    print(f"  fleet ticked to t={fleet.tick_count}\n")
    print(f"  {'vehicle':<9}{'fault':<26}{'src':<11}{'dtc/field':<12}"
          f"{'inj':>5}{'raw_fire':>9}{'stored':>8}{'raw-inj':>8}  match")
    print(f"  {'-'*9}{'-'*26}{'-'*11}{'-'*12}{'-'*5}{'-'*9}{'-'*8}{'-'*8}  -----")

    all_match = True
    any_rows = False
    for vid, state in fleet.vehicles.items():
        fname = state.pending_fault_name or "healthy"
        for ev in state.tracker.events:
            stored, recomputed = verify_latency_is_read(ev)
            if stored is None and recomputed is None:
                continue  # healthy / unknown injection — latency undefined by design
            any_rows = True
            match = stored == recomputed
            all_match = all_match and match
            key = ev.get("code") or ev.get("field") or "?"
            print(f"  {vid:<9}{fname:<26}{ev['source']:<11}{key:<12}"
                  f"{str(ev.get('injected_at')):>5}{str(ev.get('raw_first_fire_at')):>9}"
                  f"{str(stored):>8}{str(recomputed):>8}  {'OK' if match else 'MISMATCH'}")

    print()
    print(f"  detection_latency_from_event() reads event['detection_latency_ticks'] "
          f"directly (no recompute).")
    print(f"  every stored latency == raw_first_fire_at - injected_at : "
          f"{'YES (all rows)' if all_match and any_rows else 'NO'}")
    print()
    return all_match and any_rows


# ── C. false_positive vs incidental_dtcs are distinct ────────────────────────────
def section_c_fp_vs_incidental(fleet):
    print("=" * 78)
    print("C. false_positive (STRICT)  vs  incidental_dtcs  —  TWO DISTINCT SERIES")
    print("=" * 78)
    print(f"  fleet ticked to t={fleet.tick_count}\n")

    fp = {}          # vehicle -> [codes]   (rule DTC on an UN-injected vehicle)
    incidental = {}  # vehicle -> [codes]   (non-designed rule DTC on a FAULTED vehicle)
    designed = {}    # vehicle -> [codes]   (the vehicle's own injected fault's DTC)

    for vid, state in fleet.vehicles.items():
        fname = state.pending_fault_name
        seen = set()
        for ev in state.tracker.events:
            if ev["source"] != "rule_based":
                continue
            code = ev["code"]
            if code in seen:
                continue  # one row per distinct DTC code per vehicle
            seen.add(code)
            kind = classify_rule_event(fname, code)
            bucket = {FALSE_POSITIVE: fp, INCIDENTAL: incidental,
                      CORRECT_DETECTION: designed}[kind]
            bucket.setdefault(vid, []).append(code)

    print(f"  {'vehicle':<9}{'fault':<26}{'designed DTC':<16}{'incidental':<14}{'FALSE POS'}")
    print(f"  {'-'*9}{'-'*26}{'-'*16}{'-'*14}{'-'*9}")
    for vid, state in fleet.vehicles.items():
        fname = state.pending_fault_name or "healthy"
        print(f"  {vid:<9}{fname:<26}"
              f"{','.join(designed.get(vid, [])) or '-':<16}"
              f"{','.join(incidental.get(vid, [])) or '-':<14}"
              f"{','.join(fp.get(vid, [])) or '-'}")

    total_fp = sum(len(v) for v in fp.values())
    total_inc = sum(len(v) for v in incidental.values())
    print()
    print(f"  fleet-wide false_positive count : {total_fp}   "
          f"(strict: rule DTC on a vehicle with NO injected fault)")
    print(f"  fleet-wide incidental_dtc count : {total_inc}   "
          f"(non-designed rule DTC on a faulted vehicle)")
    ev6 = fleet.vehicles.get("EV-0006")
    if ev6 is not None:
        print(f"\n  EV-0006 (CellImbalance) — the load-bearing case:")
        print(f"    designed    : {designed.get('EV-0006', [])}   (P1A15 = its injected code)")
        print(f"    incidental  : {incidental.get('EV-0006', [])}   (P0A1B = secondary cascade)")
        print(f"    FALSE POS   : {fp.get('EV-0006', [])}   (MUST be empty — it is genuinely faulted)")
    healthy_fp = {v: c for v, c in fp.items()
                  if _fault_name(fleet, v) is None}
    print(f"\n  healthy vehicles contributing to false_positive : "
          f"{healthy_fp if healthy_fp else 'NONE'}")
    print()
    return total_fp == 0 and incidental.get("EV-0006") == ["P0A1B"]


if __name__ == "__main__":
    print(f"\nDEMO_FLEET roster ({len(DEMO_FLEET)} vehicles): "
          f"{[(v[0], v[2] or 'healthy', v[3]) for v in DEMO_FLEET]}\n")

    p99 = section_a_baseline_p99()

    # EV-0006's incidental P0A1B fires at t≈700+ (the pack-sag crossing), so tick well
    # past it. Healthy vehicles stay green throughout (DEMO_SOC_FLOOR holds their band).
    fleet = _run_demo_fleet(900)

    b_ok = section_b_latency_is_read(fleet)
    c_ok = section_c_fp_vs_incidental(fleet)

    print("=" * 78)
    print("CHECKPOINT 1 SUMMARY")
    print("=" * 78)
    print(f"  A baseline p99 measured       : {p99:.5f} ms  (target < {LATENCY_TARGET_MS:.0f} ms)")
    print(f"  B latency is READ (C1)        : {'PASS' if b_ok else 'FAIL'}")
    print(f"  C FP vs incidental split (C2) : {'PASS' if c_ok else 'FAIL'}")
    print()
