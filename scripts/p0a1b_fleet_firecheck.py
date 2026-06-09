"""Confirm the fix on the REAL FleetManager: first P0A1B fire tick per vehicle,
with the SOC floor ON vs OFF. Healthy vehicles must NEVER fire; the fix must not
break the intended fault cascade.

Run: cd src && PYTHONPATH=. ../.venv/Scripts/python.exe ../scripts/p0a1b_fleet_firecheck.py
"""

from dashboard_config import DEMO_FLEET, DEMO_SOC_FLOOR
from fleet_manager import FleetManager

TICKS = 6000
fault_of = {vid: fault for (vid, _seed, fault, _inj) in DEMO_FLEET}


def first_p0a1b(soc_floor):
    fleet = FleetManager(soc_floor=soc_floor)
    first = {}
    for _ in range(TICKS):
        fleet.tick_all()
        t = fleet.tick_count
        for vid, st in fleet.vehicles.items():
            if vid in first:
                continue
            if any(d["dtc"] == "P0A1B" for d in st.latest_rule_dtcs):
                first[vid] = t
    return first


for label, floor in [("floor OFF (bare drain)", None),
                     (f"floor ON  ({DEMO_SOC_FLOOR})", DEMO_SOC_FLOOR)]:
    first = first_p0a1b(floor)
    print(f"\n=== {label} — first P0A1B fire within {TICKS} ticks ===")
    for vid, _seed, fault, _inj in DEMO_FLEET:
        tag = fault or "healthy"
        when = first.get(vid)
        mark = "  <-- HEALTHY FALSE POSITIVE" if (when and fault is None) else ""
        print(f"  {vid:8} [{tag:24}] : "
              f"{('t=%d' % when) if when else 'never'}{mark}")
