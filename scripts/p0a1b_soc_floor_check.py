"""Diagnostic: size a SOC floor that keeps seeded-healthy demo vehicles in the
SOC band over which the no-false-positive property + the 315 V threshold were
validated, for an UNBOUNDED live run.

Run: cd src && PYTHONPATH=. ../.venv/Scripts/python.exe ../scripts/p0a1b_soc_floor_check.py

The Phase-2 reconciliation measured the healthy band over start SOC uniform(0.6,0.95)
drained <=1000 ticks -> worst-case end SOC ~0.27, pack min 322.91 V; 315 was chosen
8 V below that. A live loop runs SOC down to ~0 (and below, unbounded). A SOC floor
applied in the FleetManager layer (engine untouched) keeps the live loop inside that
validated band. This sizes it: which floor keeps long-run pack_min >= 322.91 with
zero P0A1B fires, across all 8 demo seeds, over a long run.
"""

import numpy as np

from dtc_registry import DTC_REGISTRY
from simulator import VehicleSimulator
from dashboard_config import DEMO_FLEET, DT

P0A1B = DTC_REGISTRY["P0A1B"]["triggers"]["pack_voltage"]["lt"]  # 315
VALIDATED_MIN = 322.91   # the Phase-2 observed healthy min that justified 315
TICKS = 10000            # ~1000 s simulated; far past SOC->0 (~2400 ticks)

seeds = [(vid, seed, fault) for (vid, seed, fault, _) in DEMO_FLEET]


def run_with_floor(seed, floor, ticks=TICKS):
    """Replicate FleetManager's layer-above clamp: after each tick, hold SOC >= floor.
    No fault injected here — we are measuring the pack_voltage (drain) channel only."""
    sim = VehicleSimulator("X", fault_profile=None, seed=seed)
    pmin = float("inf")
    fires = 0
    for _ in range(ticks):
        r = sim.tick(DT)
        pv = r["pack_voltage"]
        pmin = min(pmin, pv)
        if pv < P0A1B:
            fires += 1
        if floor is not None and sim.soc < floor:
            sim.soc = floor
    return pmin, fires


print(f"P0A1B threshold = {P0A1B} V; validated healthy min = {VALIDATED_MIN} V")
print(f"each cell: {len(seeds)} demo seeds x {TICKS} ticks (drain channel only)\n")

print(f"{'floor':>7} | {'pack_min':>9} | {'P0A1B fires (per seed)':>24} | verdict")
print("-" * 70)
for floor in [None, 0.25, 0.30, 0.35, 0.40, 0.50, 0.60]:
    mins = []
    fires = []
    for vid, seed, _ in seeds:
        pmin, f = run_with_floor(seed, floor)
        mins.append(pmin)
        fires.append(f)
    overall_min = min(mins)
    total_fires = sum(fires)
    ok = (total_fires == 0) and (overall_min >= VALIDATED_MIN)
    label = "none" if floor is None else f"{floor:.2f}"
    verdict = "OK (in validated band, 0 fires)" if ok else (
        "FIRES" if total_fires else f"0 fires but min {overall_min:.1f} < {VALIDATED_MIN}")
    print(f"{label:>7} | {overall_min:>9.2f} | {str(fires):>24} | {verdict}")

print("\nPer-seed start SOC (context):")
for vid, seed, fault in seeds:
    s = VehicleSimulator("X", fault_profile=None, seed=seed)
    tag = "healthy" if fault is None else fault
    print(f"  seed {seed:>5} ({tag}): start SOC {s.soc:.4f}")
