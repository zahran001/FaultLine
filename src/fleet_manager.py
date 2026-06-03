"""Phase 5, Step 1 — FleetManager: the live, in-process simulation loop.

Owns ALL per-vehicle engine state and advances every vehicle one tick via
tick_all(). This is the long-lived object the FastAPI background task (Step 3)
will drive; in Step 1 it is driven by a plain print loop (see __main__) so the
behavior can be watched before any API exists.

LAYER-ON-TOP CONTRACT (phase5_plan.md): the Phase 0–4 engine is FROZEN. This file
constructs and calls VehicleSimulator.tick(), RuleBasedDiagnostics.run(), and
StatisticalDiagnostics.update/detect_anomalies/detect_trend EXACTLY as Phase 4
tests do. It does not modify any of them.

Per-vehicle state held here:
  - VehicleSimulator   (one per vehicle, seeded for the demo; production=None)
  - StatisticalDiagnostics (one per vehicle — its rolling buffers ARE per-vehicle state)
  - latest_reading     (the most recent canonical reading dict)
  - latest_detections  (rule-based DTCs + slope trends + z-score anomalies this tick)
RuleBasedDiagnostics is stateless, so a single shared instance is correct.

STAGGERED FAULT INJECTION (Decision F support) — why a wrapper, not an engine change:
  The demo roster injects a fault at a tick OFFSET (inject_at_tick) so the dashboard
  shows a believable spread (a card maturing amber→red mid-run, not everything broken
  at t=0). But the Phase 2 simulator takes its fault_profile at construction and the
  profiles compute against ABSOLUTE t (e.g. CoolantBlockage's -0.12 * t). If we simply
  set sim.fault_profile at tick 40, the profile would see t=40 on its first apply and
  jump discontinuously.

  Fix (a layer above the frozen simulator, never inside it): the fault is held OUT of
  the simulator until its injection tick, then attached wrapped in _OffsetProfile,
  which feeds the profile a relative t (ticks-since-injection). The profile's physical
  story therefore starts at its own t=0 the moment it is injected — exactly as a real
  fault would begin from the moment it occurs. The simulator's fault_profile hook is
  used as-is; only WHAT we hand it is managed here.
"""

import fault_profiles
from dashboard_config import DT, DEMO_FLEET
from diagnostic_engine import RuleBasedDiagnostics, StatisticalDiagnostics
from simulator import VehicleSimulator

# Trending faults are routed to the slope layer; this is the set of fields the slope
# detector watches (the thermal channel — same routing Phase 3/4 established).
TREND_FIELDS = ("temperature",)


class _OffsetProfile:
    """Wraps a Phase 2 fault profile so it sees t RELATIVE to its injection tick.

    The wrapped profile is untouched; we only translate the t argument so a fault
    injected at absolute tick 40 begins its physical story at its own t=0. This keeps
    the staggered-injection demo honest without modifying the simulator or the profile.
    """

    def __init__(self, profile, inject_at_tick):
        self._profile = profile
        self._inject_at_tick = inject_at_tick

    def apply(self, reading, t):
        return self._profile.apply(reading, t - self._inject_at_tick)


class _VehicleState:
    """All per-vehicle state the FleetManager owns for one vehicle."""

    def __init__(self, vehicle_id, seed, fault_name, inject_at_tick):
        self.vehicle_id = vehicle_id
        self.sim = VehicleSimulator(vehicle_id, fault_profile=None, seed=seed)
        self.stat = StatisticalDiagnostics()
        # Fault is held here until its injection tick (None for healthy vehicles).
        self.pending_fault_name = fault_name
        self.inject_at_tick = inject_at_tick
        self.injected = fault_name is None  # healthy => nothing to inject

        self.latest_reading = None
        self.latest_rule_dtcs = []
        self.latest_trends = []
        self.latest_anomalies = []

    def _maybe_inject(self):
        """Attach the pending fault (wrapped for relative t) once its tick arrives."""
        if self.injected:
            return
        # sim.t is the simulator's own tick counter; it advances by DT each tick.
        if self.sim.t >= self.inject_at_tick:
            profile_cls = getattr(fault_profiles, self.pending_fault_name)
            self.sim.fault_profile = _OffsetProfile(profile_cls(), self.inject_at_tick)
            self.injected = True


class FleetManager:
    """Singleton-style owner of the live fleet. One instance per process.

    tick_all() advances every vehicle one tick, runs all three detectors, and stores
    the results as per-vehicle latest_* state. It is synchronous and fast (numpy +
    dict ops); the Step 3 background task awaits TICK_INTERVAL between calls.
    """

    def __init__(self, roster=DEMO_FLEET, dt=DT):
        self.dt = dt
        self.tick_count = 0
        self.rule_engine = RuleBasedDiagnostics()  # stateless → shared
        self.vehicles = {
            vid: _VehicleState(vid, seed, fault_name, inject_at_tick)
            for (vid, seed, fault_name, inject_at_tick) in roster
        }

    def tick_all(self):
        """Advance every vehicle exactly one tick and refresh its detections."""
        for state in self.vehicles.values():
            state._maybe_inject()

            reading = state.sim.tick(self.dt)
            state.stat.update(reading)

            state.latest_reading = reading
            state.latest_rule_dtcs = self.rule_engine.run(reading)
            state.latest_trends = state.stat.detect_trend(
                state.vehicle_id, fields=TREND_FIELDS
            )
            state.latest_anomalies = state.stat.detect_anomalies(state.vehicle_id)

        self.tick_count += 1


# — Step 1 watch loop: no API, just print fleet state so behavior is observable ———
if __name__ == "__main__":
    import sys

    n_ticks = int(sys.argv[1]) if len(sys.argv) > 1 else 120
    fleet = FleetManager()

    print(f"FleetManager: {len(fleet.vehicles)} vehicles, dt={fleet.dt}, "
          f"running {n_ticks} ticks (no wall-clock sleep — Step 1 watch mode)\n")

    # Track first-fire ticks per vehicle so we can report crossing latencies.
    first_rule = {}
    first_trend = {}

    for _ in range(n_ticks):
        fleet.tick_all()
        t = fleet.tick_count
        for vid, st in fleet.vehicles.items():
            if st.latest_rule_dtcs and vid not in first_rule:
                first_rule[vid] = (t, [d["dtc"] for d in st.latest_rule_dtcs])
            if st.latest_trends and vid not in first_trend:
                first_trend[vid] = (t, [tr["field"] for tr in st.latest_trends])

    print(f"=== Fleet state after {fleet.tick_count} ticks ===")
    for vid, st in fleet.vehicles.items():
        r = st.latest_reading
        rule = [d["dtc"] for d in st.latest_rule_dtcs]
        trend = [tr["field"] for tr in st.latest_trends]
        anom = [a["field"] for a in st.latest_anomalies]
        fault = (st.pending_fault_name or "healthy")
        inj = "" if st.inject_at_tick is None else f"@{st.inject_at_tick}"
        print(
            f"  {vid}  [{fault}{inj}]  "
            f"pack={r['pack_voltage']:.1f} temp={r['temperature']:.1f} "
            f"flow={r['coolant_flow_rate']:.2f} eff={r['inverter_efficiency']:.3f}  "
            f"rule={rule} trend={trend} anom={anom}"
        )

    print("\n=== First-fire ticks (latency from injection) ===")
    for vid, st in fleet.vehicles.items():
        inj = st.inject_at_tick
        parts = []
        if vid in first_rule:
            ft, codes = first_rule[vid]
            lat = "" if inj is None else f" (+{ft - inj} from inject)"
            parts.append(f"rule {codes} @t={ft}{lat}")
        if vid in first_trend:
            ft, fields = first_trend[vid]
            lat = "" if inj is None else f" (+{ft - inj} from inject)"
            parts.append(f"trend {fields} @t={ft}{lat}")
        if parts:
            print(f"  {vid}: " + "; ".join(parts))
        elif st.pending_fault_name:
            print(f"  {vid}: {st.pending_fault_name} — NO fire within {n_ticks} ticks")
