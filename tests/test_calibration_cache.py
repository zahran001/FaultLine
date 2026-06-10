"""Guard for the committed calibration cache (src/calibration_cache.json).

WHY this exists: src/calibration.py derives its constants from NASA's 577 MB B0005
dataset, which lives under the gitignored data/ folder and is therefore ABSENT in CI.
Before the cache, every test errored at collection (FileNotFoundError on data/metadata.csv)
because importing simulator -> calibration ran pd.read_csv at import time. The cache lets
data-less environments load identical locked constants; these tests keep it honest.

They assert:
  - the cache exists and has the locked CALIBRATION shape (5 keys, parallel SOC/voltage
    arrays of equal length) — the contract simulator.py consumes;
  - the cache does NOT drift from the raw dataset (skipped when the data is absent, e.g.
    in CI — it only has teeth where the data exists, i.e. dev machines that regenerate it);
  - _resolve_calibration falls back to the cache when the dataset is missing — the exact
    code path CI takes — without re-reading the raw data.

Flat imports (no package): resolved via pythonpath = ["src"] in pyproject.toml.
"""

import pytest

import calibration
from calibration import (
    CALIBRATION,
    CALIBRATION_CACHE,
    METADATA_CSV,
    _build_calibration,
    _load_cached_calibration,
    _resolve_calibration,
)

LOCKED_KEYS = {
    "nominal_cell_voltage_mean",
    "nominal_cell_voltage_std",
    "thermal_rise_coefficient",
    "discharge_curve_soc",
    "discharge_curve_voltage",
}


def test_cache_file_exists():
    assert CALIBRATION_CACHE.exists(), (
        f"{CALIBRATION_CACHE} is missing — CI cannot load calibration without it. "
        f"Regenerate with `python src/calibration.py` (dataset must be present)."
    )


def test_cache_has_locked_shape():
    cached = _load_cached_calibration()
    assert set(cached) == LOCKED_KEYS
    soc = cached["discharge_curve_soc"]
    voltage = cached["discharge_curve_voltage"]
    # Locked contract (CLAUDE.md): two PARALLEL arrays, not a list of pairs.
    assert len(soc) == len(voltage)
    assert all(isinstance(x, (int, float)) for x in soc + voltage)


def test_cache_matches_freshly_built_data():
    """The committed cache must equal what the raw dataset produces — no drift.

    Skipped when the dataset is absent (CI), where there is nothing to compare against.
    On a dev machine with the data, a stale cache fails here: regenerate it.
    """
    if not METADATA_CSV.exists():
        pytest.skip("NASA dataset absent — no source to validate the cache against")
    assert _load_cached_calibration() == _build_calibration()


def test_resolve_falls_back_to_cache_without_data(monkeypatch, tmp_path):
    """Simulate the CI condition: point METADATA_CSV at a nonexistent path so the
    raw-data branch is skipped, and confirm _resolve_calibration returns the cache."""
    monkeypatch.setattr(calibration, "METADATA_CSV", tmp_path / "nope.csv")
    assert _resolve_calibration() == _load_cached_calibration()


def test_loaded_calibration_matches_cache_contents():
    """Whatever source produced the live CALIBRATION, it equals the committed cache
    (the drift guard above ensures data and cache agree, so this holds either way)."""
    assert CALIBRATION == _load_cached_calibration()
