"""Phase 3 (step 1) — slope-detector calibration constants (single source of truth).

The Phase 3 StatisticalDiagnostics slope layer and tests/test_slope_calibration.py
BOTH read these constants. A future retune happens here, in one spot — never
hardcode the window/threshold in the engine or a test.

WHY THESE VALUES (calibrated against REAL seeded sim.tick() temperature readings,
not the synthetic generate_trace() in scripts/thermal_detector_comparison.py):

The plan's original 0.20 °C/tick on a 15-tick window does NOT survive contact with
the real simulator. Real healthy temperature is `25 + current**2 * k + noise` with
current = normal(120, 15) re-rolled every tick, so the I²R term swings ~±3.5 °C per
tick: healthy temperature has std ~3.76 °C and tick-to-tick jumps averaging ~4 °C.
Over a 15-tick window that noise produces healthy linear-fit slopes with std ~0.214,
which exceed 0.20 about 17.6% of the time — so a 0.20/15-tick detector fires on
~100% of HEALTHY vehicles. The threshold sat inside the noise distribution.

Fix (measured, 1000 healthy trials + 8 fault seeds):
  - WINDOW = 30 ticks. Doubling the window shrinks the healthy-slope variance enough
    that the fault ramp (~0.4 °C/tick mature) separates from healthy noise.
  - SLOPE_THRESHOLD = 0.30 °C/tick. Above the healthy-slope tail, below the fault ramp.
  - CONSECUTIVE = 3. Require 3 consecutive windows over threshold; a lone noisy window
    no longer fires.
  Result: 0.00% healthy false positives over 1000 trials; ThermalRunawayPrecursor
  fires on all 8 seeds at t≈32. Latency cost vs. the (illusory) t=5–6 preview: first
  fire is ~t=32, still inside the ~30-tick neighborhood. The earlier "fires at t=5–6"
  was noise luck on a 5-point warm-up, not real detection.

  MIN_POINTS_FOR_FIT only guards np.polyfit from degenerate tiny windows; firing
  requires a FULL window (len == WINDOW), so warm-up is effectively the full 30 ticks.

Regression guard: tests/test_slope_calibration.py asserts (a) the fault fires under
this config across all seeds and (b) a healthy vehicle does NOT fire. Do not loosen
these constants without re-running that calibration — they are inside the noise floor
of the real simulator, not arbitrary.
"""

SLOPE_WINDOW = 30           # ticks in the linear-fit window
SLOPE_THRESHOLD = 0.30      # °C/tick sustained rise that counts as a trend
CONSECUTIVE_CROSSINGS = 3   # consecutive over-threshold windows required to fire
MIN_POINTS_FOR_FIT = 5      # polyfit guard only; firing still needs a full window
