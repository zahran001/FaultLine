"""Phase 2 — VehicleSimulator (healthy baseline) tests.

The key Phase 2 guard is the field-name contract: the simulator's emitted sensor
keys must match CANONICAL_FIELDS exactly. That is what catches the locals() leak
(BUG 2) and any coolant_flow-style synonym drift on the simulator side. We import
CANONICAL_FIELDS from dtc_registry — the same single source Phase 1 uses — rather
than retyping the strings.

Flat imports (no package): resolved via pythonpath = ["src"] in pyproject.toml.
"""

import math

import pytest

from dtc_registry import CANONICAL_FIELDS, DTC_REGISTRY
from simulator import CONTEXT_FIELDS, VehicleSimulator

# Every output reading must carry exactly the canonical sensor fields + context.
EXPECTED_KEYS = CANONICAL_FIELDS | CONTEXT_FIELDS

# Fixed seeds make the tests reproducible: a red is a real regression, not bad luck.
FIXED_SEED = 1234
# A small seed set for the distributional P0A1B claim — reproducible per seed, but
# still samples across the SOC-start / noise band rather than betting on one draw.
P0A1B_SEEDS = [0, 1, 7, 42, 99, 314, 2718, 31415]


def test_field_name_contract():
    """Every emitted reading has EXACTLY the canonical sensor fields + context fields.

    No synonym, no missing key, no extra (e.g. a leaked `self`/`dt`/`t` from locals()).
    This is the simulator-side enforcement of CLAUDE.md #2 and the BUG 2 fix.
    """
    sim = VehicleSimulator("CONTRACT-001", seed=FIXED_SEED)
    for _ in range(200):
        reading = sim.tick()
        assert set(reading.keys()) == EXPECTED_KEYS, (
            f"field drift: missing={EXPECTED_KEYS - reading.keys()}, "
            f"extra={reading.keys() - EXPECTED_KEYS}"
        )


@pytest.mark.parametrize("seed", P0A1B_SEEDS)
def test_healthy_vehicle_never_trips_p0a1b(seed):
    """Deferred Phase 1 correctness test, now resolvable against the simulator.

    P0A1B fires on pack_voltage < threshold. A HEALTHY vehicle must never trip it.
    Threshold was reconciled to 315 V in Phase 2 against the observed healthy band
    (500 veh x 1000 ticks: min ~322.9, mean ~346.7, max ~394.7 V; an unseeded run
    dipped to ~319.5) — chosen ~8 V below the observed min. The interim 340 V tripped
    healthy packs ~26% of the time, so it was dropped; see the P0A1B comment in
    dtc_registry.py.

    This is a DISTRIBUTIONAL claim, so it runs over a small set of fixed seeds
    (P0A1B_SEEDS): each is reproducible (red = real regression, not luck) while the
    set still samples the band. Asserted against the registry threshold (not a
    hardcoded 315) so it tracks any future re-reconciliation of the constant.
    """
    threshold = DTC_REGISTRY["P0A1B"]["triggers"]["pack_voltage"]["lt"]
    sim = VehicleSimulator(f"HEALTHY-P0A1B-{seed}", seed=seed)
    packs = [sim.tick()["pack_voltage"] for _ in range(1000)]
    observed_min = min(packs)
    assert observed_min >= threshold, (
        f"healthy vehicle tripped P0A1B at seed {seed}: pack_voltage min "
        f"{observed_min:.2f} < threshold {threshold}"
    )


def test_no_nan_and_sane_ranges():
    """Healthy output is finite and within physically plausible bands."""
    sim = VehicleSimulator("SANITY-001", seed=FIXED_SEED)
    for _ in range(500):
        r = sim.tick()
        # No NaN / inf anywhere numeric.
        for field in CANONICAL_FIELDS | {"current", "temperature", "soc"}:
            val = r[field]
            if isinstance(val, (int, float)):
                assert math.isfinite(val), f"{field} not finite: {val}"

        assert r["bms_heartbeat"] is True
        assert 250 < r["pack_voltage"] < 420  # 96S pack, healthy span
        assert 0 <= r["soc"] <= 1
        assert 0 < r["soh"] <= 1
        assert 0 < r["coolant_flow_rate"] < 12  # healthy ~6.5
        assert 0 <= r["cell_voltage_delta"] < 0.05  # below P1A15's 0.05 trigger
        assert 0.85 < r["inverter_efficiency"] <= 1.0  # healthy ~0.94
        assert 0 < r["isolation_resistance"] < 3000  # healthy ~2000
        assert 0 < r["charge_port_temp"] < 60  # idle/healthy ~35
        assert r["temperature"] > 0
