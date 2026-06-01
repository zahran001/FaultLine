"""Phase 3 (step 3) — StatisticalDiagnostics: z-score + routing guard.

detect_trend (slope) is covered by the refactored tests/test_slope_calibration.py,
which now drives the engine's real update()/detect_trend() interface. This file
covers the z-score detector and the Decision-3 routing guard.

Fixed seeds / hand-built readings for determinism.

Flat imports (no package): resolved via pythonpath = ["src"] in pyproject.toml.
"""

import numpy as np

from simulator import VehicleSimulator
from fault_profiles import ThermalRunawayPrecursor
from diagnostic_engine import StatisticalDiagnostics

SEEDS = [0, 1, 7, 42, 99, 314, 2718, 31415]


def _healthy_reading(vid, t, temp):
    """Minimal reading carrying the z-score fields (temperature is the one we vary)."""
    return {
        "vehicle_id": vid,
        "timestamp": t,
        "temperature": temp,
        "pack_voltage": 360.0,
        "coolant_flow_rate": 6.5,
    }


# --- Z-score: detect_anomalies on a spike/step -----------------------------------

def test_zscore_fires_on_spike():
    """A sudden injected jump after a stable baseline trips detect_anomalies."""
    stat = StatisticalDiagnostics()
    rng = np.random.default_rng(42)
    for i in range(15):
        stat.update(_healthy_reading("SPIKE", i, 40.0 + rng.normal(0, 0.3)))
    stat.update(_healthy_reading("SPIKE", 15, 80.0))  # abrupt step
    fields = [a["field"] for a in stat.detect_anomalies("SPIKE")]
    assert "temperature" in fields, f"spike did not trip z-score; got {fields}"


def test_zscore_quiet_on_healthy_noise():
    """Healthy noise trips detect_anomalies only at the rate of the Gaussian tail.

    z-score is NOT a zero-false-positive detector and the plan keeps it as-is: with
    stationary noise, a single sample can land |z| > 3 by chance (that's the ~0.3%
    per-window tail across 3 fields). So we assert the false-fire RATE is low, not
    that it literally never fires — asserting "never" would be testing against the
    nature of z-score. This low-but-nonzero rate is exactly why z-score is reserved
    for genuine step/spike faults and trending faults are routed to detect_trend.
    """
    total_windows = 0
    fired_windows = 0
    for seed in range(300):
        stat = StatisticalDiagnostics()
        rng = np.random.default_rng(seed)
        vid = f"Q-{seed}"
        for i in range(60):
            stat.update(
                {
                    "vehicle_id": vid,
                    "timestamp": i,
                    "temperature": 40.0 + rng.normal(0, 0.3),
                    "pack_voltage": 360.0 + rng.normal(0, 1.5),
                    "coolant_flow_rate": 6.5 + rng.normal(0, 0.3),
                }
            )
            if len(stat.buffers[vid]) >= 10:  # detector only runs once warmed
                total_windows += 1
                if stat.detect_anomalies(vid):
                    fired_windows += 1
    rate = fired_windows / total_windows
    # Measured ~0.30% (matches 3 fields × P(|z|>3)); guard well under 2%.
    assert rate < 0.02, (
        f"healthy z-score false-fire rate {rate*100:.2f}% too high ({fired_windows}/"
        f"{total_windows}) — z-score should only flag at ~the Gaussian tail rate"
    )


# --- Routing guard (Decision 3) --------------------------------------------------

def test_thermal_ramp_routed_to_trend_not_zscore():
    """The slow thermal ramp must be caught by detect_trend, and must NOT be RELIED
    upon through detect_anomalies (z-score) alone.

    z-score is structurally weak on a slow ramp: the rolling mean chases the signal,
    so |z| plateaus. Measured across the seed set: detect_trend fires on ALL 8 seeds
    (t≈32); z-score fires on temperature for only 1/8 (incidental, via the profile's
    current*1.3 step), missing 7/8. So routing a trending fault into z-score-only
    would silently lose it — this guard fails if a future change makes detect_trend
    stop catching the ramp, or if someone deletes detect_trend expecting z-score to
    cover it.
    """
    trend_fired = 0
    zscore_fired = 0
    for seed in SEEDS:
        vid = f"ROUTE-{seed}"
        sim = VehicleSimulator(vid, fault_profile=ThermalRunawayPrecursor(), seed=seed)
        stat = StatisticalDiagnostics()
        trend_hit = False
        zscore_hit = False
        for _ in range(120):
            stat.update(sim.tick())
            if stat.detect_trend(vid, fields=("temperature",)):
                trend_hit = True
            if any(a["field"] == "temperature" for a in stat.detect_anomalies(vid)):
                zscore_hit = True
        trend_fired += trend_hit
        zscore_fired += zscore_hit

    # detect_trend is the designed, reliable path: catches the ramp on every seed.
    assert trend_fired == len(SEEDS), (
        f"detect_trend missed the thermal ramp on some seeds: {trend_fired}/{len(SEEDS)}"
    )
    # z-score is NOT a reliable substitute — it must not be the path trending faults
    # depend on. (It catches the ramp on a minority of seeds, by incidental step.)
    assert zscore_fired < len(SEEDS), (
        "z-score caught the ramp on EVERY seed — if detection now relies on z-score "
        "for a trending fault, the routing has drifted from Decision 3 (slope layer "
        "owns trending faults). Re-check the routing before relaxing this guard."
    )
