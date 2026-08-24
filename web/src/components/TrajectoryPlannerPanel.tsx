import { useMemo, useState } from 'react'

import { activityRequestHeaders, logActivity } from '../activityLog'
import type {
  GenericTrajectoryPlannerResult,
  MoonData,
  PlanetData,
  TrajectoryOptimizationMode,
  TrajectoryStartType,
  TrajectoryTargetType,
  TrajectoryWaypointType,
} from '../types'

interface TrajectoryPlannerPanelProps {
  planets: PlanetData[]
  moons: MoonData[]
  defaultStartDate: string
  onApply: (result: GenericTrajectoryPlannerResult) => void
}

interface WaypointDraft {
  id: string
  type: TrajectoryWaypointType
  bodyId: string
  zoneId: string
  flybyAltitudeKm: number
  flybyMode: 'acceleration' | 'observation'
  encounterDay: number
  burnDeltaVKmS: number
  aimpointEnabled: boolean
  aimpointClockAngleDeg: number
  aimpointScreenRadiusNorm: number
  aimpointRole: 'entry' | 'periapsis' | 'exit'
}

const ZONES = [
  ['asteroid_belt', 'Asteroidengürtel'],
  ['kuiper_belt', 'Kuipergürtel'],
  ['scattered_disk', 'Scattered Disk'],
  ['oort_cloud', 'Oortsche Wolke'],
] as const

const BOUNDARIES = [
  ['100_au', '100 AE'],
  ['termination_shock', 'Termination Shock'],
  ['heliopause', 'Heliopause'],
  ['voyager_1_distance', 'Voyager-Distanz'],
  ['custom', 'Eigene Distanz'],
] as const

const TARGET_LABELS: Record<TrajectoryTargetType, string> = {
  body: 'Planet / Körper',
  body_orbit: 'Körperorbit',
  flyby: 'Flyby',
  direction: 'Richtung',
  zone: 'Zone',
  boundary: 'Boundary',
  state_vector: 'State Vector',
}

function addYears(date: string, years: number) {
  const value = new Date(`${date}T00:00:00Z`)
  value.setUTCFullYear(value.getUTCFullYear() + years)
  return value.toISOString().slice(0, 10)
}

export function TrajectoryPlannerPanel({ planets, moons, defaultStartDate, onApply }: TrajectoryPlannerPanelProps) {
  const [startType, setStartType] = useState<TrajectoryStartType>('body')
  const [startBodyId, setStartBodyId] = useState('earth')
  const [startOrbitAltitudeKm, setStartOrbitAltitudeKm] = useState(400)
  const [startPositionText, setStartPositionText] = useState('149597870.7, 0, 0')
  const [startVelocityText, setStartVelocityText] = useState('0, 29.78, 0')
  const [departureStartDate, setDepartureStartDate] = useState(defaultStartDate)
  const [departureEndDate, setDepartureEndDate] = useState(addYears(defaultStartDate, 1))
  const [departureStepDays, setDepartureStepDays] = useState(60)
  const [targetType, setTargetType] = useState<TrajectoryTargetType>('body')
  const [targetBodyId, setTargetBodyId] = useState(() => planets.find((planet) => planet.id !== 'earth')?.id ?? planets[0]?.id ?? 'sun')
  const [zoneId, setZoneId] = useState('kuiper_belt')
  const [boundaryId, setBoundaryId] = useState('heliopause')
  const [customBoundaryAU, setCustomBoundaryAU] = useState(120)
  const [arrivalStartDate, setArrivalStartDate] = useState(addYears(defaultStartDate, 1))
  const [arrivalEndDate, setArrivalEndDate] = useState(addYears(defaultStartDate, 3))
  const [arrivalStepDays, setArrivalStepDays] = useState(90)
  const [rightAscensionDeg, setRightAscensionDeg] = useState(217.43)
  const [declinationDeg, setDeclinationDeg] = useState(-62.68)
  const [directionCoordinates, setDirectionCoordinates] = useState<'equatorial' | 'ecliptic'>('equatorial')
  const [eclipticLongitudeDeg, setEclipticLongitudeDeg] = useState(0)
  const [eclipticLatitudeDeg, setEclipticLatitudeDeg] = useState(0)
  const [directionDistanceAU, setDirectionDistanceAU] = useState(50)
  const [targetPositionText, setTargetPositionText] = useState('227939200, 0, 0')
  const [targetVelocityText, setTargetVelocityText] = useState('0, 24.13, 0')
  const [optimizationMode, setOptimizationMode] = useState<TrajectoryOptimizationMode>('balanced')
  const [maxC3Km2S2, setMaxC3Km2S2] = useState(400)
  const [maxTotalDeltaVKmS, setMaxTotalDeltaVKmS] = useState(30)
  const [maxArrivalVInfinityKmS, setMaxArrivalVInfinityKmS] = useState(30)
  const [propagationYears, setPropagationYears] = useState(20)
  const [waypoints, setWaypoints] = useState<WaypointDraft[]>([])
  const [result, setResult] = useState<GenericTrajectoryPlannerResult | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [showCandidates, setShowCandidates] = useState(false)
  const bodyOptions = useMemo(() => [
    { id: 'sun', name: 'Sonne' },
    ...planets.map(({ id, name }) => ({ id, name })),
    ...moons.map(({ id, name, parentId }) => ({ id, name: `${name} (${parentId})` })),
  ], [moons, planets])

  const addWaypoint = () => setWaypoints((current) => [...current, {
    id: `waypoint-${crypto.randomUUID()}`,
    type: 'body_flyby', bodyId: planets.find((planet) => planet.id !== startBodyId)?.id ?? planets[0]?.id ?? '', zoneId: 'asteroid_belt',
    flybyAltitudeKm: 100_000, flybyMode: 'acceleration', encounterDay: 730, burnDeltaVKmS: 0,
    aimpointEnabled: false, aimpointClockAngleDeg: 0,
    aimpointScreenRadiusNorm: 1, aimpointRole: 'periapsis',
  }])
  const updateWaypoint = (id: string, patch: Partial<WaypointDraft>) => {
    setWaypoints((current) => current.map((waypoint) => waypoint.id === id ? { ...waypoint, ...patch } : waypoint))
  }
  const reset = () => {
    setStartType('body'); setStartBodyId('earth'); setTargetType('body'); setTargetBodyId(planets.find((planet) => planet.id !== 'earth')?.id ?? planets[0]?.id ?? 'sun')
    setWaypoints([]); setResult(null); setError(null); setShowCandidates(false)
  }
  const calculate = async () => {
    setLoading(true)
    setError(null)
    try {
      const target: Record<string, unknown> = { type: targetType }
      if (['body', 'body_orbit', 'flyby'].includes(targetType)) {
        target.bodyId = targetBodyId
        target.arrivalMode = targetType === 'flyby' ? 'flyby' : 'rendezvous'
      } else if (targetType === 'zone') {
        target.zoneId = zoneId
        target.arrivalMode = 'crossing'
      } else if (targetType === 'boundary') {
        target.boundaryId = boundaryId
        if (boundaryId === 'custom') target.distanceAU = customBoundaryAU
      } else if (targetType === 'direction') {
        if (directionCoordinates === 'equatorial') {
          target.rightAscensionDeg = rightAscensionDeg
          target.declinationDeg = declinationDeg
        } else {
          target.eclipticLongitudeDeg = eclipticLongitudeDeg
          target.eclipticLatitudeDeg = eclipticLatitudeDeg
        }
        target.distanceAU = directionDistanceAU
        target.arrivalMode = 'asymptote'
      } else {
        target.targetDate = arrivalStartDate
        target.positionKm = targetPositionText.split(',').map((value) => Number(value.trim()))
        target.velocityKmS = targetVelocityText.split(',').map((value) => Number(value.trim()))
      }
      const payload = {
        start: {
          type: startType,
          bodyId: startType === 'state_vector' ? undefined : startBodyId,
          orbitAltitudeKm: startOrbitAltitudeKm,
          startDate: departureStartDate,
          ...(startType === 'state_vector' ? {
            positionKm: startPositionText.split(',').map((value) => Number(value.trim())),
            velocityKmS: startVelocityText.split(',').map((value) => Number(value.trim())),
          } : {}),
        },
        target,
        waypoints: waypoints.map((waypoint) => ({
          ...waypoint,
          bodyId: ['body_flyby'].includes(waypoint.type) ? waypoint.bodyId : undefined,
          zoneId: waypoint.type === 'zone_crossing' ? waypoint.zoneId : undefined,
          aimpoint: waypoint.type === 'body_flyby' ? {
            enabled: waypoint.aimpointEnabled,
            clockAngleDeg: waypoint.aimpointClockAngleDeg,
            screenRadiusNorm: waypoint.aimpointScreenRadiusNorm,
            role: waypoint.aimpointRole,
            altitudeKm: waypoint.flybyAltitudeKm,
          } : undefined,
        })),
        searchWindow: {
          departureStartDate, departureEndDate, departureStepDays,
          arrivalStartDate, arrivalEndDate, arrivalStepDays,
        },
        constraints: {
          maxC3Km2S2, maxTotalDeltaVKmS, maxArrivalVInfinityKmS,
          desiredSolarExitSpeedKmS: 25, targetToleranceDeg: 5,
        },
        optimizationMode,
        simulation: {
          sampleTrajectoryPoints: 360, includeUncertainty: true,
          includeAudit: true, propagationYears,
        },
      }
      const response = await fetch('/api/trajectory/plan', {
        method: 'POST', headers: activityRequestHeaders({ 'Content-Type': 'application/json' }),
        body: JSON.stringify(payload),
      })
      const responsePayload = await response.json() as GenericTrajectoryPlannerResult & { error?: string }
      if (!response.ok) throw new Error(responsePayload.error ?? `HTTP ${response.status}`)
      setResult(responsePayload)
      logActivity({ category: 'calculation', action: 'generic-trajectory-planned', values: { feasible: responsePayload.summary.feasible, flightDays: responsePayload.summary.totalFlightDays }, details: { mode: responsePayload.mode, targetType } })
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason))
    } finally {
      setLoading(false)
    }
  }

  return (
    <details className="trajectory-planner-panel">
      <summary>Trajectory Planner</summary>
      <div className="trajectory-planner-grid">
        <label><span>Starttyp</span><select value={startType} onChange={(event) => setStartType(event.target.value as TrajectoryStartType)}><option value="body">Körper</option><option value="orbit">Orbit</option><option value="state_vector">State Vector</option></select></label>
        {startType !== 'state_vector' && <label><span>Startkörper</span><select value={startBodyId} onChange={(event) => setStartBodyId(event.target.value)}>{bodyOptions.map((body) => <option key={body.id} value={body.id}>{body.name}</option>)}</select></label>}
        {startType === 'orbit' && <label><span>Startorbit-Höhe</span><input type="number" min="0" value={startOrbitAltitudeKm} onChange={(event) => setStartOrbitAltitudeKm(event.target.valueAsNumber)} /><small>km</small></label>}
        {startType === 'state_vector' && <><label><span>Startposition x,y,z</span><input value={startPositionText} onChange={(event) => setStartPositionText(event.target.value)} /><small>km</small></label><label><span>Startgeschwindigkeit x,y,z</span><input value={startVelocityText} onChange={(event) => setStartVelocityText(event.target.value)} /><small>km/s</small></label></>}
        <label><span>Startdatum von</span><input type="date" value={departureStartDate} onChange={(event) => setDepartureStartDate(event.target.value)} /></label>
        <label><span>Startdatum bis</span><input type="date" value={departureEndDate} onChange={(event) => setDepartureEndDate(event.target.value)} /></label>
        <label><span>Startraster</span><input type="number" min="1" value={departureStepDays} onChange={(event) => setDepartureStepDays(event.target.valueAsNumber)} /><small>Tage</small></label>
        <label><span>Zieltyp</span><select value={targetType} onChange={(event) => setTargetType(event.target.value as TrajectoryTargetType)}>{Object.entries(TARGET_LABELS).map(([id, label]) => <option key={id} value={id}>{label}</option>)}</select></label>
        {['body', 'body_orbit', 'flyby'].includes(targetType) && <label><span>Zielkörper</span><select value={targetBodyId} onChange={(event) => setTargetBodyId(event.target.value)}>{bodyOptions.map((body) => <option key={body.id} value={body.id}>{body.name}</option>)}</select></label>}
        {targetType === 'zone' && <label><span>Zielzone</span><select value={zoneId} onChange={(event) => setZoneId(event.target.value)}>{ZONES.map(([id, label]) => <option key={id} value={id}>{label}</option>)}</select></label>}
        {targetType === 'boundary' && <><label><span>Boundary</span><select value={boundaryId} onChange={(event) => setBoundaryId(event.target.value)}>{BOUNDARIES.map(([id, label]) => <option key={id} value={id}>{label}</option>)}</select></label>{boundaryId === 'custom' && <label><span>Eigene Distanz</span><input type="number" min="0.1" value={customBoundaryAU} onChange={(event) => setCustomBoundaryAU(event.target.valueAsNumber)} /><small>AE</small></label>}</>}
        {targetType === 'direction' && <><label><span>Koordinaten</span><select value={directionCoordinates} onChange={(event) => setDirectionCoordinates(event.target.value as typeof directionCoordinates)}><option value="equatorial">RA / Dec</option><option value="ecliptic">Ekliptikale Länge / Breite</option></select></label>{directionCoordinates === 'equatorial' ? <><label><span>Rektaszension</span><input type="number" value={rightAscensionDeg} onChange={(event) => setRightAscensionDeg(event.target.valueAsNumber)} /><small>°</small></label><label><span>Deklination</span><input type="number" min="-90" max="90" value={declinationDeg} onChange={(event) => setDeclinationDeg(event.target.valueAsNumber)} /><small>°</small></label></> : <><label><span>Ekliptikale Länge</span><input type="number" value={eclipticLongitudeDeg} onChange={(event) => setEclipticLongitudeDeg(event.target.valueAsNumber)} /><small>°</small></label><label><span>Ekliptikale Breite</span><input type="number" min="-90" max="90" value={eclipticLatitudeDeg} onChange={(event) => setEclipticLatitudeDeg(event.target.valueAsNumber)} /><small>°</small></label></>}<label><span>Darstellungsdistanz</span><input type="number" min="1" value={directionDistanceAU} onChange={(event) => setDirectionDistanceAU(event.target.valueAsNumber)} /><small>AE</small></label></>}
        {targetType === 'state_vector' && <><label><span>Zielposition x,y,z</span><input value={targetPositionText} onChange={(event) => setTargetPositionText(event.target.value)} /><small>km</small></label><label><span>Zielgeschwindigkeit x,y,z</span><input value={targetVelocityText} onChange={(event) => setTargetVelocityText(event.target.value)} /><small>km/s</small></label></>}
        {['body', 'body_orbit', 'flyby', 'state_vector'].includes(targetType) && <><label><span>Ankunft von</span><input type="date" value={arrivalStartDate} onChange={(event) => setArrivalStartDate(event.target.value)} /></label><label><span>Ankunft bis</span><input type="date" value={arrivalEndDate} onChange={(event) => setArrivalEndDate(event.target.value)} /></label><label><span>Ankunftsraster</span><input type="number" min="1" value={arrivalStepDays} onChange={(event) => setArrivalStepDays(event.target.valueAsNumber)} /><small>Tage</small></label></>}
        <label><span>Optimierung</span><select value={optimizationMode} onChange={(event) => setOptimizationMode(event.target.value as TrajectoryOptimizationMode)}><option value="balanced">Ausgewogen</option><option value="minimum_energy">Minimale Energie</option><option value="minimum_time">Minimale Zeit</option><option value="minimum_arrival_speed">Minimales Ankunfts-v∞</option><option value="maximum_exit_speed">Maximale Endgeschwindigkeit</option><option value="minimum_delta_v">Minimales Δv</option></select></label>
        <label><span>Max C3</span><input type="number" min="0" value={maxC3Km2S2} onChange={(event) => setMaxC3Km2S2(event.target.valueAsNumber)} /><small>km²/s²</small></label>
        <label><span>Max Gesamt-Δv</span><input type="number" min="0" value={maxTotalDeltaVKmS} onChange={(event) => setMaxTotalDeltaVKmS(event.target.valueAsNumber)} /><small>km/s</small></label>
        <label><span>Max Ankunfts-v∞</span><input type="number" min="0" value={maxArrivalVInfinityKmS} onChange={(event) => setMaxArrivalVInfinityKmS(event.target.valueAsNumber)} /><small>km/s</small></label>
        <label><span>Simulation</span><input type="number" min="0.1" max="50000" value={propagationYears} onChange={(event) => setPropagationYears(event.target.valueAsNumber)} /><small>Jahre</small></label>
      </div>
      <section className="trajectory-waypoints">
        <header><strong>Wegpunkte</strong><button type="button" onClick={addWaypoint}>+ Wegpunkt hinzufügen</button></header>
        {waypoints.map((waypoint, index) => <article key={waypoint.id}>
          <span>{index + 1}</span>
          <select value={waypoint.type} onChange={(event) => updateWaypoint(waypoint.id, { type: event.target.value as TrajectoryWaypointType })}><option value="body_flyby">Planet-Flyby</option><option value="solar_oberth">Solar-Oberth</option><option value="deep_space_maneuver">Deep-Space-Manöver</option><option value="zone_crossing">Zone Crossing</option><option value="manual_point">Manual Point</option></select>
          {waypoint.type === 'body_flyby' && <select value={waypoint.bodyId} onChange={(event) => updateWaypoint(waypoint.id, { bodyId: event.target.value })}>{bodyOptions.filter((body) => body.id !== 'sun').map((body) => <option key={body.id} value={body.id}>{body.name}</option>)}</select>}
          {waypoint.type === 'zone_crossing' && <select value={waypoint.zoneId} onChange={(event) => updateWaypoint(waypoint.id, { zoneId: event.target.value })}>{ZONES.map(([id, label]) => <option key={id} value={id}>{label}</option>)}</select>}
          {waypoint.type === 'body_flyby' && <input aria-label="Flyby-Höhe" type="number" min="0" value={waypoint.flybyAltitudeKm} onChange={(event) => updateWaypoint(waypoint.id, { flybyAltitudeKm: event.target.valueAsNumber })} />}
          {waypoint.type === 'body_flyby' && <input aria-label="Begegnungstag" type="number" min="1" value={waypoint.encounterDay} onChange={(event) => updateWaypoint(waypoint.id, { encounterDay: event.target.valueAsNumber })} />}
          {waypoint.type === 'body_flyby' && <select aria-label="Flyby-Modus" value={waypoint.flybyMode} onChange={(event) => updateWaypoint(waypoint.id, { flybyMode: event.target.value as WaypointDraft['flybyMode'] })}><option value="acceleration">Beschleunigung</option><option value="observation">Beobachtung</option></select>}
          <input aria-label="Burn Delta-v" type="number" min="0" step="0.1" value={waypoint.burnDeltaVKmS} onChange={(event) => updateWaypoint(waypoint.id, { burnDeltaVKmS: event.target.valueAsNumber })} />
          <button type="button" aria-label={`Wegpunkt ${index + 1} entfernen`} onClick={() => setWaypoints((current) => current.filter((item) => item.id !== waypoint.id))}>×</button>
          {waypoint.type === 'body_flyby' && <div className="trajectory-aimpoint-fields">
            <label><input type="checkbox" checked={waypoint.aimpointEnabled} onChange={(event) => updateWaypoint(waypoint.id, { aimpointEnabled: event.target.checked })} /> Aimpoint physikalisch verwenden</label>
            {waypoint.aimpointEnabled && <>
              <label>Rolle<select value={waypoint.aimpointRole} onChange={(event) => updateWaypoint(waypoint.id, { aimpointRole: event.target.value as WaypointDraft['aimpointRole'] })}><option value="entry">Entry</option><option value="periapsis">Periapsis</option><option value="exit">Exit</option></select></label>
              <label>Uhrwinkel<input type="number" step="1" value={waypoint.aimpointClockAngleDeg} onChange={(event) => updateWaypoint(waypoint.id, { aimpointClockAngleDeg: event.target.valueAsNumber })} /></label>
              <label>Scheibenradius<input type="number" min="0" max="1" step="0.05" value={waypoint.aimpointScreenRadiusNorm} onChange={(event) => updateWaypoint(waypoint.id, { aimpointScreenRadiusNorm: event.target.valueAsNumber })} /></label>
            </>}
          </div>}
        </article>)}
      </section>
      <div className="trajectory-planner-actions">
        <button type="button" disabled={loading} onClick={() => void calculate()}>{loading ? 'Berechnung läuft …' : 'Route berechnen'}</button>
        <button type="button" disabled={!result} onClick={() => result && onApply(result)}>Beste Route anzeigen</button>
        <button type="button" disabled={!result?.candidates?.length} onClick={() => setShowCandidates((current) => !current)}>Kandidaten anzeigen</button>
        <button type="button" disabled={!result || !result.summary.feasible} onClick={() => result && onApply(result)}>Als Mission übernehmen</button>
        <button type="button" onClick={reset}>Zurücksetzen</button>
      </div>
      {error && <p className="trajectory-planner-error" role="alert">{error}</p>}
      {result && <section className="trajectory-planner-result">
        <strong>{result.start.bodyId ?? result.start.type} → {result.target.bodyId ?? result.target.zoneId ?? result.target.boundaryId ?? result.target.type}</strong>
        <span>Zieltyp {result.target.type} · Flugzeit {result.summary.totalFlightDays.toLocaleString('de-DE', { maximumFractionDigits: 1 })} Tage</span>
        <span>Gesamt-Δv {(result.summary.totalDeltaVKmS ?? 0).toFixed(2)} km/s · C3 {result.summary.c3Km2S2?.toFixed(2) ?? '–'} · Ankunfts-v∞ {result.summary.arrivalVInfinityKmS?.toFixed(2) ?? '–'} km/s</span>
        <span>Endgeschwindigkeit {result.summary.finalHeliocentricSpeedKmS?.toFixed(2) ?? '–'} km/s · Ziel {result.summary.targetReached ? 'erreicht' : 'nicht erreicht'} · {result.summary.feasible ? 'machbar' : 'nicht machbar'}</span>
        <span>Modell: {result.summary.model}</span>
        {result.warnings.map((warning) => <small key={warning}>{warning}</small>)}
      </section>}
      {showCandidates && result?.candidates && <ol className="trajectory-candidate-list">{result.candidates.slice(0, 30).map((candidate) => <li key={candidate.id}><span>{candidate.departureDate} → {candidate.arrivalDate}</span><span>{candidate.flightDays.toFixed(0)} d · Δv {(candidate.totalDeltaVKmS ?? 0).toFixed(2)} km/s · Score {candidate.score.toFixed(3)}</span></li>)}</ol>}
    </details>
  )
}
