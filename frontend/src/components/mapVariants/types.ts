import type { Location, Run, State } from '../../api/types'

export interface MapVariantProps {
  locations: Location[]
  cpState: Record<string, State>
  selectedLocationId?: string
  onSelect: (location: Location | null) => void
  run: Run | null
}
