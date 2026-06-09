"""Phase 2 (second half) — fault profiles.

Each profile's apply(reading, t) returns a dict of overrides that the simulator
applies via reading.update(overrides). Profiles mutate the reading to drive a
sensor field across its DTC threshold over time, modelling a specific physical
failure.

CANONICAL-FIELD CONTRACT (CLAUDE.md #2): every key a profile reads or returns MUST
be a canonical field name. The original draft's silent bug was a profile returning
a synonym the engine never saw (e.g. coolant_flow vs coolant_flow_rate), so the
fault never fired. test_fault_profiles.py asserts every returned key is in the
simulator's output contract (CANONICAL_FIELDS | CONTEXT_FIELDS), imported from the
single source — not retyped here.

Crossing times below are the *intended* targets from the plan; the actual crossing
ticks are confirmed by the crossing-check tests, not assumed from slope arithmetic.

P0A1B (pack voltage weak) and P0AFA (SoH low) are threshold-only and intentionally
have NO profile.
"""


class ThermalRunawayPrecursor:
    """Slow temperature ramp — the Phase 3 SLOPE-detector target, not a rule-based DTC.

    A single-window z-score is structurally blind to this ramp, so it won't fire
    through the rule engine; the slope detector (15-tick window, 0.20 °C/tick) catches
    it. The +0.4 * t ramp gives the ~0.4 °C/tick slope thermal_detector_comparison.py
    is calibrated against.
    """

    def apply(self, reading, t):
        return {
            "temperature": reading["temperature"] + 0.4 * t,  # ramps up
            "current": reading["current"] * 1.3,
        }


class CoolantBlockage:
    """Pump-seizure model (Decision 4): steep coolant drain crosses the 4.0 P0C73
    threshold by ~t=21 from a ~6.5 baseline — inside the 30s latency target. Also
    heats the pack as cooling fails (slope side caught by the trend layer)."""

    def apply(self, reading, t):
        return {
            "coolant_flow_rate": max(0, reading["coolant_flow_rate"] - 0.12 * t),
            "temperature": reading["temperature"] + 0.2 * t,
        }


class CellImbalance:
    """Gradual cell drift: cell_voltage_delta creeps past the 0.05 P1A15 threshold
    around t≈250 (why Phase 4 uses a 400-tick window). Also slowly sags pack voltage, so
    on a long enough run this profile ALSO trips P0A1B (pack_voltage < 315 V, ~t≈700).

    MULTI-DTC BY DESIGN (decided before Phase 6 — see CLAUDE.md, "fault profiles keep
    multi-DTC behavior"): a profile may legitimately trip more than one DTC. CellImbalance
    trips P1A15 (its designed code) and, on a sufficiently long run, P0A1B via the pack
    sag. This is KEPT, not "fixed": forcing one-DTC-per-profile would contradict the Phase-4
    multi-fault discipline (the combo tests assert several correct DTCs fire at once because
    each keys off a distinct trigger field, using subset checks that tolerate extras) and
    real faults cascade — so multi-DTC is both more realistic and consistent with what is
    already validated. The incidental P0A1B is therefore documented EXPECTED behavior, not a
    latent surprise. For metrics it is an "incidental/secondary DTC", NEVER a false positive
    (a false positive is a DTC on an UN-injected vehicle; EV-0006 is genuinely faulted).

    HONEST about the mechanism (correction-with-provenance): the pack-voltage sag is an
    INDEPENDENT flat ramp (`-0.05 * t`), NOT derived from the cell-imbalance delta. The
    co-occurrence of imbalance and low pack voltage is intentional, but the coupling is
    SIMPLIFIED — two parallel linear ramps, not a delta-driven model where the worst-cell
    delta physically pulls the pack terminal voltage down. A higher-fidelity profile would
    couple them; this models the symptom co-occurrence, not the underlying circuit."""

    def apply(self, reading, t):
        return {
            "cell_voltage_delta": reading["cell_voltage_delta"] + 0.0002 * t,
            "pack_voltage": reading["pack_voltage"] - 0.05 * t,
        }


class SensorDropout:
    """BMS communication loss: heartbeat goes None immediately (U0100 from t=0)."""

    def apply(self, reading, t):
        return {"bms_heartbeat": None}


class HVIsolationFault:
    """HV isolation breakdown: isolation_resistance drains toward 0, crossing the
    500 P0AA6 threshold from a ~2000 baseline."""

    def apply(self, reading, t):
        return {"isolation_resistance": max(0, reading["isolation_resistance"] - 5 * t)}


class ChargePortOverheat:
    """Connector resistance rises (corrosion/poor seating) → port heats under charge.
    Crosses the 85 °C P0C2E threshold around t≈55 from a ~35 °C baseline."""

    def apply(self, reading, t):
        return {"charge_port_temp": reading["charge_port_temp"] + 0.9 * t}


class InverterDegradation:
    """Gate-driver / IGBT thermal wear → efficiency sags below the 0.88 P0A78 threshold
    (crosses around t≈75 from a ~0.94 baseline). Also dumps waste heat."""

    def apply(self, reading, t):
        return {
            "inverter_efficiency": reading["inverter_efficiency"] - 0.0008 * t,
            "temperature": reading["temperature"] + 0.1 * t,
        }
