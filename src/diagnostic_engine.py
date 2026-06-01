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

from dtc_registry import DTC_REGISTRY


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
