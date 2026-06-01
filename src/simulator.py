"""Phase 2 — Vehicle fault simulator (HEALTHY baseline only).

Emits synthetic per-tick sensor readings calibrated against the Phase 0 NASA B0005
constants (see calibration.py). Fault profiles are NOT implemented here — the
fault_profile hook is in place but no profiles exist yet.

Two bug fixes from the original draft are baked in (see CLAUDE.md #4):
  BUG 1 — cell_voltage double-count: the discharge curve IS cell voltage as a
    function of SOC, so we interpolate it directly and add ONLY sensor noise. We do
    NOT add nominal_cell_voltage_mean on top (that double-counts the voltage).
  BUG 2 — locals() leak: fault injection builds an EXPLICIT reading dict and passes
    that to profile.apply(); it never passes locals() (which would leak self/dt/t).

Flat imports (no package): resolved via pythonpath = ["src"] in pyproject.toml.
"""

import numpy as np

from calibration import CALIBRATION
from dtc_registry import CANONICAL_FIELDS  # single source of truth for field names

# Output sensor-field contract: exactly the 8 canonical fields plus these context
# fields. Asserted by tests/test_simulator.py so a locals() leak or a coolant_flow-style
# synonym on the simulator side fails loudly instead of silently disabling a fault.
CONTEXT_FIELDS = {"vehicle_id", "timestamp", "current", "temperature", "soc"}

CELLS_IN_SERIES = 96  # 96S pack


class VehicleSimulator:
    """Generates a stream of healthy per-tick readings for one vehicle.

    The fault_profile argument is reserved for Phase 2's fault-injection step; when
    None (the only supported mode for now) the simulator emits a healthy baseline.
    """

    def __init__(self, vehicle_id, fault_profile=None, seed=None):
        self.vehicle_id = vehicle_id
        # Local generator instead of the global np.random state. seed=None preserves
        # production behavior (fresh, OS-entropy randomness each run); a fixed seed
        # gives reproducible streams for tests.
        self.rng = np.random.default_rng(seed)
        self.soc = self.rng.uniform(0.6, 0.95)
        self.soh = self.rng.uniform(0.80, 1.0)
        self.fault_profile = fault_profile  # injected fault, or None
        self.t = 0

    def tick(self, dt=1.0):
        # --- Healthy baseline from NASA calibration --------------------------------
        # BUG 1 fix: interpolate cell voltage DIRECTLY from the SOC→voltage discharge
        # curve and add only sensor noise — do not add nominal_cell_voltage_mean.
        cell_voltage = (
            np.interp(
                self.soc,
                CALIBRATION["discharge_curve_soc"],
                CALIBRATION["discharge_curve_voltage"],
            )
            + self.rng.normal(0, CALIBRATION["nominal_cell_voltage_std"])
        )
        pack_voltage = cell_voltage * CELLS_IN_SERIES
        current = self.rng.normal(120, 15)
        temperature = (
            25
            + current**2 * CALIBRATION["thermal_rise_coefficient"]
            + self.rng.normal(0, 0.5)
        )
        coolant_flow_rate = self.rng.normal(6.5, 0.3)
        cell_voltage_delta = abs(self.rng.normal(0, 0.008))
        isolation_resistance = self.rng.normal(2000, 50)
        inverter_efficiency = self.rng.normal(0.94, 0.01)
        charge_port_temp = self.rng.normal(35, 3)  # °C, idle/healthy port
        bms_heartbeat = True

        # --- BUG 2 fix: assemble an EXPLICIT reading dict (never locals()) ----------
        # Field names match the canonical registry contract exactly.
        reading = {
            "vehicle_id": self.vehicle_id,
            "timestamp": self.t,
            "pack_voltage": pack_voltage,
            "current": current,
            "temperature": temperature,
            "coolant_flow_rate": coolant_flow_rate,
            "cell_voltage_delta": cell_voltage_delta,
            "isolation_resistance": isolation_resistance,
            "inverter_efficiency": inverter_efficiency,
            "charge_port_temp": charge_port_temp,
            "soc": self.soc,
            "soh": self.soh,
            "bms_heartbeat": bms_heartbeat,
        }

        # --- Fault injection: profile mutates the reading in place ------------------
        # Hook only — no profiles implemented in this step.
        if self.fault_profile:
            overrides = self.fault_profile.apply(reading, self.t)
            reading.update(overrides)

        # --- Advance state and round for output ------------------------------------
        self.soc -= (current * dt) / 360_000
        self.t += dt
        reading["soc"] = self.soc
        reading["timestamp"] = self.t

        return {
            "vehicle_id": reading["vehicle_id"],
            "timestamp": reading["timestamp"],
            "pack_voltage": round(reading["pack_voltage"], 2),
            "current": round(reading["current"], 2),
            "temperature": round(reading["temperature"], 2),
            "coolant_flow_rate": round(reading["coolant_flow_rate"], 2),
            "cell_voltage_delta": round(reading["cell_voltage_delta"], 4),
            "isolation_resistance": round(reading["isolation_resistance"], 1),
            "inverter_efficiency": round(reading["inverter_efficiency"], 4),
            "charge_port_temp": round(reading["charge_port_temp"], 2),
            "soc": round(reading["soc"], 4),
            "soh": round(reading["soh"], 4),
            "bms_heartbeat": reading["bms_heartbeat"],
        }


if __name__ == "__main__":
    sim = VehicleSimulator("DEMO-001")
    packs = []
    for _ in range(1000):
        r = sim.tick()
        packs.append(r["pack_voltage"])
    packs = np.array(packs)
    print(f"healthy pack_voltage over 1000 ticks: "
          f"min={packs.min():.2f}  mean={packs.mean():.2f}  max={packs.max():.2f}")
    print(f"sample reading: {sim.tick()}")
