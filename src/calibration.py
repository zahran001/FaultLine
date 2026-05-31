"""Phase 0 — NASA Battery Dataset Calibration.

Derives real-world-grounded simulator constants from NASA's Prognostics Center
18650 Li-ion cycling data (battery B0005).

Data layout (under the gitignored data/ folder):
  - data/metadata.csv      : index of test runs. Columns include `type`
                             (charge/discharge/impedance), `battery_id`,
                             `filename` (per-cycle file), `Capacity`.
  - data/data/<file>.csv   : per-cycle time series with columns
                             Voltage_measured, Current_measured,
                             Temperature_measured, Current_load, Voltage_load, Time.

The CALIBRATION dict shape is a locked contract (see CLAUDE.md):
the discharge curve is TWO PARALLEL ARRAYS (soc / voltage), not a list of pairs,
because np.interp(x, xp, fp) consumes it that way downstream in the simulator.
"""

from pathlib import Path

import numpy as np
import pandas as pd

# --- Paths (pathlib only; never hardcode separators) ---------------------------
DATA_DIR = Path(__file__).resolve().parent.parent / "data"
METADATA_CSV = DATA_DIR / "metadata.csv"
CYCLE_DIR = DATA_DIR / "data"

BATTERY_ID = "B0005"
N_EARLY_CYCLES = 5  # cycles closest to full health
SOC_BREAKPOINTS = [0.0, 0.2, 0.4, 0.6, 0.8, 1.0]

# Current the simulator models a 96S EV pack drawing under load (see simulator.py,
# `current = np.random.normal(120, 15)`). The NASA cell only ever draws ~2 A in
# constant-current discharge, so the thermal coefficient must be bridged from the
# cell's observed rise to the pack's current regime (see _thermal_rise_coefficient).
SIM_LOAD_CURRENT = 120.0
ACTIVE_DISCHARGE_CURRENT = 1.0  # |I| above this = under load (vs. rest periods)

# SOC window over which the 6-breakpoint discharge curve faithfully tracks voltage.
# Outside it (the steep knees near full charge and the cutoff cliff near depletion)
# a linear interpolation can't follow the curvature, so residuals there are curve-fit
# error, not sensor noise — see _curve_residual_std.
PLATEAU_SOC_LO, PLATEAU_SOC_HI = 0.15, 0.85


def _discharge_cycle_files(metadata: pd.DataFrame) -> list[Path]:
    """Return per-cycle file paths for B0005's first N_EARLY_CYCLES discharge runs.

    metadata.csv preserves chronological run order, so the first discharge rows
    for the battery are the ones closest to full health.
    """
    rows = metadata[
        (metadata["battery_id"] == BATTERY_ID)
        & (metadata["type"] == "discharge")
    ]
    early = rows.head(N_EARLY_CYCLES)
    return [CYCLE_DIR / str(name) for name in early["filename"]]


def _nominal_voltage_mean(cycle_files: list[Path]) -> float:
    """Mean of Voltage_measured pooled across the early discharge cycles.

    Full-sweep mean (~3.2-4.2 V, cell-level; the simulator applies the x96 pack
    scaling, so we deliberately do NOT scale here). This is the SOC-sweep average,
    not a single operating-point value.
    """
    voltages = np.concatenate(
        [pd.read_csv(f)["Voltage_measured"].to_numpy() for f in cycle_files]
    )
    return float(voltages.mean())


def _curve_residual_std(cycle_files: list[Path], curve_voltage: list[float]) -> float:
    """Std of PLATEAU voltage residuals around the discharge curve — in-state sensor noise.

    This is the std the simulator actually needs: in Phase 2 the baseline cell
    voltage is `np.interp(soc, curve_soc, curve_voltage) + normal(0, std)`, so the
    std must isolate per-tick sensor jitter from the SOC sweep itself. The full-sweep
    voltage std (~0.23 V) conflates the two and, after x96 pack scaling, would inject
    ~±22 V of random pack jitter every tick — randomly tripping voltage DTCs.

    Curve-residual std, restricted to the plateau (PLATEAU_SOC_LO..HI): for each early
    cycle, subtract the interpolated curve value at each sample's SOC from its measured
    voltage, keep only plateau samples, pool across cycles, and take the std. SOC is
    normalized elapsed Time, matching _discharge_curve.

    Why plateau-only: pooled over ALL SOC the residual std is ~0.10 V, but ~90% of that
    variance is *curve-fit error* at the knees — the 6-breakpoint linear curve can't
    track the steep cutoff cliff (SOC<0.15 residuals have std~0.17, mean~-0.26, i.e.
    structured, not random). On the flat plateau the curve faithfully represents the
    operating voltage, so the residual there is pure sensor scatter (~0.018, matching an
    independent tick-to-tick diff estimate of ~0.027 and the plan's ~0.042 ballpark).
    """
    soc_grid = np.array(SOC_BREAKPOINTS)
    curve = np.array(curve_voltage)
    residuals = []
    for f in cycle_files:
        df = pd.read_csv(f)
        t = df["Time"].to_numpy()
        v = df["Voltage_measured"].to_numpy()
        soc = 1.0 - (t - t.min()) / (t.max() - t.min())
        plateau = (soc >= PLATEAU_SOC_LO) & (soc <= PLATEAU_SOC_HI)
        v_curve = np.interp(soc[plateau], soc_grid, curve)
        residuals.append(v[plateau] - v_curve)
    return float(np.concatenate(residuals).std())


def _thermal_rise_coefficient(cycle_files: list[Path]) -> float:
    """Coefficient k for the simulator's  Temperature = 25 + k * Current²  model.

    PROVENANCE (read before trusting this number):
    A direct least-squares fit of Temperature vs Current² on this dataset is NOT
    meaningful. NASA's B0005 discharge cycles are *constant-current* (~2 A for the
    whole load phase), so Current² has essentially no spread to regress against,
    and temperature instead tracks accumulated heat over elapsed time. A raw
    polyfit(I², T) on this data yields a non-physical slope (~ -0.73).

    What IS physically real in the data: each early cycle heats the cell by a
    consistent ~14 °C above its ~24 °C rest baseline at its ~2 A (I²≈4.05) load.
    The simulator, however, models a 96S EV PACK drawing ~120 A — a different
    current regime entirely. So the coefficient returned here is the NASA-observed
    full-load temperature *rise magnitude*, scaled so it reproduces at the
    simulator's modeled pack current:

        k = ΔT_observed_NASA / SIM_LOAD_CURRENT²

    i.e. the rise magnitude is NASA-derived; the coefficient is scaled to the
    simulator's 120 A regime. It is deliberately NOT a raw fit of this dataset.
    """
    rises = []
    for f in cycle_files:
        df = pd.read_csv(f)
        i = df["Current_measured"].to_numpy()
        t = df["Temperature_measured"].to_numpy()
        under_load = np.abs(i) > ACTIVE_DISCHARGE_CURRENT
        # Rise from the start of the load phase to the cycle's peak temperature.
        rises.append(t[under_load].max() - t[under_load][0])
    delta_t = float(np.mean(rises))
    return delta_t / (SIM_LOAD_CURRENT ** 2)


def _discharge_curve(cycle_files: list[Path]) -> list[float]:
    """Averaged SOC→voltage curve at the 6 SOC breakpoints.

    SOC is derived as normalized elapsed Time within each cycle: at the start of
    discharge the cell is full (SOC=1.0, highest voltage) and at the end it is
    depleted (SOC=0.0, lowest voltage). We interpolate each cycle's
    Voltage_measured onto the shared SOC grid, then average across cycles.

    Chosen method: normalized elapsed Time (not cumulative charge). For these
    constant-current discharge cycles the two are near-equivalent, and Time is
    directly recorded and monotonic, avoiding integration noise.
    """
    soc_grid = np.array(SOC_BREAKPOINTS)
    per_cycle = []
    for f in cycle_files:
        df = pd.read_csv(f)
        t = df["Time"].to_numpy()
        v = df["Voltage_measured"].to_numpy()
        # SOC = 1.0 at start (t=0), 0.0 at end (t=max).
        soc = 1.0 - (t - t.min()) / (t.max() - t.min())
        # np.interp needs ascending xp, so sort by SOC.
        order = np.argsort(soc)
        v_on_grid = np.interp(soc_grid, soc[order], v[order])
        per_cycle.append(v_on_grid)
    return [float(x) for x in np.mean(per_cycle, axis=0)]


def _build_calibration() -> dict:
    metadata = pd.read_csv(METADATA_CSV)
    cycle_files = _discharge_cycle_files(metadata)

    thermal_k = _thermal_rise_coefficient(cycle_files)
    curve_voltage = _discharge_curve(cycle_files)
    v_mean = _nominal_voltage_mean(cycle_files)
    # Residual std depends on the curve, so compute it after the curve.
    v_std = _curve_residual_std(cycle_files, curve_voltage)

    return {
        "nominal_cell_voltage_mean": round(v_mean, 4),
        "nominal_cell_voltage_std": round(v_std, 4),
        "thermal_rise_coefficient": round(thermal_k, 6),
        "discharge_curve_soc": list(SOC_BREAKPOINTS),
        "discharge_curve_voltage": [round(v, 4) for v in curve_voltage],
    }


CALIBRATION = _build_calibration()


if __name__ == "__main__":
    import json

    print(f"Battery: {BATTERY_ID}  (first {N_EARLY_CYCLES} discharge cycles)\n")
    print(json.dumps(CALIBRATION, indent=4))

    # --- Sanity flags ----------------------------------------------------------
    print("\n--- sanity checks ---")
    v = CALIBRATION["discharge_curve_voltage"]
    monotonic = all(v[i] < v[i + 1] for i in range(len(v) - 1))
    print(f"discharge curve monotonic increasing in SOC: {monotonic}  {v}")

    k = CALIBRATION["thermal_rise_coefficient"]
    rise_at_sim = k * SIM_LOAD_CURRENT ** 2
    print(
        f"thermal_rise_coefficient: {k}  (plan reference ~0.00083)\n"
        f"  -> NASA-derived full-load rise, scaled to {SIM_LOAD_CURRENT:.0f} A sim regime\n"
        f"  -> reproduces +{rise_at_sim:.1f} °C at the simulator's modeled pack current"
    )

    m = CALIBRATION["nominal_cell_voltage_mean"]
    in_range = 3.0 <= m <= 4.3
    print(f"nominal_cell_voltage_mean: {m} V  (plausible cell range 3.0-4.3: {in_range})")
