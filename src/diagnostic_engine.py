"""Phase 3 (step 2) — rule-based diagnostic layer.

RuleBasedDiagnostics reads DTC_REGISTRY and, for each reading, returns the list of
active DTCs (hard-threshold detection). The statistical/slope layer is a separate
step and is NOT built here.

Three correctness properties (the bugs the plan's corrected `all(...)` version fixed —
preserved here and guarded by tests/test_rule_engine.py):

  1. FULL-MATCH ONLY — a multi-condition DTC fires only if ALL its conditions hold,
     never on a partial match. (The original returned on the first trigger field.)
  2. AT MOST ONCE PER READING — each DTC appears at most once per reading. (The
     original appended per-matching-field, so a DTC could be duplicated.)
  3. eq:None SENTINEL — U0100's trigger is {"bms_heartbeat": {"eq": None}}. _check
     matches when the field IS None for an explicit eq:None, and returns False on None
     for every other operator. It uses `value == condition["eq"]`, NOT truthiness, so
     0 / 0.0 / False never spuriously match a None-expecting condition.

Flat imports (no package): resolved via pythonpath = ["src"] in pyproject.toml.
"""

from collections import deque

import numpy as np

from dtc_registry import DTC_REGISTRY
from slope_detector_config import (
    CONSECUTIVE_CROSSINGS,
    MIN_POINTS_FOR_FIT,
    SLOPE_THRESHOLD,
    SLOPE_WINDOW,
)


class RuleBasedDiagnostics:
    def run(self, reading):
        active_dtcs = []
        for code, definition in DTC_REGISTRY.items():
            # A DTC fires only if ALL of its trigger conditions are met (full match),
            # and is appended at most once (loop is over DTCs, not trigger fields).
            if all(
                self._check(reading.get(field), condition)
                for field, condition in definition["triggers"].items()
            ):
                active_dtcs.append(
                    {
                        "dtc": code,
                        "description": definition["description"],
                        "severity": definition["severity"],
                        "repair_procedure": definition["repair_procedure"],
                        "detected_at": reading["timestamp"],
                    }
                )
        return active_dtcs

    def _check(self, value, condition):
        # Explicit equality first: `value == condition["eq"]` matches eq:None only when
        # value IS None (0 == None is False), so 0/0.0/False never match a None sentinel.
        if "eq" in condition:
            return value == condition["eq"]
        # For every other operator, a missing/None field cannot satisfy the condition.
        if value is None:
            return False
        if "lt" in condition:
            return value < condition["lt"]
        if "gt" in condition:
            return value > condition["gt"]
        return False


class StatisticalDiagnostics:
    """Two detectors run in parallel (Decision 3), routed by fault shape:

    - detect_anomalies (z-score) — for spike/step faults (sudden glitch, abrupt sag).
    - detect_trend (slope) — for trending faults (thermal ramp, inverter wear). This
      is the CALIBRATED detector; it reads window/threshold/consec from
      slope_detector_config.py and owns the slope math (the calibration test calls
      THIS code, not a reference copy).

    A single-window z-score is structurally blind to slow ramps (the rolling mean
    chases the signal and std inflates, so |z| plateaus and never crosses 3), which is
    exactly why trending faults are routed to detect_trend, not detect_anomalies.
    """

    Z_THRESHOLD = 3.0
    ZSCORE_FIELDS = ("temperature", "pack_voltage", "coolant_flow_rate")

    def __init__(self, window=60):
        # Buffer must hold at least a full slope window so detect_trend can fire.
        self.window = max(window, SLOPE_WINDOW)
        self.buffers = {}  # vehicle_id -> deque of readings
        # Per-vehicle, per-field consecutive-over-threshold run counters for the slope
        # detector. detect_trend is called once per tick (after update); the counter
        # persists across those calls, matching the calibration's consec-crossings logic.
        self._trend_runs = {}  # vehicle_id -> {field: int}

    def update(self, reading):
        vid = reading["vehicle_id"]
        if vid not in self.buffers:
            self.buffers[vid] = deque(maxlen=self.window)
        self.buffers[vid].append(reading)

    # --- Z-score: spike/step faults (kept close to the plan) ---------------------
    def detect_anomalies(self, vehicle_id):
        buf = list(self.buffers.get(vehicle_id, []))
        if len(buf) < 10:
            return []

        anomalies = []
        for field in self.ZSCORE_FIELDS:
            values = np.array([r[field] for r in buf if r[field] is not None])
            if len(values) < 10:
                continue
            z = (values[-1] - values.mean()) / (values.std() + 1e-9)
            if abs(z) > self.Z_THRESHOLD:
                anomalies.append(
                    {
                        "field": field,
                        "z_score": round(z, 2),
                        "current_value": values[-1],
                        "baseline_mean": round(values.mean(), 2),
                    }
                )
        return anomalies

    # --- Slope: trending faults (the calibrated detector) ------------------------
    @staticmethod
    def _slope(window_vals):
        """Linear-fit slope over the window; 0.0 for a degenerate tiny window."""
        if len(window_vals) < MIN_POINTS_FOR_FIT:
            return 0.0
        x = np.arange(len(window_vals))
        slope, _ = np.polyfit(x, np.array(window_vals), 1)
        return slope

    def detect_trend(self, vehicle_id, fields=("temperature",)):
        """Flag fields whose linear-fit slope exceeds SLOPE_THRESHOLD over a FULL
        SLOPE_WINDOW for CONSECUTIVE_CROSSINGS consecutive ticks.

        Call once per tick (after update). A full window is required before any fire,
        so warm-up is the full window — no short-window noise fires. The per-field run
        counter resets the moment a window drops back under threshold.
        """
        buf = self.buffers.get(vehicle_id)
        runs = self._trend_runs.setdefault(vehicle_id, {})
        trends = []
        for field in fields:
            window_vals = [r[field] for r in buf][-SLOPE_WINDOW:] if buf else []
            crossed = (
                len(window_vals) == SLOPE_WINDOW
                and self._slope(window_vals) > SLOPE_THRESHOLD
            )
            if crossed:
                runs[field] = runs.get(field, 0) + 1
                if runs[field] >= CONSECUTIVE_CROSSINGS:
                    trends.append(
                        {"field": field, "slope": round(self._slope(window_vals), 3)}
                    )
            else:
                runs[field] = 0
        return trends
