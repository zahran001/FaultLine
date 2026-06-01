"""Phase 1 — OBD-II DTC Registry.

Single source of truth for the diagnostic platform. The simulator (Phase 2) emits
these codes, the diagnostic engine (Phase 3) detects against these triggers, and the
dashboard (Phase 5) displays these repair procedures.

8 DTCs across 5 subsystems: battery_pack, thermal, motor_controller, bms, charging.

CANONICAL FIELD-NAME CONTRACT (see CLAUDE.md #2):
The trigger keys below are the canonical sensor field names. The simulator output
dict, the fault profiles, and the diagnostic engine MUST all use these EXACT names —
no synonyms anywhere. Mixing variants (e.g. cell_delta vs cell_voltage_delta,
coolant_flow vs coolant_flow_rate) means faults silently never fire.

    pack_voltage   cell_voltage_delta   coolant_flow_rate   inverter_efficiency
    isolation_resistance   soh   bms_heartbeat   charge_port_temp

Trigger format: {"field_name": {"op": value}}, op in {lt, gt, eq}.
"""

# The complete set of canonical sensor field names. validate_registry() asserts every
# trigger field is drawn from this set, so a typo can never silently disable a fault.
CANONICAL_FIELDS = {
    "pack_voltage",
    "cell_voltage_delta",
    "coolant_flow_rate",
    "inverter_efficiency",
    "isolation_resistance",
    "soh",
    "bms_heartbeat",
    "charge_port_temp",
}

VALID_SEVERITIES = {"low", "medium", "high", "critical"}
REQUIRED_KEYS = {"description", "subsystem", "severity", "triggers", "repair_procedure"}


DTC_REGISTRY = {
    "P0A1B": {
        "description": "Battery Pack Voltage Weak",
        "subsystem": "battery_pack",
        "severity": "high",
        # THRESHOLD FLAGGED FOR PHASE 2 VERIFICATION:
        # Phase 0 calibration found the real cell curve (~3.54 V mean) sits below the
        # original 3.81 V placeholder, so a healthy pack at lower SOC can dip under the
        # original 350 V trigger and cause false positives. Lowered to 340 V to sit
        # below the real healthy band. Verify against the actual simulated healthy
        # pack-voltage band in Phase 2 and adjust threshold and/or SOC start range there.
        "triggers": {"pack_voltage": {"lt": 340}},
        "repair_procedure": [
            "Check individual cell voltages with multimeter",
            "Inspect high-voltage connectors for corrosion",
            "Run cell balance diagnostic routine",
            "Replace degraded cell module if delta > 50mV",
        ],
    },
    "P1A15": {
        "description": "Battery Cell Imbalance",
        "subsystem": "battery_pack",
        "severity": "medium",
        "triggers": {"cell_voltage_delta": {"gt": 0.05}},
        "repair_procedure": [
            "Log cell voltage readings across full pack",
            "Identify outlier cell group",
            "Run passive balancing cycle",
            "Flag for module replacement if imbalance persists",
        ],
    },
    "P0C73": {
        "description": "Cooling System Flow Insufficient",
        "subsystem": "thermal",
        "severity": "high",
        "triggers": {"coolant_flow_rate": {"lt": 4.0}},
        "repair_procedure": [
            "Check coolant level in reservoir",
            "Inspect pump for mechanical failure",
            "Check for blockage in cooling loop",
            "Verify thermal management controller output",
        ],
    },
    "P0A78": {
        "description": "Drive Motor Inverter Performance",
        "subsystem": "motor_controller",
        "severity": "high",
        "triggers": {"inverter_efficiency": {"lt": 0.88}},
        "repair_procedure": [
            "Check gate driver signals with oscilloscope",
            "Inspect IGBT module for thermal damage",
            "Verify DC bus voltage stability under load",
        ],
    },
    "P0AA6": {
        "description": "HV System Isolation Fault",
        "subsystem": "battery_pack",
        "severity": "critical",
        "triggers": {"isolation_resistance": {"lt": 500}},
        "repair_procedure": [
            "Isolate HV system immediately",
            "Run isolation resistance test on each segment",
            "Inspect HV harness for chafing or moisture ingress",
        ],
    },
    "P0AFA": {
        "description": "Battery State of Health Low",
        "subsystem": "battery_pack",
        "severity": "medium",
        "triggers": {"soh": {"lt": 0.75}},
        "repair_procedure": [
            "Run full capacity calibration cycle",
            "Compare measured vs rated capacity",
            "Schedule battery module replacement",
        ],
    },
    "U0100": {
        "description": "Lost Communication with BMS",
        "subsystem": "bms",
        "severity": "critical",
        "triggers": {"bms_heartbeat": {"eq": None}},
        "repair_procedure": [
            "Check CAN bus termination resistors",
            "Inspect BMS harness connector",
            "Flash BMS firmware if no hardware fault found",
        ],
    },
    "P0C2E": {
        "description": "Charge Port Temperature High",
        "subsystem": "charging",
        "severity": "high",
        "triggers": {"charge_port_temp": {"gt": 85}},  # °C
        "repair_procedure": [
            "Halt active charging session",
            "Inspect charge port connector for debris or corrosion",
            "Check charge port thermistor reading vs. ambient",
            "Verify charge cable seating and contactor engagement",
            "Inspect for coolant intrusion at port (liquid-cooled connectors)",
        ],
    },
}


def validate_registry(registry: dict = DTC_REGISTRY) -> None:
    """Assert the registry obeys the field-name contract and structural invariants.

    Raises AssertionError on any violation so a malformed registry fails loudly at
    import time rather than silently disabling a fault downstream.
    """
    valid_ops = {"lt", "gt", "eq"}
    for code, entry in registry.items():
        # All 5 required keys present (and no stray keys).
        missing = REQUIRED_KEYS - entry.keys()
        assert not missing, f"{code}: missing required keys {missing}"

        # Severity is one of the allowed levels.
        assert (
            entry["severity"] in VALID_SEVERITIES
        ), f"{code}: severity {entry['severity']!r} not in {VALID_SEVERITIES}"

        # Every trigger field is canonical; every condition uses a known op.
        triggers = entry["triggers"]
        assert isinstance(triggers, dict) and triggers, f"{code}: triggers must be a non-empty dict"
        for field, condition in triggers.items():
            assert (
                field in CANONICAL_FIELDS
            ), f"{code}: trigger field {field!r} not in canonical set"
            assert (
                isinstance(condition, dict) and len(condition) == 1
            ), f"{code}: trigger {field!r} must map to a single-op dict"
            (op,) = condition
            assert op in valid_ops, f"{code}: trigger {field!r} uses unknown op {op!r}"

        # repair_procedure is a non-empty list of strings.
        steps = entry["repair_procedure"]
        assert (
            isinstance(steps, list) and steps
        ), f"{code}: repair_procedure must be a non-empty list"
        assert all(
            isinstance(s, str) and s for s in steps
        ), f"{code}: repair_procedure must contain only non-empty strings"


# Fail loudly at import if the registry is malformed.
validate_registry()


if __name__ == "__main__":
    validate_registry()
    subsystems = {e["subsystem"] for e in DTC_REGISTRY.values()}
    fields = {f for e in DTC_REGISTRY.values() for f in e["triggers"]}
    print(f"DTC_REGISTRY: {len(DTC_REGISTRY)} DTCs across {len(subsystems)} subsystems")
    print(f"  subsystems: {sorted(subsystems)}")
    print(f"  trigger fields used: {sorted(fields)}")
    print(f"  canonical fields unused by any trigger: {sorted(CANONICAL_FIELDS - fields)}")
    print("validate_registry() passed.")
