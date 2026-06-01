# FaultLine

Vehicle diagnostic fault-detection engine. Ingests OBD-II / CAN bus telemetry, matches against known fault profiles, and emits Diagnostic Trouble Codes (DTCs).

Covers **5 subsystems** (battery pack, thermal, motor controller, BMS, charging) and **8 DTCs**.

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
