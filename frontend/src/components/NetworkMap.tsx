import { useEffect, useMemo, useRef, useState } from 'react'
import maplibregl, { type ExpressionSpecification, type GeoJSONSource, type Map as MapLibreMap, type MapMouseEvent, type Marker } from 'maplibre-gl'
import { Compass, MapPin, Minus, Plus, RotateCcw } from 'lucide-react'
import type { FeatureCollection, Point } from 'geojson'
import { STATE_COLORS, type Location, type State } from '../api/types'

interface MapProperties {
  location_id: string
  name: string
  cp_count: number
  asleep_count: number
  confidence: string
  dominant: string
}

interface NetworkMapProps {
  locations: Location[]
  cpState: Record<string, State>
  selectedLocationId?: string
  onSelect: (location: Location | null) => void
}

const states = Object.keys(STATE_COLORS) as State[]

function makeFeatureCollection(locations: Location[], cpState: Record<string, State>): FeatureCollection<Point, MapProperties> {
  return {
    type: 'FeatureCollection',
    features: locations.map((location) => {
      const shares = new Map<State, number>()
      for (const cp of location.charge_points) {
        const state = cpState[cp.cp_id] ?? cp.state ?? 'IDLE'
        shares.set(state, (shares.get(state) ?? 0) + 1)
      }
      const dominant = [...shares.entries()].sort((left, right) => right[1] - left[1])[0]?.[0] ?? 'IDLE'
      const asleep_count = (shares.get('STANDBY') ?? 0) + (shares.get('DEEP_SLEEP') ?? 0)
      return {
        type: 'Feature',
        geometry: { type: 'Point', coordinates: [location.lon, location.lat] },
        properties: { location_id: location.location_id, name: location.name, cp_count: location.charge_points.length, asleep_count, confidence: location.confidence, dominant },
      }
    }),
  }
}

function gradientFor(location: Location, statesByCp: Record<string, State>) {
  const counts = new Map<State, number>()
  for (const cp of location.charge_points) {
    const state = statesByCp[cp.cp_id] ?? cp.state ?? 'IDLE'
    counts.set(state, (counts.get(state) ?? 0) + 1)
  }
  let cursor = 0
  const slices = [...counts.entries()].map(([state, count]) => {
    const start = cursor
    cursor += count / location.charge_points.length * 100
    return `${STATE_COLORS[state]} ${start}% ${cursor}%`
  })
  return `conic-gradient(${slices.join(',')})`
}

export function NetworkMap({ locations, cpState, selectedLocationId, onSelect }: NetworkMapProps) {
  const containerRef = useRef<HTMLDivElement>(null)
  const mapRef = useRef<MapLibreMap | null>(null)
  const markersRef = useRef(new Map<string, { marker: Marker; element: HTMLButtonElement }>())
  const [zoom, setZoom] = useState(5.7)
  const [mapReady, setMapReady] = useState(false)
  const data = useMemo(() => makeFeatureCollection(locations, cpState), [locations, cpState])
  const locationsRef = useRef(locations)
  const onSelectRef = useRef(onSelect)
  locationsRef.current = locations
  onSelectRef.current = onSelect

  useEffect(() => {
    if (!containerRef.current || mapRef.current) return
    const map = new maplibregl.Map({
      container: containerRef.current,
      style: 'https://tiles.openfreemap.org/styles/positron',
      center: [24.9, 45.8],
      zoom: 5.7,
      minZoom: 4.5,
      maxZoom: 15,
      attributionControl: false,
    })
    mapRef.current = map
    map.addControl(new maplibregl.AttributionControl({ compact: true }), 'bottom-right')
    map.addControl(new maplibregl.NavigationControl({ showCompass: false, showZoom: false }), 'top-right')
    map.on('load', () => {
      map.addSource('stations', {
        type: 'geojson',
        data: makeFeatureCollection(locationsRef.current, {}),
        cluster: true,
        clusterMaxZoom: 7,
        clusterRadius: 48,
        clusterProperties: {
          asleep_count: ['+', ['get', 'asleep_count']],
          cp_count: ['+', ['get', 'cp_count']],
        },
      })
      map.addLayer({
        id: 'station-clusters',
        type: 'circle',
        source: 'stations',
        filter: ['has', 'point_count'],
        paint: {
          'circle-color': '#e4efff',
          'circle-radius': ['step', ['get', 'point_count'], 19, 10, 23, 30, 28],
          'circle-stroke-color': '#ffffff',
          'circle-stroke-width': 2,
        },
      })
      map.addLayer({
        id: 'cluster-count',
        type: 'symbol',
        source: 'stations',
        filter: ['has', 'point_count'],
        layout: { 'text-field': ['concat', ['get', 'point_count_abbreviated'], '\n', ['get', 'asleep_count'], ' asleep'], 'text-size': 10, 'text-font': ['Open Sans Semibold', 'Arial Unicode MS Bold'], 'text-line-height': 1.2, 'text-allow-overlap': true },
        paint: { 'text-color': '#24588d' },
      })
      map.addLayer({
        id: 'station-points',
        type: 'circle',
        source: 'stations',
        filter: ['!', ['has', 'point_count']],
        paint: {
          'circle-color': ['match', ['get', 'dominant'], 'CHARGING', STATE_COLORS.CHARGING, 'IDLE', STATE_COLORS.IDLE, 'WAKING', STATE_COLORS.WAKING, 'STANDBY', STATE_COLORS.STANDBY, 'DEEP_SLEEP', STATE_COLORS.DEEP_SLEEP, 'OFFLINE', STATE_COLORS.OFFLINE, '#4388ea'] as ExpressionSpecification,
          'circle-radius': 6,
          'circle-stroke-color': '#fff',
          'circle-stroke-width': 1.5,
          'circle-opacity': ['case', ['<', ['zoom'], 7.8], 1, 0],
          'circle-stroke-opacity': ['case', ['<', ['zoom'], 7.8], 1, 0],
        },
      })
      map.addLayer({
        id: 'low-confidence-outline',
        type: 'circle',
        source: 'stations',
        filter: ['all', ['!', ['has', 'point_count']], ['==', ['get', 'confidence'], 'low']],
        paint: { 'circle-radius': 9, 'circle-color': 'rgba(0,0,0,0)', 'circle-stroke-color': '#526781', 'circle-stroke-width': 1.5, 'circle-stroke-opacity': ['case', ['<', ['zoom'], 7.8], 1, 0] },
      })
      setMapReady(true)
    })
    map.on('zoom', () => setZoom(map.getZoom()))
    map.on('click', 'station-clusters', (event: MapMouseEvent) => {
      const feature = map.queryRenderedFeatures(event.point, { layers: ['station-clusters'] })[0]
      if (!feature) return
      const clusterId = feature.properties?.cluster_id
      const source = map.getSource('stations') as GeoJSONSource
      void source.getClusterExpansionZoom(Number(clusterId)).then((nextZoom) => {
        map.easeTo({ center: (feature.geometry as Point).coordinates as [number, number], zoom: nextZoom + 0.2, duration: 450 })
      }).catch(() => undefined)
    })
    map.on('click', 'station-points', (event: MapMouseEvent) => {
      const id = map.queryRenderedFeatures(event.point, { layers: ['station-points'] })[0]?.properties?.location_id
      if (typeof id === 'string') onSelectRef.current(locationsRef.current.find((location) => location.location_id === id) ?? null)
    })
    map.on('mouseenter', 'station-clusters', () => { map.getCanvas().style.cursor = 'pointer' })
    map.on('mouseleave', 'station-clusters', () => { map.getCanvas().style.cursor = '' })
    map.on('mouseenter', 'station-points', () => { map.getCanvas().style.cursor = 'pointer' })
    map.on('mouseleave', 'station-points', () => { map.getCanvas().style.cursor = '' })
    return () => {
      for (const item of markersRef.current.values()) item.marker.remove()
      markersRef.current.clear()
      map.remove()
      mapRef.current = null
    }
  }, [])

  useEffect(() => {
    const source = mapRef.current?.getSource('stations') as GeoJSONSource | undefined
    source?.setData(data)
  }, [data])

  useEffect(() => {
    const map = mapRef.current
    if (!map || !mapReady) return
    const markers = markersRef.current
    for (const location of locations) {
      let entry = markers.get(location.location_id)
      if (!entry) {
        const element = document.createElement('button')
        element.className = 'map-dot'
        element.type = 'button'
        element.setAttribute('aria-label', `Open ${location.name}`)
        element.addEventListener('click', (event) => {
          event.stopPropagation()
          onSelectRef.current(locationsRef.current.find((item) => item.location_id === location.location_id) ?? null)
        })
        const marker = new maplibregl.Marker({ element, anchor: 'center' }).setLngLat([location.lon, location.lat]).addTo(map)
        entry = { marker, element }
        markers.set(location.location_id, entry)
      }
      entry.element.style.background = gradientFor(location, cpState)
      entry.element.classList.toggle('is-selected', location.location_id === selectedLocationId)
      entry.element.classList.toggle('is-low-confidence', location.confidence === 'low')
      const counts = new Map<State, number>()
      location.charge_points.forEach((cp) => { const state = cpState[cp.cp_id] ?? cp.state ?? 'IDLE'; counts.set(state, (counts.get(state) ?? 0) + 1) })
      entry.element.title = `${location.name} · ${[...counts].map(([state, count]) => `${state.replace('_', ' ')} ${Math.round(count / location.charge_points.length * 100)}%`).join(' · ')}`
      entry.element.style.display = zoom >= 7.8 ? 'block' : 'none'
    }
  }, [locations, cpState, zoom, mapReady, selectedLocationId])

  useEffect(() => {
    if (!selectedLocationId) return
    const location = locations.find((item) => item.location_id === selectedLocationId)
    if (location) mapRef.current?.flyTo({ center: [location.lon, location.lat], zoom: Math.max(mapRef.current.getZoom(), 9), duration: 700 })
  }, [selectedLocationId, locations])

  function zoomBy(delta: number) { mapRef.current?.easeTo({ zoom: Math.max(4.5, Math.min(15, (mapRef.current?.getZoom() ?? 6) + delta)), duration: 260 }) }

  return (
    <section className="map-card" aria-label="Romania charging network map">
      <div className="map-topline">
        <div className="map-title"><div className="map-title-icon"><MapPin size={15} /></div><div><strong>Romania network</strong><span>{locations.length} locations · {locations.reduce((sum, location) => sum + location.charge_points.length, 0)} charge points</span></div></div>
        <div className="map-status"><span className="live-dot" /> {zoom < 7.8 ? 'Clusters' : 'Stations'}<span className="zoom-badge">z {zoom.toFixed(1)}</span></div>
      </div>
      <div className="map-canvas" ref={containerRef}>
        <div className="map-controls">
          <button aria-label="Zoom in" onClick={() => zoomBy(0.8)}><Plus size={16} /></button>
          <button aria-label="Zoom out" onClick={() => zoomBy(-0.8)}><Minus size={16} /></button>
          <button aria-label="Reset map view" onClick={() => mapRef.current?.easeTo({ center: [24.9, 45.8], zoom: 5.7, duration: 500 })}><RotateCcw size={15} /></button>
        </div>
        <div className="map-north"><Compass size={14} /> N</div>
      </div>
      <div className="map-legend">
        {states.map((state) => <span key={state}><i style={{ background: STATE_COLORS[state] }} />{state === 'DEEP_SLEEP' ? 'Deep sleep' : state.charAt(0) + state.slice(1).toLowerCase()}</span>)}
        <span className="legend-low"><i /> Low confidence</span>
      </div>
    </section>
  )
}
