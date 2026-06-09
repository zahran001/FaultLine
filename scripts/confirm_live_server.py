"""Confirm the SOC-floor fix against the RUNNING uvicorn server (the brief's
"confirm by watching the running server" step). Polls /fleet past the tick where
healthy vehicles fired P0A1B WITHOUT the floor (~t=2154-2379), and asserts the
seeded-healthy vehicles stay green the whole time.

Run (server must be up on :8000):
  ../.venv/Scripts/python.exe ../scripts/confirm_live_server.py
"""

import json
import time
import urllib.request

BASE = "http://127.0.0.1:8000"
HEALTHY = {"EV-0001", "EV-0002", "EV-0003", "EV-0008"}
DURATION_S = 280          # past t~2400 (server ticks 0.1 s/tick), the no-floor fire band
POLL_EVERY_S = 20


def get(path):
    with urllib.request.urlopen(BASE + path, timeout=5) as r:
        return json.loads(r.read())


violations = []
last = None
t0 = time.time()
print(f"polling {BASE}/fleet every {POLL_EVERY_S}s for {DURATION_S}s "
      f"(past the no-floor healthy-fire band t~2154-2379)\n")
while time.time() - t0 < DURATION_S:
    fleet = get("/fleet")
    last = fleet
    by = {v["id"]: v for v in fleet["vehicles"]}
    healthy_states = {vid: by[vid]["status"] for vid in HEALTHY}
    bad = {vid: s for vid, s in healthy_states.items() if s != "green"}
    if bad:
        violations.append((fleet["tick"], bad))
    print(f"  tick {fleet['tick']:>5}: "
          + "  ".join(f"{v['id']}={v['status']}" for v in fleet["vehicles"]))
    time.sleep(POLL_EVERY_S)

print(f"\nfinal tick: {last['tick']}")
print("healthy vehicles non-green at any poll:",
      violations if violations else "NONE  (PASS)")

# Spot-check a healthy vehicle's pack_voltage + dtcs at the end.
ev1 = get("/vehicle/EV-0001/readings")["reading"]
ev1_dtcs = get("/vehicle/EV-0001/dtcs")["detections"]
print(f"\nEV-0001 @tick {last['tick']}: pack_voltage={ev1['pack_voltage']} V, "
      f"soc={ev1['soc']}, P0A1B fired={any(d.get('dtc')=='P0A1B' for d in ev1_dtcs)}")
# EV-0006 (CellImbalance) for contrast: its P0A1B is profile-driven, expected present.
ev6_dtcs = get("/vehicle/EV-0006/dtcs")["detections"]
print(f"EV-0006 (CellImbalance) active DTCs: "
      f"{sorted(d['dtc'] for d in ev6_dtcs if d.get('dtc'))}")
