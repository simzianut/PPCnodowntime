import { useState } from 'react'
import { NetworkMap } from '../NetworkMap'
import type { MapVariantProps } from './types'
import { VariantA } from './VariantA'
import { VariantB } from './VariantB'
import { VariantC } from './VariantC'
import { VariantD } from './VariantD'
import './scratchpad.css'

const VARIANTS = [
  { key: 'now', label: 'Current', render: ({ run: _run, ...p }: MapVariantProps) => <NetworkMap {...p} /> },
  { key: 'a', label: 'A · Waste vs saved', render: (p: MapVariantProps) => <VariantA {...p} /> },
  { key: 'b', label: 'B · Without / with agent', render: (p: MapVariantProps) => <VariantB {...p} /> },
  { key: 'c', label: 'C · Counties', render: (p: MapVariantProps) => <VariantC {...p} /> },
  { key: 'd', label: 'D · Live actions', render: (p: MapVariantProps) => <VariantD {...p} /> },
] as const

function initialKey() {
  const fromUrl = new URLSearchParams(window.location.search).get('map')
  if (fromUrl && VARIANTS.some((v) => v.key === fromUrl)) return fromUrl
  try { return localStorage.getItem('map-variant') ?? 'now' } catch { return 'now' }
}

export function MapScratchpad(props: MapVariantProps) {
  const [key, setKey] = useState(initialKey)
  const pick = (next: string) => {
    setKey(next)
    try { localStorage.setItem('map-variant', next) } catch { /* private mode */ }
    const url = new URL(window.location.href)
    url.searchParams.set('map', next)
    window.history.replaceState(null, '', url)
  }
  const variant = VARIANTS.find((v) => v.key === key) ?? VARIANTS[0]
  return <div className="map-scratchpad">
    <div className="variant-switcher" role="tablist" aria-label="Map variant">
      {VARIANTS.map((v) => <button key={v.key} role="tab" aria-selected={v.key === variant.key} onClick={() => pick(v.key)}>{v.label}</button>)}
    </div>
    {variant.render(props)}
  </div>
}
