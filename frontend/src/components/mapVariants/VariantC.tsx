import { NetworkMap } from '../NetworkMap'
import type { MapVariantProps } from './types'

export function VariantC({ run: _run, ...props }: MapVariantProps) {
  return <NetworkMap {...props} />
}
