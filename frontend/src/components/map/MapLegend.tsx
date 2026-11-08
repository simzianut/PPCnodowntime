import { STATE_COLORS } from '../../api/types'
import { STATUS_LABELS, STATUS_ORDER } from '../../map/energy'

export function MapLegend() {
  return (
    <div className="nm-legend" aria-label="Map legend">
      <section className="nm-legend-group nm-legend-asleep" aria-label="Cluster color meaning">
        <strong>Cluster color · Average asleep share</strong>
        <div className="nm-gradient" aria-label="Low to high average asleep share"><span>Low</span><i /><span>High</span></div>
        <small>Mean asleep share; charging and offline connectors are excluded.</small>
      </section>
      <section className="nm-legend-group nm-legend-status" aria-label="Station status colors">
        <strong>Station pin · Dominant connector status</strong>
        <div className="nm-legend-statuses">{STATUS_ORDER.map((state) => <span key={state}><i style={{ background: STATE_COLORS[state] }} />{STATUS_LABELS[state]}</span>)}</div>
      </section>
    </div>
  )
}
