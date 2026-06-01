"""Phase 1 — DTC registry contract tests.

This is the single enforceable guard for the canonical field-name contract
(CLAUDE.md #2). It must stay green through Phase 2/3: if the simulator or engine
drifts a field name (e.g. coolant_flow vs coolant_flow_rate), or a registry edit
introduces a non-canonical trigger field, these tests fail in CI rather than the
fault silently never firing.

Flat imports (no package): `from dtc_registry import ...` resolves via
pythonpath = ["src"] in pyproject.toml. Do not rewrite to `from src.x import`.
"""

import copy

import pytest

from dtc_registry import (
    CANONICAL_FIELDS,
    DTC_REGISTRY,
    REQUIRED_KEYS,
    VALID_SEVERITIES,
    validate_registry,
)

EXPECTED_DTCS = {
    "P0A1B", "P1A15", "P0C73", "P0A78", "P0AA6", "P0AFA", "U0100", "P0C2E",
}
EXPECTED_SUBSYSTEMS = {
    "battery_pack", "thermal", "motor_controller", "bms", "charging",
}


# --- Shape / count invariants (the "8 DTCs, 5 subsystems" claim, enforced) -------

def test_exact_dtc_set():
    assert set(DTC_REGISTRY) == EXPECTED_DTCS
    assert len(DTC_REGISTRY) == 8


def test_all_five_subsystems_covered():
    subsystems = {e["subsystem"] for e in DTC_REGISTRY.values()}
    assert subsystems == EXPECTED_SUBSYSTEMS
    assert len(subsystems) == 5


# --- The canonical field-name contract (the whole point of this file) ------------

def test_every_trigger_field_is_canonical():
    for code, entry in DTC_REGISTRY.items():
        for field in entry["triggers"]:
            assert field in CANONICAL_FIELDS, (
                f"{code}: trigger field {field!r} is not canonical — "
                f"a synonym here means the fault silently never fires"
            )


def test_no_canonical_field_misspelled_away():
    """Every canonical field is referenced by at least one trigger.

    Guards the reverse drift: if a field were renamed in the registry, it would
    drop out of the used set and this catches it.
    """
    used = {f for e in DTC_REGISTRY.values() for f in e["triggers"]}
    assert used == CANONICAL_FIELDS


# --- Structural invariants validate_registry() promises --------------------------

def test_required_keys_present():
    for code, entry in DTC_REGISTRY.items():
        assert REQUIRED_KEYS <= entry.keys(), f"{code}: missing {REQUIRED_KEYS - entry.keys()}"


def test_severity_valid():
    for code, entry in DTC_REGISTRY.items():
        assert entry["severity"] in VALID_SEVERITIES, f"{code}: bad severity"


def test_repair_procedure_nonempty_string_list():
    for code, entry in DTC_REGISTRY.items():
        steps = entry["repair_procedure"]
        assert isinstance(steps, list) and steps, f"{code}: empty repair_procedure"
        assert all(isinstance(s, str) and s for s in steps), f"{code}: non-string step"


def test_trigger_ops_are_known():
    for code, entry in DTC_REGISTRY.items():
        for field, condition in entry["triggers"].items():
            assert len(condition) == 1
            (op,) = condition
            assert op in {"lt", "gt", "eq"}, f"{code}/{field}: unknown op {op!r}"


def test_u0100_uses_eq_none_sentinel():
    """U0100 detects a missing heartbeat via eq:None — the one eq trigger."""
    assert DTC_REGISTRY["U0100"]["triggers"] == {"bms_heartbeat": {"eq": None}}


def test_p0a1b_threshold_in_safe_band():
    """Guard-rail for the P0A1B pack_voltage threshold — a BAND, not an exact value.

    Phase 0 calibration showed the real cell curve (~3.54 V mean) sits below the
    original 3.81 V placeholder, so the original 350 V trigger caused healthy-pack
    false positives. The threshold was lowered (currently 340 V) to sit below the
    real healthy band, but that exact value is PROVISIONAL: Phase 2 reconciles it
    against the simulator's actual healthy pack-voltage distribution.

    This test asserts the two things that must hold regardless of the final number:
      1. the operator is "lt" (a band check is meaningless if it silently flips
         to gt/eq), and
      2. the value is in a safe band: 300 <= value < 350. The strict upper bound
         excludes the KNOWN-BAD 350 (the false-positive value); the lower bound
         keeps it sane.

    Do NOT tighten this to `== 340`: that would be a brittle change-detector that
    breaks on every legitimate Phase 2 retune and proves nothing about correctness.
    The real "a healthy vehicle never trips P0A1B" test lands in Phase 2, asserted
    against the simulated healthy distribution — see CLAUDE.md open items.
    """
    trigger = DTC_REGISTRY["P0A1B"]["triggers"]["pack_voltage"]
    (op,) = trigger
    assert op == "lt", f"P0A1B pack_voltage operator must be 'lt', got {op!r}"
    value = trigger["lt"]
    assert 300 <= value < 350, (
        f"P0A1B threshold {value} outside safe band [300, 350); "
        f"350 is the known-bad false-positive value"
    )


# --- validate_registry() actually rejects violations (guard proves it guards) ----

def test_validate_passes_on_real_registry():
    validate_registry()  # must not raise


def test_validate_rejects_noncanonical_field():
    bad = copy.deepcopy(DTC_REGISTRY)
    bad["P0C73"]["triggers"] = {"coolant_flow": {"lt": 4.0}}  # the classic synonym bug
    with pytest.raises(AssertionError):
        validate_registry(bad)


def test_validate_rejects_bad_severity():
    bad = copy.deepcopy(DTC_REGISTRY)
    bad["P0A1B"]["severity"] = "catastrophic"
    with pytest.raises(AssertionError):
        validate_registry(bad)


def test_validate_rejects_missing_key():
    bad = copy.deepcopy(DTC_REGISTRY)
    del bad["P0AFA"]["repair_procedure"]
    with pytest.raises(AssertionError):
        validate_registry(bad)


def test_validate_rejects_empty_repair_procedure():
    bad = copy.deepcopy(DTC_REGISTRY)
    bad["P0AA6"]["repair_procedure"] = []
    with pytest.raises(AssertionError):
        validate_registry(bad)
