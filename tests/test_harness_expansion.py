"""Phase 4 expansion — distinct end-to-end cases beyond the 9 base cases (Decision 5).

Honest expansion: genuinely distinct coverage, no padding, no reskinned duplicates.
Three axes, each with a strict distinctness rule:

  1. BOUNDARY (full pipeline) — a profile drives a field JUST PAST / exactly-at /
     JUST SHORT of a threshold through VehicleSimulator -> engine. Distinct from
     test_rule_engine.py's step-2 boundary tests, which assert against hand-built
     dicts / _check directly; these run the real pipeline.
  2. MULTI-FAULT COMBINATIONS — composed at the TEST level (the simulator still takes
     one profile; a test-only Composite chains apply() calls). The genuinely new
     coverage: both DTCs fire, no interference, same-field combos behave sanely.
  3. PER-FAULT VARIANTS — only where they assert a DISTINCT claim (steeper vs
     shallower rate both caught within their windows = window-adequacy across rates).

Carry-forward constraints: window-bound assertions (never == exact tick); 30s latency
is rule-based scope only; rule-based/slope == zero on healthy, z-score by rate.

Seed set held at 8 via the base SEED for single cases; coverage grows along case TYPE,
not seeds (widening seeds would invoke the z-score tail-variance caveat for no gain).

Flat imports (no package): resolved via pythonpath = ["src"] in pyproject.toml.
"""

import pytest

from simulator import VehicleSimulator
from diagnostic_engine import RuleBasedDiagnostics, StatisticalDiagnostics
import fault_profiles as fp

SEED = 42
RULE_WINDOW = 400  # covers slowest base fault (CellImbalance ~t=152) with headroom


# --- test-only helpers -----------------------------------------------------------

class _Composite:
    """Chain several profiles' apply() so one reading carries all their mutations.

    Test-only: the simulator still takes a single fault_profile. Each profile sees the
    prior profiles' mutations (so same-field combos compound), and we verified the
    result is order-independent for the additive profiles used here.
    """

    def __init__(self, *profiles):
        self.profiles = profiles

    def apply(self, reading, t):
        overrides = {}
        for p in self.profiles:
            view = dict(reading)
            view.update(overrides)
            overrides.update(p.apply(view, t))
        return overrides


class _ConstField:
    """Pin one field to a constant value every tick — for boundary cases.

    Drives the field to a fixed offset relative to a threshold so we can test
    just-past / exactly-at / just-short through the full pipeline.
    """

    def __init__(self, field, value):
        self.field = field
        self.value = value

    def apply(self, reading, t):
        return {self.field: self.value}


class _LinearRate:
    """Drive a field linearly at a tunable rate (for steeper/shallower variants)."""

    def __init__(self, field, rate, direction):
        self.field = field
        self.rate = rate
        self.direction = direction  # -1 drains, +1 rises

    def apply(self, reading, t):
        return {self.field: reading[self.field] + self.direction * self.rate * t}


def _rule_codes(profile, window=RULE_WINDOW, seed=SEED, vid="EXP"):
    engine = RuleBasedDiagnostics()
    sim = VehicleSimulator(vid, fault_profile=profile, seed=seed)
    codes = set()
    for _ in range(window):
        for d in engine.run(sim.tick()):
            codes.add(d["dtc"])
    return codes


def _slope_fires(profile, field="temperature", window=120, seed=SEED, vid="EXP"):
    stat = StatisticalDiagnostics()
    sim = VehicleSimulator(vid, fault_profile=profile, seed=seed)
    for _ in range(window):
        stat.update(sim.tick())
        if stat.detect_trend(vid, fields=(field,)):
            return True
    return False


# =================================================================================
# AXIS 1 — BOUNDARY VALUES (full pipeline). For each rule-based threshold: a profile
# pinning the field just-past fires; exactly-at and just-short do NOT.
# =================================================================================

# (field, dtc, op, threshold, eps) — eps small offset around the boundary.
BOUNDARY = [
    ("coolant_flow_rate", "P0C73", "lt", 4.0, 0.1),
    ("inverter_efficiency", "P0A78", "lt", 0.88, 0.01),
    ("isolation_resistance", "P0AA6", "lt", 500, 5),
    ("soh", "P0AFA", "lt", 0.75, 0.02),       # soh has no fault profile; pin it directly
    ("cell_voltage_delta", "P1A15", "gt", 0.05, 0.005),
    ("charge_port_temp", "P0C2E", "gt", 85, 1),
    ("pack_voltage", "P0A1B", "lt", 315, 3),  # reconciled threshold, full-path check
]


@pytest.mark.parametrize("field,dtc,op,thr,eps", BOUNDARY, ids=[b[1] for b in BOUNDARY])
def test_boundary_just_past_fires(field, dtc, op, thr, eps):
    """A profile pinning the field just on the firing side trips the DTC end-to-end."""
    past = thr - eps if op == "lt" else thr + eps
    assert dtc in _rule_codes(_ConstField(field, past)), (
        f"{dtc}: {field}={past} (just past {op} {thr}) did not fire through the pipeline"
    )


@pytest.mark.parametrize("field,dtc,op,thr,eps", BOUNDARY, ids=[b[1] for b in BOUNDARY])
def test_boundary_exactly_at_does_not_fire(field, dtc, op, thr, eps):
    """Exactly-at the threshold does NOT fire (strict lt/gt)."""
    assert dtc not in _rule_codes(_ConstField(field, thr)), (
        f"{dtc}: {field}={thr} (exactly at {op} {thr}) wrongly fired (strict {op})"
    )


@pytest.mark.parametrize("field,dtc,op,thr,eps", BOUNDARY, ids=[b[1] for b in BOUNDARY])
def test_boundary_just_short_does_not_fire(field, dtc, op, thr, eps):
    """A profile pinning the field just on the safe side does NOT fire."""
    short = thr + eps if op == "lt" else thr - eps
    assert dtc not in _rule_codes(_ConstField(field, short)), (
        f"{dtc}: {field}={short} (just short of {op} {thr}) wrongly fired"
    )


# =================================================================================
# AXIS 2 — MULTI-FAULT COMBINATIONS (composed at test level).
# =================================================================================

# Different-field pairs: both DTCs must fire, neither suppressed.
COMBO_PAIRS = [
    ((fp.CoolantBlockage, fp.CellImbalance), {"P0C73", "P1A15"}),
    ((fp.HVIsolationFault, fp.ChargePortOverheat), {"P0AA6", "P0C2E"}),
    ((fp.InverterDegradation, fp.SensorDropout), {"P0A78", "U0100"}),
    ((fp.CellImbalance, fp.ChargePortOverheat), {"P1A15", "P0C2E"}),
]


@pytest.mark.parametrize(
    "profiles,expected",
    COMBO_PAIRS,
    ids=["+".join(p.__name__ for p in c[0]) for c in COMBO_PAIRS],
)
def test_combo_both_dtcs_fire_no_interference(profiles, expected):
    """Two faults on different fields: BOTH DTCs fire; neither suppresses the other."""
    composite = _Composite(*[p() for p in profiles])
    codes = _rule_codes(composite)
    assert expected <= codes, f"combo {expected} not all fired; got {sorted(codes)}"


def test_combo_triple_all_fire():
    """Three different-field faults at once — all three DTCs fire."""
    composite = _Composite(fp.CoolantBlockage(), fp.CellImbalance(), fp.ChargePortOverheat())
    codes = _rule_codes(composite)
    assert {"P0C73", "P1A15", "P0C2E"} <= codes, f"got {sorted(codes)}"


def test_combo_same_field_rule_based_no_suppression():
    """Two faults whose profiles BOTH add to temperature (Coolant + Inverter) still
    each fire their own DTC — because P0C73/P0A78 key off coolant_flow_rate /
    inverter_efficiency, not the shared temperature. Compounding temperature does not
    suppress detection of either."""
    composite = _Composite(fp.CoolantBlockage(), fp.InverterDegradation())
    codes = _rule_codes(composite)
    assert {"P0C73", "P0A78"} <= codes, f"same-field combo suppressed a DTC: {sorted(codes)}"


def test_combo_order_independent():
    """Composition order does not change the fired DTC set for additive same-field
    profiles (Coolant + Inverter both add temperature)."""
    a = _rule_codes(_Composite(fp.CoolantBlockage(), fp.InverterDegradation()), vid="OA")
    b = _rule_codes(_Composite(fp.InverterDegradation(), fp.CoolantBlockage()), vid="OB")
    assert a == b, f"order-dependent result: {sorted(a)} vs {sorted(b)}"


def test_combo_rule_plus_slope_same_field():
    """Coolant + ThermalRunaway both push temperature. Sane behavior: the rule-based
    coolant DTC (P0C73) still fires AND the compounded temperature ramp is caught by
    the slope layer. (ThermalRunaway has no rule-based DTC — it's slope-only.)"""
    composite = _Composite(fp.CoolantBlockage(), fp.ThermalRunawayPrecursor())
    assert "P0C73" in _rule_codes(composite, vid="RS"), "rule-based P0C73 suppressed by combo"
    assert _slope_fires(composite, vid="RS2"), "slope layer missed the compounded temp ramp"


# =================================================================================
# AXIS 3 — PER-FAULT VARIANTS (distinct claim: window adequacy across rates).
# =================================================================================

# (field, dtc, direction, rate, window) — steeper crosses sooner, shallower later;
# each must still fire within ITS window. Distinct from base = tests rate/window fit.
RATE_VARIANTS = [
    ("coolant_flow_rate", "P0C73", -1, 0.30, 60),   # steep drain
    ("coolant_flow_rate", "P0C73", -1, 0.05, 200),  # shallow drain, needs wider window
    ("charge_port_temp", "P0C2E", +1, 2.0, 60),     # steep rise
    ("charge_port_temp", "P0C2E", +1, 0.4, 200),    # shallow rise, needs wider window
]


@pytest.mark.parametrize(
    "field,dtc,direction,rate,window",
    RATE_VARIANTS,
    ids=[f"{v[1]}-rate{v[3]}" for v in RATE_VARIANTS],
)
def test_rate_variant_caught_in_its_window(field, dtc, direction, rate, window):
    """A steeper and a shallower version of a fault are each caught within their own
    window — confirms window adequacy across detection rates, not just the base rate."""
    profile = _LinearRate(field, rate, direction)
    assert dtc in _rule_codes(profile, window=window), (
        f"{dtc} at rate {rate} not caught within {window} ticks"
    )
