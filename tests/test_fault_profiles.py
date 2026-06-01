"""Phase 2 (second half) — fault-profile crossing checks.

These are LIGHTWEIGHT crossing checks, not DTC-firing tests (those are Phase 4).
We assert directly on the mutated reading field — no engine dependency — that each
profile drives its target sensor across its DTC threshold within the intended
window, and that profiles only ever emit canonical field names.

Fixed seeds for determinism (consistent with test_simulator.py): a red is a real
regression, not bad luck. Observed crossing ticks (seed=42) are recorded in each
test so a future shift is visible.

Flat imports (no package): resolved via pythonpath = ["src"] in pyproject.toml.
"""

import numpy as np
import pytest

from simulator import CONTEXT_FIELDS, VehicleSimulator
from dtc_registry import CANONICAL_FIELDS
from fault_profiles import (
    CellImbalance,
    ChargePortOverheat,
    CoolantBlockage,
    HVIsolationFault,
    InverterDegradation,
    SensorDropout,
    ThermalRunawayPrecursor,
)

SEED = 42

# A profile may legitimately write any field in the simulator's full output contract
# (the 8 canonical sensor fields plus context fields like temperature/current/pack_voltage).
OUTPUT_CONTRACT = CANONICAL_FIELDS | CONTEXT_FIELDS


def _run_until(profile, field, predicate, window, seed=SEED):
    """Run a seeded sim with the profile injected; return (tick, value) at first
    crossing, or (None, None) if it never crosses within the window."""
    sim = VehicleSimulator("XING", fault_profile=profile, seed=seed)
    for tick in range(1, window + 1):
        r = sim.tick()
        if predicate(r[field]):
            return tick, r[field]
    return None, None


# --- Crossing checks: one per fault-driven profile -------------------------------
# (window, observed-crossing-tick @ seed=42) recorded inline.

def test_coolant_blockage_crosses_4():
    # Pump seizure: crosses 4.0 by ~t=21 (observed t=21 @ seed 42), inside 30s latency.
    tick, val = _run_until(CoolantBlockage(), "coolant_flow_rate", lambda x: x < 4.0, 40)
    assert tick is not None, "coolant_flow_rate never dropped below 4.0 within 40 ticks"
    assert tick <= 30, f"crossed too late at t={tick} (val={val:.3f}); 30s latency target"


def test_cell_imbalance_crosses_0_05():
    # Gradual drift. Plan's clean-slope estimate is ~250, but cell_voltage_delta also
    # carries per-tick noise, so the first crossing is earlier and seed-dependent
    # (observed t=152 @ seed 42). Assert it crosses within the 400-tick Phase 4 window.
    tick, val = _run_until(CellImbalance(), "cell_voltage_delta", lambda x: x > 0.05, 400)
    assert tick is not None, "cell_voltage_delta never exceeded 0.05 within 400 ticks"
    assert tick <= 400, f"crossed at t={tick} (val={val:.4f})"


def test_hv_isolation_crosses_500():
    # isolation_resistance drains at -5*t from ~2000; observed crossing t=285 @ seed 42.
    tick, val = _run_until(HVIsolationFault(), "isolation_resistance", lambda x: x < 500, 600)
    assert tick is not None, "isolation_resistance never dropped below 500 within 600 ticks"
    assert tick <= 400, f"crossed at t={tick} (val={val:.1f})"


def test_charge_port_overheat_crosses_85():
    # +0.9*t from ~35 °C; crosses 85 around t≈55 (observed t=56 @ seed 42).
    tick, val = _run_until(ChargePortOverheat(), "charge_port_temp", lambda x: x > 85, 120)
    assert tick is not None, "charge_port_temp never exceeded 85 within 120 ticks"
    assert tick <= 80, f"crossed at t={tick} (val={val:.2f})"


def test_inverter_degradation_crosses_0_88():
    # -0.0008*t from ~0.94; crosses 0.88 around t≈75 (observed t=62 @ seed 42 — earlier
    # because efficiency also carries per-tick noise). Assert it crosses within ~150.
    tick, val = _run_until(InverterDegradation(), "inverter_efficiency", lambda x: x < 0.88, 150)
    assert tick is not None, "inverter_efficiency never dropped below 0.88 within 150 ticks"
    assert tick <= 120, f"crossed at t={tick} (val={val:.4f})"


def test_sensor_dropout_immediate():
    # U0100: heartbeat None from the very first tick.
    sim = VehicleSimulator("DROP", fault_profile=SensorDropout(), seed=SEED)
    r = sim.tick()
    assert r["bms_heartbeat"] is None, f"expected None heartbeat at t=1, got {r['bms_heartbeat']!r}"


def test_thermal_runaway_slope_in_expected_range():
    """ThermalRunawayPrecursor must produce the ~0.4 °C/tick ramp the Phase 3 slope
    detector is calibrated against.

    Measured over a 60-tick window, where the +0.4*t ramp dominates the baseline
    temperature jitter (current=normal(120,15) re-rolled per tick). NOTE: over a short
    15-tick window anchored at t=0 the *fixed-window* slope can read as low as ~0.07,
    because the ramp has barely grown and noise dominates — but Phase 3 uses a ROLLING
    15-tick window that fires at t≈5–6 once the buffer slope steepens. So the profile
    is correct at 0.4; the short-fixed-window reading is a measurement artifact, not a
    slope to retune.
    """
    sim = VehicleSimulator("THERM", fault_profile=ThermalRunawayPrecursor(), seed=SEED)
    temps = np.array([sim.tick()["temperature"] for _ in range(60)])
    slope = np.polyfit(np.arange(len(temps)), temps, 1)[0]
    assert 0.30 <= slope <= 0.50, (
        f"thermal slope {slope:.3f} °C/tick outside expected band [0.30, 0.50] "
        f"(target ~0.4); Phase 3 slope detector is calibrated against this"
    )


# --- Profile output is canonical -------------------------------------------------

ALL_PROFILES = [
    CoolantBlockage(),
    CellImbalance(),
    HVIsolationFault(),
    SensorDropout(),
    ChargePortOverheat(),
    InverterDegradation(),
    ThermalRunawayPrecursor(),
]


@pytest.mark.parametrize("profile", ALL_PROFILES, ids=lambda p: type(p).__name__)
def test_profile_output_is_canonical(profile):
    """Every key a profile returns from apply() must be in the output contract.

    Catches synonym drift on the profile side (e.g. coolant_flow vs coolant_flow_rate)
    BEFORE Phase 4 — the original silent-fault bug. Builds a representative reading
    from a real healthy tick so apply() has the fields it reads.
    """
    sim = VehicleSimulator("CANON", seed=SEED)
    reading = sim.tick()
    for t in (0, 1, 50, 250):
        overrides = profile.apply(dict(reading), t)
        stray = set(overrides) - OUTPUT_CONTRACT
        assert not stray, (
            f"{type(profile).__name__}.apply() returned non-canonical keys {stray} "
            f"at t={t} — these would be silently ignored by the engine"
        )
