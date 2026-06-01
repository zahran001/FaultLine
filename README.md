# FaultLine

Vehicle diagnostic fault-detection engine. Ingests OBD-II / CAN bus telemetry, matches against known fault profiles, and emits Diagnostic Trouble Codes (DTCs).

Covers **5 subsystems** (battery pack, thermal, motor controller, BMS, charging) and **8 DTCs**.

## Testing

A **42-case end-to-end harness** injects each fault through the real simulator, feeds
readings to the diagnostic engine, and asserts the correct DTC (or slope detection)
fires — broken down as:

- **9 base cases** — 6 rule-based inject→DTC, thermal slope detection, no-false-positives, detection latency.
- **33 expansion cases** — 21 boundary (just-past fires / exactly-at and just-short do not, full pipeline), 8 multi-fault combinations, 4 per-fault rate variants.

The full pytest suite is **100 tests** (the harness plus registry/contract, simulator,
fault-profile, and detector-calibration tests).

## Setup

```bash
python -m venv .venv
.venv\Scripts\activate   # Windows
pip install -r requirements.txt
```

## Run tests

```bash
pytest
```

## Data

Place NASA dataset CSVs in `data/` (git-ignored).
