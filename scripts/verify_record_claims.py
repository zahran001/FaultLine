"""Verify the concrete numeric claims going into README_PROJECT_RECORD.md's Phase 5
record against running code. Read-only; prints PASS/FAIL per claim.

Run (from the repo root):  .venv/Scripts/python.exe scripts/verify_record_claims.py

Flat imports (no package) are resolved by putting src/ on sys.path below — the same
bootstrap the phase6_checkpoint*.py scripts use, so this runs from anywhere. (pytest gets
src/ via pyproject's pythonpath instead; neither changes the modules' bare-import style or
adds a package, per the CLAUDE.md flat-import contract.)
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import dashboard_config as cfg  # noqa: E402
import slope_detector_config as slope  # noqa: E402
from simulator import VehicleSimulator  # noqa: E402
from diagnostic_engine import RuleBasedDiagnostics, StatisticalDiagnostics  # noqa: E402
from event_tracker import DTCEventTracker, SOURCE_ZSCORE  # noqa: E402
import api  # noqa: E402

SEEDS = [0, 1, 7, 42, 99, 314, 2718, 31415]
TICKS = 600


def check(name, got, expect):
    ok = got == expect
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}: {got!r}" + ("" if ok else f"  (expected {expect!r})"))
    return ok


print("== config constants ==")
check("DT", cfg.DT, 1.0)
check("TICK_INTERVAL", cfg.TICK_INTERVAL, 0.1)
check("EVENT_PERSISTENCE_CROSSINGS", cfg.EVENT_PERSISTENCE_CROSSINGS, 3)
check("RULE_EVENT_OPEN_CROSSINGS", cfg.RULE_EVENT_OPEN_CROSSINGS, 1)
check("RULE_EVENT_CLOSE_CROSSINGS", cfg.RULE_EVENT_CLOSE_CROSSINGS, 5)
check("DEMO_SOC_FLOOR", cfg.DEMO_SOC_FLOOR, 0.35)
check("slope WINDOW/THRESH/CONSEC",
      (slope.SLOPE_WINDOW, slope.SLOPE_THRESHOLD, slope.CONSECUTIVE_CROSSINGS), (30, 0.30, 3))
print(f"  close-gate(5) > slope consec({slope.CONSECUTIVE_CROSSINGS}): "
      f"{cfg.RULE_EVENT_CLOSE_CROSSINGS > slope.CONSECUTIVE_CROSSINGS}")

print("\n== API surface ==")
routes = sorted(r.path for r in api.app.routes if getattr(r, 'methods', None) and 'GET' in r.methods
                and r.path.startswith(('/fleet', '/vehicle')))
print(f"  GET endpoints: {routes}")

print("\n== z-score smoothing (Decision D): raw anomaly flags vs smoothed z-score EVENTS, healthy ==")
raw_per_veh, evt_per_veh = [], []
for seed in SEEDS:
    sim = VehicleSimulator(f"H-{seed}", fault_profile=None, seed=seed)
    stat = StatisticalDiagnostics()
    tracker = DTCEventTracker()
    rule = RuleBasedDiagnostics()
    raw = 0
    for _ in range(TICKS):
        r = sim.tick()
        stat.update(r)
        anoms = stat.detect_anomalies(f"H-{seed}")
        trends = stat.detect_trend(f"H-{seed}", fields=("temperature",))
        raw += len(anoms)
        tracker.update(rule.run(r), anoms, trends, t=r["timestamp"], injected_at=None)
    z_events = sum(1 for e in tracker.events if e["source"] == SOURCE_ZSCORE)
    raw_per_veh.append(raw)
    evt_per_veh.append(z_events)
print(f"  raw anomaly flags/veh : mean {sum(raw_per_veh)/len(raw_per_veh):.2f}  per-seed {raw_per_veh}")
print(f"  smoothed z EVENTS/veh : mean {sum(evt_per_veh)/len(evt_per_veh):.2f}  per-seed {evt_per_veh}")
print(f"  => claim '~5.75 raw vs 0 smoothed events': "
      f"raw~{sum(raw_per_veh)/len(raw_per_veh):.2f}, smoothed total {sum(evt_per_veh)}")
