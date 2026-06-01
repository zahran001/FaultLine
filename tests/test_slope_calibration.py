"""Phase 3 — slope-detector calibration regression guard (now validates ENGINE code).

The slope-layer analogue of test_p0a1b_threshold_in_safe_band: it locks the
slope-detector config (window / threshold / consecutive-crossings) against the REAL
seeded simulator, so nobody loosens it later without seeing the basis.

It asserts, against real sim.tick() readings fed through the engine's PRODUCTION
interface (StatisticalDiagnostics.update() + detect_trend()):
  - ThermalRunawayPrecursor FIRES under the locked config across all 8 seeds.
  - a HEALTHY vehicle does NOT fire (the false-positive guard).

Step 3 refactor: the slope math now lives in StatisticalDiagnostics.detect_trend
(the engine owns it). This test drives that real code — feeding each reading via
update() then calling detect_trend() per tick — instead of a reference copy, so it
validates production behavior, not a reimplementation.

Config is read from src/slope_detector_config.py (single source) — see that file for
WHY window=30 / threshold=0.30 / consec=3 (the plan's 0.20/15-tick had a 100% healthy
false-positive rate on the real simulator). Do not hardcode the numbers here.

Flat imports (no package): resolved via pythonpath = ["src"] in pyproject.toml.
"""

from simulator import VehicleSimulator
from fault_profiles import ThermalRunawayPrecursor
from diagnostic_engine import StatisticalDiagnostics
from slope_detector_config import (
    CONSECUTIVE_CROSSINGS,
    SLOPE_THRESHOLD,
    SLOPE_WINDOW,
)

# Seed set matching test_simulator.py — distributional, reproducible per seed.
SEEDS = [0, 1, 7, 42, 99, 314, 2718, 31415]
N_TICKS = 120


def _first_trend_fire(vehicle_id, seed, fault_profile=None):
    """Run a seeded sim through the ENGINE's real update()/detect_trend() interface.

    Returns the tick detect_trend first fires on temperature, or None within N_TICKS.
    """
    sim = VehicleSimulator(vehicle_id, fault_profile=fault_profile, seed=seed)
    stat = StatisticalDiagnostics()
    for tick in range(1, N_TICKS + 1):
        stat.update(sim.tick())
        if stat.detect_trend(vehicle_id, fields=("temperature",)):
            return tick
    return None


def test_thermal_ramp_fires_across_seeds():
    """ThermalRunawayPrecursor fires under the locked config — a DETERMINISM check.

    A "fires under the locked config" guard, NOT distributional coverage of 8 draws.
    The fire tick is identical (t≈32) across all 8 seeds because the +0.4*t ramp is
    deterministic and dominates the per-tick noise by then, so detect_trend crosses at
    the same window regardless of seed. The seed loop confirms that holds across the
    set; it does not sample a spread of fire ticks. We assert "fires", not the exact
    tick, to avoid brittleness.
    """
    for seed in SEEDS:
        fire = _first_trend_fire(f"THERM-{seed}", seed, ThermalRunawayPrecursor())
        assert fire is not None, (
            f"engine detect_trend failed to catch thermal ramp at seed {seed} "
            f"(window={SLOPE_WINDOW}, thr={SLOPE_THRESHOLD}, consec={CONSECUTIVE_CROSSINGS})"
        )


def test_healthy_vehicle_does_not_fire():
    """A HEALTHY vehicle must NOT trip detect_trend — the false-positive guard.

    The plan's 0.20/15-tick config fired on ~100% of healthy vehicles (healthy
    temperature std ~3.76 °C from current²·k noise). The locked config measured 0.00%
    over 1000 healthy trials; we guard a representative set here so a regression that
    reintroduces short-window / low-threshold sensitivity fails loudly.
    """
    for seed in SEEDS:
        fire = _first_trend_fire(f"HEALTHY-{seed}", seed, fault_profile=None)
        assert fire is None, (
            f"healthy vehicle FALSE-fired detect_trend at seed {seed}, tick {fire} "
            f"(window={SLOPE_WINDOW}, thr={SLOPE_THRESHOLD}, consec={CONSECUTIVE_CROSSINGS})"
        )
