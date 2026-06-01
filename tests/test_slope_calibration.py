"""Phase 3 (step 1) — slope-detector calibration regression guard.

The slope-layer analogue of test_p0a1b_threshold_in_safe_band: it locks the
slope-detector config (window / threshold / consecutive-crossings) against the REAL
seeded simulator, so nobody loosens it later without seeing the basis.

It asserts, against real sim.tick() temperature readings:
  - ThermalRunawayPrecursor FIRES under the chosen config across all 8 seeds.
  - a HEALTHY vehicle does NOT fire (the false-positive guard).

Config is read from src/slope_detector_config.py (single source) — see that file
for WHY window=30 / threshold=0.30 / consec=3 (the plan's 0.20/15-tick had a 100%
healthy false-positive rate on the real simulator). Do not hardcode the numbers here.

Flat imports (no package): resolved via pythonpath = ["src"] in pyproject.toml.
"""

from collections import deque

import numpy as np

from simulator import VehicleSimulator
from fault_profiles import ThermalRunawayPrecursor
from slope_detector_config import (
    CONSECUTIVE_CROSSINGS,
    MIN_POINTS_FOR_FIT,
    SLOPE_THRESHOLD,
    SLOPE_WINDOW,
)

# Seed set matching test_simulator.py — distributional, reproducible per seed.
SEEDS = [0, 1, 7, 42, 99, 314, 2718, 31415]
N_TICKS = 120


def _slope(window_vals):
    """Linear-fit slope (°C/tick) over the window; 0.0 for a degenerate tiny window.

    This is the reference slope-detector math the Phase 3 engine will reuse — kept
    here (not in the engine) because the engine isn't built yet; the calibration is.
    """
    if len(window_vals) < MIN_POINTS_FOR_FIT:
        return 0.0
    x = np.arange(len(window_vals))
    slope, _ = np.polyfit(x, np.array(window_vals), 1)
    return slope


def _first_fire_tick(temps):
    """First tick at which CONSECUTIVE_CROSSINGS full-window slopes exceed threshold.

    Firing requires a FULL window (len == SLOPE_WINDOW), so warm-up is the full
    window — no short-window noise fires.
    """
    buf = deque(maxlen=SLOPE_WINDOW)
    run = 0
    for tick, temp in enumerate(temps, start=1):
        buf.append(temp)
        if len(buf) == SLOPE_WINDOW and _slope(buf) > SLOPE_THRESHOLD:
            run += 1
            if run >= CONSECUTIVE_CROSSINGS:
                return tick
        else:
            run = 0
    return None


def _temps(vehicle_id, seed, fault_profile=None):
    sim = VehicleSimulator(vehicle_id, fault_profile=fault_profile, seed=seed)
    return [sim.tick()["temperature"] for _ in range(N_TICKS)]


def test_thermal_ramp_fires_across_seeds():
    """ThermalRunawayPrecursor fires under the locked config — a DETERMINISM check.

    This is a "fires under the locked config" guard, NOT distributional coverage of 8
    different draws. The fire tick is identical (t=32) across all 8 seeds because the
    +0.4*t ramp is deterministic and dominates the per-tick noise by t≈32, so the
    30-tick slope crosses 0.30 at the same window regardless of seed. The seed loop
    just confirms that holds across the seed set; it does not sample a spread of fire
    ticks. We assert "fires" (not the exact tick) to avoid brittleness.
    """
    for seed in SEEDS:
        temps = _temps(f"THERM-{seed}", seed, ThermalRunawayPrecursor())
        fire = _first_fire_tick(temps)
        assert fire is not None, (
            f"slope detector failed to catch thermal ramp at seed {seed} "
            f"(window={SLOPE_WINDOW}, thr={SLOPE_THRESHOLD}, consec={CONSECUTIVE_CROSSINGS})"
        )


def test_healthy_vehicle_does_not_fire():
    """A HEALTHY vehicle must NOT trip the slope detector — the false-positive guard.

    The plan's 0.20/15-tick config fired on ~100% of healthy vehicles (healthy
    temperature std ~3.76 °C from current²·k noise). This config measured 0.00% over
    1000 healthy trials; we guard a representative set here so a regression that
    reintroduces short-window/low-threshold sensitivity fails loudly.
    """
    for seed in SEEDS:
        temps = _temps(f"HEALTHY-{seed}", seed, fault_profile=None)
        fire = _first_fire_tick(temps)
        assert fire is None, (
            f"healthy vehicle FALSE-fired the slope detector at seed {seed}, tick {fire} "
            f"(window={SLOPE_WINDOW}, thr={SLOPE_THRESHOLD}, consec={CONSECUTIVE_CROSSINGS})"
        )
