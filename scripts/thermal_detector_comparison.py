"""
thermal_detector_comparison.py

Purpose: show, tick by tick, why a rolling-window z-score goes blind to a slow
thermal ramp while a slope detector catches it cleanly. Run this against a
ThermalRunawayPrecursor-style trace, read the two scores side by side, and pick
your thresholds + window lengths from the data instead of guessing.

This is self-contained: it reproduces just the temperature channel of the
simulator (healthy baseline + linear fault ramp + noise) so you can run it
before the real simulator.py exists. Once simulator.py is wired up, swap
`generate_trace()` for real sim.tick() readings and the detector logic is unchanged.

No third-party deps beyond numpy.
"""

import numpy as np

# ---- Knobs you'll tune from the output -------------------------------------
HEALTHY_TEMP_MEAN = 35.0      # °C, healthy under-load baseline (from calibration)
HEALTHY_TEMP_STD  = 0.5       # °C, sensor + load noise
RAMP_PER_TICK     = 0.4       # °C/tick — ThermalRunawayPrecursor rate
N_TICKS           = 120

ZSCORE_WINDOW     = 60        # single-window z-score
SLOPE_WINDOW      = 15        # ticks used for the linear-fit slope
SPLIT_SHORT       = 5         # split-window: recent slice
SPLIT_LONG        = 60        # split-window: trailing baseline slice

Z_THRESHOLD       = 3.0       # classic z cutoff
SLOPE_THRESHOLD   = 0.20      # °C/tick sustained — the value you're calibrating
# ----------------------------------------------------------------------------


def generate_trace(n=N_TICKS, ramp=RAMP_PER_TICK, fault_starts=20, seed=0):
    """Healthy baseline for `fault_starts` ticks, then a linear thermal ramp."""
    rng = np.random.default_rng(seed)
    temps = []
    for t in range(n):
        base = HEALTHY_TEMP_MEAN + rng.normal(0, HEALTHY_TEMP_STD)
        fault = ramp * max(0, t - fault_starts)   # ramp only after injection
        temps.append(base + fault)
    return np.array(temps)


def zscore_single(window_vals):
    """Classic windowed z-score: is the latest value unusual vs. the window?"""
    if len(window_vals) < 10:
        return 0.0
    mean = window_vals.mean()
    std = window_vals.std()
    return (window_vals[-1] - mean) / (std + 1e-9)


def zscore_fixed_baseline(latest):
    """Z-score against a FIXED healthy baseline instead of the rolling mean."""
    return (latest - HEALTHY_TEMP_MEAN) / (HEALTHY_TEMP_STD + 1e-9)


def zscore_split(window_vals, short=SPLIT_SHORT, long=SPLIT_LONG):
    """Short recent slice vs. long trailing baseline (recent slice excluded)."""
    if len(window_vals) < short + 10:
        return 0.0
    recent = window_vals[-short:]
    baseline = window_vals[:-short][-long:]
    if len(baseline) < 10:
        return 0.0
    return (recent.mean() - baseline.mean()) / (baseline.std() + 1e-9)


def slope_score(window_vals):
    """Linear-fit slope over the window, in °C/tick. Detects rate-of-rise."""
    if len(window_vals) < 5:
        return 0.0
    x = np.arange(len(window_vals))
    slope, _ = np.polyfit(x, window_vals, 1)
    return slope


def main():
    temps = generate_trace()

    print(f"{'tick':>4} {'temp':>7} {'z_single':>9} {'z_fixed':>8} "
          f"{'z_split':>8} {'slope':>7}  flags")
    print("-" * 70)

    first_catch = {}  # detector name -> tick it first fired

    for t in range(len(temps)):
        win_z   = temps[max(0, t - ZSCORE_WINDOW + 1): t + 1]
        win_sl  = temps[max(0, t - SLOPE_WINDOW + 1): t + 1]
        win_sp  = temps[max(0, t - SPLIT_LONG - SPLIT_SHORT + 1): t + 1]

        z_single = zscore_single(win_z)
        z_fixed  = zscore_fixed_baseline(temps[t])
        z_split  = zscore_split(win_sp)
        sl       = slope_score(win_sl)

        flags = []
        if abs(z_single) > Z_THRESHOLD:
            flags.append("Z_SINGLE")
            first_catch.setdefault("z_single", t)
        if abs(z_fixed) > Z_THRESHOLD:
            flags.append("Z_FIXED")
            first_catch.setdefault("z_fixed", t)
        if abs(z_split) > Z_THRESHOLD:
            flags.append("Z_SPLIT")
            first_catch.setdefault("z_split", t)
        if sl > SLOPE_THRESHOLD:
            flags.append("SLOPE")
            first_catch.setdefault("slope", t)

        # print every 5th tick to keep it readable, plus any tick that fires
        if t % 5 == 0 or flags:
            print(f"{t:>4} {temps[t]:>7.2f} {z_single:>9.2f} {z_fixed:>8.2f} "
                  f"{z_split:>8.2f} {sl:>7.3f}  {' '.join(flags)}")

    print("-" * 70)
    print("Fault injected at tick 20 (ramp = %.2f °C/tick)\n" % RAMP_PER_TICK)
    print("First detection by each method:")
    for name in ["z_single", "z_fixed", "z_split", "slope"]:
        tick = first_catch.get(name)
        if tick is None:
            print(f"  {name:<10}: NEVER FIRED   <-- blind to this fault")
        else:
            print(f"  {name:<10}: tick {tick:>3}  ({tick - 20} ticks after injection)")

    print("\nPeak single-window z-score reached:",
          round(max(abs(zscore_single(temps[max(0, t - ZSCORE_WINDOW + 1): t + 1]))
                    for t in range(len(temps))), 2))


if __name__ == "__main__":
    main()
