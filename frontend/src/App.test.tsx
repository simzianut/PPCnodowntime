import { render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import type { Location } from './api/types'
import { StationDetail } from './App'

const location = {
  location_id: 'station-1', name: 'Central Station', partner: 'Example', address: 'Main Street', county_code: 'B',
  lat: 44.4, lon: 26.1, context_type: 'retail', primary_source: 'fixture', confidence: 'high',
  charge_points: [
    { cp_id: 'ac-sleep', power_class: 'AC_22', max_kw: 22, connectors: ['Type 2'], current_type: 'AC' },
    { cp_id: 'ac-idle', power_class: 'AC_22', max_kw: 22, connectors: ['Type 2'], current_type: 'AC' },
    { cp_id: 'dc-charge', power_class: 'DC_50', max_kw: 50, connectors: ['CCS'], current_type: 'DC' },
  ],
} as Location

describe('selected station summary', () => {
  it('shows identity, location, status mix and energy saved while omitting detailed metrics', () => {
    render(<StationDetail location={location} cpState={{ 'ac-sleep': 'STANDBY', 'ac-idle': 'IDLE', 'dc-charge': 'CHARGING' }} onClose={vi.fn()} />)

    expect(screen.getByRole('heading', { name: 'Central Station' })).toBeInTheDocument()
    expect(screen.getByText('Main Street · B')).toBeInTheDocument()
    expect(screen.getByText('Stand by')).toBeInTheDocument()
    expect(screen.getByText('Idle')).toBeInTheDocument()
    expect(screen.getByText('Charging')).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'Energy saved level' })).toBeInTheDocument()
    expect(screen.getByText('32%')).toBeInTheDocument()

    for (const label of ['Current output', 'Expected wait', 'Anomaly score', 'Partner', 'Site type', 'Source', 'Position', 'Arrival probability within 30 min', 'Agent commands']) {
      expect(screen.queryByText(label)).not.toBeInTheDocument()
    }
    expect(screen.queryByText('Charge points')).not.toBeInTheDocument()
  })

  it('shows an unavailable energy level when all connectors are charging or offline', () => {
    render(<StationDetail location={location} cpState={{ 'ac-sleep': 'OFFLINE', 'ac-idle': 'CHARGING', 'dc-charge': 'CHARGING' }} onClose={vi.fn()} />)
    expect(screen.getByText('Unavailable')).toBeInTheDocument()
    expect(screen.queryByRole('progressbar')).not.toBeInTheDocument()
  })
})
