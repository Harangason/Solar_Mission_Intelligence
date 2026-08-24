import { Billboard, Html, Line } from '@react-three/drei'
import { type ReactNode, useEffect, useMemo, useState } from 'react'
import * as THREE from 'three'

import { AU_KM } from '../missionSimulation'
import { toScenePosition } from '../orbitalMath'
import { DraggableInfoLabel } from './DraggableInfoLabel'

function RouteSectionStateLabel({ children, title }: { children: ReactNode; title: string }) {
  const [expanded, setExpanded] = useState(true)

  return (
    <div className={`route-section-state-label${expanded ? '' : ' collapsed'}`}>
      <div className="route-section-state-header">
        <strong>{title}</strong>
        <button
          type="button"
          className="route-section-state-toggle"
          data-no-drag
          aria-label={`${title} Details ${expanded ? 'einklappen' : 'ausklappen'}`}
          aria-expanded={expanded}
          onClick={() => setExpanded((current) => !current)}
          onDoubleClick={(event) => event.stopPropagation()}
        >
          <span aria-hidden="true">{expanded ? '−' : '+'}</span>
        </button>
      </div>
      {expanded && <div className="route-section-state-body">{children}</div>}
    </div>
  )
}

export interface WaypointRouteResult {
  calculationPersistence?: {
    runId: string
    variantId: string
  }
  startDate: string
  genericTarget?: {
    type: string
    zoneId?: string
    boundaryId?: string
    distanceAU?: number
    innerRadiusAU?: number
    outerRadiusAU?: number
    radiusAU?: number
  }
  totalFlightDays: number
  spacecraftIntegration?: {
    validated: boolean
    routeGeometryPreserved: boolean
    wetMassKg: number
    payloadMassKg: number
    requestedOberthDeltaVKmS: number
    achievedOberthDeltaVKmS: number
    propellantUsedKg: number
    enabledPropulsionModules: Array<string | null>
    warnings: string[]
  } | null
  warnings?: string[]
  solarPassage?: {
    entryIndex: number
    periapsisIndex: number
    exitIndex: number
    corridorRadiusKm: number
    periapsisRadiusKm: number
    entryPositionKm: [number, number, number]
    exitPositionKm: [number, number, number]
    entryDirection: [number, number, number]
    exitDirection: [number, number, number]
    passageAngleDeg: number
    orbitDirection: 'prograde' | 'retrograde'
    outboundAfterPeriapsis: boolean
  }
  solarBoundary?: {
    definition: string
    radiusAu: number
    desiredExitSpeedKmS: number | null
    actualExitSpeedKmS: number
    speedResidualKmS: number
    toleranceKmS: number | null
    speedBoundaryReached: boolean
    availableOberthDeltaVKmS: number
    requiredOberthVectorDeltaVKmS: number
    maximumExitSpeedWithAvailableBurnKmS: number
    minimumOberthDeltaVForDesiredSpeedKmS: number
    additionalDeltaVRequiredKmS: number
    energeticallyReachable: boolean
    constraintKind: 'propulsion-delta-v'
    electricalPowerDeficit: false
    energyBoundModel: string
    entryDate: string
    entryElapsedDays: number
    entryPositionKm: [number, number, number]
    perihelionDateTime: string
    perihelionElapsedDays: number
    perihelionPositionKm: [number, number, number]
  }
  audit?: {
    runId: string
    createdAtUtc: string
    logFile: string
    documentation: string
  }
  waypoint: {
    id: string
    name: string
    encounterDay: number
    entryDay?: number
    exitDay?: number
    flybyAltitudeKm: number
    minimumFlybyAltitudeKm?: number
    trajectoryIndex?: number
    positionKm: [number, number, number]
  }
  trajectory: Array<{
    elapsedDays: number
    positionKm: [number, number, number]
    waypointPositionKm?: [number, number, number]
    waypointRelativePositionKm?: [number, number, number]
  }>
  segments?: Array<{
    id: string
    label: string
    startIndex: number
    endIndex: number
  }>
  transitionDiagnostics?: {
    entryPositionResidualKmBeforePatch: number
    entryVelocityResidualKmS: number
    exitPositionResidualKm: number
    exitVelocityResidualKmS: number
    exitTargetInjectionDeltaVKmS?: number
    exitTargetInjectionApplied?: boolean
    exitTargetInjectionDirectionChangeDeg?: number
    burnToLambertDirectionChangeDeg?: number
    lambertToHyperbolaDirectionChangeDeg?: number
    lambertPropagationEndpointResidualKm?: number
    lambertPropagationVelocityResidualKmS?: number
    lambertSelection?: {
      transferSide: string
      motion: string
      revolutionFamily?: number
      injectionDeltaVKmS: number
      directionChangeDeg: number
      candidateCount: number
    }
    bidirectionalMatch?: {
      method: string
      maximumTurnDeg: number
      demandedTurnDeg: number
      turnClosureResidualDeg: number
      boundaryVelocityResidualKmS: number
      backwardAlignmentDeg: number
      initialTargetProgressKmS: number
      backwardIterations: number
      passiveMatch: boolean
    }
  }
  uncertainty?: {
    confidenceLevelPct: number
    model: string
    stateContinuity?: string
    kalmanEnabled: boolean
    navigationCycleHours: number
    covariance: Array<{
      elapsedDays: number
      positionSigmaKm: number
      velocitySigmaKmS: number
      radius95Km: number
    }>
    samples: Array<{
      id: number
      trajectory: Array<{ elapsedDays: number; positionKm: [number, number, number] }>
    }>
    summary: {
      startRadius95Km: number
      waypointRadius95Km: number
      maximumRadius95Km: number
      navigationCycles: number
    }
  }
  outgoingDirection: [number, number, number]
  entryCorridor?: {
    enabled: boolean
    surface: string
    selectionStrategy: string
    centerDirection: [number, number, number]
    horizontalHalfAngleDeg: number
    verticalHalfAngleDeg: number
    rotationDeg: number
    selectedDirection?: [number, number, number] | null
    selectedHorizontalOffsetDeg?: number | null
    selectedVerticalOffsetDeg?: number | null
    evaluatedTargetCount: number
    selectedRequiredInjectionDeltaVKmS?: number | null
    actualEntryDirection: [number, number, number]
    actualHorizontalOffsetDeg: number
    actualVerticalOffsetDeg: number
    actualEntryPositionKm: [number, number, number]
    entryInsideCorridor: boolean
  }
  routeSections?: Array<{
    id: string
    sectionType?: string
    originId: string
    targetId: string
    targetName: string
    entryIndex: number
    periapsisIndex: number
    exitIndex: number
    entryDay: number
    periapsisDay: number
    exitDay: number
    entryPositionKm: [number, number, number]
    entryDirection: [number, number, number]
    entryLatitudeDeg: number
    minimumAltitudeKm: number
    requiredTransitionDeltaVKmS: number
    availableTransitionDeltaVKmS?: number
    transitionDeltaVDeficitKmS?: number
    departureRadialSpeedKmS?: number
    departureDirectionChangeDeg?: number
    backtracksFromOuterTarget?: boolean
    transferDurationDays?: number
    corridorInsertionDeltaVKmS: number
    entryVelocityPreserved?: boolean
    lookaheadTargetId?: string | null
    lookaheadAlignmentDeg?: number
    selectedBPlaneClockDeg?: number
    predictedPassiveTurnDeg?: number
    desiredDepartureDirection?: [number, number, number] | null
    predictedOutgoingDirection?: [number, number, number]
    requestedPassageAngleDeg?: number
    selectedPassageAngleDeg?: number
    corridor: {
      enabled: boolean
      entryInsideCorridor: boolean
      centerDirection?: [number, number, number]
      passageSignedAngleDeg?: number
      exitAngleSelection?: {
        method: string
        lookaheadTargetId: string
        requestedAngleDeg: number
        selectedAngleDeg: number
        autoExtendedAngleDeg?: number
        transferPreviewDays?: number
        desiredExitDirection?: [number, number, number]
        desiredExitRadialDirection?: [number, number, number]
        optimizedPassageNormal?: [number, number, number]
        lineOfSightClear?: boolean
        bestApproximation?: boolean
        requiresCurvedTransfer?: boolean
        futureTargetDistanceKm?: number
        keepOutRadiusKm?: number
        departureClearanceKm?: number
        straightLineClearanceDeficitKm?: number
      } | null
    }
  }>
  highFidelityNBody?: {
    enabled: boolean
    converged: boolean
    collision?: boolean
    forceModel?: string
    differentialCorrection?: {
      correctionMagnitudeKmS: number
      entryPositionResidualKm: number
      entryVelocityResidualKmS: number
      requiredDepartureDeltaVKmS?: number
      feasibleWithConfiguredBurn?: boolean
    }
    flyby?: {
      periapsisDay: number
      periapsisRadiusKm: number
      periapsisAltitudeKm: number
      turnAngleDeg: number
      exitPositionResidualToPatchedConicKm: number
      exitVelocityResidualToPatchedConicKmS: number
    }
    trajectory: Array<{
      elapsedDays: number
      positionKm: [number, number, number]
      velocityKmS: [number, number, number]
    }>
  }
  flybyGeometry?: {
    curveModel?: string
    targetingMode?: string
    sampleCount?: number
    stateContinuousWithinFlyby?: boolean
    separateTargetImpulseAtSoiExit?: boolean
    incomingExcessDirection: [number, number, number]
    outgoingExcessDirection: [number, number, number]
    incomingHeliocentricDirection: [number, number, number]
    outgoingHeliocentricDirection: [number, number, number]
    gravityOnlyOutgoingDirection?: [number, number, number]
    targetInjectionDirection?: [number, number, number]
    targetAsymptoteDirection?: [number, number, number]
    periapsisRadiusKm: number
    planetRadiusKm: number
    sphereOfInfluenceRadiusKm?: number
    hyperbolaEccentricity: number
    semiMajorAxisMagnitudeKm: number
    hyperbolicAnomalyLimit?: number
    axisX?: [number, number, number]
    axisY?: [number, number, number]
    flybyPlaneNormal?: [number, number, number]
    entryRelativePositionKm?: [number, number, number]
    periapsisRelativePositionKm?: [number, number, number]
    exitRelativePositionKm?: [number, number, number]
    entryLatitudeDeg?: number
    periapsisLatitudeDeg?: number
    exitLatitudeDeg?: number
    verticalTurnDeg?: number
    aimpoint?: {
      enabled: boolean
      clockAngleDeg: number
      screenRadiusNorm: number
      role: 'entry' | 'periapsis' | 'exit'
      altitudeKm: number
      requestedRelativePositionKm?: [number, number, number] | null
      relativePositionKm?: [number, number, number] | null
      absolutePositionKm?: [number, number, number] | null
      requestedDirection?: [number, number, number] | null
      alignmentBeforeDeg?: number | null
      alignmentAfterDeg?: number | null
      warning?: string | null
    }
    relativeTrajectory?: Array<{
      elapsedDays: number
      anomaly: number
      positionKm: [number, number, number]
      velocityKmS: [number, number, number]
    }>
  }
  validation?: {
    collisionFree: boolean
    minimumSolarRadiusKm: number
    sunRadiusKm: number
    minimumSolarAltitudeKm: number
  }
  summary: {
    flybyMode: 'acceleration' | 'observation' | 'multi-section'
    requiredInjectionDeltaVKmS: number
    availableInjectionDeltaVKmS?: number
    solarDepartureInjectionApplied?: boolean
    incomingExcessSpeedKmS: number
    turnAngleDeg: number
    heliocentricSpeedBeforeKmS: number
    heliocentricSpeedAfterKmS: number
    speedGainKmS: number
    targetDepartureSpeedKmS?: number
    solarEscapeSpeedAtExitKmS?: number
    targetCorrectionDeltaVKmS?: number
    targetInjectionApplied?: boolean
    passiveTargeting?: boolean
    actualTrajectoryMode?: 'target-injection' | 'gravity-only'
    actualTargetAlignmentDeg?: number | null
    desiredSolarExitSpeedKmS?: number | null
    actualSolarExitSpeedKmS?: number
    solarExitSpeedResidualKmS?: number
    solarSpeedBoundaryReached?: boolean
    targetProgressMonotonic?: boolean
    minimumTargetProgressRateKmS?: number
    targetingIterations?: number
    targetingMode?: string
    courseChangeDeg?: number
    periapsisSpeedKmS: number
    observationWindowHours: number
    targetAlignmentDeg: number
    feasibleWithConfiguredBurn: boolean
    hypotheticalInterstellarAsymptote?: boolean
    interstellarVisualizationDistanceAu?: number | null
    highFidelityNBodyConverged?: boolean
    highFidelityNBodyCollision?: boolean
    highFidelityRequiredDepartureDeltaVKmS?: number
    entryCorridorTargeted?: boolean
    entryInsideCorridor?: boolean
    warnings?: string[]
    model: string
  }
}

interface PlannedWaypointRouteProps {
  route: WaypointRouteResult
  orbitScale: number
  inclinationScale: number
  elapsedDays: number
  showDispersion: boolean
  dispersionWidth: number
  showNavigationGuide: boolean
  encounterBodyRadius: number
  probeScale: number
  targetPosition?: THREE.Vector3
  onInfoDragChange?: (label: string, active: boolean) => void
}

function scenePosition(position: [number, number, number], orbitScale: number, inclinationScale: number) {
  return toScenePosition(
    new THREE.Vector3(position[0] / AU_KM, position[2] / AU_KM, position[1] / AU_KM),
    orbitScale,
    inclinationScale,
  )
}

export function PlannedWaypointRoute({ route, orbitScale, inclinationScale, elapsedDays, showDispersion, dispersionWidth, showNavigationGuide, encounterBodyRadius, probeScale, targetPosition, onInfoDragChange }: PlannedWaypointRouteProps) {
  const physicalPoints = useMemo(
    () => route.trajectory.map((point) => scenePosition(point.positionKm, orbitScale, inclinationScale)),
    [inclinationScale, orbitScale, route],
  )
  const continuousNBodyPoints = useMemo(
    () => route.highFidelityNBody?.trajectory.map(
      (point) => scenePosition(point.positionKm, orbitScale, inclinationScale),
    ) ?? [],
    [inclinationScale, orbitScale, route.highFidelityNBody],
  )
  const waypoint = scenePosition(route.waypoint.positionKm, orbitScale, inclinationScale)
  const zoneRadii = route.genericTarget?.type === 'zone'
    ? {
        inner: scenePosition([(route.genericTarget.innerRadiusAU ?? route.genericTarget.distanceAU ?? 0) * AU_KM, 0, 0], orbitScale, inclinationScale).length(),
        outer: scenePosition([(route.genericTarget.outerRadiusAU ?? route.genericTarget.distanceAU ?? 0) * AU_KM, 0, 0], orbitScale, inclinationScale).length(),
      }
    : null
  const boundaryRadius = route.genericTarget?.type === 'boundary'
    ? scenePosition([(route.genericTarget.radiusAU ?? route.genericTarget.distanceAU ?? 0) * AU_KM, 0, 0], orbitScale, inclinationScale).length()
    : 0
  const waypointIndex = route.waypoint.trajectoryIndex ?? route.trajectory.findIndex((point) => point.elapsedDays >= route.waypoint.encounterDay)
  const flybySegment = route.segments?.find((segment) => segment.id === 'jupiter-hyperbola')
  // The global view stays in one inertial frame and uses one transform only.
  // A planet-centred enlargement would move the SOI endpoints and falsify
  // tangents, so that representation lives in the separate FlybyFocusInset.
  const points = physicalPoints
  const routeCurrentIndex = useMemo(() => {
    let low = 0
    let high = route.trajectory.length - 1
    while (low < high) {
      const middle = Math.ceil((low + high) / 2)
      if (route.trajectory[middle].elapsedDays <= elapsedDays) low = middle
      else high = middle - 1
    }
    return low
  }, [elapsedDays, route.trajectory])
  const routeProbePosition = useMemo(() => {
    const current = points[routeCurrentIndex]
    const next = points[Math.min(points.length - 1, routeCurrentIndex + 1)]
    if (!current || !next || current === next) return current
    const currentDay = route.trajectory[routeCurrentIndex].elapsedDays
    const nextDay = route.trajectory[routeCurrentIndex + 1]?.elapsedDays ?? currentDay
    const fraction = nextDay > currentDay
      ? THREE.MathUtils.clamp((elapsedDays - currentDay) / (nextDay - currentDay), 0, 1)
      : 0
    return current.clone().lerp(next, fraction)
  }, [elapsedDays, points, route.trajectory, routeCurrentIndex])
  const uncertainty = useMemo(() => {
    if (!route.uncertainty) return null
    const currentIndex = routeCurrentIndex
    const maximumRadius = Math.max(...route.uncertainty.covariance.map((point) => point.radius95Km), 1)
    const displayRadii = route.uncertainty.covariance.map((point) => (
      dispersionWidth * (0.22 + 0.78 * point.radius95Km / maximumRadius)
    ))
    const currentCenter = points[currentIndex]
    const radius95Km = route.uncertainty.covariance[currentIndex]?.radius95Km ?? 0
    return { currentIndex, currentCenter, displayRadius: displayRadii[currentIndex], displayRadii, radius95Km }
  }, [dispersionWidth, inclinationScale, orbitScale, points, route, routeCurrentIndex])
  const corridorGeometry = useMemo(() => {
    if (!uncertainty || points.length < 2) return null
    const radialSegments = 10
    const vertices: number[] = []
    const indices: number[] = []
    const indexedPoints = points.map((point, index) => ({ point, index }))
    const runs = [indexedPoints]
    runs.forEach((run) => {
      const vertexOffset = vertices.length / 3
      const tangents = run.map(({ point }, index) => {
        const before = run[Math.max(0, index - 1)].point
        const after = run[Math.min(run.length - 1, index + 1)].point
        return after.clone().sub(before).normalize()
      })
      let transportedNormal = new THREE.Vector3(0, 1, 0)
      if (Math.abs(transportedNormal.dot(tangents[0])) > 0.92) transportedNormal.set(1, 0, 0)
      run.forEach(({ point, index: trajectoryIndex }, runIndex) => {
        const tangent = tangents[runIndex]
        const binormal = tangent.clone().cross(transportedNormal).normalize()
        const normal = binormal.clone().cross(tangent).normalize()
        transportedNormal = normal
        const radius = uncertainty.displayRadii[trajectoryIndex] ?? dispersionWidth
        for (let radialIndex = 0; radialIndex < radialSegments; radialIndex += 1) {
          const angle = radialIndex / radialSegments * Math.PI * 2
          const vertex = point.clone()
            .addScaledVector(normal, Math.cos(angle) * radius)
            .addScaledVector(binormal, Math.sin(angle) * radius)
          vertices.push(vertex.x, vertex.y, vertex.z)
        }
      })
      for (let runIndex = 0; runIndex < run.length - 1; runIndex += 1) {
        for (let radialIndex = 0; radialIndex < radialSegments; radialIndex += 1) {
          const nextRadial = (radialIndex + 1) % radialSegments
          const current = vertexOffset + runIndex * radialSegments + radialIndex
          const next = vertexOffset + runIndex * radialSegments + nextRadial
          const ahead = vertexOffset + (runIndex + 1) * radialSegments + radialIndex
          const aheadNext = vertexOffset + (runIndex + 1) * radialSegments + nextRadial
          indices.push(current, ahead, next, next, ahead, aheadNext)
        }
      }
    })
    const geometry = new THREE.BufferGeometry()
    geometry.setAttribute('position', new THREE.Float32BufferAttribute(vertices, 3))
    geometry.setIndex(indices)
    geometry.computeVertexNormals()
    return geometry
  }, [dispersionWidth, points, uncertainty])
  useEffect(() => () => corridorGeometry?.dispose(), [corridorGeometry])
  const localFlyby = useMemo(() => {
    if (!flybySegment) return null
    const states = route.trajectory.slice(flybySegment.startIndex, flybySegment.endIndex + 1)
    const localPoints = points.slice(flybySegment.startIndex, flybySegment.endIndex + 1)
    if (states.length < 2 || localPoints.length < 2) return null
    const periapsisOffset = Math.max(0, Math.min(localPoints.length - 1, waypointIndex - flybySegment.startIndex))
    return { points: localPoints, states, entry: localPoints[0], periapsis: localPoints[periapsisOffset], exit: localPoints[localPoints.length - 1] }
  }, [flybySegment, points, route.trajectory, waypointIndex])
  const localAimpoint = useMemo(() => {
    const aimpoint = route.flybyGeometry?.aimpoint
    if (!aimpoint?.absolutePositionKm) return null
    return scenePosition(aimpoint.absolutePositionKm, orbitScale, inclinationScale)
  }, [orbitScale, inclinationScale, route.flybyGeometry?.aimpoint?.absolutePositionKm])
  const departureFeasible = route.summary.solarDepartureInjectionApplied
    ?? route.summary.feasibleWithConfiguredBurn
  const targetCourseAchieved = Boolean(route.summary.passiveTargeting || route.summary.targetInjectionApplied)
  const homogeneousRouteColor = route.summary.feasibleWithConfiguredBurn ? '#67f59a' : '#ff9c63'
  const showEncounterGhost = Math.abs(elapsedDays - route.waypoint.encounterDay) > 1
  return (
    <group>
      {zoneRadii && zoneRadii.outer > zoneRadii.inner && (
        <mesh rotation={[Math.PI / 2, 0, 0]} renderOrder={-2}>
          <torusGeometry args={[(zoneRadii.inner + zoneRadii.outer) / 2, (zoneRadii.outer - zoneRadii.inner) / 2, 24, 160]} />
          <meshBasicMaterial color="#5bc8ff" transparent opacity={0.075} side={THREE.DoubleSide} depthWrite={false} />
        </mesh>
      )}
      {boundaryRadius > 0 && (
        <mesh renderOrder={-2}>
          <sphereGeometry args={[boundaryRadius, 64, 32]} />
          <meshBasicMaterial color="#b98cff" transparent opacity={0.045} wireframe depthWrite={false} />
        </mesh>
      )}
      {showDispersion && uncertainty && route.uncertainty && (
        <group>
          {corridorGeometry && (
            <mesh geometry={corridorGeometry} renderOrder={-1}>
              <meshBasicMaterial color="#4bc8f5" transparent opacity={0.085} side={THREE.DoubleSide} depthWrite={false} blending={THREE.AdditiveBlending} />
            </mesh>
          )}
          <Billboard position={uncertainty.currentCenter}>
            <mesh>
              <ringGeometry args={[uncertainty.displayRadius * 0.94, uncertainty.displayRadius, 64]} />
              <meshBasicMaterial color="#8fe6ff" transparent opacity={0.62} side={THREE.DoubleSide} depthWrite={false} />
            </mesh>
            <Html center position={[0, uncertainty.displayRadius + 0.22, 0]}>
              <span className="dispersion-label">95 % · ±{uncertainty.radius95Km.toLocaleString('de-DE', { maximumFractionDigits: 0 })} km</span>
            </Html>
          </Billboard>
        </group>
      )}
      <Line points={points} color={homogeneousRouteColor} lineWidth={2.45} dashed={!departureFeasible || continuousNBodyPoints.length > 1} dashSize={0.2} gapSize={0.11} transparent opacity={continuousNBodyPoints.length > 1 ? 0.34 : departureFeasible ? 0.97 : 0.8} />
      {continuousNBodyPoints.length > 1 && (
        <Line
          points={continuousNBodyPoints}
          color={route.highFidelityNBody?.collision ? '#ff425f' : route.highFidelityNBody?.converged ? '#a6ff63' : '#ffb347'}
          lineWidth={2.8}
          transparent
          opacity={0.98}
        />
      )}
      {routeProbePosition && <mesh position={routeProbePosition}><octahedronGeometry args={[Math.max(0.012, 0.006 * Math.sqrt(probeScale)), 1]} /><meshStandardMaterial color="#fff4b0" emissive="#ff8d3a" emissiveIntensity={1.35} /></mesh>}
      {route.routeSections?.map((section, index) => {
        const entry = points[section.entryIndex]
        const periapsis = points[section.periapsisIndex]
        const exit = points[section.exitIndex]
        const followingTargetName = route.routeSections?.[index + 1]?.targetName
        if (!entry || !periapsis || !exit) return null
        const sectionLabel = `${String(index + 1).padStart(2, '0')} · ${section.targetName}-Eintritt`
        const initialOffset: [number, number] = index % 2 === 0
          ? [32, -104 - index * 14]
          : [-232, -88 - index * 14]
        return (
          <group key={`calculated-section-${section.id}`}>
            <mesh position={entry}>
              <sphereGeometry args={[0.032, 14, 14]} />
              <meshStandardMaterial color="#65ddff" emissive="#159bc8" emissiveIntensity={0.8} />
            </mesh>
            <mesh position={periapsis}>
              <sphereGeometry args={[0.035, 14, 14]} />
              <meshStandardMaterial color="#ffe171" emissive="#e8901d" emissiveIntensity={0.9} />
            </mesh>
            <mesh position={exit}>
              <sphereGeometry args={[0.032, 14, 14]} />
              <meshStandardMaterial color="#65ff9a" emissive="#19bd62" emissiveIntensity={0.8} />
            </mesh>
            <DraggableInfoLabel
              initialOffset={initialOffset}
              label={sectionLabel}
              onDragChange={onInfoDragChange}
              position={entry}
            >
              <RouteSectionStateLabel title={sectionLabel}>
                <small>
                  Breite {section.entryLatitudeDeg >= 0 ? '+' : ''}{section.entryLatitudeDeg.toFixed(1)}° ·
                  Korridor {section.corridor.entryInsideCorridor ? 'getroffen' : 'verfehlt'}
                </small>
                <small>
                  Übergang Δv {section.requiredTransitionDeltaVKmS.toFixed(2)} km/s ·
                  Einschuss {section.corridorInsertionDeltaVKmS.toFixed(2)} km/s
                </small>
                {followingTargetName && (
                  <small>
                    Fly-by auf {followingTargetName} vorausgerichtet ·
                    Restwinkel {(section.lookaheadAlignmentDeg ?? 0).toFixed(1)}°
                  </small>
                )}
              </RouteSectionStateLabel>
            </DraggableInfoLabel>
          </group>
        )
      })}
      {showNavigationGuide && targetPosition && (
        <Line points={[points[0], waypoint, targetPosition]} color="#8be8ff" lineWidth={1.15} dashed dashSize={1.0} gapSize={0.55} transparent opacity={0.9} depthWrite={false} />
      )}
      {targetPosition && localFlyby && !targetCourseAchieved && (
        <>
          <Line points={[localFlyby.exit, targetPosition]} color="#39ff78" lineWidth={1.35} dashed dashSize={0.7} gapSize={0.38} transparent opacity={0.72} depthWrite={false} />
          <Html center position={localFlyby.exit.clone().lerp(targetPosition, 0.24)}>
            <span className="dispersion-label">Soll-Zielkurs · Δv {(route.summary.targetCorrectionDeltaVKmS ?? 0).toFixed(2)} km/s fehlt</span>
          </Html>
        </>
      )}
      <group position={waypoint}>
        {showEncounterGhost && <mesh>
          <sphereGeometry args={[encounterBodyRadius, 24, 24]} />
          <meshBasicMaterial color="#79dfff" wireframe transparent opacity={0.42} depthWrite={false} />
        </mesh>}
        {showEncounterGhost && route.waypoint.id === 'saturn' && <mesh rotation={[Math.PI / 2, 0, 0]}>
          <torusGeometry args={[encounterBodyRadius * 1.8, encounterBodyRadius * 0.12, 10, 48]} />
          <meshBasicMaterial color="#efc17c" transparent opacity={0.72} />
        </mesh>}
        <DraggableInfoLabel initialOffset={[32, 28]} label={`${route.waypoint.name} am Begegnungstag`} onDragChange={onInfoDragChange}>
          <span className="interstellar-label flyby-label compact-flyby-label">
            <strong>{route.waypoint.name} · Begegnung Tag {route.waypoint.encounterDay.toFixed(1)}</strong>
            <small>Perizentrum {(route.waypoint.flybyAltitudeKm).toLocaleString('de-DE', { maximumFractionDigits: 0 })} km · Kurs {(route.summary.courseChangeDeg ?? 0).toFixed(1)}° · Gewinn {route.summary.speedGainKmS >= 0 ? '+' : ''}{route.summary.speedGainKmS.toFixed(2)} km/s</small>
            <small>Unverdeckte Hyperbel im Flyby-Fokus · Zielimpuls Δv {(route.summary.targetCorrectionDeltaVKmS ?? 0).toFixed(2)} km/s</small>
            {route.flybyGeometry?.aimpoint?.absolutePositionKm && (
              <small>
                Aimpoint {(route.flybyGeometry?.aimpoint?.role ?? 'periapsis')} · Höhe {(route.flybyGeometry?.aimpoint?.altitudeKm ?? route.waypoint.flybyAltitudeKm).toLocaleString('de-DE', { maximumFractionDigits: 0 })} km
              </small>
            )}
            {route.flybyGeometry?.aimpoint?.warning && <small>{route.flybyGeometry.aimpoint.warning}</small>}
            {route.entryCorridor?.enabled && (
              <small>
                SOI-Korridor {route.entryCorridor.entryInsideCorridor ? 'getroffen' : 'verfehlt'} ·
                Zielversatz {route.entryCorridor.actualHorizontalOffsetDeg.toFixed(1)}° /
                {route.entryCorridor.actualVerticalOffsetDeg.toFixed(1)}° ·
                {route.entryCorridor.evaluatedTargetCount} Zielpunkte bewertet
              </small>
            )}
            {route.highFidelityNBody?.enabled && route.highFidelityNBody.flyby && (
              <small>
                N-Körper {route.highFidelityNBody.converged ? 'konvergiert' : 'nicht konvergiert'} · tatsächliche Höhe {route.highFidelityNBody.flyby.periapsisAltitudeKm.toLocaleString('de-DE', { maximumFractionDigits: 0 })} km · Abflugkorrektur {(route.highFidelityNBody.differentialCorrection?.correctionMagnitudeKmS ?? 0).toFixed(3)} km/s
              </small>
            )}
            {route.summary.warnings?.length ? <small>{route.summary.warnings[0]}</small> : null}
          </span>
        </DraggableInfoLabel>
      </group>
      {localFlyby && <>
        <mesh position={localFlyby.entry}><sphereGeometry args={[0.022, 12, 12]} /><meshBasicMaterial color="#67dcff" /></mesh>
        <mesh position={localFlyby.exit}><sphereGeometry args={[0.022, 12, 12]} /><meshBasicMaterial color="#55ff8a" /></mesh>
      </>}
      {localAimpoint && (
        <>
          <mesh position={localAimpoint}><sphereGeometry args={[0.028, 12, 12]} /><meshStandardMaterial color="#ff6be1" emissive="#ff6be1" emissiveIntensity={0.48} /></mesh>
          <Html center position={[localAimpoint.x, localAimpoint.y + 0.11, localAimpoint.z]}>
            <span className="dispersion-label">
              Aimpoint {route.flybyGeometry?.aimpoint?.role ?? 'periapsis'} · h {(route.flybyGeometry?.aimpoint?.altitudeKm ?? route.waypoint.flybyAltitudeKm).toLocaleString('de-DE', { maximumFractionDigits: 0 })} km
            </span>
          </Html>
        </>
      )}
    </group>
  )
}
