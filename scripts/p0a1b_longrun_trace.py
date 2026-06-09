"""Diagnostic: why seeded-HEALTHY demo vehicles trip P0A1B on a long-running server.

Run:  cd src && ../.venv/Scripts/python.exe ../scripts/p0a1b_longrun_trace.py

The bounded Phase-2/4 tests run <=1000 ticks; the live FleetManager loop runs
unbounded. This script reproduces the long-run regime against the REAL seeded
VehicleSimulator (no re-derivation — it calls sim.tick()) and answers the brief's
question: which hypothesis holds, and at exactly what tick / SOC does pack_voltage
cross the 315 V P0A1B threshold.
"""

import numpy as np

from calibration import CALIBRATION
from dtc_registry import DTC_REGISTRY
from simulator import VehicleSimulator, CELLS_IN_SERIES
from dashboard_config import DEMO_FLEET, DT

P0A1B = DTC_REGISTRY["P0A1B"]["triggers"]["pack_voltage"]["lt"]  # 315
curve_soc = np.array(CALIBRATION["discharge_curve_soc"])
curve_v = np.array(CALIBRATION["discharge_curve_voltage"])
std = CALIBRATION["nominal_cell_voltage_std"]


def banner(s):
    print("\n" + "=" * 78 + f"\n{s}\n" + "=" * 78)


# ---------------------------------------------------------------------------
banner("1. The deterministic floor vs the P0A1B threshold")
floor_cell = curve_v[0]                      # interp clamps below SOC=0 to this
floor_pack = floor_cell * CELLS_IN_SERIES
thr_cell = P0A1B / CELLS_IN_SERIES
print(f"discharge curve SOC : {list(curve_soc)}")
print(f"discharge curve V   : {list(curve_v)}  (cell V)")
print(f"per-cell noise std  : {std} V  -> pack-noise std {std * CELLS_IN_SERIES:.2f} V")
print()
print(f"P0A1B threshold     : {P0A1B} V pack  =  {thr_cell:.5f} V/cell")
print(f"curve floor (SOC=0) : {floor_cell:.4f} V/cell  =  {floor_pack:.2f} V pack")
print()
gap_cell = floor_cell - thr_cell
gap_pack = floor_pack - P0A1B
print(f"threshold is BELOW the curve floor by {gap_pack:.2f} V pack ({gap_cell:.4f} V/cell).")
print(f"=> deterministic pack_voltage NEVER reaches {P0A1B} V, even at SOC=0 (or below).")
z_needed = -gap_cell / std
from math import erf
p_fire = 0.5 * (1 + erf(z_needed / 2 ** 0.5))
print(f"=> only a noise dip of {z_needed:.2f} sigma fires it; P(fire | SOC<=0) ~ {p_fire*100:.1f}% per tick.")


# ---------------------------------------------------------------------------
banner("2. Real seeded SOC trace per healthy demo vehicle (calls sim.tick())")
healthy = [(vid, seed) for (vid, seed, fault, _) in DEMO_FLEET if fault is None]
MAX_TICKS = 4000

for vid, seed in healthy:
    sim = VehicleSimulator(vid, fault_profile=None, seed=seed)
    soc0 = sim.soc
    first_fire = None
    soc_at_fire = None
    drains = []
    checkpoints = {}
    prev_soc = sim.soc
    for i in range(1, MAX_TICKS + 1):
        r = sim.tick(DT)
        drains.append(prev_soc - sim.soc)
        prev_soc = sim.soc
        pv = r["pack_voltage"]
        # capture pack_voltage near a few SOC checkpoints
        for cp in (0.6, 0.4, 0.2, 0.05, 0.0):
            if cp not in checkpoints and r["soc"] <= cp:
                checkpoints[cp] = (i, pv)
        if first_fire is None and pv < P0A1B:
            first_fire = i
            soc_at_fire = r["soc"]
    mean_drain = float(np.mean(drains))
    print(f"\n{vid} (seed={seed})")
    print(f"  start SOC            : {soc0:.4f}")
    print(f"  mean drain/tick      : {mean_drain:.6f} SOC  (~{mean_drain*100:.4f}%/tick)")
    print(f"  ticks to SOC<=0      : "
          f"{checkpoints.get(0.0, ('>%d' % MAX_TICKS, None))[0]}")
    print(f"  pack_voltage at SOC checkpoints (tick, pack_V):")
    for cp in (0.6, 0.4, 0.2, 0.05, 0.0):
        if cp in checkpoints:
            tk, pv = checkpoints[cp]
            print(f"      SOC~{cp:<4}: tick {tk:>5}  pack {pv:.2f} V")
    if first_fire is not None:
        print(f"  >>> FIRST P0A1B fire : tick {first_fire}  (SOC={soc_at_fire:.4f}, "
              f"wall-clock ~{first_fire * 0.1:.0f}s @ TICK_INTERVAL=0.1)")
    else:
        print(f"  >>> P0A1B never fired within {MAX_TICKS} ticks")


# ---------------------------------------------------------------------------
banner("3. Once SOC<=0: is the pack genuinely 'drained-low', or is it noise?")
# Take EV-0001, drain it well past SOC=0, then sample 2000 ticks in the clamped regime.
sim = VehicleSimulator("EV-0001", fault_profile=None, seed=0)
while sim.soc > -0.05:
    sim.tick(DT)
samp = [sim.tick(DT)["pack_voltage"] for _ in range(2000)]
samp = np.array(samp)
fire_rate = float((samp < P0A1B).mean())
print(f"in the SOC<=0 clamped regime (EV-0001, 2000 ticks):")
print(f"  pack_voltage  min={samp.min():.2f}  mean={samp.mean():.2f}  max={samp.max():.2f}")
print(f"  fraction < {P0A1B} V (P0A1B fires) : {fire_rate*100:.1f}% of ticks")
print(f"  => the pack sits at the curve floor ~{floor_pack:.1f} V and STRADDLES 315 on noise.")
print(f"  => not a clean 'empty pack reads low'; it's noise around a floor just above 315.")
