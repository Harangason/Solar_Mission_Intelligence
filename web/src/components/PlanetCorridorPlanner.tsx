import {
  useEffect,
  useRef,
  useState,
  type Dispatch,
  type KeyboardEvent,
  type PointerEvent,
  type SetStateAction,
} from 'react'

import type {
  CorridorMainProjection,
  EntryCorridorDefinition,
} from '../entryCorridorGeometry'
import {
  evaluateCorridorFeasibility,
  withCorridorFeasibility,
  type CorridorTargetPhysics,
} from '../corridorFeasibility'
import { ROUTE_INTERSTELLAR_SYSTEMS } from '../interstellarTargets'
import type { RoutePassageDefinition } from '../routeSections'
import type { MoonData, PlanetData, SunData } from '../types'
import type { Vector3Tuple } from '../targetAlignedProjection'
import { LocalPlanetThreeD } from './LocalPlanetThreeD'
import { SunwardCorridorView } from './SunwardCorridorView'

interface PlanetCorridorPlannerProps {
  planets: PlanetData[]
  moons: MoonData[]
  sun: SunData
  originId: string
  onOriginChange: (objectId: string) => void
  waypointId: string
  onWaypointChange: (planetId: string) => void
  definition: EntryCorridorDefinition
  onDefinitionChange: Dispatch<SetStateAction<EntryCorridorDefinition>>
  deltaVMinusKmS: number
  deltaVPlusKmS: number
  onDeltaVMinusChange: (value: number) => void
  onDeltaVPlusChange: (value: number) => void
  sectionNumber: number
  passage: RoutePassageDefinition
  sunToTargetDirection?: Vector3Tuple | null
  actualEntryDirection?: Vector3Tuple | null
  entryFlightDirection?: Vector3Tuple | null
  exitRadialDirection?: Vector3Tuple | null
  exitFlightDirection?: Vector3Tuple | null
  passageNormalDirection?: Vector3Tuple | null
  entrySourceName?: string | null
  exitTargetName?: string | null
  epochLabel: string
}

type CorridorProjection = CorridorMainProjection | 'local3d'

const CANVAS_WIDTH = 1000
const CANVAS_HEIGHT = 620
const CENTER_X = 635
const CENTER_Y = 310
const PLANET_RADIUS = 160

function polarPoint(radius: number, angleDeg: number) {
  const angle = angleDeg * Math.PI / 180
  return {
    x: CENTER_X + Math.cos(angle) * radius,
    y: CENTER_Y + Math.sin(angle) * radius,
  }
}

function arcPath(radius: number, startAngle: number, endAngle: number) {
  const start = polarPoint(radius, startAngle)
  const end = polarPoint(radius, endAngle)
  return `M ${start.x} ${start.y} A ${radius} ${radius} 0 ${endAngle - startAngle > 180 ? 1 : 0} 1 ${end.x} ${end.y}`
}

function directedArcPath(radius: number, startAngle: number, endAngle: number, sweep: 0 | 1) {
  const start = polarPoint(radius, startAngle)
  const end = polarPoint(radius, endAngle)
  const largeArc = Math.abs(endAngle - startAngle) > 180 ? 1 : 0
  return `M ${start.x} ${start.y} A ${radius} ${radius} 0 ${largeArc} ${sweep} ${end.x} ${end.y}`
}

function corridorBandPath(innerRadius: number, outerRadius: number, startAngle: number, endAngle: number) {
  const outerStart = polarPoint(outerRadius, startAngle)
  const outerEnd = polarPoint(outerRadius, endAngle)
  const innerEnd = polarPoint(innerRadius, endAngle)
  const innerStart = polarPoint(innerRadius, startAngle)
  const largeArc = endAngle - startAngle > 180 ? 1 : 0
  return [
    `M ${outerStart.x} ${outerStart.y}`,
    `A ${outerRadius} ${outerRadius} 0 ${largeArc} 1 ${outerEnd.x} ${outerEnd.y}`,
    `L ${innerEnd.x} ${innerEnd.y}`,
    `A ${innerRadius} ${innerRadius} 0 ${largeArc} 0 ${innerStart.x} ${innerStart.y}`,
    'Z',
  ].join(' ')
}

function finitePositive(value: number, fallback: number) {
  return Number.isFinite(value) && value >= 0 ? value : fallback
}

function normalizedDirection(direction: EntryCorridorDefinition['centerDirection']) {
  const length = Math.hypot(...direction)
  return length > 0
    ? [direction[0] / length, direction[1] / length, direction[2] / length] as const
    : [1, 0, 0] as const
}

export function PlanetCorridorPlanner({
  planets,
  moons,
  sun,
  originId,
  onOriginChange,
  waypointId,
  onWaypointChange,
  definition,
  onDefinitionChange,
  deltaVMinusKmS,
  deltaVPlusKmS,
  onDeltaVMinusChange,
  onDeltaVPlusChange,
  sectionNumber,
  passage,
  sunToTargetDirection = null,
  actualEntryDirection = null,
  entryFlightDirection = null,
  exitRadialDirection = null,
  exitFlightDirection = null,
  passageNormalDirection = null,
  entrySourceName = null,
  exitTargetName = null,
  epochLabel,
}: PlanetCorridorPlannerProps) {
  const svgRef = useRef<SVGSVGElement>(null)
  const dragging = useRef(false)
  const mainProjection = definition.mainProjection ?? 'side'
  const [projection, setProjection] = useState<CorridorProjection>(mainProjection)
  const isTopProjection = projection === 'top'
  const isSideProjection = projection === 'side'
  const isSunwardProjection = projection === 'sunward'
  const isLocalThreeDProjection = projection === 'local3d'
  const isMainProjection = projection === mainProjection
  const isInterstellarTarget = ROUTE_INTERSTELLAR_SYSTEMS.some((system) => system.id === waypointId)
  const passageDirection = passage.orbitDirection
  const selectedTarget = waypointId === 'sun'
    ? sun
    : planets.find((planet) => planet.id === waypointId)
      ?? moons.find((moon) => moon.id === waypointId)
      ?? ROUTE_INTERSTELLAR_SYSTEMS.find((system) => system.id === waypointId)
      ?? planets[0]
  const localPlanet = planets.find((planet) => planet.id === waypointId)
    ?? (selectedTarget && 'parentId' in selectedTarget
      ? planets.find((planet) => planet.id === selectedTarget.parentId)
      : undefined)
  const localThreeDBody: PlanetData | undefined = waypointId === 'sun'
    ? {
        id: sun.id,
        name: sun.name,
        massKg: 1.9885e30,
        radiusKm: sun.radiusKm,
        temperatureK: 5772,
        distanceAu: 0,
        orbitalPeriodDays: 0,
        surfaceGravity: sun.surfaceGravity,
        color: sun.color,
        hasRings: false,
      }
    : localPlanet
  const localPlanetMoons = localPlanet
    ? moons.filter((moon) => moon.parentId === localPlanet.id)
    : []
  const autoProjectionTargetRef = useRef('')

  useEffect(() => {
    const targetKey = `${waypointId}:${localThreeDBody?.id ?? ''}`
    if (autoProjectionTargetRef.current === targetKey) return
    autoProjectionTargetRef.current = targetKey
    if (localThreeDBody && !isInterstellarTarget) setProjection('local3d')
    else if (projection === 'local3d') setProjection(mainProjection)
  }, [isInterstellarTarget, localThreeDBody?.id, mainProjection, projection, waypointId])
  const targetPhysics: CorridorTargetPhysics = {
    radiusKm: selectedTarget && 'radiusKm' in selectedTarget ? selectedTarget.radiusKm : undefined,
    surfaceGravity: selectedTarget && 'surfaceGravity' in selectedTarget ? selectedTarget.surfaceGravity : undefined,
    allowCurvedApproach: true,
  }
  const targetColor = selectedTarget && 'color' in selectedTarget
    ? selectedTarget.color
    : selectedTarget && 'parentId' in selectedTarget
      ? planets.find((planet) => planet.id === selectedTarget.parentId)?.color ?? '#b9c7d6'
      : '#d6a36f'
  const feasibility = evaluateCorridorFeasibility(definition, targetPhysics)
  const direction = normalizedDirection(definition.centerDirection)
  const projectionAngleDeg = Math.atan2(
    isTopProjection ? direction[1] : direction[2],
    direction[0],
  ) * 180 / Math.PI
  const centerAngle = 180 + projectionAngleDeg
  const projectionMagnitude = Math.hypot(
    direction[0],
    isTopProjection ? direction[1] : direction[2],
  )
  const physicalCorridorRadius = PLANET_RADIUS * feasibility.corridorRadiusRatio
  const displayCorridorRadius = isMainProjection
    ? physicalCorridorRadius
    : Math.max(2, physicalCorridorRadius * projectionMagnitude)
  const halfWidth = isTopProjection
    ? definition.horizontalHalfAngleDeg
    : definition.verticalHalfAngleDeg
  const startAngle = centerAngle - halfWidth
  const endAngle = centerAngle + halfWidth
  const orthogonalHalfWidth = isTopProjection
    ? definition.verticalHalfAngleDeg
    : definition.horizontalHalfAngleDeg
  const physicalHalfThickness = 10 + orthogonalHalfWidth * 2.2
  const halfThickness = isMainProjection
    ? physicalHalfThickness
    : Math.max(7, physicalHalfThickness * Math.max(0.28, projectionMagnitude))
  const innerRadius = Math.max(1, displayCorridorRadius - halfThickness)
  const outerRadius = displayCorridorRadius + halfThickness
  const corridorCenter = polarPoint(displayCorridorRadius, centerAngle)
  const projectionTouchesObject = !isMainProjection && innerRadius <= PLANET_RADIUS
  const centerAngleRad = centerAngle * Math.PI / 180
  const radialX = Math.cos(centerAngleRad)
  const radialY = Math.sin(centerAngleRad)
  const minimumPoint = polarPoint(innerRadius - 14, centerAngle)
  const maximumPoint = polarPoint(outerRadius + 14, centerAngle)
  const minimumTextAnchor = radialX > 0.18 ? 'end' : radialX < -0.18 ? 'start' : 'middle'
  const maximumTextAnchor = radialX > 0.18 ? 'start' : radialX < -0.18 ? 'end' : 'middle'
  const minimumLabelY = minimumPoint.y + (Math.abs(radialX) <= 0.18 ? (radialY > 0 ? -7 : 17) : 5)
  const maximumLabelY = maximumPoint.y + (Math.abs(radialX) <= 0.18 ? (radialY > 0 ? 18 : -8) : 5)
  const corridorTitlePoint = {
    x: corridorCenter.x - radialY * (isMainProjection ? 110 : 72),
    y: corridorCenter.y + radialX * (isMainProjection ? 110 : 72) + 6,
  }
  const directionArrowRadius = Math.max(PLANET_RADIUS + 36, displayCorridorRadius - 18)
  const directionArrowStartAngle = centerAngle - 20
  const directionArrowEndAngle = centerAngle + 20
  const directionArrowSweep: 0 | 1 = passageDirection === 'retrograde' ? 0 : 1
  const directionArrowLabel = polarPoint(directionArrowRadius + 20, centerAngle)
  const directionArrowMarkerId = `corridor-direction-arrow-${sectionNumber}-${projection}`
  const approachStart = { x: 105, y: CENTER_Y }
  const approachLineStart = { x: approachStart.x + 18, y: approachStart.y }
  const minusTarget = polarPoint(displayCorridorRadius, startAngle)
  const plusTarget = polarPoint(displayCorridorRadius, endAngle)
  const selectedOrigin = originId === 'sun'
    ? sun
    : planets.find((planet) => planet.id === originId)
      ?? moons.find((moon) => moon.id === originId)
      ?? ROUTE_INTERSTELLAR_SYSTEMS.find((system) => system.id === originId)
  const originName = selectedOrigin?.name ?? originId
  const originColor = selectedOrigin && 'color' in selectedOrigin
    ? selectedOrigin.color
    : selectedOrigin && 'parentId' in selectedOrigin
      ? planets.find((planet) => planet.id === selectedOrigin.parentId)?.color ?? '#b9c7d6'
      : '#9fcde6'
  const safetyRadiusPx = PLANET_RADIUS * feasibility.safetyRadiusRatio
  const clearanceLabel = feasibility.safetyRadiusKm === null
    ? 'Schematischer Mindestabstand'
    : `Sicherheitsradius ${Math.round(feasibility.safetyRadiusKm).toLocaleString('de-DE')} km`

  const commitDefinitionChange: Dispatch<SetStateAction<EntryCorridorDefinition>> = (action) => {
    onDefinitionChange((current) => {
      const next = typeof action === 'function' ? action(current) : action
      return withCorridorFeasibility(next, targetPhysics)
    })
  }
  const changeTarget = (targetId: string) => {
    const target = targetId === 'sun'
      ? sun
      : planets.find((planet) => planet.id === targetId)
        ?? moons.find((moon) => moon.id === targetId)
        ?? ROUTE_INTERSTELLAR_SYSTEMS.find((system) => system.id === targetId)
    const nextTargetPhysics: CorridorTargetPhysics = {
      radiusKm: target && 'radiusKm' in target ? target.radiusKm : undefined,
      surfaceGravity: target && 'surfaceGravity' in target ? target.surfaceGravity : undefined,
      allowCurvedApproach: true,
    }
    onWaypointChange(targetId)
    onDefinitionChange((current) => withCorridorFeasibility(current, nextTargetPhysics))
  }

  const updateCenterFromPointer = (event: PointerEvent<SVGElement>) => {
    const svg = svgRef.current
    const matrix = svg?.getScreenCTM()
    if (!svg || !matrix) return
    const point = new DOMPoint(event.clientX, event.clientY).matrixTransform(matrix.inverse())
    const screenAngle = Math.atan2(point.y - CENTER_Y, point.x - CENTER_X)
    setProjectionAngle((screenAngle - Math.PI) * 180 / Math.PI)
  }
  const setProjectionAngle = (angleDeg: number) => {
    const angle = angleDeg * Math.PI / 180
    commitDefinitionChange((current) => ({
      ...current,
      enabled: true,
      centerDirection: (() => {
        const currentDirection = normalizedDirection(current.centerDirection)
        if (isTopProjection) {
          const z = Math.max(-0.98, Math.min(0.98, currentDirection[2]))
          const planarLength = Math.sqrt(1 - z * z)
          return [
            Math.cos(angle) * planarLength,
            Math.sin(angle) * planarLength,
            z,
          ]
        }
        const y = Math.max(-0.98, Math.min(0.98, currentDirection[1]))
        const planarLength = Math.sqrt(1 - y * y)
        return [
          Math.cos(angle) * planarLength,
          y,
          Math.sin(angle) * planarLength,
        ]
      })(),
    }))
  }
  const moveCorridorWithKeyboard = (event: KeyboardEvent<SVGPathElement>) => {
    if (!isMainProjection || (event.key !== 'ArrowLeft' && event.key !== 'ArrowRight')) return
    event.preventDefault()
    setProjectionAngle(projectionAngleDeg + (event.key === 'ArrowRight' ? 1 : -1))
  }

  return (
    <div className="planet-corridor-planner">
      <div className="corridor-planner-controls">
        <div className="active-route-section-label">
          <small>Aktiver Abschnitt {String(sectionNumber).padStart(2, '0')}</small>
          <strong>{originName} → {selectedTarget?.name ?? 'Ziel'}</strong>
        </div>
        <label>
          <span>Ursprung</span>
          <select value={originId} onChange={(event) => onOriginChange(event.target.value)}>
            <optgroup label="Sonnensystem">
              {waypointId !== 'sun' && <option value="sun">Sonne</option>}
              {planets.filter((planet) => planet.id !== waypointId).map((planet) => (
                <option key={`origin-${planet.id}`} value={planet.id}>{planet.name}</option>
              ))}
            </optgroup>
            <optgroup label="Monde">
              {moons.filter((moon) => moon.id !== waypointId).map((moon) => (
                <option key={`origin-${moon.id}`} value={moon.id}>
                  {planets.find((planet) => planet.id === moon.parentId)?.name ?? moon.parentId} · {moon.name}
                </option>
              ))}
            </optgroup>
            <optgroup label="Exoplanetensysteme">
              {ROUTE_INTERSTELLAR_SYSTEMS.filter((system) => system.id !== waypointId).map((system) => (
                <option key={`origin-${system.id}`} value={system.id}>{system.name} · {system.distanceLy.toFixed(1)} Lj</option>
              ))}
            </optgroup>
          </select>
        </label>
        <label>
          <span>Zielobjekt</span>
          <select value={waypointId} onChange={(event) => changeTarget(event.target.value)}>
            <optgroup label="Sonnensystem">
              {originId !== 'sun' && <option value="sun">Sonne</option>}
              {planets.filter((planet) => planet.id !== originId).map((planet) => (
                <option key={planet.id} value={planet.id}>{planet.name}</option>
              ))}
            </optgroup>
            <optgroup label="Monde">
              {moons.filter((moon) => moon.id !== originId).map((moon) => (
                <option key={moon.id} value={moon.id}>
                  {planets.find((planet) => planet.id === moon.parentId)?.name ?? moon.parentId} · {moon.name}
                </option>
              ))}
            </optgroup>
            <optgroup label="Exoplanetensysteme">
              {ROUTE_INTERSTELLAR_SYSTEMS.filter((system) => system.id !== originId).map((system) => (
                <option key={system.id} value={system.id}>{system.name} · {system.distanceLy.toFixed(1)} Lj</option>
              ))}
            </optgroup>
          </select>
        </label>
        <label className="corridor-enable">
          <span>Zielkorridor verwenden</span>
          <input type="checkbox" checked={definition.enabled && !isInterstellarTarget} disabled={isInterstellarTarget} onChange={(event) => commitDefinitionChange((current) => ({ ...current, enabled: event.target.checked }))} />
        </label>
        <label>
          <span>Bogenbreite ±</span>
          <input type="range" min="1" max="70" step="1" value={definition.horizontalHalfAngleDeg} disabled={isInterstellarTarget} onChange={(event) => commitDefinitionChange((current) => ({ ...current, horizontalHalfAngleDeg: event.target.valueAsNumber, enabled: true }))} />
          <output>{definition.horizontalHalfAngleDeg.toFixed(0)}°</output>
        </label>
        <label>
          <span>Position {isSunwardProjection ? 'Querebene' : isTopProjection ? 'x–y' : 'x–z'}</span>
          <input
            type="range"
            min="-180"
            max="180"
            step="1"
            value={projectionAngleDeg}
            disabled={!isMainProjection || isInterstellarTarget}
            onChange={(event) => setProjectionAngle(event.target.valueAsNumber)}
          />
          <output>{projectionAngleDeg.toFixed(0)}°</output>
        </label>
        <label>
          <span>Min/Max-Spanne ±</span>
          <input type="range" min="1" max="30" step="1" value={definition.verticalHalfAngleDeg} disabled={isInterstellarTarget} onChange={(event) => commitDefinitionChange((current) => ({ ...current, verticalHalfAngleDeg: event.target.valueAsNumber, enabled: true }))} />
          <output>{definition.verticalHalfAngleDeg.toFixed(0)}°</output>
        </label>
        <label>
          <span>Δv −</span>
          <input type="number" min="0" step="0.1" value={deltaVMinusKmS} onChange={(event) => onDeltaVMinusChange(finitePositive(event.target.valueAsNumber, deltaVMinusKmS))} />
          <output>km/s</output>
        </label>
        <label>
          <span>Δv +</span>
          <input type="number" min="0" step="0.1" value={deltaVPlusKmS} onChange={(event) => onDeltaVPlusChange(finitePositive(event.target.valueAsNumber, deltaVPlusKmS))} />
          <output>km/s</output>
        </label>
      </div>

      <p className="corridor-instruction">
        Ein gemeinsamer räumlicher Zielkorridor: Seite, Draufsicht, Sonnenebene und die lokale 3D-Ansicht verwenden dasselbe aktive Zielobjekt.
      </p>

      <div className="corridor-projection-switcher" role="group" aria-label="Korridoransicht">
        <div className={isSideProjection ? 'corridor-projection-option active' : 'corridor-projection-option'}>
          <button
            type="button"
            aria-pressed={isSideProjection}
            onClick={() => setProjection('side')}
          >
            Seitenansicht · x–z
          </button>
          <label>
            <input
              type="checkbox"
              checked={mainProjection === 'side'}
              onChange={(event) => {
                if (!event.target.checked) return
                commitDefinitionChange((current) => ({ ...current, mainProjection: 'side' }))
                setProjection('side')
              }}
            />
            Main
          </label>
        </div>
        <div className={isTopProjection ? 'corridor-projection-option active' : 'corridor-projection-option'}>
          <button
            type="button"
            aria-pressed={isTopProjection}
            onClick={() => setProjection('top')}
          >
            Draufsicht · x–y
          </button>
          <label>
            <input
              type="checkbox"
              checked={mainProjection === 'top'}
              onChange={(event) => {
                if (!event.target.checked) return
                commitDefinitionChange((current) => ({ ...current, mainProjection: 'top' }))
                setProjection('top')
              }}
            />
            Main
          </label>
        </div>
        <div className={isSunwardProjection ? 'corridor-projection-option active' : 'corridor-projection-option'}>
          <button
            type="button"
            aria-pressed={isSunwardProjection}
            disabled={!sunToTargetDirection || isInterstellarTarget || waypointId === 'sun'}
            onClick={() => setProjection('sunward')}
          >
            Von der Sonne · Querebene
          </button>
          <label>
            <input
              type="checkbox"
              checked={mainProjection === 'sunward'}
              disabled={!sunToTargetDirection || isInterstellarTarget || waypointId === 'sun'}
              onChange={(event) => {
                if (!event.target.checked) return
                commitDefinitionChange((current) => ({ ...current, mainProjection: 'sunward' }))
                setProjection('sunward')
              }}
            />
            Main
          </label>
        </div>
        <div className={isLocalThreeDProjection ? 'corridor-projection-option active' : 'corridor-projection-option'}>
          <button
            type="button"
            aria-pressed={isLocalThreeDProjection}
            disabled={!localThreeDBody}
            onClick={() => setProjection('local3d')}
          >
            3D lokal{localThreeDBody ? ` · ${localThreeDBody.name}` : ''}
          </button>
        </div>
        <span className={isMainProjection ? 'projection-mode-badge main' : 'projection-mode-badge'}>
          {isLocalThreeDProjection
            ? 'Aktiver Zielplanet · 3D-Ansicht'
            : isSunwardProjection
            ? 'Zielbezogen · bearbeitbar'
            : isMainProjection
            ? 'Main · bearbeitbar'
            : projectionTouchesObject
              ? 'Nur Ansicht · Projektion auf dem Zielkörper'
              : 'Nur Ansicht · synchronisierte Projektion'}
        </span>
      </div>

      {!isInterstellarTarget && <div className={`corridor-feasibility-status ${feasibility.blocked ? 'blocked' : 'clear'}`} role="status">
        <strong>{feasibility.blocked ? 'Zielkorridor gesperrt' : 'Zielkorridor frei'}</strong>
        <span>
          {feasibility.blocked
            ? feasibility.reasons.join(' ')
            : `${clearanceLabel} wird eingehalten.`}
        </span>
      </div>}

      {isLocalThreeDProjection && localThreeDBody
        ? (
          <div className="corridor-local-three-d">
            <LocalPlanetThreeD
              planet={localThreeDBody}
              moons={localPlanetMoons}
              epochLabel={epochLabel}
              corridorDefinition={definition}
              actualEntryDirection={actualEntryDirection}
              entryFlightDirection={entryFlightDirection}
              exitRadialDirection={exitRadialDirection}
              exitFlightDirection={exitFlightDirection}
              passageNormalDirection={passageNormalDirection}
              entrySourceName={entrySourceName}
              exitTargetName={exitTargetName}
              passage={passage}
            />
            <p className="two-d-footnote">Aktiver Zielplanet · ziehen zum Drehen · Mausrad zum Zoomen.</p>
          </div>
        )
        : isInterstellarTarget
        ? (
          <div className="hypothetical-target-direction" role="status">
            <strong>{selectedTarget?.name ?? waypointId} bleibt hypothetisch</strong>
            <span>Keine lokale Ephemeride, kein Zielkörper und kein lokaler Zielkorridor. Dargestellt wird ausschließlich der räumliche Richtungsstrahl mit 50 AE.</span>
          </div>
        )
        : isSunwardProjection && sunToTargetDirection
          ? (
            <SunwardCorridorView
              targetName={selectedTarget?.name ?? waypointId}
              targetColor={targetColor}
              definition={definition}
              sunToTargetDirection={sunToTargetDirection}
              actualEntryDirection={actualEntryDirection}
              corridorRadiusRatio={feasibility.corridorRadiusRatio}
              safetyRadiusRatio={feasibility.safetyRadiusRatio}
              blocked={feasibility.blocked}
              sectionNumber={sectionNumber}
              onCenterDirectionChange={(centerDirection) => commitDefinitionChange((current) => ({
                ...current,
                centerDirection,
                enabled: true,
              }))}
            />
          )
          : (
      <svg
        ref={svgRef}
        className={`planet-corridor-canvas${isMainProjection ? '' : ' readonly'}`}
        viewBox={`0 0 ${CANVAS_WIDTH} ${CANVAS_HEIGHT}`}
        preserveAspectRatio="xMidYMid meet"
        role="group"
        aria-label={`Zielzentrierter Korridor für ${selectedTarget?.name ?? 'das Zielobjekt'} · ${isTopProjection ? 'Draufsicht' : 'Seitenansicht'}`}
        onPointerMove={(event) => {
          if (isMainProjection && dragging.current) updateCenterFromPointer(event)
        }}
        onPointerUp={() => { dragging.current = false }}
        onPointerCancel={() => { dragging.current = false }}
      >
        <defs>
          <radialGradient id="planet-fill" cx="38%" cy="32%">
            <stop offset="0%" stopColor={targetColor} stopOpacity="1" />
            <stop offset="100%" stopColor="#111b2c" stopOpacity="1" />
          </radialGradient>
          <marker id="approach-arrow" markerWidth="6" markerHeight="6" refX="5.5" refY="3" orient="auto">
            <path d="M 0 0 L 6 3 L 0 6 Z" fill="#9feaff" />
          </marker>
          <marker id="coordinate-arrow" markerWidth="8" markerHeight="8" refX="7" refY="4" orient="auto">
            <path d="M 0 0 L 8 4 L 0 8 Z" className="coordinate-arrow-head" />
          </marker>
          <marker id={directionArrowMarkerId} markerWidth="8" markerHeight="8" refX="7" refY="4" orient="auto">
            <path d="M 0 0 L 8 4 L 0 8 Z" className="target-rotation-arrow-head" />
          </marker>
        </defs>

        <rect width={CANVAS_WIDTH} height={CANVAS_HEIGHT} className="corridor-space" />
        <g className="corridor-coordinate-system" aria-hidden="true">
          <line
            x1="42"
            y1={CENTER_Y}
            x2={CANVAS_WIDTH - 35}
            y2={CENTER_Y}
            className="coordinate-axis reference-plane"
            markerEnd="url(#coordinate-arrow)"
          />
          <line
            x1={CENTER_X}
            y1={CANVAS_HEIGHT - 34}
            x2={CENTER_X}
            y2="28"
            className="coordinate-axis"
            markerEnd="url(#coordinate-arrow)"
          />
          {[CENTER_X - 220, CENTER_X - 110, CENTER_X + 110, CENTER_X + 220].map((x) => (
            <line key={`x-tick-${x}`} x1={x} y1={CENTER_Y - 7} x2={x} y2={CENTER_Y + 7} className="coordinate-tick" />
          ))}
          {[CENTER_Y - 220, CENTER_Y - 110, CENTER_Y + 110, CENTER_Y + 220].map((y) => (
            <line key={`y-tick-${y}`} x1={CENTER_X - 7} y1={y} x2={CENTER_X + 7} y2={y} className="coordinate-tick" />
          ))}
          <text x={CANVAS_WIDTH - 46} y={CENTER_Y - 13} textAnchor="end" className="coordinate-axis-label">
            {isTopProjection ? '+x · Bahnebene' : '+x · Bezugsebene z = 0'}
          </text>
          <text x={CENTER_X + 14} y="45" className="coordinate-axis-label">
            {isTopProjection ? '+y · Bahnebene' : '+z · Höhe / Bahnabweichung'}
          </text>
        </g>
        <circle
          cx={CENTER_X}
          cy={CENTER_Y}
          r={safetyRadiusPx}
          className={`corridor-safety-envelope ${feasibility.blocked ? 'blocked' : 'clear'}`}
          aria-hidden="true"
        />
        <text
          x={CENTER_X}
          y={CENTER_Y + safetyRadiusPx + 20}
          textAnchor="middle"
          className="corridor-safety-label"
          aria-hidden="true"
        >
          Mindestabstand + Gravitationsreserve
        </text>
        <circle cx={CENTER_X} cy={CENTER_Y} r={PLANET_RADIUS} fill="url(#planet-fill)" className="corridor-planet" />
        <text x={CENTER_X} y={CENTER_Y + 55} textAnchor="middle" className="corridor-planet-name">{selectedTarget?.name ?? 'Ziel'}</text>
        <g className="coordinate-origin" aria-hidden="true">
          <circle cx={CENTER_X} cy={CENTER_Y} r="7" />
          <line x1={CENTER_X - 13} y1={CENTER_Y} x2={CENTER_X + 13} y2={CENTER_Y} />
          <line x1={CENTER_X} y1={CENTER_Y - 13} x2={CENTER_X} y2={CENTER_Y + 13} />
          <text x={CENTER_X + 18} y={CENTER_Y - 18}>(0,0)</text>
        </g>

        <path d={arcPath(innerRadius, startAngle - 8, endAngle + 8)} className="corridor-guide-arc minimum" />
        <path d={arcPath(outerRadius, startAngle - 8, endAngle + 8)} className="corridor-guide-arc maximum" />
        <path
          d={directedArcPath(directionArrowRadius, directionArrowStartAngle, directionArrowEndAngle, directionArrowSweep)}
          className="target-rotation-arrow"
          markerEnd={`url(#${directionArrowMarkerId})`}
        />
        <text
          x={directionArrowLabel.x}
          y={directionArrowLabel.y + 4}
          textAnchor="middle"
          className="target-rotation-label"
        >
          {passageDirection === 'retrograde' ? 'retrograd' : 'prograd'}
        </text>
        <path
          d={corridorBandPath(innerRadius, outerRadius, startAngle, endAngle)}
          className={`${definition.enabled ? 'target-corridor-band' : 'target-corridor-band disabled'}${feasibility.blocked ? ' blocked' : ''}${isMainProjection ? '' : ' projected'}${projectionTouchesObject ? ' on-object' : ''}`}
          role="slider"
          tabIndex={isMainProjection ? 0 : -1}
          aria-label="Position des Zielkorridors"
          aria-disabled={!isMainProjection}
          aria-invalid={feasibility.blocked || undefined}
          aria-valuemin={-180}
          aria-valuemax={180}
          aria-valuenow={Math.round(projectionAngleDeg)}
          onKeyDown={moveCorridorWithKeyboard}
          onPointerDown={(event) => {
            if (!isMainProjection) return
            event.stopPropagation()
            dragging.current = true
            event.currentTarget.setPointerCapture(event.pointerId)
            updateCenterFromPointer(event)
          }}
        />
        <path d={arcPath(displayCorridorRadius, startAngle, endAngle)} className={`target-corridor-centerline${isMainProjection ? '' : ' projected'}`} />
        <circle cx={corridorCenter.x} cy={corridorCenter.y} r="9" className="corridor-drag-handle" />
        <text
          x={corridorTitlePoint.x}
          y={corridorTitlePoint.y}
          textAnchor="middle"
          className="target-corridor-title"
        >
          {isMainProjection ? 'Zielkorridor' : 'Korridorprojektion'}
        </text>
        <text
          x={minimumPoint.x}
          y={minimumLabelY}
          textAnchor={minimumTextAnchor}
          className="corridor-boundary-label minimum"
        >
          Minimum
        </text>
        <text
          x={maximumPoint.x}
          y={maximumLabelY}
          textAnchor={maximumTextAnchor}
          className="corridor-boundary-label maximum"
        >
          Maximum
        </text>

        <path d={`M ${approachLineStart.x} ${approachLineStart.y} L ${corridorCenter.x} ${corridorCenter.y}`} className={`approach-vector${feasibility.blocked ? ' blocked' : ''}`} markerEnd="url(#approach-arrow)" />
        <path d={`M ${approachLineStart.x} ${approachLineStart.y} L ${minusTarget.x} ${minusTarget.y}`} className={`delta-v-limit minus${feasibility.blocked ? ' blocked' : ''}`} />
        <path d={`M ${approachLineStart.x} ${approachLineStart.y} L ${plusTarget.x} ${plusTarget.y}`} className={`delta-v-limit plus${feasibility.blocked ? ' blocked' : ''}`} />
        <circle
          cx={approachStart.x}
          cy={approachStart.y}
          r="15"
          fill={originColor}
          className="origin-object-symbol"
        />
        <text x={approachStart.x + 35} y={approachStart.y - 18} className="delta-v-label">Δv −{deltaVMinusKmS.toFixed(1)} km/s</text>
        <text x={approachStart.x + 35} y={approachStart.y + 34} className="delta-v-label">Δv +{deltaVPlusKmS.toFixed(1)} km/s</text>
        <text x={approachStart.x - 26} y={approachStart.y + 48} textAnchor="middle" className="probe-label">{originName} · Ursprung</text>
        <g className="corridor-legend" role="group" aria-label="Legende des Zielkorridors" transform="translate(755 474)">
          <rect width="226" height="128" rx="12" />
          <text x="14" y="23" className="corridor-legend-title">Legende</text>
          <line x1="14" y1="43" x2="48" y2="43" className="corridor-legend-line minimum" />
          <text x="58" y="47">Minimum · objektnahe Grenze</text>
          <line x1="14" y1="67" x2="48" y2="67" className="corridor-legend-line maximum" />
          <text x="58" y="71">Maximum · äußerste Grenze</text>
          <line x1="14" y1="91" x2="48" y2="91" className="corridor-legend-line safety" />
          <text x="58" y="95">Sicherheitsradius</text>
          <line x1="14" y1="115" x2="48" y2="115" className="corridor-legend-line delta-v" />
          <text x="58" y="119">Erreichbarer Δv-Fächer</text>
        </g>
      </svg>
          )}
    </div>
  )
}
