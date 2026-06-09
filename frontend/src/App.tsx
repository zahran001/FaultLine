import { useState } from 'react'
import { FleetOverview } from './views/FleetOverview'
import { VehicleDetail } from './views/VehicleDetail'
import './app.css'

// Two top-level screens: the fleet grid, and a per-vehicle detail (which itself
// holds the readings/detections panel and the fault-timeline). State lives only in
// React — no router, no storage. Selecting a card sets the active vehicle id.
export default function App() {
  const [selected, setSelected] = useState<string | null>(null)

  return (
    <div className="app">
      <header className="app__bar">
        <div className="app__brand">
          <span className="app__mark" aria-hidden>
            <span className="app__mark-bar" />
          </span>
          <div>
            <h1 className="app__title">FAULTLINE</h1>
            <span className="label">EV FLEET DIAGNOSTICS · CONTROL ROOM</span>
          </div>
        </div>
        <nav className="app__crumbs">
          <button
            className={`crumb ${selected ? '' : 'crumb--active'}`}
            onClick={() => setSelected(null)}
          >
            FLEET
          </button>
          {selected && (
            <>
              <span className="crumb__sep">/</span>
              <span className="crumb crumb--active">{selected}</span>
            </>
          )}
        </nav>
      </header>

      <main className="app__main">
        {selected === null ? (
          <FleetOverview onSelect={setSelected} />
        ) : (
          <VehicleDetail vehicleId={selected} onBack={() => setSelected(null)} />
        )}
      </main>
    </div>
  )
}
