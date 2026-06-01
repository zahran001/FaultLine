# Vehicle Diagnostic Automation Platform — Full Project Plan

---

## Architecture

```
OBD-II Fault Schema
        │
        ▼
Fault Simulator (Python)
[Physics-grounded, DTC-emitting]
        │
        ▼
Fault Injection Framework
[Parameterized fault profiles]
        │
        ▼
Diagnostic Engine
[Rule-based + statistical: slope (trends) & z-score (spikes)]
        │
        ▼
Pytest Automation Harness
[Fault injection → assert correct DTC output]
        │
        ├──────────────────────┐
        ▼                      ▼
FastAPI Backend          OpenTelemetry
        │                 + Grafana
        ▼
React Technician Dashboard
[Fault timeline, guided repair, DTC lookup]
```

---

## Phase 0 — NASA Battery Dataset Calibration

Do this first. It takes a day and gives you defensible simulator parameters for every subsequent phase.

**What the dataset is:**
NASA's Prognostics Center ran controlled charge/discharge cycles on 18650 Li-ion cells to failure, recording voltage, current, temperature, and impedance at each step. It's real lab data, widely cited in battery research.

**What you extract from it:**

```python
import pandas as pd
import numpy as np

# Load a NASA battery cycle file
df = pd.read_csv("nasa_battery_B0005.csv")

# Extract your calibration targets
nominal_voltage_mean = df[df["type"] == "discharge"]["voltage"].mean()
nominal_voltage_std  = df[df["type"] == "discharge"]["voltage"].std()
thermal_rise_per_amp = df.groupby("current")["temperature"].mean()
soc_discharge_curve  = df[["capacity", "voltage"]].dropna()

print(nominal_voltage_mean)   # ~3.8V per cell, scale to pack
print(thermal_rise_per_amp)   # your I²R constant comes from here
```

**Output of Phase 0:** A `calibration.py` config file with real-world grounded constants.

Note the discharge curve is stored as **two parallel arrays** (SOC breakpoints and their
corresponding voltages), not a list of pairs — this is what `np.interp` expects downstream.
Getting this shape right now avoids a refactor in Phase 2.

```python
# calibration.py

CALIBRATION = {
    "nominal_cell_voltage_mean": 3.81,
    "nominal_cell_voltage_std": 0.042,
    "thermal_rise_coefficient": 0.00083,   # derived from NASA data
    # Two parallel arrays for np.interp(soc, xp=soc, fp=voltage):
    "discharge_curve_soc":     [0.0, 0.2, 0.4, 0.6, 0.8, 1.0],
    "discharge_curve_voltage": [3.40, 3.55, 3.68, 3.78, 3.90, 4.10],
}
```

Every subsequent simulator parameter flows from this.

---

## Phase 1 — OBD-II Fault Schema

Define your fault vocabulary before writing any simulator code. Everything downstream references this.

> **RESOLVED (Decision 1):** The registry now spans **5 subsystems** — `battery_pack`,
> `thermal`, `motor_controller`, `bms`, and `charging` (added below as `P0C2E`). The
> "5 EV subsystems" prose is now accurate; keep the README consistent with this count.

> **Field-name contract:** the keys used in `triggers` below are the canonical sensor
> field names. The simulator output dict, the fault profiles, and the diagnostic engine
> **must all use these exact names**: `pack_voltage`, `cell_voltage_delta`,
> `coolant_flow_rate`, `inverter_efficiency`, `isolation_resistance`, `soh`,
> `bms_heartbeat`, `charge_port_temp`. (The original draft mixed `cell_delta`/`cell_voltage_delta`
> and `coolant_flow`/`coolant_flow_rate`, which meant several faults could never fire.)

```python
# dtc_registry.py

DTC_REGISTRY = {
    "P0A1B": {
        "description": "Battery Pack Voltage Weak",
        "subsystem": "battery_pack",
        "severity": "high",
        "triggers": {"pack_voltage": {"lt": 350}},
        "repair_procedure": [
            "Check individual cell voltages with multimeter",
            "Inspect high-voltage connectors for corrosion",
            "Run cell balance diagnostic routine",
            "Replace degraded cell module if delta > 50mV",
        ],
    },
    "P1A15": {
        "description": "Battery Cell Imbalance",
        "subsystem": "battery_pack",
        "severity": "medium",
        "triggers": {"cell_voltage_delta": {"gt": 0.05}},
        "repair_procedure": [
            "Log cell voltage readings across full pack",
            "Identify outlier cell group",
            "Run passive balancing cycle",
            "Flag for module replacement if imbalance persists",
        ],
    },
    "P0C73": {
        "description": "Cooling System Flow Insufficient",
        "subsystem": "thermal",
        "severity": "high",
        "triggers": {"coolant_flow_rate": {"lt": 4.0}},
        "repair_procedure": [
            "Check coolant level in reservoir",
            "Inspect pump for mechanical failure",
            "Check for blockage in cooling loop",
            "Verify thermal management controller output",
        ],
    },
    "P0A78": {
        "description": "Drive Motor Inverter Performance",
        "subsystem": "motor_controller",
        "severity": "high",
        "triggers": {"inverter_efficiency": {"lt": 0.88}},
        "repair_procedure": [
            "Check gate driver signals with oscilloscope",
            "Inspect IGBT module for thermal damage",
            "Verify DC bus voltage stability under load",
        ],
    },
    "P0AA6": {
        "description": "HV System Isolation Fault",
        "subsystem": "battery_pack",
        "severity": "critical",
        "triggers": {"isolation_resistance": {"lt": 500}},
        "repair_procedure": [
            "Isolate HV system immediately",
            "Run isolation resistance test on each segment",
            "Inspect HV harness for chafing or moisture ingress",
        ],
    },
    "P0AFA": {
        "description": "Battery State of Health Low",
        "subsystem": "battery_pack",
        "severity": "medium",
        "triggers": {"soh": {"lt": 0.75}},
        "repair_procedure": [
            "Run full capacity calibration cycle",
            "Compare measured vs rated capacity",
            "Schedule battery module replacement",
        ],
    },
    "U0100": {
        "description": "Lost Communication with BMS",
        "subsystem": "bms",
        "severity": "critical",
        "triggers": {"bms_heartbeat": {"eq": None}},
        "repair_procedure": [
            "Check CAN bus termination resistors",
            "Inspect BMS harness connector",
            "Flash BMS firmware if no hardware fault found",
        ],
    },
    "P0C2E": {
        "description": "Charge Port Temperature High",
        "subsystem": "charging",
        "severity": "high",
        "triggers": {"charge_port_temp": {"gt": 85}},   # °C
        "repair_procedure": [
            "Halt active charging session",
            "Inspect charge port connector for debris or corrosion",
            "Check charge port thermistor reading vs. ambient",
            "Verify charge cable seating and contactor engagement",
            "Inspect for coolant intrusion at port (liquid-cooled connectors)",
        ],
    },
}
```

This registry is your single source of truth. The simulator emits these codes, the diagnostic engine detects against these triggers, and the dashboard displays these repair procedures.

> **RESOLVED (Decision 2):** `P0A78` stays. The simulator now emits `inverter_efficiency`
> (below), and an `InverterDegradation` fault profile (Phase 2) drives it below the `0.88`
> threshold so the DTC is backed by both a profile and a test. Every *fault-driven* DTC has a
> corresponding fault profile; P0A1B (pack voltage weak) and P0AFA (SoH low) are threshold-only
> — they have triggers but no fault profile and no Phase 4 test.

---

## Phase 2 — Fault Simulator

Now build the simulator using your NASA-calibrated constants.

Two fixes from the original draft are baked in here:
1. The baseline `cell_voltage` no longer double-counts. The discharge curve **is** the
   voltage as a function of SOC; we interpolate it directly and add only sensor noise,
   rather than adding it on top of `nominal_cell_voltage_mean`.
2. Fault injection builds an explicit `reading` dict and passes that to the profile,
   instead of `locals()` (which leaked `self`, `dt`, etc. into the profile).

```python
# simulator.py

from calibration import CALIBRATION
from dtc_registry import DTC_REGISTRY
import numpy as np

class VehicleSimulator:
    def __init__(self, vehicle_id, fault_profile=None):
        self.vehicle_id = vehicle_id
        self.soc = np.random.uniform(0.6, 0.95)
        self.soh = np.random.uniform(0.80, 1.0)
        self.fault_profile = fault_profile  # injected fault, or None
        self.t = 0

    def tick(self, dt=1.0):
        # --- Healthy baseline from NASA calibration ---
        # Interpolate cell voltage directly from the SOC→voltage discharge curve.
        cell_voltage = (
            np.interp(
                self.soc,
                CALIBRATION["discharge_curve_soc"],
                CALIBRATION["discharge_curve_voltage"],
            )
            + np.random.normal(0, CALIBRATION["nominal_cell_voltage_std"])
        )
        pack_voltage  = cell_voltage * 96          # 96S pack
        current       = np.random.normal(120, 15)
        temperature   = (
            25 + current**2
            * CALIBRATION["thermal_rise_coefficient"]
            + np.random.normal(0, 0.5)
        )
        coolant_flow_rate   = np.random.normal(6.5, 0.3)
        cell_voltage_delta  = abs(np.random.normal(0, 0.008))
        isolation_resistance = np.random.normal(2000, 50)
        inverter_efficiency = np.random.normal(0.94, 0.01)
        charge_port_temp    = np.random.normal(35, 3)   # °C, idle/healthy port
        bms_heartbeat = True

        # --- Assemble the canonical reading dict (field names match the registry) ---
        reading = {
            "vehicle_id":           self.vehicle_id,
            "timestamp":            self.t,
            "pack_voltage":         pack_voltage,
            "current":              current,
            "temperature":          temperature,
            "coolant_flow_rate":    coolant_flow_rate,
            "cell_voltage_delta":   cell_voltage_delta,
            "isolation_resistance": isolation_resistance,
            "inverter_efficiency":  inverter_efficiency,
            "charge_port_temp":     charge_port_temp,
            "soc":                  self.soc,
            "soh":                  self.soh,
            "bms_heartbeat":        bms_heartbeat,
        }

        # --- Fault injection: profile mutates the reading in place ---
        if self.fault_profile:
            overrides = self.fault_profile.apply(reading, self.t)
            reading.update(overrides)

        # --- Advance state and round for output ---
        self.soc -= (current * dt) / 360_000
        self.t   += dt
        reading["soc"] = self.soc
        reading["timestamp"] = self.t

        return {
            "vehicle_id":           reading["vehicle_id"],
            "timestamp":            reading["timestamp"],
            "pack_voltage":         round(reading["pack_voltage"], 2),
            "current":              round(reading["current"], 2),
            "temperature":          round(reading["temperature"], 2),
            "coolant_flow_rate":    round(reading["coolant_flow_rate"], 2),
            "cell_voltage_delta":   round(reading["cell_voltage_delta"], 4),
            "isolation_resistance": round(reading["isolation_resistance"], 1),
            "inverter_efficiency":  round(reading["inverter_efficiency"], 4),
            "charge_port_temp":     round(reading["charge_port_temp"], 2),
            "soc":                  round(reading["soc"], 4),
            "soh":                  round(reading["soh"], 4),
            "bms_heartbeat":        reading["bms_heartbeat"],
        }
```

**Fault profiles** are separate objects, each defining how a fault evolves over time.
They read and return the **canonical field names** so the engine actually sees the change.

```python
# fault_profiles.py

class ThermalRunawayPrecursor:
    def apply(self, reading, t):
        return {
            "temperature": reading["temperature"] + 0.4 * t,  # ramps up
            "current":     reading["current"] * 1.3,
        }

class CoolantBlockage:
    # Pump-seizure model (Decision 4): steep drain crosses the 4.0 threshold by ~t=21,
    # inside the 30s latency target.
    def apply(self, reading, t):
        return {
            "coolant_flow_rate": max(0, reading["coolant_flow_rate"] - 0.12 * t),
            "temperature":       reading["temperature"] + 0.2 * t,
        }

class CellImbalance:
    def apply(self, reading, t):
        return {
            "cell_voltage_delta": reading["cell_voltage_delta"] + 0.0002 * t,
            "pack_voltage":       reading["pack_voltage"] - 0.05 * t,
        }

class SensorDropout:
    def apply(self, reading, t):
        return {"bms_heartbeat": None}

class HVIsolationFault:
    def apply(self, reading, t):
        return {"isolation_resistance": max(0, reading["isolation_resistance"] - 5 * t)}

class ChargePortOverheat:
    """Connector resistance rises (corrosion/poor seating) → port heats under charge.
    Crosses the 85°C P0C2E threshold around t≈55 from a ~35°C baseline."""
    def apply(self, reading, t):
        return {"charge_port_temp": reading["charge_port_temp"] + 0.9 * t}

class InverterDegradation:
    """Gate-driver / IGBT thermal wear → efficiency sags below the 0.88 P0A78 threshold
    (crosses around t≈75 from a ~0.94 baseline). Also dumps waste heat."""
    def apply(self, reading, t):
        return {
            "inverter_efficiency": reading["inverter_efficiency"] - 0.0008 * t,
            "temperature":         reading["temperature"] + 0.1 * t,
        }
```

> **Detection-timing note (Decision 4):** `CellImbalance` ramps at `+0.0002 * t`, crossing
> the `0.05` threshold around t≈250 ticks. This models *gradual* drift, so the 400-tick test
> window (Phase 4) is intentional headroom rather than a borderline accident. If you'd rather
> it fire fast, raise the slope — but keep the test window and the slope consistent either way.

---

## Phase 3 — Diagnostic Engine

Two layers, both reading from the DTC registry.

**Layer 1 — Rule-based (deterministic):**

```python
# diagnostic_engine.py

from dtc_registry import DTC_REGISTRY

class RuleBasedDiagnostics:
    def run(self, reading):
        active_dtcs = []
        for code, definition in DTC_REGISTRY.items():
            # A DTC fires only if ALL of its trigger conditions are met.
            if all(
                self._check(reading.get(field), condition)
                for field, condition in definition["triggers"].items()
            ):
                active_dtcs.append({
                    "dtc": code,
                    "description": definition["description"],
                    "severity": definition["severity"],
                    "repair_procedure": definition["repair_procedure"],
                    "detected_at": reading["timestamp"],
                })
        return active_dtcs

    def _check(self, value, condition):
        if "eq" in condition:
            return value == condition["eq"]
        if value is None:
            return False
        if "lt" in condition:
            return value < condition["lt"]
        if "gt" in condition:
            return value > condition["gt"]
        return False
```

> **FIX:** the original `_check` returned a result on the *first* trigger field only, and the
> outer loop appended a DTC per matching field — so a multi-condition DTC could fire on a
> partial match, and single-field DTCs could be appended twice. The `all(...)` form above
> requires every trigger condition to hold and appends each DTC at most once. It also
> handles `None` cleanly (only an explicit `eq: None` matches a missing/None value).

**Layer 2 — Statistical (rolling window):**

```python
import numpy as np
from collections import deque

class StatisticalDiagnostics:
    def __init__(self, window=60):
        self.buffers = {}   # vehicle_id → deque of readings
        self.window  = window

    def update(self, reading):
        vid = reading["vehicle_id"]
        if vid not in self.buffers:
            self.buffers[vid] = deque(maxlen=self.window)
        self.buffers[vid].append(reading)

    def detect_anomalies(self, vehicle_id):
        buf = list(self.buffers.get(vehicle_id, []))
        if len(buf) < 10:
            return []

        anomalies = []
        for field in ["temperature", "pack_voltage", "coolant_flow_rate"]:
            values = np.array([r[field] for r in buf
                               if r[field] is not None])
            if len(values) < 10:
                continue
            z = (values[-1] - values.mean()) / (values.std() + 1e-9)
            if abs(z) > 3.0:
                anomalies.append({
                    "field": field,
                    "z_score": round(z, 2),
                    "current_value": values[-1],
                    "baseline_mean": round(values.mean(), 2),
                })
        return anomalies
```

> **RESOLVED (Decision 3):** Run **two statistical detectors in parallel**, routed by fault
> shape:
> - **Slope detection** (linear-fit rate-of-rise over the window) for *trending* faults —
>   `ThermalRunawayPrecursor`, the thermal side of `CoolantBlockage`, `InverterDegradation`.
>   A single-window z-score is structurally blind to slow ramps: the rolling mean chases the
>   signal and the std inflates, so z plateaus around ~1.7–2.9 and never crosses 3 on a
>   dangerously climbing temperature.
> - **Z-score** retained for fields that genuinely *step or spike* (sudden sensor glitch,
>   abrupt voltage sag), where it's the correct tool.
>
> **Data-backed thresholds** (from `thermal_detector_comparison.py`): on a 0.4°C/tick ramp,
> the single-window z-score peaks at **2.92** and never fires, while a slope detector with a
> **15-tick window** and a **0.20 °C/tick** threshold catches the fault ~6 ticks after
> injection and stays lit for its duration (a linear ramp has constant slope). Healthy noise
> produces slopes within ±0.05, so 0.20 sits cleanly in the gap. The thermal test asserts
> against the **slope** layer, not rule-based.
>
> Add a `detect_trend()` method alongside the existing `detect_anomalies()` (z-score). Sketch:
>
> ```python
> def detect_trend(self, vehicle_id, fields=("temperature",), threshold=0.20):
>     buf = list(self.buffers.get(vehicle_id, []))
>     if len(buf) < 15:
>         return []
>     trends = []
>     for field in fields:
>         vals = np.array([r[field] for r in buf[-15:] if r[field] is not None])
>         if len(vals) < 5:
>             continue
>         slope = np.polyfit(np.arange(len(vals)), vals, 1)[0]
>         if slope > threshold:
>             trends.append({"field": field, "slope": round(slope, 3)})
>     return trends
> ```
>
> Caveat to keep in mind: slope detection misses a fault that has *plateaued* at a dangerous
> level but stopped rising — so it complements, not replaces, an absolute-threshold check.

---

## Phase 4 — Pytest Automation Harness

This is your direct answer to "automation scripting and software testing" in the JD.

All fault classes are imported. The latency test asserts on simulated **time**
(`reading["timestamp"]`), not loop count, so the assertion stays honest if `dt` ever changes.

```python
# tests/test_diagnostic_engine.py
import pytest
from simulator import VehicleSimulator
from fault_profiles import (
    ThermalRunawayPrecursor,
    CoolantBlockage,
    CellImbalance,
    HVIsolationFault,
    SensorDropout,
    ChargePortOverheat,
    InverterDegradation,
)
from diagnostic_engine import RuleBasedDiagnostics, StatisticalDiagnostics

ENGINE = RuleBasedDiagnostics()

@pytest.mark.parametrize("fault,expected_dtc", [
    (CoolantBlockage(),         "P0C73"),
    (CellImbalance(),           "P1A15"),
    (HVIsolationFault(),        "P0AA6"),
    (SensorDropout(),           "U0100"),
    (ChargePortOverheat(),      "P0C2E"),
    (InverterDegradation(),     "P0A78"),
])
def test_fault_detected(fault, expected_dtc):
    sim = VehicleSimulator("TEST-001", fault_profile=fault)
    detected = []
    for _ in range(400):                  # run 400 ticks (CellImbalance needs ~250)
        reading  = sim.tick()
        detected += ENGINE.run(reading)

    codes = [d["dtc"] for d in detected]
    assert expected_dtc in codes, f"Expected {expected_dtc}, got {set(codes)}"

def test_thermal_precursor_caught_by_slope_layer():
    """ThermalRunawayPrecursor is a slow ramp — the rule-based and z-score layers
    miss it, so it must be caught by the slope detector (Decision 3)."""
    stat = StatisticalDiagnostics()
    sim  = VehicleSimulator("THERMAL-001", fault_profile=ThermalRunawayPrecursor())
    fired = False
    for _ in range(120):
        reading = sim.tick()
        stat.update(reading)
        if stat.detect_trend("THERMAL-001", fields=("temperature",)):
            fired = True
            break
    assert fired, "Slope layer failed to catch the thermal ramp"

def test_no_false_positives_on_healthy_vehicle():
    sim = VehicleSimulator("HEALTHY-001", fault_profile=None)
    detected = []
    for _ in range(600):
        detected += ENGINE.run(sim.tick())
    assert detected == [], f"False positives: {detected}"

def test_detection_latency():
    """Fault must be detected within 30 simulated seconds of injection."""
    sim = VehicleSimulator("LATENCY-001", fault_profile=CoolantBlockage())
    for _ in range(600):
        reading  = sim.tick()
        detected = ENGINE.run(reading)
        if any(d["dtc"] == "P0C73" for d in detected):
            assert reading["timestamp"] < 30, (
                f"Detection too slow: {reading['timestamp']}s"
            )
            return
    pytest.fail("Fault never detected")
```

> **RESOLVED (Decision 4):** Tune fault slopes to match the physical story they model, then
> make slope and threshold internally consistent — do **not** widen the assertion to force a
> pass. `CoolantBlockage` models a **pump seizure** (acute failure), so steepen its drain so
> flow crosses `4.0` within the 30s latency target (e.g. `-0.12 * t` reaches 4.0 by ~t=21
> from a ~6.5 baseline). `CellImbalance` models gradual drift; either raise its slope or keep
> the 400-tick window — both are now stated up front so the borderline case is intentional,
> not accidental. Final slope values get nailed down here in Phase 4 by running the tests.

> **RESOLVED (Decision 5):** Base harness is **9 cases** (6 rule-based faults + healthy +
> latency + thermal-slope). The "40+" figure is reached honestly by expanding the
> parametrize lists: boundary values around each threshold (just-above / just-below /
> exactly-at), multiple slopes and severities per fault, and multi-fault combinations. The
> README states the real count once the expansion is written — no inflated claim ahead of it.

GitHub Actions runs this on every push on a Linux runner — hits the Linux requirement directly.

---

## Phase 5 — FastAPI Backend + React Dashboard

Keep this lean. The diagnostic engine is the project, not the UI.

**Backend** — three endpoints:

```python
GET  /fleet                  # all vehicles + active fault count
GET  /vehicle/{id}/dtcs      # active DTCs + repair procedures
GET  /vehicle/{id}/timeline  # fault event history
```

**Dashboard** — three views:

- **Fleet overview** — table of vehicles, green/amber/red by active fault severity
- **Vehicle detail** — live sensor readings, active DTCs, repair procedure steps
- **Fault timeline** — chart showing when each DTC triggered, detection latency annotation

React + TypeScript, hits the JavaScript requirement from the JD directly.

---

## Phase 6 — Observability

Instrument the diagnostic engine itself with OpenTelemetry. Track:

- Diagnostic run latency per vehicle (p99 target: < 200ms)
- False positive rate across the fleet
- Fault detection latency from injection to DTC emission
- Active fault count over time

Grafana dashboard on top. This is the same observability stack from your existing resume — directly reusable knowledge.

---

## Build Order

```
Week 1  → Phase 0 (NASA calibration) + Phase 1 (DTC registry)
Week 2  → Phase 2 (Simulator + fault profiles)
Week 3  → Phase 3 (Diagnostic engine, both layers)
Week 4  → Phase 4 (Pytest harness + GitHub Actions)
Week 5  → Phase 5 (FastAPI + React dashboard)
Week 6  → Phase 6 (OpenTelemetry + Grafana) + polish
```

---

## Resolved Decisions

1. **Subsystem count** — RESOLVED: 5 subsystems. Added `charging` / `P0C2E` (Charge Port
   Temperature High). README states "5 subsystems, 8 DTCs." (The registry has 8 entries;
   earlier "9 DTCs" prose was a miscount — see the registry block above.)
2. **`inverter_efficiency`** — RESOLVED: keep `P0A78`; backed by the `InverterDegradation`
   fault profile and a test. Every *fault-driven* DTC has a profile; P0A1B and P0AFA are
   threshold-only (triggers but no fault profile / no test).
3. **Statistical layer on trending faults** — RESOLVED: slope detection (15-tick window,
   0.20 °C/tick threshold) for trending faults, z-score for spike/step faults, run in
   parallel. Thresholds data-backed by `thermal_detector_comparison.py`. Thermal test
   asserts against the slope layer.
4. **Fault slopes vs. latency target** — RESOLVED: slopes match the physical story.
   `CoolantBlockage` = pump seizure (`-0.12 * t`, crosses 4.0 by ~t=21, inside 30s target);
   `CellImbalance` = gradual drift (400-tick window, intentional). No assertion-widening.
   Final values confirmed when tests run in Phase 4.
5. **Test count** — RESOLVED: 9 base cases, expanded to 40+ via boundary values, per-fault
   slope/severity variants, and multi-fault combos. README states the real count after the
   expansion is written.

> All five carry a one-line implementation tail into their build phase (the exact slope
> numbers and the test-list expansion get verified against running code, not hard-committed
> here). Directions are fixed; only the empirical fine-tuning remains.
