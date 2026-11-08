import { render, screen, within } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { STATE_COLORS } from '../../api/types'
import { MapLegend } from './MapLegend'

describe('map legend', () => {
  it('explains the cluster asleep-share gradient and every station state color', () => {
    render(<MapLegend />)

    expect(screen.getByText(/Cluster color · Average asleep share/)).toBeInTheDocument()
    expect(screen.getByRole('region', { name: 'Cluster color meaning' })).toHaveTextContent('Low')
    expect(screen.getByRole('region', { name: 'Cluster color meaning' })).toHaveTextContent('High')

    const statusLegend = screen.getByRole('region', { name: 'Station status colors' })
    const labels = ['Charging', 'Idle', 'Stand by', 'Waking', 'Deep sleep', 'Offline']
    for (const label of labels) expect(within(statusLegend).getByText(label)).toBeInTheDocument()
    const order = ['CHARGING', 'IDLE', 'STANDBY', 'WAKING', 'DEEP_SLEEP', 'OFFLINE'] as const
    const swatches = Array.from(statusLegend.querySelectorAll<HTMLElement>('.nm-legend-statuses i'))
    expect(swatches).toHaveLength(order.length)
    order.forEach((state, index) => {
      const channels = STATE_COLORS[state].match(/[a-f\d]{2}/gi)?.map((channel) => parseInt(channel, 16))
      expect(getComputedStyle(swatches[index]).backgroundColor).toBe(`rgb(${channels?.join(', ')})`)
    })
  })
})
