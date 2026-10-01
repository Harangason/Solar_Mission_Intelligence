import {
  useEffect,
  useMemo,
  useRef,
  useState,
  type CSSProperties,
  type Dispatch,
  type PointerEvent,
  type SetStateAction,
} from 'react'
import * as THREE from 'three'

import { activityRequestHeaders, logActivity } from '../activityLog'
import {
  buildTemporalCandidateGraph,
  constellationSearchBudget,
  constellationSearchWindow,
  selectAdaptiveLaunchWindowCandidates,
  selectTemporallyDiverseCandidates,
  temporalRefinementNeighbors,
} from '../constellationGraph'
import type { EntryCorridorDefinition } from '../entryCorridorGeometry'
import { ROUTE_INTERSTELLAR_SYSTEMS } from '../interstellarTargets'
import { DEFAULT_MISSION_CONFIG } from '../missionSimulation'
import { sanitizeMoonCatalogue } from '../moonCatalogue'
import { createOrbitPoints, planetPositionAt, toScenePosition } from '../orbitalMath'
import {
  directionBetweenRouteObjects,
  interstellarTargetDirection,
} from '../routeObjectDirections'
import { projectPhysicsDirectionToView } from '../routeDirectionMath'
import {
  MAX_PARTIAL_ORBIT_ANGLE_DEG,
  normalizeRouteSections,
  type RouteBoundaryBehavior,
  type RouteSectionDefinition,
} from '../routeSections'
import { validateRouteGeometry } from '../routeGeometryValidation'
import type { MissionConfig, MoonCatalogue, SolarSystemData } from '../types'
import type { Vector3Tuple } from '../targetAlignedProjection'
import type { WaypointRouteResult } from './PlannedWaypointRoute'
import { PlanetCorridorPlanner } from './PlanetCorridorPlanner'
import {
  RouteCalculationDialog,
  type RouteCalculationCandidateTrace,
  type RouteCalculationRunSummary,
  type RouteCalculationTrace,
} from './RouteCalculationDialog'
import { RouteSectionList } from './RouteSectionList'
import { TwoDPlanetDetails } from './TwoDPlanetDetails'

type Projection = 'corridor' | 'side' | 'top'
type OrbitalProjection = 'side' | 'top'

interface TwoDViewProps {
  projectId: string
  routeSections: RouteSectionDefinition[]
  onRouteSectionsChange: Dispatch<SetStateAction<RouteSectionDefinition[]>>
  activeRouteSectionId: string
  onActiveRouteSectionChange: (sectionId: string) => void
  plannedMissionDate: string | null
  plannedRoute: WaypointRouteResult | null
  onApplyPlannedSolution: (
    date: string,
    sections: RouteSectionDefinition[],
    route: WaypointRouteResult,
  ) => void
  missionConfig: MissionConfig | null
  solverDialogOnly?: boolean
  onSolverDialogClose?: () => void
  displayOnly?: boolean
}

interface ConstellationSearchResult {
  id: string
  date: string
  sections: RouteSectionDefinition[]
  route: WaypointRouteResult
  quality: number
  geometryValid: boolean
  hypotheticalInterstellarAsymptote: boolean
  flightReady: boolean
  good: boolean
  corridorSatisfied: boolean
  collisionFree: boolean
  requiredInjectionDeltaVKmS: number
  availableInjectionDeltaVKmS: number
  targetCorrectionDeltaVKmS: number
  corridorInsertionDeficitKmS: number
  targetAlignmentDeg: number
}

interface PersistedCalculationSection {
  source_section_id?: string
  origin_body_id?: string
  target_body_id?: string
  target_name?: string
  details_json?: string
}

type PersistedRoutePoint =
  | [number, number, number]
  | { elapsedDays?: number; positionKm?: [number, number, number] }

interface PersistedCalculationVariant {
  id: string
  calculationRunId: string
  date: string
  sections?: PersistedCalculationSection[]
  routePoints?: PersistedRoutePoint[]
  totalFlightDays?: number
  quality?: number
  geometryValid?: boolean
  hypotheticalInterstellarAsymptote?: boolean
  feasible?: boolean
  corridorSatisfied?: boolean
  collisionFree?: boolean
  requiredInjectionDeltaVKmS?: number
  availableInjectionDeltaVKmS?: number
  targetCorrectionDeltaVKmS?: number
  corridorInsertionDeficitKmS?: number
  targetAlignmentDeg?: number
  warnings?: Array<string | { message?: string }>
}

function parsePersistedRouteSection(
  section: PersistedCalculationSection,
  fallback: RouteSectionDefinition | undefined,
): RouteSectionDefinition | null {
  if (section.details_json) {
    try {
      const parsed = JSON.parse(section.details_json) as RouteSectionDefinition
      if (parsed && parsed.originId && parsed.targetId) return parsed
    } catch {
      // Older persisted rows can still be reconstructed from the columns below.
    }
  }
  if (!fallback) return null
  return {
    ...fallback,
    id: section.source_section_id || fallback.id,
    originId: section.origin_body_id || fallback.originId,
    targetId: section.target_body_id || fallback.targetId,
  }
}

function persistedRoutePoint(
  point: PersistedRoutePoint,
): { elapsedDays: number | null; positionKm: [number, number, number] } | null {
  if (Array.isArray(point) && point.length >= 3) {
    const [x, y, z] = point
    if ([x, y, z].every(Number.isFinite)) return { elapsedDays: null, positionKm: [x, y, z] }
  }
  if (point && !Array.isArray(point) && Array.isArray(point.positionKm)) {
    const [x, y, z] = point.positionKm
    if ([x, y, z].every(Number.isFinite)) {
      return {
        elapsedDays: Number.isFinite(point.elapsedDays) ? point.elapsedDays as number : null,
        positionKm: [x, y, z],
      }
    }
  }
  return null
}

const TARGET_GOOD_CONSTELLATION_RESULTS = 10
const GOOD_INTERSTELLAR_ALIGNMENT_DEG = 10

interface MLCandidateRanker {
  featureNames: string[]
  weights: Record<string, number>
  featureMeans?: Record<string, number>
  featureScales?: Record<string, number>
  intercept: number
  verdict?: string
}

function scoreWithMLRanker(
  model: MLCandidateRanker | null,
  features: Record<string, number | boolean>,
) {
  if (!model) return 0
  return model.featureNames.reduce((score, featureName) => (
    score + (
      (
        (Number(features[featureName] ?? 0) || 0)
        - (Number(model.featureMeans?.[featureName] ?? 0) || 0)
      ) / (Number(model.featureScales?.[featureName] ?? 1) || 1)
    ) * (Number(model.weights[featureName] ?? 0) || 0)
  ), Number(model.intercept ?? 0) || 0)
}

const ADAPTIVE_PASSAGE_ANGLE_ROUNDS = [
  [315, 360, 405, 450, 540],
  [180, 225, 270, 630, 720],
  [90, 135, 765, 900, 1080],
] as const

function adaptivePassageVariants(
  sections: RouteSectionDefinition[],
  round: number,
): RouteSectionDefinition[][] {
  const angles = ADAPTIVE_PASSAGE_ANGLE_ROUNDS[
    Math.min(round, ADAPTIVE_PASSAGE_ANGLE_ROUNDS.length - 1)
  ]
  const variants: RouteSectionDefinition[][] = []
  sections.forEach((section, sectionIndex) => {
    if (sectionIndex >= sections.length - 1 || isInterstellarRouteObject(section.targetId)) return
    for (const angleDeg of angles) {
      for (const orbitDirection of ['prograde', 'retrograde'] as const) {
        variants.push(sections.map((candidateSection, candidateIndex) => (
          candidateIndex === sectionIndex
            ? {
                ...candidateSection,
                corridor: { ...candidateSection.corridor },
                passage: {
                  ...candidateSection.passage,
                  mode: angleDeg === 360 ? 'full-orbit' : 'partial-orbit',
                  orbitAngleDeg: angleDeg,
                  orbitDirection,
                },
              }
            : {
                ...candidateSection,
                corridor: { ...candidateSection.corridor },
                passage: { ...candidateSection.passage },
              }
        )))
      }
    }
  })
  return variants
}

function formatRoutePathLabel(sections: RouteSectionDefinition[]) {
  const chains: string[] = []
  let currentChain: string[] = []
  for (const section of sections) {
    if (currentChain.length === 0) {
      currentChain = [section.originId, section.targetId]
    } else if (currentChain[currentChain.length - 1] === section.originId) {
      currentChain.push(section.targetId)
    } else {
      chains.push(currentChain.join(' → '))
      currentChain = [section.originId, section.targetId]
    }
  }
  if (currentChain.length > 0) chains.push(currentChain.join(' → '))
  return chains.join(' · ')
}

interface AiChatMessage {
  id: string
  role: 'assistant' | 'user'
  text: string
  basedOnSolverRunIds?: string[]
  proposedActions?: AiProposedAction[]
  auditRunId?: string
}

interface AiProposedAction {
  type: 'focus-route-section' | 'set-projection' | 'run-route-solver'
  sectionId: string | null
  projection: Projection | null
  requiresConfirmation: true
}

interface AiChatResponse {
  reply: string
  basedOnSolverRunIds: string[]
  proposedActions: AiProposedAction[]
  auditRunId: string
  model: string
  error?: string
}

interface AiPlausibilityFinding {
  code: string
  message: string
  severity: 'info' | 'warning' | 'error'
  sourceRefs?: string[]
}

interface AiPlausibilityReport {
  reportId: string
  solverRunId: string
  status: 'pass' | 'warning' | 'fail'
  findings: AiPlausibilityFinding[]
  requiredFixes: string[]
  displaySafe: boolean
  auditRunId: string
  error?: string
}

interface AiCalculationSeed {
  startDate: string
  encounterDay: number | null
  routeMode: 'gravity-assist' | 'solar-oberth' | 'direct' | 'hybrid'
  priority: number
  rationale: string
  routeSectionIds: string[]
}

interface AiCalculationWindow {
  label: string
  startDate: string
  endDate: string
  priority: number
  reason: string
}

interface AiCalculationProposal {
  strategy: 'gravity-assist' | 'solar-oberth' | 'hybrid' | 'corridor-refinement'
  searchWindows: AiCalculationWindow[]
  candidateSeeds: AiCalculationSeed[]
  rejectionHints: string[]
  expectedImprovement: string
  basedOnHistoricalRunIds: string[]
  requiresSolverValidation: true
}

interface AiCalculationSuggestion {
  suggestionId: string
  role: 'calculation'
  proposal: AiCalculationProposal
  rationale: string
  auditRunId: string
  error?: string
}

const EXTENT = 30
const SIDE_HALF_HEIGHT = EXTENT * 7 / 16
const SIDE_LABEL_Y = [-6.6, 6.5, -4.7, 4.6, -2.8, 2.7, -7.9, 7.8]
const INTERSTELLAR_PLOT_DISTANCE = EXTENT * 0.96
const AI_CHAT_SUGGESTIONS = [
  'Was sollte ich am Zielkorridor pruefen?',
  'Wie verbessere ich diese Route?',
  'Erklaere mir die aktuelle Ansicht.',
]

function downsampleRoutePoints(trajectory: WaypointRouteResult['trajectory']) {
  const limit = 320
  if (trajectory.length <= limit) return trajectory.map((point) => point.positionKm)
  const stride = Math.ceil(trajectory.length / limit)
  const points = trajectory
    .filter((_, index) => index % stride === 0)
    .map((point) => point.positionKm)
  const lastPoint = trajectory.at(-1)?.positionKm
  if (lastPoint && points.at(-1) !== lastPoint) points.push(lastPoint)
  return points
}

function missionDateAfterDays(startDate: string, elapsedDays: number) {
  return new Date(new Date(`${startDate}T00:00:00Z`).getTime() + elapsedDays * 86_400_000).toISOString().slice(0, 10)
}

function project(position: THREE.Vector3, projection: OrbitalProjection): [number, number] {
  const [x, y, z] = [position.x, position.y, position.z]
  return [x, projection === 'top' ? -z : -y]
}

function pathFromPoints(points: THREE.Vector3[], projection: OrbitalProjection = 'top') {
  return points.map((point, index) => {
    const [x, y] = project(point, projection)
    return `${index === 0 ? 'M' : 'L'} ${x.toFixed(3)} ${y.toFixed(3)}`
  }).join(' ')
}

function linePath(start: PreviewPoint, end: PreviewPoint) {
  return `M ${start.x.toFixed(3)} ${start.y.toFixed(3)} L ${end.x.toFixed(3)} ${end.y.toFixed(3)}`
}

function routePassageRadius(targetId: string) {
  if (targetId === 'sun') return 0.95
  if (targetId === 'jupiter' || targetId === 'saturn') return 0.48
  return 0.34
}

function routeTurnCapacityDeg(targetId: string) {
  if (targetId === 'sun') return 150
  if (targetId === 'jupiter') return 82
  if (targetId === 'saturn') return 58
  if (targetId === 'venus' || targetId === 'earth') return 38
  if (targetId === 'mars') return 28
  return 18
}

function clamp(value: number, min: number, max: number) {
  return Math.min(max, Math.max(min, value))
}

function positiveAngleDeg(angleDeg: number) {
  return ((angleDeg % 360) + 360) % 360
}

function angleBetweenVectors(a: PreviewPoint, b: PreviewPoint) {
  const length = Math.hypot(a.x, a.y) * Math.hypot(b.x, b.y)
  if (length <= 0.000001) return 180
  const cosine = Math.max(-1, Math.min(1, (a.x * b.x + a.y * b.y) / length))
  return Math.acos(cosine) * 180 / Math.PI
}

function routeVector(from: PreviewPoint, to: PreviewPoint): PreviewPoint {
  return { x: to.x - from.x, y: to.y - from.y }
}

function dateFromTimestamp(timestampMs: number) {
  return new Date(timestampMs).toISOString().slice(0, 10)
}

function routeVerticalBias(section: RouteSectionDefinition) {
  const [, , corridorZ] = section.corridor.centerDirection
  return corridorZ
}

function routeSectionUsesVerticalCorridor(section: RouteSectionDefinition) {
  return (
    section.corridor.enabled
    && (section.corridor.mainProjection ?? 'side') === 'side'
    && Math.abs(routeVerticalBias(section)) > 0.18
  )
}

function vectorAngle(from: PreviewPoint, to: PreviewPoint) {
  return Math.atan2(to.y - from.y, to.x - from.x)
}

function polarPoint(center: PreviewPoint, radius: number, angle: number): PreviewPoint {
  return {
    x: center.x + Math.cos(angle) * radius,
    y: center.y + Math.sin(angle) * radius,
  }
}

function projectedCorridorPoint(
  section: RouteSectionDefinition,
  target: PreviewPoint,
  projection: OrbitalProjection,
) {
  if (!section.corridor.enabled) return target
  const direction = projectedPhysicsDirection(section.corridor.centerDirection, projection)
  return direction
    ? addPoint(target, scaleVector(direction, routePassageRadius(section.targetId)))
    : target
}

function projectedPassageCorridors(
  section: RouteSectionDefinition,
  target: PreviewPoint,
  projection: OrbitalProjection,
) {
  if (!section.corridor.enabled) return null
  const direction = projectedPhysicsDirection(section.corridor.centerDirection, projection)
  if (!direction) return null
  const radius = routePassageRadius(section.targetId)
  const entryAngle = Math.atan2(direction.y, direction.x)
  const passageAngle = section.passage.mode === 'full-orbit'
    ? 360
    : section.passage.mode === 'partial-orbit'
      ? section.passage.orbitAngleDeg
      : 0
  const directionSign = section.passage.orbitDirection === 'prograde' ? 1 : -1
  const exitAngle = entryAngle + directionSign * passageAngle * Math.PI / 180
  const halfAngleDeg = projection === 'top'
    ? section.corridor.horizontalHalfAngleDeg
    : section.corridor.verticalHalfAngleDeg
  const halfAngle = halfAngleDeg * Math.PI / 180
  return {
    entryPoint: pointOnCircle(target, radius, entryAngle),
    exitPoint: pointOnCircle(target, radius, exitAngle),
    entryArc: sampledArcPath(target, radius, entryAngle - halfAngle, entryAngle + halfAngle),
    exitArc: sampledArcPath(target, radius, exitAngle - halfAngle, exitAngle + halfAngle),
  }
}

function routePassagePath(
  section: RouteSectionDefinition,
  origin: PreviewPoint,
  target: PreviewPoint,
  nextTarget: PreviewPoint | null,
  nextSection: RouteSectionDefinition | null,
  projection: OrbitalProjection,
  approachCovered: boolean,
) {
  if (isInterstellarRouteObject(section.targetId)) return approachCovered ? '' : linePath(origin, target)
  const linkedTarget = nextTarget && nextSection
    ? projectedCorridorPoint(nextSection, nextTarget, projection)
    : nextTarget
  if (projection === 'side' && routeSectionUsesVerticalCorridor(section)) {
    const sign = routeVerticalBias(section) >= 0 ? -1 : 1
    const departure = linkedTarget ?? {
      x: target.x + (target.x - origin.x) / (Math.hypot(target.x - origin.x, target.y - origin.y) || 1) * 1.8,
      y: target.y + (target.y - origin.y) / (Math.hypot(target.x - origin.x, target.y - origin.y) || 1) * 1.8,
    }
    const radius = routePassageRadius(section.targetId)
    const travelsRight = departure.x > origin.x
    const entryOffset = travelsRight ? -radius : radius
    const exitOffset = -entryOffset
    const entry = {
      x: target.x + entryOffset,
      y: target.y,
    }
    const exit = {
      x: target.x + exitOffset,
      y: target.y,
    }
    const sweep = sign < 0
      ? (travelsRight ? 1 : 0)
      : (travelsRight ? 0 : 1)
    const start = approachCovered
      ? `M ${entry.x.toFixed(3)} ${entry.y.toFixed(3)}`
      : `M ${origin.x.toFixed(3)} ${origin.y.toFixed(3)} L ${entry.x.toFixed(3)} ${entry.y.toFixed(3)}`
    return `${start} A ${radius.toFixed(3)} ${radius.toFixed(3)} 0 0 ${sweep} ${exit.x.toFixed(3)} ${exit.y.toFixed(3)} L ${departure.x.toFixed(3)} ${departure.y.toFixed(3)}`
  }
  if (projection === 'side') {
    if (approachCovered) return linkedTarget ? linePath(target, linkedTarget) : ''
    return linkedTarget
      ? `M ${origin.x.toFixed(3)} ${origin.y.toFixed(3)} L ${target.x.toFixed(3)} ${target.y.toFixed(3)} L ${linkedTarget.x.toFixed(3)} ${linkedTarget.y.toFixed(3)}`
      : linePath(origin, target)
  }
  const passage = section.passage
  const linkedExit = Boolean(nextTarget)
  const configuredEntryDirection = section.corridor.enabled
    ? projectedPhysicsDirection(section.corridor.centerDirection, projection)
    : null
  const radius = routePassageRadius(section.targetId)
  if (passage.mode === 'direct') {
    const fallbackEntryDirection = normalizePreviewVector({
      x: origin.x - target.x,
      y: origin.y - target.y,
    })
    const entryDirection = configuredEntryDirection ?? fallbackEntryDirection
    const entry = addPoint(target, scaleVector(entryDirection, radius))
    const start = approachCovered
      ? `M ${entry.x.toFixed(3)} ${entry.y.toFixed(3)}`
      : `M ${origin.x.toFixed(3)} ${origin.y.toFixed(3)} L ${entry.x.toFixed(3)} ${entry.y.toFixed(3)}`
    return linkedTarget
      ? `${start} L ${linkedTarget.x.toFixed(3)} ${linkedTarget.y.toFixed(3)}`
      : start
  }

  const directionSign = passage.orbitDirection === 'prograde' ? 1 : -1
  const originAngle = vectorAngle(target, origin)
  const inboundDistance = Math.hypot(origin.x - target.x, origin.y - target.y)
  const entryTangentOffset = Math.acos(clamp(radius / Math.max(radius, inboundDistance), -1, 1))
  const entryAngle = configuredEntryDirection
    ? Math.atan2(configuredEntryDirection.y, configuredEntryDirection.x)
    : originAngle + directionSign * entryTangentOffset
  const requestedAngle = passage.mode === 'full-orbit'
    ? Math.PI * 2
    : passage.mode === 'partial-orbit'
      ? clamp(passage.orbitAngleDeg, 1, MAX_PARTIAL_ORBIT_ANGLE_DEG) * Math.PI / 180
      : Math.PI / 2
  let selectedAngle = requestedAngle
  if (linkedTarget) {
    const outboundAngle = vectorAngle(target, linkedTarget)
    const outboundDistance = Math.hypot(linkedTarget.x - target.x, linkedTarget.y - target.y)
    const tangentOffset = Math.acos(clamp(radius / Math.max(radius, outboundDistance), -1, 1))
    const tangentExitAngle = outboundAngle - directionSign * tangentOffset
    selectedAngle = positiveAngleDeg(
      directionSign * (tangentExitAngle - entryAngle) * 180 / Math.PI,
    ) * Math.PI / 180
    while (selectedAngle + 0.0001 < requestedAngle) selectedAngle += Math.PI * 2
  }
  const exitAngle = entryAngle + directionSign * selectedAngle
  const entry = polarPoint(target, radius, entryAngle)
  const exit = polarPoint(target, radius, exitAngle)
  const arcSpan = Math.abs(selectedAngle)
  const largeArc = arcSpan > Math.PI ? 1 : 0
  const sweep = directionSign > 0 ? 1 : 0
  const exitEnd = linkedTarget ?? target
  const approachPath = approachCovered
    ? `M ${target.x.toFixed(3)} ${target.y.toFixed(3)} L ${entry.x.toFixed(3)} ${entry.y.toFixed(3)}`
    : `M ${origin.x.toFixed(3)} ${origin.y.toFixed(3)} L ${entry.x.toFixed(3)} ${entry.y.toFixed(3)}`

  if (selectedAngle >= Math.PI * 2 - 0.0001) {
    return [
      approachPath,
      sampledArcPath(target, radius, entryAngle, exitAngle),
      linkedExit ? `L ${exitEnd.x.toFixed(3)} ${exitEnd.y.toFixed(3)}` : '',
    ].filter(Boolean).join(' ')
  }

  return [
    approachPath,
    `A ${radius.toFixed(3)} ${radius.toFixed(3)} 0 ${largeArc} ${sweep} ${exit.x.toFixed(3)} ${exit.y.toFixed(3)}`,
    linkedExit ? `L ${exitEnd.x.toFixed(3)} ${exitEnd.y.toFixed(3)}` : '',
  ].filter(Boolean).join(' ')
}

function routeScenePosition(positionKm: [number, number, number]) {
  return toScenePosition(
    new THREE.Vector3(
      positionKm[0] / 149_597_870.7,
      positionKm[2] / 149_597_870.7,
      positionKm[1] / 149_597_870.7,
    ),
  )
}

function routePlotPoint(
  objectId: string,
  projection: OrbitalProjection,
  orbitGeometry: Array<{ planet: SolarSystemData['planets'][number]; position: THREE.Vector3 }>,
  moonCatalogue: MoonCatalogue | null,
): PreviewPoint | null {
  if (objectId === 'sun') return { x: 0, y: 0 }
  const planetPosition = orbitGeometry.find(({ planet }) => planet.id === objectId)?.position
  if (planetPosition) {
    const [x, y] = project(planetPosition, projection)
    return { x, y }
  }
  const moon = moonCatalogue?.moons.find((item) => item.id === objectId)
  if (moon) {
    const parentPosition = orbitGeometry.find(({ planet }) => planet.id === moon.parentId)?.position
    if (parentPosition) {
      const [x, y] = project(parentPosition, projection)
      return { x, y }
    }
  }
  if (isInterstellarRouteObject(objectId)) {
    const direction = interstellarPreviewDirection(objectId, projection)
    if (direction) return scaleVector(direction, INTERSTELLAR_PLOT_DISTANCE)
  }
  return null
}

function plotRayEndpoint(origin: PreviewPoint, direction: PreviewPoint, projection: OrbitalProjection) {
  const horizontalLimit = EXTENT - 1.25
  const verticalLimit = (projection === 'top' ? EXTENT : SIDE_HALF_HEIGHT) - 1.25
  const intersections = [
    direction.x > 0.0001 ? (horizontalLimit - origin.x) / direction.x : Number.POSITIVE_INFINITY,
    direction.x < -0.0001 ? (-horizontalLimit - origin.x) / direction.x : Number.POSITIVE_INFINITY,
    direction.y > 0.0001 ? (verticalLimit - origin.y) / direction.y : Number.POSITIVE_INFINITY,
    direction.y < -0.0001 ? (-verticalLimit - origin.y) / direction.y : Number.POSITIVE_INFINITY,
  ].filter((distance) => distance > 0)
  const distance = Math.min(...intersections)
  return addPoint(origin, scaleVector(direction, Number.isFinite(distance) ? distance : 0))
}

function projectedPhysicsDirection(
  direction: [number, number, number] | null | undefined,
  projection: OrbitalProjection,
) {
  if (!direction) return null
  const projected = projectPhysicsDirectionToView(direction, projection)
  return projected ? { x: projected[0], y: projected[1] } : null
}

export function TwoDView({
  projectId,
  routeSections: rawRouteSections,
  onRouteSectionsChange,
  activeRouteSectionId,
  onActiveRouteSectionChange,
  plannedMissionDate,
  plannedRoute,
  onApplyPlannedSolution,
  missionConfig,
  solverDialogOnly = false,
  onSolverDialogClose,
  displayOnly = false,
}: TwoDViewProps) {
  const routeSections = useMemo(
    () => normalizeRouteSections(rawRouteSections),
    [rawRouteSections],
  )
  const [data, setData] = useState<SolarSystemData | null>(null)
  const [moonCatalogue, setMoonCatalogue] = useState<MoonCatalogue | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [projection, setProjection] = useState<Projection>('corridor')
  const [selectedPlanetId, setSelectedPlanetId] = useState('earth')
  const [previewSectionId, setPreviewSectionId] = useState<string | null>(null)
  const [orbitZoom, setOrbitZoom] = useState(1)
  const [constellationSearchStatus, setConstellationSearchStatus] = useState('')
  const [constellationSearchRunning, setConstellationSearchRunning] = useState(false)
  const [retrospectiveSearchEnabled, setRetrospectiveSearchEnabled] = useState(false)
  const [constellationResults, setConstellationResults] = useState<ConstellationSearchResult[]>([])
  const [selectedConstellationResultId, setSelectedConstellationResultId] = useState('')
  const [routeCalculationTrace, setRouteCalculationTrace] = useState<RouteCalculationTrace | null>(null)
  const [routeCalculationDialogOpen, setRouteCalculationDialogOpen] = useState(false)
  const [calculationRunHistory, setCalculationRunHistory] = useState<RouteCalculationRunSummary[]>([])
  const [calculationHistoryLoading, setCalculationHistoryLoading] = useState(false)
  const solverAutoStartedRef = useRef(false)
  const searchCancelledRef = useRef(false)
  const [aiChatInput, setAiChatInput] = useState('')
  const [aiChatLoading, setAiChatLoading] = useState(false)
  const [aiChatError, setAiChatError] = useState('')
  const [aiRecording, setAiRecording] = useState(false)
  const [aiAudioStatus, setAiAudioStatus] = useState('')
  const [aiSpeechMessageId, setAiSpeechMessageId] = useState<string | null>(null)
  const [aiPlausibilityReport, setAiPlausibilityReport] = useState<AiPlausibilityReport | null>(null)
  const [aiPlausibilityLoading, setAiPlausibilityLoading] = useState(false)
  const [aiPlausibilityError, setAiPlausibilityError] = useState('')
  const [aiCalculationSuggestion, setAiCalculationSuggestion] = useState<AiCalculationSuggestion | null>(null)
  const [aiCalculationLoading, setAiCalculationLoading] = useState(false)
  const [aiCalculationError, setAiCalculationError] = useState('')
  const [aiCalculationBiasActive, setAiCalculationBiasActive] = useState(false)
  const [aiChatMessages, setAiChatMessages] = useState<AiChatMessage[]>([
    {
      id: 'assistant-welcome',
      role: 'assistant',
      text: 'Ich bin die Interaktions-KI fuer diese 2D-Planung. Konkrete Missionswerte nenne ich nur mit Bezug auf einen vorhandenen Solver-Lauf.',
    },
  ])
  const searchRunningRef = useRef(false)
  const orbitPlotRef = useRef<HTMLDivElement>(null)
  const aiRecorderRef = useRef<MediaRecorder | null>(null)
  const aiRecordingChunksRef = useRef<Blob[]>([])
  const aiRecordingStreamRef = useRef<MediaStream | null>(null)
  const aiPlaybackRef = useRef<HTMLAudioElement | null>(null)
  const aiPlaybackUrlRef = useRef('')
  const aiPlausibilityRunRef = useRef('')
  const previousOrbitZoomRef = useRef(orbitZoom)
  const previousProjectionRef = useRef(projection)
  const orbitPanRef = useRef({
    active: false,
    moved: false,
    pointerId: -1,
    startX: 0,
    startY: 0,
    scrollLeft: 0,
    scrollTop: 0,
  })
  const todayTimestampMs = useMemo(() => Date.now(), [])

  useEffect(() => {
    const plot = orbitPlotRef.current
    const previousZoom = previousOrbitZoomRef.current
    const projectionChanged = previousProjectionRef.current !== projection
    previousOrbitZoomRef.current = orbitZoom
    previousProjectionRef.current = projection
    if (!plot) return

    if (projectionChanged) {
      plot.scrollLeft = (plot.scrollWidth - plot.clientWidth) / 2
      plot.scrollTop = (plot.scrollHeight - plot.clientHeight) / 2
      return
    }

    const centerX = (plot.scrollLeft + plot.clientWidth / 2) / previousZoom
    const centerY = (plot.scrollTop + plot.clientHeight / 2) / previousZoom
    plot.scrollLeft = centerX * orbitZoom - plot.clientWidth / 2
    plot.scrollTop = centerY * orbitZoom - plot.clientHeight / 2
  }, [orbitZoom, projection])

  useEffect(() => {
    const controller = new AbortController()
    Promise.all([
      fetch('/api/solar-system', { signal: controller.signal }),
      fetch('/moons.json', { signal: controller.signal }),
    ])
      .then(async ([solarResponse, moonResponse]) => {
        if (!solarResponse.ok || !moonResponse.ok) {
          throw new Error(`Solardaten konnten nicht geladen werden (${solarResponse.status}/${moonResponse.status}).`)
        }
        return Promise.all([
          solarResponse.json() as Promise<SolarSystemData>,
          moonResponse.json() as Promise<MoonCatalogue>,
        ])
      })
      .then(([solarData, moons]) => {
        setData(solarData)
        setMoonCatalogue(sanitizeMoonCatalogue(moons))
      })
      .catch((reason: unknown) => {
        if (!controller.signal.aborted) setError(reason instanceof Error ? reason.message : 'Solardaten konnten nicht geladen werden.')
      })
    return () => controller.abort()
  }, [])

  useEffect(() => {
    const controller = new AbortController()
    const query = new URLSearchParams({ limit: '25' })
    if (projectId) query.set('projectId', projectId)
    setCalculationHistoryLoading(true)
    fetch(`/api/calculations/runs?${query}`, {
      headers: activityRequestHeaders(),
      signal: controller.signal,
    })
      .then(async (response) => {
        const payload = await response.json() as {
          runs?: RouteCalculationRunSummary[]
          error?: string
        }
        if (!response.ok) throw new Error(payload.error ?? `HTTP ${response.status}`)
        return payload.runs ?? []
      })
      .then(async (runs) => {
        if (controller.signal.aborted) return
        setCalculationRunHistory(runs)
        if (runs.length === 0) {
          setRouteCalculationTrace(null)
          return
        }
        const response = await fetch(
          `/api/calculations/runs/${encodeURIComponent(runs[0].runId)}`,
          { headers: activityRequestHeaders(), signal: controller.signal },
        )
        const payload = await response.json() as RouteCalculationTrace & { error?: string }
        if (!response.ok) throw new Error(payload.error ?? `HTTP ${response.status}`)
        if (!controller.signal.aborted) setRouteCalculationTrace(payload)
      })
      .catch((reason: unknown) => {
        if (!controller.signal.aborted) {
          setConstellationSearchStatus(
            reason instanceof Error
              ? `Berechnungshistorie: ${reason.message}`
              : 'Berechnungshistorie konnte nicht geladen werden.',
          )
        }
      })
      .finally(() => {
        if (!controller.signal.aborted) setCalculationHistoryLoading(false)
      })
    return () => controller.abort()
  }, [projectId])

  useEffect(() => () => {
    searchCancelledRef.current = true
    aiRecordingStreamRef.current?.getTracks().forEach((track) => track.stop())
    aiPlaybackRef.current?.pause()
    if (aiPlaybackUrlRef.current) URL.revokeObjectURL(aiPlaybackUrlRef.current)
  }, [])

  const activeDate = plannedRoute?.startDate ?? plannedMissionDate ?? new Date(todayTimestampMs).toISOString().slice(0, 10)
  const timestampMs = useMemo(
    () => new Date(`${activeDate}T00:00:00Z`).getTime(),
    [activeDate],
  )
  const epochLabel = `${plannedRoute || plannedMissionDate ? 'Missionsstart' : 'Heute'} · ${new Date(timestampMs).toLocaleDateString('de-DE', { timeZone: 'UTC' })}`
  const orbitGeometry = useMemo(
    () => data?.planets.map((planet) => ({
      planet,
      orbit: createOrbitPoints(planet),
      position: planetPositionAt(planet, timestampMs),
    })) ?? [],
    [data, timestampMs],
  )
  const selectedPlanet = data?.planets.find((planet) => planet.id === selectedPlanetId) ?? data?.planets[0] ?? null
  const selectedMoons = useMemo(
    () => selectedPlanet && moonCatalogue
      ? moonCatalogue.moons.filter((moon) => moon.parentId === selectedPlanet.id)
      : [],
    [moonCatalogue, selectedPlanet],
  )
  const activeRouteSection = routeSections.find((section) => section.id === activeRouteSectionId) ?? routeSections[0]
  const calculatedActiveRouteSection = plannedRoute?.routeSections?.find(
    (section) => section.id === activeRouteSection?.id,
  ) ?? null
  const activeRouteSectionIndex = activeRouteSection
    ? routeSections.findIndex((section) => section.id === activeRouteSection.id)
    : -1
  const linkedNextRouteSection = activeRouteSectionIndex >= 0
    && routeSections[activeRouteSectionIndex + 1]?.originId === activeRouteSection?.targetId
    ? routeSections[activeRouteSectionIndex + 1]
    : null
  const calculatedNextRouteSection = linkedNextRouteSection
    ? plannedRoute?.routeSections?.find((section) => section.id === linkedNextRouteSection.id) ?? null
    : null
  const entryFlightDirection = useMemo<Vector3Tuple | null>(() => {
    if (!data || !activeRouteSection) return null
    const targetTimestamp = timestampMs
      + (calculatedActiveRouteSection?.entryDay ?? 0) * 86_400_000
    return directionBetweenRouteObjects(
      activeRouteSection.originId,
      activeRouteSection.targetId,
      timestampMs,
      targetTimestamp,
      data.planets,
      moonCatalogue?.moons ?? [],
    )
  }, [activeRouteSection, calculatedActiveRouteSection?.entryDay, data, moonCatalogue, timestampMs])
  const exitFlightDirection = useMemo<Vector3Tuple | null>(() => {
    const solverDirection = calculatedActiveRouteSection?.corridor.exitAngleSelection?.desiredExitDirection
      ?? calculatedNextRouteSection?.desiredDepartureDirection
      ?? calculatedNextRouteSection?.predictedOutgoingDirection
    if (solverDirection) return solverDirection
    if (!data || !activeRouteSection || !linkedNextRouteSection) return null
    const originTimestamp = timestampMs
      + (calculatedActiveRouteSection?.exitDay ?? calculatedActiveRouteSection?.entryDay ?? 0) * 86_400_000
    const targetTimestamp = timestampMs
      + (calculatedNextRouteSection?.entryDay ?? calculatedActiveRouteSection?.exitDay ?? 0) * 86_400_000
    return directionBetweenRouteObjects(
      activeRouteSection.targetId,
      linkedNextRouteSection.targetId,
      originTimestamp,
      targetTimestamp,
      data.planets,
      moonCatalogue?.moons ?? [],
    )
  }, [
    activeRouteSection,
    calculatedActiveRouteSection,
    calculatedNextRouteSection,
    data,
    linkedNextRouteSection,
    moonCatalogue,
    timestampMs,
  ])
  const sunToActiveTargetDirection = useMemo<Vector3Tuple | null>(() => {
    if (!data || !activeRouteSection || activeRouteSection.targetId === 'sun') return null
    if (isInterstellarRouteObject(activeRouteSection.targetId)) return null
    const moon = moonCatalogue?.moons.find((item) => item.id === activeRouteSection.targetId)
    const targetPlanetId = moon?.parentId ?? activeRouteSection.targetId
    const targetPlanet = data.planets.find((planet) => planet.id === targetPlanetId)
    if (!targetPlanet) return null
    const encounterTimestamp = timestampMs
      + (calculatedActiveRouteSection?.entryDay ?? 0) * 86_400_000
    const scenePosition = planetPositionAt(targetPlanet, encounterTimestamp)
    const length = scenePosition.length()
    if (length <= 1e-9) return null
    return [
      scenePosition.x / length,
      scenePosition.z / length,
      scenePosition.y / length,
    ]
  }, [activeRouteSection, calculatedActiveRouteSection?.entryDay, data, moonCatalogue, timestampMs])
  const projectionLabel = projection === 'corridor'
    ? 'Zielkorridor'
    : projection === 'side'
      ? 'Kantenansicht'
      : 'Draufsicht'
  const missionStateForAi = () => ({
    schemaVersion: '1.0',
    startDate: activeDate,
    originId: routeSections[0]?.originId ?? activeRouteSection?.originId ?? 'earth',
    targetId: routeSections[routeSections.length - 1]?.targetId ?? activeRouteSection?.targetId ?? 'earth',
    waypointIds: routeSections.map((section) => section.targetId),
    routeSections,
    constraints: {
      maxDeltaVKmS: routeSections.reduce(
        (sum, section) => sum + section.deltaVMinusKmS + section.deltaVPlusKmS,
        0,
      ),
      maxDurationDays: missionConfig ? missionConfig.missionYears * 365.25 : null,
      minimumConfidencePct: null,
    },
    solverRunId: plannedRoute?.audit?.runId ?? null,
  })
  const solverResultForAi = () => {
    if (!plannedRoute?.audit?.runId) return null
    const solverValid = Boolean(
      plannedRoute.summary.feasibleWithConfiguredBurn
      && plannedRoute.validation?.collisionFree !== false
      && plannedRoute.highFidelityNBody?.collision !== true,
    )
    const warnings = [
      ...(plannedRoute.warnings ?? []),
      ...(plannedRoute.summary.warnings ?? []),
    ]
    return {
      schemaVersion: '1.0',
      runId: plannedRoute.audit.runId,
      solverType: 'segmented-route',
      status: solverValid ? 'success' : 'best-effort',
      missionStateRef: null,
      result: {
        startDate: plannedRoute.startDate,
        totalFlightDays: plannedRoute.totalFlightDays,
        summary: plannedRoute.summary,
        waypoint: plannedRoute.waypoint ? {
          id: plannedRoute.waypoint.id,
          encounterDay: plannedRoute.waypoint.encounterDay,
          encounterDate: missionDateAfterDays(plannedRoute.startDate, plannedRoute.waypoint.encounterDay),
        } : null,
        routeSections: plannedRoute.routeSections?.map((section) => ({
          id: section.id,
          originId: section.originId,
          targetId: section.targetId,
          entryInsideCorridor: section.corridor.entryInsideCorridor,
          requiredTransitionDeltaVKmS: section.requiredTransitionDeltaVKmS,
          corridorInsertionDeltaVKmS: section.corridorInsertionDeltaVKmS,
        })) ?? [],
      },
      validation: {
        solverValid,
        nBodyValid: plannedRoute.highFidelityNBody
          ? plannedRoute.highFidelityNBody.converged && plannedRoute.highFidelityNBody.collision !== true
          : null,
        errors: solverValid ? [] : ['Der Solver hat keine Flugfreigabe erteilt.'],
        warnings,
      },
    }
  }
  const uiStateForPlausibility = () => {
    if (!plannedRoute) return {}
    const solverValid = Boolean(
      plannedRoute.summary.feasibleWithConfiguredBurn
      && plannedRoute.validation?.collisionFree !== false
      && plannedRoute.highFidelityNBody?.collision !== true,
    )
    return {
      displayedSolverRunId: plannedRoute.audit?.runId ?? null,
      projection,
      activeRouteSectionId,
      routeSectionIds: routeSections.map((section) => section.id),
      displayedFlightReady: solverValid,
      displayedStartDate: plannedRoute.startDate,
      displayedOptimizedStartDate: plannedRoute.startDate,
      displayedTotalFlightDays: plannedRoute.totalFlightDays,
      displayedEncounterDay: plannedRoute.waypoint?.encounterDay ?? null,
      displayedEncounterDate: plannedRoute.waypoint
        ? missionDateAfterDays(plannedRoute.startDate, plannedRoute.waypoint.encounterDay)
        : null,
      displayedRouteTargetId: plannedRoute.routeSections?.at(-1)?.targetId ?? null,
      nBodyVisible: Boolean(plannedRoute.highFidelityNBody),
      routeSectionCount: routeSections.length,
    }
  }
  const runPlausibilityCheck = async () => {
    const solverResult = solverResultForAi()
    if (!solverResult || aiPlausibilityLoading) return
    setAiPlausibilityLoading(true)
    setAiPlausibilityError('')
    try {
      const response = await fetch('/api/ai/plausibility-check', {
        method: 'POST',
        headers: activityRequestHeaders({ 'Content-Type': 'application/json' }),
        body: JSON.stringify({
          missionState: missionStateForAi(),
          solverResult,
          uiState: uiStateForPlausibility(),
        }),
      })
      const payload = await response.json() as AiPlausibilityReport | { error?: string }
      if (!response.ok || !('status' in payload)) {
        throw new Error(payload.error || `Plausibilitaetspruefung antwortet mit HTTP ${response.status}.`)
      }
      setAiPlausibilityReport(payload)
    } catch (reason) {
      setAiPlausibilityError(reason instanceof Error ? reason.message : 'Plausibilitaetspruefung fehlgeschlagen.')
    } finally {
      setAiPlausibilityLoading(false)
    }
  }
  const recentSolverHistoryForAi = () => constellationResults.slice(0, 12).map((result) => ({
    id: result.id,
    date: result.date,
    quality: result.quality,
    flightReady: result.flightReady,
    deltaVDeficitKmS: Math.max(
      0,
      result.requiredInjectionDeltaVKmS
        + result.targetCorrectionDeltaVKmS
        - result.availableInjectionDeltaVKmS,
    ) + result.corridorInsertionDeficitKmS,
    targetAlignmentDeg: result.targetAlignmentDeg,
    corridorSatisfied: result.corridorSatisfied,
    collisionFree: result.collisionFree,
  }))
  const requestCalculationSuggestion = async () => {
    if (aiCalculationLoading || routeSections.length === 0) return
    setAiCalculationLoading(true)
    setAiCalculationError('')
    try {
      const response = await fetch('/api/ai/calculation-suggest', {
        method: 'POST',
        headers: activityRequestHeaders({ 'Content-Type': 'application/json' }),
        body: JSON.stringify({
          missionState: missionStateForAi(),
          solverResult: solverResultForAi(),
          uiState: uiStateForPlausibility(),
          recentSolverHistory: recentSolverHistoryForAi(),
        }),
      })
      const payload = await response.json() as AiCalculationSuggestion | { error?: string }
      if (!response.ok || !('proposal' in payload)) {
        throw new Error(payload.error || `Berechnungs-KI antwortet mit HTTP ${response.status}.`)
      }
      setAiCalculationSuggestion(payload)
      setAiCalculationBiasActive(false)
    } catch (reason) {
      setAiCalculationError(reason instanceof Error ? reason.message : 'Berechnungs-KI konnte keinen Suchraum vorschlagen.')
    } finally {
      setAiCalculationLoading(false)
    }
  }

  useEffect(() => {
    const runId = plannedRoute?.audit?.runId ?? ''
    if (!runId || aiPlausibilityRunRef.current === runId) return
    aiPlausibilityRunRef.current = runId
    void runPlausibilityCheck()
  }, [plannedRoute?.audit?.runId])

  const sendAiChatMessage = async (message = aiChatInput) => {
    const trimmedMessage = message.trim()
    if (!trimmedMessage || aiChatLoading) return
    const timestamp = Date.now().toString(36)
    const userMessage: AiChatMessage = { id: `user-${timestamp}`, role: 'user', text: trimmedMessage }
    const history = aiChatMessages
      .filter((item) => item.id !== 'assistant-welcome')
      .map((item) => ({ role: item.role, content: item.text }))
      .slice(-12)
    setAiChatMessages((current) => [...current, userMessage].slice(-12))
    setAiChatInput('')
    setAiChatError('')
    setAiChatLoading(true)
    try {
      const response = await fetch('/api/ai/mission-chat', {
        method: 'POST',
        headers: activityRequestHeaders({ 'Content-Type': 'application/json' }),
        body: JSON.stringify({
          message: trimmedMessage,
          history,
          missionState: missionStateForAi(),
          solverResult: solverResultForAi(),
          viewState: { projection, activeRouteSectionId },
        }),
      })
      const payload = await response.json() as AiChatResponse | { error?: string }
      if (!response.ok || !('reply' in payload)) {
        throw new Error(payload.error || `KI-Endpunkt antwortet mit HTTP ${response.status}.`)
      }
      const assistantMessage: AiChatMessage = {
        id: `assistant-${timestamp}`,
        role: 'assistant',
        text: payload.reply,
        basedOnSolverRunIds: payload.basedOnSolverRunIds,
        proposedActions: payload.proposedActions,
        auditRunId: payload.auditRunId,
      }
      setAiChatMessages((current) => [...current, assistantMessage].slice(-12))
    } catch (reason) {
      setAiChatError(reason instanceof Error ? reason.message : 'Die Interaktions-KI konnte nicht antworten.')
    } finally {
      setAiChatLoading(false)
    }
  }
  const stopAiRecording = () => {
    const recorder = aiRecorderRef.current
    if (recorder && recorder.state !== 'inactive') recorder.stop()
  }
  const startAiRecording = async () => {
    if (aiRecording || aiChatLoading) return
    if (!navigator.mediaDevices?.getUserMedia || typeof MediaRecorder === 'undefined') {
      setAiChatError('Audioaufnahme wird von diesem Browser nicht unterstuetzt.')
      return
    }
    setAiChatError('')
    setAiAudioStatus('')
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true })
      const mimeType = MediaRecorder.isTypeSupported('audio/webm;codecs=opus')
        ? 'audio/webm;codecs=opus'
        : MediaRecorder.isTypeSupported('audio/webm')
          ? 'audio/webm'
          : ''
      const recorder = new MediaRecorder(stream, mimeType ? { mimeType } : undefined)
      aiRecorderRef.current = recorder
      aiRecordingStreamRef.current = stream
      aiRecordingChunksRef.current = []
      recorder.addEventListener('dataavailable', (event) => {
        if (event.data.size > 0) aiRecordingChunksRef.current.push(event.data)
      })
      recorder.addEventListener('stop', () => {
        const recordingMimeType = recorder.mimeType || mimeType || 'audio/webm'
        const audioBlob = new Blob(aiRecordingChunksRef.current, { type: recordingMimeType })
        stream.getTracks().forEach((track) => track.stop())
        aiRecorderRef.current = null
        aiRecordingStreamRef.current = null
        setAiRecording(false)
        void transcribeAiRecording(audioBlob)
      })
      recorder.start()
      setAiRecording(true)
      setAiAudioStatus('Aufnahme laeuft...')
    } catch (reason) {
      aiRecordingStreamRef.current?.getTracks().forEach((track) => track.stop())
      aiRecorderRef.current = null
      aiRecordingStreamRef.current = null
      setAiRecording(false)
      setAiChatError(reason instanceof Error ? reason.message : 'Mikrofon konnte nicht gestartet werden.')
    }
  }
  const transcribeAiRecording = async (audioBlob: Blob) => {
    if (audioBlob.size === 0) {
      setAiChatError('Die Audioaufnahme ist leer.')
      setAiAudioStatus('')
      return
    }
    setAiAudioStatus('Sprache wird transkribiert...')
    setAiChatError('')
    try {
      const formData = new FormData()
      formData.append('audio', audioBlob, 'mission-chat.webm')
      const response = await fetch('/api/ai/transcribe', {
        method: 'POST',
        headers: activityRequestHeaders(),
        body: formData,
      })
      const payload = await response.json() as { transcript?: string; error?: string }
      if (!response.ok || !payload.transcript) {
        throw new Error(payload.error || `Transkription antwortet mit HTTP ${response.status}.`)
      }
      setAiChatInput(payload.transcript)
      setAiAudioStatus('Transkript eingefuegt. Pruefen und senden.')
    } catch (reason) {
      setAiChatError(reason instanceof Error ? reason.message : 'Sprache konnte nicht transkribiert werden.')
      setAiAudioStatus('')
    }
  }
  const speakAiMessage = async (message: AiChatMessage) => {
    if (aiSpeechMessageId || !message.text.trim()) return
    setAiSpeechMessageId(message.id)
    setAiAudioStatus('Antwort wird vorgelesen...')
    setAiChatError('')
    try {
      const response = await fetch('/api/ai/speech', {
        method: 'POST',
        headers: activityRequestHeaders({ 'Content-Type': 'application/json' }),
        body: JSON.stringify({ text: message.text }),
      })
      if (!response.ok) {
        const payload = await response.json().catch(() => ({ error: '' })) as { error?: string }
        throw new Error(payload.error || `Sprachausgabe antwortet mit HTTP ${response.status}.`)
      }
      const audioBlob = await response.blob()
      aiPlaybackRef.current?.pause()
      if (aiPlaybackUrlRef.current) URL.revokeObjectURL(aiPlaybackUrlRef.current)
      const audioUrl = URL.createObjectURL(audioBlob)
      const player = new Audio(audioUrl)
      aiPlaybackUrlRef.current = audioUrl
      aiPlaybackRef.current = player
      player.addEventListener('ended', () => setAiAudioStatus(''))
      await player.play()
    } catch (reason) {
      setAiChatError(reason instanceof Error ? reason.message : 'Sprachausgabe konnte nicht gestartet werden.')
      setAiAudioStatus('')
    } finally {
      setAiSpeechMessageId(null)
    }
  }
  const applyAiAction = (action: AiProposedAction) => {
    if (action.type === 'focus-route-section' && action.sectionId) {
      onActiveRouteSectionChange(action.sectionId)
      return
    }
    if (action.type === 'set-projection' && action.projection) {
      setProjection(action.projection)
      return
    }
    if (action.type === 'run-route-solver') void findBestConstellation()
  }
  const aiActionLabel = (action: AiProposedAction) => {
    if (action.type === 'focus-route-section') return 'Routenabschnitt fokussieren'
    if (action.type === 'set-projection') return `${action.projection} öffnen`
    return 'Solver-Suche starten'
  }
  const previewSection = routeSections.find((section) => section.id === previewSectionId) ?? null
  const previewSectionIndex = previewSection ? routeSections.findIndex((section) => section.id === previewSection.id) : -1
  const previewPreviousSection = previewSectionIndex > 0 ? routeSections[previewSectionIndex - 1] ?? null : null
  const previewNextSection = previewSectionIndex >= 0 ? routeSections[previewSectionIndex + 1] ?? null : null
  const orbitalProjection: OrbitalProjection = projection === 'top' ? 'top' : 'side'
  const plannedRoutePoints = useMemo(
    () => plannedRoute?.trajectory.map((point) => routeScenePosition(point.positionKm)) ?? [],
    [plannedRoute],
  )
  const solarPassagePlot = useMemo(() => {
    const passage = plannedRoute?.solarPassage
    if (!passage) return null
    const entryPoint = plannedRoutePoints[passage.entryIndex]
    const periapsisPoint = plannedRoutePoints[passage.periapsisIndex]
    const exitPoint = plannedRoutePoints[passage.exitIndex]
    if (!entryPoint || !periapsisPoint || !exitPoint) return null
    const [entryX, entryY] = project(entryPoint, orbitalProjection)
    const [periapsisX, periapsisY] = project(periapsisPoint, orbitalProjection)
    const [exitX, exitY] = project(exitPoint, orbitalProjection)
    const entry = { x: entryX, y: entryY }
    const exit = { x: exitX, y: exitY }
    const radius = Math.max(0.01, Math.hypot(entry.x, entry.y))
    const entryAngle = Math.atan2(entry.y, entry.x)
    const exitAngle = Math.atan2(exit.y, exit.x)
    const corridorHalfAngle = 8 * Math.PI / 180
    return {
      entry,
      periapsis: { x: periapsisX, y: periapsisY },
      exit,
      entryCorridorPath: sampledArcPath(
        { x: 0, y: 0 },
        radius,
        entryAngle - corridorHalfAngle,
        entryAngle + corridorHalfAngle,
      ),
      exitCorridorPath: sampledArcPath(
        { x: 0, y: 0 },
        Math.max(0.01, Math.hypot(exit.x, exit.y)),
        exitAngle - corridorHalfAngle,
        exitAngle + corridorHalfAngle,
      ),
      entryLabel: {
        x: entry.x + (entry.x >= 0 ? .38 : -.38),
        y: entry.y + (entry.y >= 0 ? .48 : -.32),
        anchor: (entry.x >= 0 ? 'start' : 'end') as 'start' | 'end',
      },
      exitLabel: {
        x: exit.x + (exit.x >= 0 ? .38 : -.38),
        y: exit.y + (exit.y >= 0 ? .48 : -.32),
        anchor: (exit.x >= 0 ? 'start' : 'end') as 'start' | 'end',
      },
      outbound: passage.outboundAfterPeriapsis,
    }
  }, [orbitalProjection, plannedRoute, plannedRoutePoints])
  const plannedRouteFlightReady = Boolean(
    plannedRoute?.summary.feasibleWithConfiguredBurn
    && plannedRoute.routeSections?.every((section) => section.corridor.entryInsideCorridor)
    && plannedRoute.validation?.collisionFree !== false
    && plannedRoute.highFidelityNBody?.collision !== true,
  )
  const plannedRoutePlausibilitySafe = Boolean(
    plannedRouteFlightReady
    && plannedRoute?.audit?.runId
    && aiPlausibilityReport?.solverRunId === plannedRoute.audit.runId
    && aiPlausibilityReport.displaySafe
    && aiPlausibilityReport.status === 'pass',
  )
  const routeSketchSegments = useMemo(() => {
    if (!data || routeSections.length === 0) return []
    return routeSections.map((section, index) => {
      if (isInterstellarRouteObject(section.targetId)) return null
      const origin = routePlotPoint(section.originId, orbitalProjection, orbitGeometry, moonCatalogue)
      const target = routePlotPoint(section.targetId, orbitalProjection, orbitGeometry, moonCatalogue)
      if (!origin || !target) return null
      const nextSection = routeSections[index + 1]
      const nextSectionOwnsVerticalApproach = (
        orbitalProjection === 'side'
        && nextSection
        && routeSectionUsesVerticalCorridor(nextSection)
      )
      const nextTarget = (
        nextSection?.originId === section.targetId
        && !nextSectionOwnsVerticalApproach
        && !isInterstellarRouteObject(nextSection.targetId)
      )
        ? routePlotPoint(nextSection.targetId, orbitalProjection, orbitGeometry, moonCatalogue)
        : null
      const previousSection = routeSections[index - 1]
      const approachCovered = (
        previousSection?.targetId === section.originId
        && !(orbitalProjection === 'side' && routeSectionUsesVerticalCorridor(section))
      )
      const path = routePassagePath(
        section,
        origin,
        target,
        nextTarget,
        nextSection ?? null,
        orbitalProjection,
        approachCovered,
      )
      if (!path) return null
      const passageCorridors = projectedPassageCorridors(section, target, orbitalProjection)
      return {
        id: section.id,
        index,
        path,
        origin,
        target,
        passageCorridors,
        targetName: routeObjectName(section.targetId, data.planets, moonCatalogue?.moons ?? []),
        hasPassageArc: section.passage.mode !== 'direct',
        outOfPlane: routeSectionUsesVerticalCorridor(section) || section.passage.orbitAngleDeg > 360,
      }
    }).filter((segment): segment is NonNullable<typeof segment> => Boolean(segment))
  }, [data, moonCatalogue, orbitGeometry, orbitalProjection, routeSections])
  const interstellarDirectionMarker = useMemo(() => {
    if (!data || plannedRoutePoints.length > 1) return null
    const terminalIndex = routeSections.reduce(
      (lastIndex, section, index) => isInterstellarRouteObject(section.targetId) ? index : lastIndex,
      -1,
    )
    const terminalSection = routeSections[terminalIndex]
    if (!terminalSection) return null
    const precedingSection = terminalIndex > 0
      && routeSections[terminalIndex - 1]?.targetId === terminalSection.originId
      ? routeSections[terminalIndex - 1]
      : null
    const calculatedPrecedingSection = precedingSection
      ? plannedRoute?.routeSections?.find((section) => section.id === precedingSection.id)
      : null
    const calculatedTerminalSection = plannedRoute?.routeSections?.find((section) => section.id === terminalSection.id)
    const origin = routePlotPoint(
      terminalSection.originId,
      orbitalProjection,
      orbitGeometry,
      moonCatalogue,
    )
    const solverDirection = projectedPhysicsDirection(
      calculatedPrecedingSection?.corridor.exitAngleSelection?.desiredExitDirection
        ?? calculatedTerminalSection?.predictedOutgoingDirection
        ?? calculatedTerminalSection?.desiredDepartureDirection,
      orbitalProjection,
    )
    const catalogueDirection = interstellarPreviewDirection(terminalSection.targetId, orbitalProjection)
    const direction = solverDirection ?? catalogueDirection
    if (!origin || !direction) return null
    return {
      origin,
      endpoint: plotRayEndpoint(origin, direction, orbitalProjection),
      direction,
      source: solverDirection
        ? 'Solver'
        : 'Katalogziel',
      targetName: routeObjectName(
        terminalSection.targetId,
        data.planets,
        moonCatalogue?.moons ?? [],
      ),
    }
  }, [data, moonCatalogue, orbitGeometry, orbitalProjection, plannedRoute, plannedRoutePoints.length, routeSections])
  const viewBox = orbitalProjection === 'top'
    ? `${-EXTENT} ${-EXTENT} ${EXTENT * 2} ${EXTENT * 2}`
    : `${-EXTENT} ${-SIDE_HALF_HEIGHT} ${EXTENT * 2} ${SIDE_HALF_HEIGHT * 2}`

  const scoreRouteConstellation = (timestamp: number, sections = routeSections) => {
    if (!data || !moonCatalogue || sections.length === 0) return null
    const geometry = data.planets.map((planet) => ({
      planet,
      position: planetPositionAt(planet, timestamp),
    }))
    const points = sections.map((section, index) => {
      const origin = routePlotPoint(section.originId, 'top', geometry, moonCatalogue)
      const target = routePlotPoint(section.targetId, 'top', geometry, moonCatalogue)
      const nextSection = sections[index + 1]
      const nextTarget = nextSection?.originId === section.targetId
        ? routePlotPoint(nextSection.targetId, 'top', geometry, moonCatalogue)
        : null
      return origin && target ? { section, origin, target, nextTarget } : null
    })
    if (points.some((point) => point === null)) return null
    let score = 0
    let gravityRisk = 0
    let alignmentPenalty = 0
    for (const point of points) {
      if (!point) continue
      const inbound = routeVector(point.origin, point.target)
      const outbound = point.nextTarget ? routeVector(point.target, point.nextTarget) : null
      const interstellarTarget = isInterstellarRouteObject(point.section.targetId)
      if (outbound) {
        const turnAngle = angleBetweenVectors(inbound, outbound)
        const capacity = routeTurnCapacityDeg(point.section.targetId)
        const overCapacity = Math.max(0, turnAngle - capacity)
        gravityRisk += overCapacity
        score += Math.max(0, 80 - Math.abs(turnAngle - Math.min(52, capacity * 0.72)))
        score -= overCapacity * 4
      }
      if (interstellarTarget) {
        const desired = interstellarPreviewDirection(point.section.targetId, 'top')
        if (desired) {
          const alignment = angleBetweenVectors(routeVector(point.origin, point.target), desired)
          alignmentPenalty += alignment
          score += Math.max(0, 120 - alignment * 2)
        }
      }
      if (point.section.passage.mode !== 'direct') {
        const requested = point.section.passage.mode === 'full-orbit' ? 360 : point.section.passage.orbitAngleDeg
        const capacity = routeTurnCapacityDeg(point.section.targetId)
        const requestedPenalty = point.section.targetId === 'sun'
          ? Math.max(0, Math.abs((requested % 360) - (outbound ? angleBetweenVectors(inbound, outbound) : requested)) - 34)
          : Math.max(0, requested - capacity * 2.1)
        gravityRisk += requestedPenalty / 4
        score -= requestedPenalty
      }
    }
    return { score, gravityRisk, alignmentPenalty }
  }

  const optimizeSolarPassagesForDate = (timestamp: number) => {
    void timestamp
    const maxOrbitAngleDeg = routeSections.reduce((maximum, section) => (
      section.targetId === 'sun' && section.passage.mode !== 'direct'
        ? Math.max(maximum, section.passage.mode === 'full-orbit' ? 360 : section.passage.orbitAngleDeg)
        : maximum
    ), 0)
    return {
      sections: routeSections.map((section) => ({
        ...section,
        corridor: { ...section.corridor },
        passage: { ...section.passage },
      })),
      changes: 0,
      maxOrbitAngleDeg,
    }
  }

  const findBestConstellation = async () => {
    if (!data || !moonCatalogue || searchRunningRef.current) return
    if (routeSections.length === 0) {
      setConstellationSearchStatus('Keine Route vorhanden.')
      return
    }
    searchCancelledRef.current = false
    searchRunningRef.current = true
    setConstellationSearchRunning(true)
    let activeMLRanker: MLCandidateRanker | null = null
    try {
      const mlResponse = await fetch('/api/ai/ml/train', {
        method: 'POST',
        headers: activityRequestHeaders({ 'Content-Type': 'application/json' }),
        body: JSON.stringify({ projectId }),
      })
      const mlPayload = await mlResponse.json() as {
        verdict?: string
        model?: MLCandidateRanker
      }
      if (mlResponse.ok && mlPayload.verdict === 'ready' && mlPayload.model) {
        activeMLRanker = mlPayload.model
      }
    } catch {
      // ML only changes candidate order. The physical solver remains usable
      // when no trained ranker is available.
    }
    const requestedBase = new Date(`${plannedMissionDate ?? activeDate}T00:00:00Z`).getTime()
    const today = new Date(todayTimestampMs)
    const todaySearchTimestamp = Date.UTC(
      today.getUTCFullYear(), today.getUTCMonth(), today.getUTCDate(),
    )
    const base = retrospectiveSearchEnabled
      ? requestedBase
      : Math.max(requestedBase, todaySearchTimestamp)
    const aiSeedTimestamps = aiCalculationBiasActive && aiCalculationSuggestion
      ? aiCalculationSuggestion.proposal.candidateSeeds
          .map((seed) => ({
            timestamp: new Date(`${seed.startDate}T00:00:00Z`).getTime(),
            priority: seed.priority,
          }))
          .filter((seed) => Number.isFinite(seed.timestamp))
      : []
    const aiWindowCenters = aiCalculationBiasActive && aiCalculationSuggestion
      ? aiCalculationSuggestion.proposal.searchWindows
          .map((window) => {
            const start = new Date(`${window.startDate}T00:00:00Z`).getTime()
            const end = new Date(`${window.endDate}T00:00:00Z`).getTime()
            return Number.isFinite(start) && Number.isFinite(end)
              ? { timestamp: (start + end) / 2, priority: window.priority }
              : null
          })
          .filter((item): item is { timestamp: number; priority: number } => item !== null)
      : []
    const aiPriorityBoost = (timestamp: number) => {
      const anchors = [...aiSeedTimestamps, ...aiWindowCenters]
      if (anchors.length === 0) return 0
      return Math.max(
        0,
        ...anchors.map((anchor) => {
          const distanceDays = Math.abs(timestamp - anchor.timestamp) / 86_400_000
          return anchor.priority * 260 - distanceDays * 3.2
        }),
      )
    }
    const involvedPlanetPeriods = routeSections
      .flatMap((section) => [section.originId, section.targetId])
      .map((bodyId) => data.planets.find((planet) => planet.id === bodyId)?.orbitalPeriodDays ?? 0)
      .filter((periodDays) => periodDays > 0)
    const {
      searchStartDay,
      searchEndDay,
      broadStepDays,
      longestRelevantPeriodDays,
    } = constellationSearchWindow(involvedPlanetPeriods, routeSections.length)
    const searchStartTimestamp = base + searchStartDay * 86_400_000
    const searchEndTimestamp = base + searchEndDay * 86_400_000
    const directBodySection = routeSections.length === 1 ? routeSections[0] : null
    const directOrigin = directBodySection
      ? data.planets.find((planet) => planet.id === directBodySection.originId)
      : undefined
    const directTarget = directBodySection
      ? data.planets.find((planet) => planet.id === directBodySection.targetId)
      : undefined
    const directPlanetSections = routeSections.filter((section) => {
      const origin = data.planets.find((planet) => planet.id === section.originId)
      const target = data.planets.find((planet) => planet.id === section.targetId)
      return Boolean(origin && target && origin.id !== target.id)
    })
    const allDirectPlanetSections = (
      routeSections.length > 0
      && directPlanetSections.length === routeSections.length
    )
    const lambertLaunchSeeds: Array<{
      timestamp: number
      arrivalDate: string
      c3Km2S2: number
      arrivalVInfinityKmS: number
      score: number
    }> = []
    let directLambertConstraints: {
      minFlightDays: number
      maxFlightDays: number
      arrivalStepDays: number
      propagationYears: number
    } | null = null
    const hohmannFlightDaysForSection = (section: RouteSectionDefinition) => {
      const origin = data.planets.find((planet) => planet.id === section.originId)
      const target = data.planets.find((planet) => planet.id === section.targetId)
      if (!origin || !target) return 260
      const transferSemiMajorKm = (origin.distanceAu + target.distanceAu) * 149_597_870.7 / 2
      return Math.PI * Math.sqrt(transferSemiMajorKm ** 3 / 1.32712440018e11) / 86_400
    }
    if (allDirectPlanetSections) {
      const hohmannFlightDays = Math.max(
        ...routeSections.map((section) => hohmannFlightDaysForSection(section)),
      )
      const departureSampleStepDays = Math.max(
        broadStepDays,
        Math.ceil((searchEndDay - searchStartDay) / 60),
      )
      const minFlightDays = Math.max(20, Math.floor(hohmannFlightDays * 0.55))
      const maxFlightDays = Math.ceil(hohmannFlightDays * 1.8)
      const arrivalSampleStepDays = Math.max(
        5,
        Math.ceil((maxFlightDays - minFlightDays) / 10),
      )
      directLambertConstraints = {
        minFlightDays,
        maxFlightDays,
        arrivalStepDays: arrivalSampleStepDays,
        propagationYears: Math.max(1, maxFlightDays / 365.25),
      }
    }
    if (directOrigin && directTarget && directOrigin.id !== directTarget.id && directLambertConstraints) {
      setConstellationSearchStatus(
        `Lambert-Porkchop-Grobraster: ${directOrigin.name} → ${directTarget.name} …`,
      )
      try {
        const lambertResponse = await fetch('/api/trajectory/plan', {
          method: 'POST',
          headers: activityRequestHeaders({ 'Content-Type': 'application/json' }),
          body: JSON.stringify({
            start: {
              type: 'body',
              bodyId: directOrigin.id,
              orbitAltitudeKm: missionConfig?.parkingOrbitAltitudeKm
                ?? DEFAULT_MISSION_CONFIG.parkingOrbitAltitudeKm,
              startDate: dateFromTimestamp(searchStartTimestamp),
            },
            target: {
              type: 'body', bodyId: directTarget.id, arrivalMode: 'flyby',
            },
            waypoints: [],
            searchWindow: {
              departureStartDate: dateFromTimestamp(searchStartTimestamp),
              departureEndDate: dateFromTimestamp(searchEndTimestamp),
              departureStepDays: Math.max(
                broadStepDays,
                Math.ceil((searchEndDay - searchStartDay) / 60),
              ),
              arrivalStepDays: directLambertConstraints.arrivalStepDays,
            },
            constraints: {
              minFlightDays: directLambertConstraints.minFlightDays,
              maxFlightDays: directLambertConstraints.maxFlightDays,
            },
            optimizationMode: 'balanced',
            simulation: {
              sampleTrajectoryPoints: 36,
              includeUncertainty: false,
              includeAudit: false,
              propagationYears: directLambertConstraints.propagationYears,
            },
          }),
        })
        const lambertPayload = await lambertResponse.json() as {
          candidates?: Array<{
            departureDate?: string
            arrivalDate?: string
            c3Km2S2?: number
            arrivalVInfinityKmS?: number
            score?: number
          }>
        }
        if (lambertResponse.ok) {
          const bestByDeparture = new Map<string, typeof lambertLaunchSeeds[number]>()
          for (const candidate of lambertPayload.candidates ?? []) {
            if (!candidate.departureDate || !candidate.arrivalDate) continue
            const timestamp = new Date(`${candidate.departureDate}T00:00:00Z`).getTime()
            if (!Number.isFinite(timestamp)) continue
            const seed = {
              timestamp,
              arrivalDate: candidate.arrivalDate,
              c3Km2S2: candidate.c3Km2S2 ?? Number.POSITIVE_INFINITY,
              arrivalVInfinityKmS: candidate.arrivalVInfinityKmS ?? Number.POSITIVE_INFINITY,
              score: candidate.score ?? Number.POSITIVE_INFINITY,
            }
            const current = bestByDeparture.get(candidate.departureDate)
            if (!current || seed.score < current.score) {
              bestByDeparture.set(candidate.departureDate, seed)
            }
          }
          lambertLaunchSeeds.push(
            ...[...bestByDeparture.values()].sort((left, right) => left.score - right.score),
          )
        }
      } catch {
        // The physical route solver remains available if this additional
        // launch-window pre-search cannot be produced.
      }
    }
    const lambertPriorityBoost = (timestamp: number) => {
      if (lambertLaunchSeeds.length === 0) return 0
      const finiteC3 = lambertLaunchSeeds
        .map((seed) => seed.c3Km2S2)
        .filter(Number.isFinite)
      const c3Scale = Math.max(1, ...finiteC3)
      return Math.max(0, ...lambertLaunchSeeds.map((seed, index) => {
        const distanceDays = Math.abs(timestamp - seed.timestamp) / 86_400_000
        const energyQuality = Number.isFinite(seed.c3Km2S2)
          ? 1 - Math.min(1, seed.c3Km2S2 / c3Scale)
          : 0
        return 900 * energyQuality
          + Math.max(0, 220 - index * 4)
          - distanceDays * 2.5
      }))
    }
    const estimatedGeometricNodeCount = Math.max(
      1,
      Math.floor((searchEndDay - searchStartDay) / broadStepDays) + 1
        + aiSeedTimestamps.length
        + aiWindowCenters.length
        + lambertLaunchSeeds.length,
    )
    let {
      preflightSolverBudget,
      fullValidationBudget,
    } = constellationSearchBudget(estimatedGeometricNodeCount, routeSections.length)
    const candidates: Array<{
      timestamp: number
      score: number
      gravityRisk: number
      alignmentPenalty: number
      sections: RouteSectionDefinition[]
      solarChanges: number
      maxSolarOrbitAngleDeg: number
    }> = []
    const buildCandidate = (timestamp: number) => {
      if (timestamp < searchStartTimestamp || timestamp > searchEndTimestamp) return null
      const optimized = optimizeSolarPassagesForDate(timestamp)
      const result = scoreRouteConstellation(timestamp, optimized.sections)
      if (!result) return null
      const daysFromBase = Math.abs((timestamp - base) / 86_400_000)
      const timePenalty = Math.min(90, daysFromBase / 55)
      const solarPenalty = optimized.maxOrbitAngleDeg > 360 ? (optimized.maxOrbitAngleDeg - 360) / 3.8 : 0
      return {
        timestamp,
        ...result,
        score: result.score - solarPenalty - timePenalty
          + aiPriorityBoost(timestamp) + lambertPriorityBoost(timestamp),
        sections: optimized.sections,
        solarChanges: optimized.changes,
        maxSolarOrbitAngleDeg: optimized.maxOrbitAngleDeg,
      }
    }
    const evaluate = (timestamp: number) => {
      const candidate = buildCandidate(timestamp)
      if (candidate) candidates.push(candidate)
      return candidate
    }
    const routeLabel = formatRoutePathLabel(routeSections)
    let searchRunId = ''
    try {
      const runResponse = await fetch('/api/calculations/runs', {
        method: 'POST',
        headers: activityRequestHeaders({ 'Content-Type': 'application/json' }),
        body: JSON.stringify({
          projectId,
          runType: 'constellation-search',
          solverName: 'adaptive-porkchop-lambert-best-first',
          routeLabel,
          baseDate: dateFromTimestamp(base),
          searchStartDate: dateFromTimestamp(base + searchStartDay * 86_400_000),
          searchEndDate: dateFromTimestamp(base + searchEndDay * 86_400_000),
          broadStepDays,
          preflightBudget: preflightSolverBudget,
          fullValidationBudget,
          input: {
            routeSections,
            missionConfig: missionConfig ?? DEFAULT_MISSION_CONFIG,
            searchMode: retrospectiveSearchEnabled
              ? 'retrospective-what-if'
              : 'future-planning',
            aiCalculationSuggestionId: aiCalculationBiasActive
              ? aiCalculationSuggestion?.suggestionId ?? ''
              : '',
          },
        }),
      })
      const runPayload = await runResponse.json() as {
        id?: string
        startedAtUtc?: string
        error?: string
      }
      if (!runResponse.ok || runPayload.error) {
        throw new Error(runPayload.error || `HTTP ${runResponse.status}`)
      }
      if (!runPayload.id) throw new Error("Persistenzantwort enthält keine Lauf-ID.")
      searchRunId = runPayload.id
      setCalculationRunHistory((current) => [
        {
          runId: searchRunId,
          routeLabel,
          status: 'running',
          startedAtUtc: runPayload.startedAtUtc ?? new Date().toISOString(),
        },
        ...current.filter((run) => run.runId !== searchRunId),
      ])
    } catch (error) {
      const message = error instanceof Error ? error.message : 'Berechnungslauf konnte nicht angelegt werden.'
      setConstellationSearchStatus(`Persistenzfehler: ${message}`)
      searchRunningRef.current = false
      setConstellationSearchRunning(false)
      return
    }
    setRouteCalculationTrace({
      runId: searchRunId,
      routeLabel,
      running: true,
      baseDate: dateFromTimestamp(base),
      searchStartDate: dateFromTimestamp(base + searchStartDay * 86_400_000),
      searchEndDate: dateFromTimestamp(base + searchEndDay * 86_400_000),
      broadStepDays,
      graphNodes: 0,
      graphEdges: 0,
      geometricShortlist: 0,
      geometryPoints: [],
      preflightBudget: preflightSolverBudget,
      fullValidationBudget,
      candidates: [],
      resultCount: 0,
      flightReadyCount: 0,
      goodResultCount: 0,
      targetGoodResults: TARGET_GOOD_CONSTELLATION_RESULTS,
      adaptiveRound: 0,
      progressPercent: 0,
      progressMessage: 'Suchgraph wird aufgebaut …',
    })
    setRouteCalculationDialogOpen(true)
    const updateSearchProgress = (progressPercent: number, progressMessage: string) => {
      setConstellationSearchStatus(progressMessage)
      setRouteCalculationTrace((current) => (
        current?.runId === searchRunId
          ? {
              ...current,
              progressPercent: Math.max(
                current.progressPercent ?? 0,
                Math.min(99, Math.round(progressPercent)),
              ),
              progressMessage,
            }
          : current
      ))
    }
    const updateCalculationCandidate = (candidateTrace: RouteCalculationCandidateTrace) => {
      setRouteCalculationTrace((current) => {
        if (!current || current.runId !== searchRunId) return current
        const exists = current.candidates.some(
          (candidate) => candidate.iteration === candidateTrace.iteration,
        )
        return {
          ...current,
          candidates: exists
            ? current.candidates.map((candidate) => (
                candidate.iteration === candidateTrace.iteration
                  ? candidateTrace
                  : candidate
              ))
            : [...current.candidates, candidateTrace],
        }
      })
    }
    const updateCalculationPerformance = (result: ConstellationSearchResult) => {
      setRouteCalculationTrace((current) => {
        if (!current || current.runId !== searchRunId) return current
        return {
          ...current,
          candidates: current.candidates.map((candidate) => (
            candidate.id === result.id
              ? {
                  ...candidate,
                  status: result.flightReady ? 'performance-valid' : result.good ? 'success' : 'rejected',
                  feasible: result.flightReady,
                  performanceEvaluated: true,
                  requiredInjectionDeltaVKmS: result.requiredInjectionDeltaVKmS,
                  availableInjectionDeltaVKmS: result.availableInjectionDeltaVKmS,
                  targetCorrectionDeltaVKmS: result.targetCorrectionDeltaVKmS,
                  corridorInsertionDeficitKmS: result.corridorInsertionDeficitKmS,
                }
              : candidate
          )),
        }
      })
    }
    const persistRunUpdate = async (values: Record<string, unknown>) => {
      const response = await fetch(`/api/calculations/runs/${encodeURIComponent(searchRunId)}`, {
        method: 'PATCH',
        headers: activityRequestHeaders({ 'Content-Type': 'application/json' }),
        body: JSON.stringify(values),
      })
      const payload = await response.json() as { error?: string }
      if (!response.ok) throw new Error(payload.error ?? `HTTP ${response.status}`)
      if (typeof values.status === 'string') {
        setCalculationRunHistory((current) => current.map((run) => (
          run.runId === searchRunId ? { ...run, status: values.status as string } : run
        )))
      }
    }
    const persistVariantUpdate = async (
      variantId: string,
      values: Record<string, unknown>,
    ) => {
      const response = await fetch(
        `/api/calculations/runs/${encodeURIComponent(searchRunId)}/variants/${encodeURIComponent(variantId)}`,
        {
          method: 'PATCH',
          headers: activityRequestHeaders({ 'Content-Type': 'application/json' }),
          body: JSON.stringify(values),
        },
      )
      const payload = await response.json() as { error?: string }
      if (!response.ok) throw new Error(payload.error ?? `HTTP ${response.status}`)
    }
    logActivity({
      category: 'calculation',
      action: 'constellation-search-started',
      details: {
        searchRunId,
        route: routeSections.map((section) => `${section.originId}>${section.targetId}`).join('|'),
      },
      values: {
        baseDate: dateFromTimestamp(base),
        broadStepDays,
        searchStartDay,
        searchEndDay,
        longestRelevantPeriodDays,
        graphAlgorithm: 'adaptive-porkchop-lambert-best-first',
        aiCalculationSuggestionId: aiCalculationBiasActive ? aiCalculationSuggestion?.suggestionId ?? '' : '',
        aiSeedCount: aiSeedTimestamps.length,
        lambertLaunchWindowCount: lambertLaunchSeeds.length,
      },
    })
    try {
      for (const seed of [...aiSeedTimestamps, ...aiWindowCenters]) {
        evaluate(seed.timestamp)
      }
      for (const seed of lambertLaunchSeeds) evaluate(seed.timestamp)
      for (let day = searchStartDay; day <= searchEndDay; day += broadStepDays) {
        evaluate(base + day * 86_400_000)
      }
      const candidateGraph = buildTemporalCandidateGraph(candidates)
      const completeCoverageMode = true
      const adaptiveLaunchWindows = selectAdaptiveLaunchWindowCandidates(
        candidateGraph,
        Math.max(90, Math.min(730, longestRelevantPeriodDays / 8)),
        routeSections.length,
      )
      const geometricShortlist = completeCoverageMode
        ? candidateGraph.nodes
        : adaptiveLaunchWindows.candidates
      const adaptiveBudgets = constellationSearchBudget(
        candidateGraph.nodes.length,
        routeSections.length,
        geometricShortlist.length,
      )
      preflightSolverBudget = completeCoverageMode
        ? candidateGraph.nodes.length
        : adaptiveBudgets.preflightSolverBudget
      fullValidationBudget = completeCoverageMode
        ? candidateGraph.nodes.length
        : adaptiveBudgets.fullValidationBudget
      const graphEdges = [...candidateGraph.neighbors.values()]
        .reduce((sum, edges) => sum + edges.length, 0) / 2
      const shortlistedTimestamps = new Set(
        geometricShortlist.map((candidate) => candidate.timestamp),
      )
      const geometryStride = Math.max(1, Math.ceil(candidateGraph.nodes.length / 1_200))
      const geometryPoints = candidateGraph.nodes
        .filter((node, index) => (
          index === 0
          || index === candidateGraph.nodes.length - 1
          || index % geometryStride === 0
          || shortlistedTimestamps.has(node.timestamp)
        ))
        .map((node) => ({
          date: dateFromTimestamp(node.timestamp),
          score: node.score,
          shortlisted: shortlistedTimestamps.has(node.timestamp),
        }))
      setRouteCalculationTrace((current) => (
        current?.runId === searchRunId
          ? {
              ...current,
              graphNodes: candidateGraph.nodes.length,
              graphEdges,
              geometricShortlist: geometricShortlist.length,
              geometryPoints,
            }
          : current
      ))
      updateSearchProgress(10, 'Suchgraph erstellt · dynamische Vorprüfung startet …')
      await persistRunUpdate({
        graphNodes: candidateGraph.nodes.length,
        graphEdges,
        shortlistCount: geometricShortlist.length,
        preflightBudget: preflightSolverBudget,
        fullValidationBudget,
        geometryPoints,
      })
      logActivity({
        category: 'calculation',
        action: 'constellation-graph-built',
        status: 'success',
        values: {
          nodes: candidateGraph.nodes.length,
          edges: graphEdges,
          shortlistNodes: geometricShortlist.length,
          localLaunchWindowPeaks: adaptiveLaunchWindows.localPeakCount,
          qualifiedLaunchWindowNodes: adaptiveLaunchWindows.qualifiedNodeCount,
          adaptiveQualityFloor: adaptiveLaunchWindows.qualityFloor,
          adaptiveCoverageTarget: adaptiveLaunchWindows.coverageTarget,
          coverageMode: completeCoverageMode ? 'complete-grid' : 'adaptive-shortlist',
          searchStartDate: dateFromTimestamp(base + searchStartDay * 86_400_000),
          searchEndDate: dateFromTimestamp(base + searchEndDay * 86_400_000),
        },
        details: {
          searchRunId,
          algorithm: completeCoverageMode
            ? 'complete-launch-grid+lambert-best-first-ordering'
            : 'adaptive-launch-window-landscape+lambert-best-first-refinement',
        },
      })
      if (geometricShortlist.length === 0) {
        setConstellationSearchStatus('Keine bewertbare Konstellation gefunden.')
        await persistRunUpdate({
          status: 'rejected',
          error: 'Keine bewertbare geometrische Konstellation gefunden.',
        })
        return
      }

      type SolvedCandidate = {
        variantId: string
        candidate: typeof geometricShortlist[number]
        route: WaypointRouteResult
        quality: number
        geometryValid: boolean
        maximumEndpointResidualKm: number
        targetCorrectionDeltaVKmS: number
        requiredInjectionDeltaVKmS: number
        availableInjectionDeltaVKmS: number
        corridorInsertionDeficitKmS: number
        targetAlignmentDeg: number
        corridorSatisfied: boolean
        collisionFree: boolean
        mlPriorityScore: number
      }
      const solveCandidate = async (
        candidate: typeof geometricShortlist[number],
        fullCorridorCheck: boolean,
        stage: string,
        iteration: number,
      ): Promise<SolvedCandidate | null> => {
        if (searchCancelledRef.current) return null
        const startDate = dateFromTimestamp(candidate.timestamp)
        const candidateTraceId = `pending:${searchRunId}:${iteration}`
        const runningTrace: RouteCalculationCandidateTrace = {
          id: candidateTraceId,
          iteration,
          date: startDate,
          stage,
          fullCorridorCheck,
          status: 'running',
          geometricScore: candidate.score,
        }
        updateCalculationCandidate(runningTrace)
        // The route is a binding user specification.  Every requested section
        // must reach the solver in the original order; local passages are
        // resolved only after the complete coarse geometry has been validated.
        const selectedSections = candidate.sections
        const solverSections = fullCorridorCheck
          ? selectedSections
          : selectedSections.map((section) => ({
              ...section,
              corridor: { ...section.corridor, enabled: false },
            }))
        if (solverSections.length === 0) return null
        const response = await fetch('/api/route/simulate', {
          method: 'POST',
          headers: activityRequestHeaders({ 'Content-Type': 'application/json' }),
          body: JSON.stringify({
            preferLambertBodyToBody: Boolean(
              directLambertConstraints
              && allDirectPlanetSections
              && solverSections.length === routeSections.length
              && solverSections.every((section, sectionIndex) => (
                section.originId === routeSections[sectionIndex]?.originId
                && section.targetId === routeSections[sectionIndex]?.targetId
              ))
            ),
            lambertConstraints: directLambertConstraints ?? undefined,
            mission: { ...(missionConfig ?? DEFAULT_MISSION_CONFIG), startDate },
            waypointId: solverSections[0]?.targetId ?? 'earth',
            flybyAltitudeKm: 100_000,
            flybyMode: 'acceleration',
            // Basin ranking may use the ideal geometry model, but a binding
            // corridor validation must run the target-coupled performance
            // model.  Otherwise the solar/planetary exit is only aimed by a
            // schematic tangent and the following section is charged an
            // artificial mid-course correction.
            calculationStage: fullCorridorCheck ? 'performance' : 'geometry',
            routeSections: solverSections,
            calculationPersistence: {
              runId: searchRunId,
              iteration,
              startDate,
              stage,
              fullCorridorCheck,
              geometricScore: candidate.score,
            },
          }),
        })
        const payload = await response.json() as WaypointRouteResult | {
          error?: string
          calculationVariantId?: string
        }
        if (searchCancelledRef.current) return null
        if (!response.ok || 'error' in payload) {
          const message = 'error' in payload && payload.error ? payload.error : `HTTP ${response.status}`
          const structural = /keine lokale Ephemeride|benötigt zuerst|kann nur der letzte|Mindestens ein 2D-Routenabschnitt|Endpunkt/.test(message)
          updateCalculationCandidate({
            ...runningTrace,
            id: 'calculationVariantId' in payload && payload.calculationVariantId
              ? payload.calculationVariantId
              : runningTrace.id,
            status: structural ? 'error' : 'rejected',
            message,
          })
          logActivity({
            category: 'calculation',
            action: 'constellation-candidate',
            status: structural ? 'error' : 'rejected',
            message,
            details: {
              searchRunId,
              stage,
              rejectionKind: structural ? 'structural' : 'constraint',
            },
            values: {
              iteration,
              startDate,
              geometricScore: candidate.score,
              fullCorridorCheck,
            },
          })
          if (structural) throw new Error(`Modellfehler in der Konstellationssuche: ${message}`)
          return null
        }
        const route = payload as WaypointRouteResult
        const persistedVariantId = route.calculationPersistence?.variantId
        if (!persistedVariantId) {
          throw new Error(`Persistenzantwort für Variante ${iteration} enthält keine Varianten-ID.`)
        }
        const targetCorrectionDeltaVKmS = route.summary.targetCorrectionDeltaVKmS ?? 0
        const requiredInjectionDeltaVKmS = route.summary.requiredInjectionDeltaVKmS
        const availableInjectionDeltaVKmS = route.summary.availableInjectionDeltaVKmS ?? requiredInjectionDeltaVKmS
        const targetAlignmentDeg = route.summary.actualTargetAlignmentDeg ?? route.summary.targetAlignmentDeg
        const geometry = validateRouteGeometry(selectedSections, route, fullCorridorCheck)
        const corridorSatisfied = geometry.corridorsSatisfied
        const collisionFree = geometry.collisionFree && route.highFidelityNBody?.collision !== true
        const corridorInsertionDeficitKmS = route.routeSections?.reduce(
          (sum, section, sectionIndex) => (
            sum + Math.max(
              0,
              section.corridorInsertionDeltaVKmS
                - (selectedSections[sectionIndex]?.deltaVPlusKmS ?? 0),
            )
          ),
          0,
        ) ?? 0
        const endpointPenalty = Number.isFinite(geometry.maximumEndpointResidualKm)
          ? Math.min(2_000, Math.log10(1 + geometry.maximumEndpointResidualKm) * 140)
          : 5_000
        const quality = (
          candidate.score
          + (geometry.sectionOrderValid ? 800 : -4_000)
          + (geometry.stateContinuous ? 600 : -4_000)
          + (geometry.endpointsReached ? 1_000 : -5_000)
          + (geometry.monotonicTime ? 300 : -3_000)
          + (collisionFree ? 600 : -8_000)
          + (!fullCorridorCheck || corridorSatisfied ? 350 : -2_000)
          - endpointPenalty
        )
        const mlPriorityScore = scoreWithMLRanker(activeMLRanker, {
          geometricScore: candidate.score,
          targetAlignmentDeg,
          deltaVDeficitKmS: Math.max(
            0,
            requiredInjectionDeltaVKmS
              + targetCorrectionDeltaVKmS
              - availableInjectionDeltaVKmS,
          ) + corridorInsertionDeficitKmS,
          requiredInjectionDeltaVKmS,
          availableInjectionDeltaVKmS,
          targetCorrectionDeltaVKmS,
          corridorInsertionDeficitKmS,
          corridorSatisfied,
          collisionFree,
          fullCorridorCheck,
          annualPhaseSin: Math.sin(2 * Math.PI * (candidate.timestamp - Date.UTC(2000, 0, 1)) / (365.2425 * 86_400_000)),
          annualPhaseCos: Math.cos(2 * Math.PI * (candidate.timestamp - Date.UTC(2000, 0, 1)) / (365.2425 * 86_400_000)),
          jupiterPhaseSin: Math.sin(2 * Math.PI * (candidate.timestamp - Date.UTC(2000, 0, 1)) / (4_332.59 * 86_400_000)),
          jupiterPhaseCos: Math.cos(2 * Math.PI * (candidate.timestamp - Date.UTC(2000, 0, 1)) / (4_332.59 * 86_400_000)),
        })
        logActivity({
          category: 'calculation',
          action: 'constellation-candidate',
          status: geometry.valid ? 'success' : 'rejected',
          details: {
            searchRunId,
            variantId: persistedVariantId,
            stage,
            rejectionKind: geometry.valid ? '' : 'geometry',
            geometryErrors: geometry.errors.join(' | '),
          },
          values: {
            iteration,
            startDate,
            geometricScore: candidate.score,
            quality,
            geometryValid: geometry.valid,
            maximumEndpointResidualKm: geometry.maximumEndpointResidualKm,
            requiredInjectionDeltaVKmS,
            availableInjectionDeltaVKmS,
            targetCorrectionDeltaVKmS,
            corridorInsertionDeficitKmS,
            deltaVDeficitKmS: Math.max(
              0,
              requiredInjectionDeltaVKmS
                + targetCorrectionDeltaVKmS
                - availableInjectionDeltaVKmS,
            ) + corridorInsertionDeficitKmS,
            corridorSatisfied,
            collisionFree,
            fullCorridorCheck,
            targetAlignmentDeg,
            mlPriorityScore,
            annualPhaseSin: Math.sin(2 * Math.PI * (candidate.timestamp - Date.UTC(2000, 0, 1)) / (365.2425 * 86_400_000)),
            annualPhaseCos: Math.cos(2 * Math.PI * (candidate.timestamp - Date.UTC(2000, 0, 1)) / (365.2425 * 86_400_000)),
            jupiterPhaseSin: Math.sin(2 * Math.PI * (candidate.timestamp - Date.UTC(2000, 0, 1)) / (4_332.59 * 86_400_000)),
            jupiterPhaseCos: Math.cos(2 * Math.PI * (candidate.timestamp - Date.UTC(2000, 0, 1)) / (4_332.59 * 86_400_000)),
          },
        })
        await persistVariantUpdate(persistedVariantId, {
          status: geometry.valid ? 'geometry-valid' : 'rejected',
          quality,
          feasible: null,
          geometryValid: geometry.valid,
          sectionOrderValid: geometry.sectionOrderValid,
          stateContinuous: geometry.stateContinuous,
          endpointsReached: geometry.endpointsReached,
          maximumEndpointResidualKm: geometry.maximumEndpointResidualKm,
          performanceEvaluated: false,
          hypotheticalInterstellarAsymptote: (
            route.summary.hypotheticalInterstellarAsymptote === true
          ),
          corridorSatisfied,
          collisionFree,
          targetAlignmentDeg,
        })
        updateCalculationCandidate({
          ...runningTrace,
          id: persistedVariantId,
          status: geometry.valid ? 'geometry-valid' : 'rejected',
          message: geometry.valid ? undefined : geometry.errors.join(' '),
          quality,
          geometryValid: geometry.valid,
          endpointsReached: geometry.endpointsReached,
          maximumEndpointResidualKm: geometry.maximumEndpointResidualKm,
          performanceEvaluated: false,
          corridorSatisfied,
          collisionFree,
          targetAlignmentDeg,
          totalFlightDays: route.totalFlightDays,
          routePoints: downsampleRoutePoints(route.trajectory),
        })
        if (!geometry.valid) return null
        return {
          variantId: persistedVariantId,
          candidate,
          route,
          quality,
          geometryValid: geometry.valid,
          maximumEndpointResidualKm: geometry.maximumEndpointResidualKm,
          targetCorrectionDeltaVKmS,
          requiredInjectionDeltaVKmS,
          availableInjectionDeltaVKmS,
          corridorInsertionDeficitKmS,
          targetAlignmentDeg,
          corridorSatisfied,
          collisionFree,
          mlPriorityScore,
        }
      }

      const preflightCandidates: SolvedCandidate[] = []
      const solvedTimestamps = new Set<number>()
      let solverIteration = 0
      for (let index = 0; index < geometricShortlist.length && !searchCancelledRef.current; index += 1) {
        const candidate = geometricShortlist[index]
        const startDate = dateFromTimestamp(candidate.timestamp)
        updateSearchProgress(
          10 + 40 * index / Math.max(1, geometricShortlist.length),
          `Dynamische Vorprüfung ${index + 1}/${geometricShortlist.length}: ${new Date(`${startDate}T00:00:00Z`).toLocaleDateString('de-DE', { timeZone: 'UTC' })}`,
        )
        solverIteration += 1
        solvedTimestamps.add(candidate.timestamp)
        const solved = await solveCandidate(candidate, false, 'basin-preflight', solverIteration)
        if (solved) preflightCandidates.push(solved)
      }
      updateSearchProgress(
        50,
        completeCoverageMode
          ? 'Vollständige Vorprüfung abgeschlossen · Korridor-Vollprüfung startet …'
          : 'Dynamische Vorprüfung abgeschlossen · Graph-Nachsuche …',
      )
      const refinementSeeds = selectTemporallyDiverseCandidates(
        [...preflightCandidates].sort((left, right) => (
          right.mlPriorityScore - left.mlPriorityScore
          || right.quality - left.quality
        )),
        (candidate) => candidate.candidate.timestamp,
        3,
        Math.max(180, Math.min(730, longestRelevantPeriodDays / 10)),
      )
      const refinementFrontier = refinementSeeds.map((solved) => ({ solved, level: 0 }))
      while (refinementFrontier.length > 0 && solverIteration < preflightSolverBudget && !searchCancelledRef.current) {
        const parent = refinementFrontier.shift()
        if (!parent || parent.level >= 4) continue
        const neighbors = temporalRefinementNeighbors(
          parent.solved.candidate.timestamp,
          parent.level,
          broadStepDays,
        )
          .map((timestamp) => buildCandidate(timestamp))
          .filter((candidate): candidate is NonNullable<typeof candidate> => candidate !== null)
          .filter((candidate) => !solvedTimestamps.has(candidate.timestamp))
        const solvedNeighbors: SolvedCandidate[] = []
        for (let index = 0; index < neighbors.length && !searchCancelledRef.current; index += 1) {
          if (solverIteration >= preflightSolverBudget) break
          const candidate = neighbors[index]
          const startDate = dateFromTimestamp(candidate.timestamp)
          updateSearchProgress(
            50 + 15 * solverIteration / Math.max(1, preflightSolverBudget),
            `Graph-Nachsuche Ebene ${parent.level + 1} · ${index + 1}/${neighbors.length}: ${new Date(`${startDate}T00:00:00Z`).toLocaleDateString('de-DE', { timeZone: 'UTC' })}`,
          )
          solverIteration += 1
          solvedTimestamps.add(candidate.timestamp)
          const solved = await solveCandidate(
            candidate,
            false,
            `graph-refinement-level-${parent.level + 1}`,
            solverIteration,
          )
          if (solved) {
            preflightCandidates.push(solved)
            solvedNeighbors.push(solved)
          }
        }
        const bestNeighbor = solvedNeighbors.sort(
          (left, right) => (
            right.mlPriorityScore - left.mlPriorityScore
            || right.quality - left.quality
          ),
        )[0]
        if (bestNeighbor) {
          refinementFrontier.push({ solved: bestNeighbor, level: parent.level + 1 })
        }
      }
      const rankedPreflightCandidates = [...preflightCandidates]
        .sort((left, right) => (
          right.mlPriorityScore - left.mlPriorityScore
          || right.quality - left.quality
        ))
      const fullValidationBasinSeparationDays = Math.max(
        180,
        Math.min(730, longestRelevantPeriodDays / 10),
      )
      const fullValidationSeeds = completeCoverageMode
        ? rankedPreflightCandidates
        : selectTemporallyDiverseCandidates(
            rankedPreflightCandidates,
            (candidate) => candidate.candidate.timestamp,
            fullValidationBudget,
            fullValidationBasinSeparationDays,
          )
      // A corridor and passage are binding mission inputs.  Preflight runs
      // may rank dates, but must never replace the user's spatial aimpoint or
      // requested passage before authoritative full validation.
      const fullValidationShortlist = fullValidationSeeds
      updateSearchProgress(65, 'Graph-Nachsuche abgeschlossen · Korridor-Vollprüfung …')
      const solvedCandidates: SolvedCandidate[] = []
      const solvedCandidateIsGood = (solved: SolvedCandidate) => (
        solved.route.summary.feasibleWithConfiguredBurn
        && solved.corridorSatisfied
        && solved.collisionFree
        && (
          solved.route.summary.hypotheticalInterstellarAsymptote !== true
          || solved.targetAlignmentDeg <= GOOD_INTERSTELLAR_ALIGNMENT_DEG
        )
      )
      let goodSolvedCandidateCount = 0
      for (let index = 0; index < fullValidationShortlist.length && !searchCancelledRef.current; index += 1) {
        const candidate = fullValidationShortlist[index].candidate
        const startDate = dateFromTimestamp(candidate.timestamp)
        updateSearchProgress(
          65 + 20 * index / Math.max(1, fullValidationShortlist.length),
          `Korridor-Vollprüfung ${index + 1}/${fullValidationShortlist.length}: ${new Date(`${startDate}T00:00:00Z`).toLocaleDateString('de-DE', { timeZone: 'UTC' })}`,
        )
        solverIteration += 1
        const solved = await solveCandidate(candidate, true, 'corridor-full-validation', solverIteration)
        if (solved) {
          solvedCandidates.push(solved)
          if (solvedCandidateIsGood(solved)) goodSolvedCandidateCount += 1
          setRouteCalculationTrace((current) => (
            current?.runId === searchRunId
              ? {
                  ...current,
                  resultCount: solvedCandidates.length,
                  goodResultCount: goodSolvedCandidateCount,
                }
              : current
          ))
          if (!completeCoverageMode && goodSolvedCandidateCount >= TARGET_GOOD_CONSTELLATION_RESULTS) break
        }
      }
      updateSearchProgress(85, 'Korridor-Vollprüfung abgeschlossen · adaptive Passageprüfung …')
      const validatedSignatures = new Set(fullValidationShortlist.map(({ candidate }) => (
        `${candidate.timestamp}:${JSON.stringify(candidate.sections.map((section) => section.passage))}`
      )))
      const adaptiveSeedSignatures = new Set<string>()
      const adaptiveSeeds = [
        ...[...solvedCandidates]
          .sort((left, right) => right.quality - left.quality)
          .map((solved) => solved.candidate),
        ...fullValidationShortlist.map(({ candidate }) => candidate),
      ].filter((candidate) => {
        const signature = `${candidate.timestamp}:${JSON.stringify(
          candidate.sections.map((section) => section.passage),
        )}`
        if (adaptiveSeedSignatures.has(signature)) return false
        adaptiveSeedSignatures.add(signature)
        return true
      }).slice(0, 8)
      let scheduledFullValidationCount = fullValidationShortlist.length
      for (
        let adaptiveRound = 0;
        adaptiveRound < ADAPTIVE_PASSAGE_ANGLE_ROUNDS.length
          && (
            !completeCoverageMode
            || goodSolvedCandidateCount < TARGET_GOOD_CONSTELLATION_RESULTS
          );
        adaptiveRound += 1
      ) {
        if (searchCancelledRef.current) break
        const adaptiveCandidates = adaptiveSeeds.flatMap((seed) => (
          adaptivePassageVariants(seed.sections, adaptiveRound)
            .map((sections) => ({ ...seed, sections }))
        )).filter((candidate) => {
          const signature = `${candidate.timestamp}:${JSON.stringify(
            candidate.sections.map((section) => section.passage),
          )}`
          if (validatedSignatures.has(signature)) return false
          validatedSignatures.add(signature)
          return true
        })
        if (adaptiveCandidates.length === 0) break
        scheduledFullValidationCount += adaptiveCandidates.length
        setRouteCalculationTrace((current) => (
          current?.runId === searchRunId
            ? {
                ...current,
                adaptiveRound: adaptiveRound + 1,
                fullValidationBudget: scheduledFullValidationCount,
                goodResultCount: goodSolvedCandidateCount,
              }
            : current
        ))
        await persistRunUpdate({ fullValidationBudget: scheduledFullValidationCount })
        for (let index = 0; index < adaptiveCandidates.length && !searchCancelledRef.current; index += 1) {
          if (!completeCoverageMode && goodSolvedCandidateCount >= TARGET_GOOD_CONSTELLATION_RESULTS) break
          const candidate = adaptiveCandidates[index]
          const startDate = dateFromTimestamp(candidate.timestamp)
          updateSearchProgress(
            85 + 14 * (
              adaptiveRound + index / Math.max(1, adaptiveCandidates.length)
            ) / Math.max(1, ADAPTIVE_PASSAGE_ANGLE_ROUNDS.length),
            `Adaptive Passagensuche R${adaptiveRound + 1} · ${index + 1}/${adaptiveCandidates.length}: ${new Date(`${startDate}T00:00:00Z`).toLocaleDateString('de-DE', { timeZone: 'UTC' })}`,
          )
          solverIteration += 1
          const solved = await solveCandidate(
            candidate,
            true,
            `adaptive-passage-round-${adaptiveRound + 1}`,
            solverIteration,
          )
          if (solved) {
            solvedCandidates.push(solved)
            if (solvedCandidateIsGood(solved)) goodSolvedCandidateCount += 1
            setRouteCalculationTrace((current) => (
              current?.runId === searchRunId
                ? {
                    ...current,
                    resultCount: solvedCandidates.length,
                    goodResultCount: goodSolvedCandidateCount,
                  }
                : current
            ))
          }
        }
      }
      if (searchCancelledRef.current) return
      const rankedResults: ConstellationSearchResult[] = solvedCandidates
        .map((solved) => {
          const flightReady = (
            solved.route.summary.feasibleWithConfiguredBurn
            && solved.route.summary.hypotheticalInterstellarAsymptote !== true
            && solved.corridorSatisfied
            && solved.collisionFree
          )
          const good = solvedCandidateIsGood(solved)
          return {
            id: solved.variantId,
            date: dateFromTimestamp(solved.candidate.timestamp),
            sections: solved.candidate.sections,
            route: solved.route,
            quality: solved.quality,
            geometryValid: solved.geometryValid,
            hypotheticalInterstellarAsymptote: (
              solved.route.summary.hypotheticalInterstellarAsymptote === true
            ),
            flightReady,
            good,
            corridorSatisfied: solved.corridorSatisfied,
            collisionFree: solved.collisionFree,
            requiredInjectionDeltaVKmS: solved.requiredInjectionDeltaVKmS,
            availableInjectionDeltaVKmS: solved.availableInjectionDeltaVKmS,
            targetCorrectionDeltaVKmS: solved.targetCorrectionDeltaVKmS,
            corridorInsertionDeficitKmS: solved.corridorInsertionDeficitKmS,
            targetAlignmentDeg: solved.targetAlignmentDeg,
          }
        })
        .sort((left, right) => (
          Number(right.flightReady) - Number(left.flightReady)
          || Number(right.good) - Number(left.good)
          || right.quality - left.quality
        ))
      const bestResult = rankedResults[0]
      const best = solvedCandidates.find((solved) => (
        dateFromTimestamp(solved.candidate.timestamp) === bestResult?.date
      ))
      if (!best || !bestResult) {
        setConstellationSearchStatus('Der Solver konnte keinen Kandidaten propagieren. Route und Eingaben bleiben erhalten.')
        setRouteCalculationTrace((current) => (
          current?.runId === searchRunId
            ? {
                ...current,
                resultCount: solvedCandidates.length,
                flightReadyCount: 0,
                error: 'Kein Kandidat konnte bis zur Korridor-Vollprüfung propagiert werden.',
              }
            : current
        ))
        logActivity({
          category: 'calculation',
          action: 'constellation-search-completed',
          status: 'rejected',
          message: 'Kein Kandidat konnte bis zur Korridor-Vollprüfung propagiert werden.',
          details: {
            searchRunId,
            resultKind: 'no-propagable-candidate',
          },
          values: {
            iterations: solverIteration,
            preflightCandidates: preflightCandidates.length,
            fullValidationCandidates: solvedCandidates.length,
          },
        })
        await persistRunUpdate({
          status: 'rejected',
          resultCount: solvedCandidates.length,
          flightReadyCount: 0,
          error: 'Kein Kandidat konnte bis zur Korridor-Vollprüfung propagiert werden.',
        })
        return
      }
      const bestDate = dateFromTimestamp(best.candidate.timestamp)
      const goodResultCount = rankedResults.filter((result) => result.good).length
      const flightReadyResultCount = rankedResults.filter((result) => result.flightReady).length
      const goodResultTargetReached = goodResultCount >= TARGET_GOOD_CONSTELLATION_RESULTS
      const coverageCompletedSuccessfully = completeCoverageMode && goodResultCount > 0
      const runCompletedSuccessfully = goodResultTargetReached || coverageCompletedSuccessfully
      const stopReason = completeCoverageMode
        ? (
            goodResultCount > 0
              ? `Suchraster vollständig geprüft: ${goodResultCount} gute Resultate gefunden.`
              : `Suchraster vollständig geprüft: keine gute Lösung innerhalb der harten Randbedingungen gefunden.`
          )
        : (
            goodResultTargetReached
              ? `Ziel erreicht: ${goodResultCount} gute Resultate gefunden.`
              : `Alle Zeit- und Passagevarianten ausgeschöpft: ${goodResultCount}/${TARGET_GOOD_CONSTELLATION_RESULTS} gute Resultate. Verbleibende harte Randbedingungen oder Δv-Grenzen verhindern weitere gültige Lösungen.`
          )
      setConstellationResults(rankedResults)
      setSelectedConstellationResultId(bestResult.id)
      const deltaVDeficitKmS = Math.max(
        0,
        best.requiredInjectionDeltaVKmS
          + best.targetCorrectionDeltaVKmS
          - best.availableInjectionDeltaVKmS,
      ) + best.corridorInsertionDeficitKmS
      await Promise.all(rankedResults.map(async (result, index) => {
        const resultDeficitKmS = Math.max(
          0,
          result.requiredInjectionDeltaVKmS
            + result.targetCorrectionDeltaVKmS
            - result.availableInjectionDeltaVKmS,
        ) + result.corridorInsertionDeficitKmS
        await persistVariantUpdate(result.id, {
          status: result.flightReady ? 'performance-valid' : result.good ? 'success' : 'rejected',
          rank: index + 1,
          selected: false,
          feasible: result.flightReady,
          corridorSatisfied: result.corridorSatisfied,
          collisionFree: result.collisionFree,
          corridorInsertionDeficitKmS: result.corridorInsertionDeficitKmS,
          deltaVDeficitKmS: resultDeficitKmS,
          targetAlignmentDeg: result.targetAlignmentDeg,
          performanceEvaluated: true,
        })
        updateCalculationPerformance(result)
      }))
      await persistRunUpdate({
        status: runCompletedSuccessfully ? 'completed' : 'rejected',
        resultCount: rankedResults.length,
        flightReadyCount: flightReadyResultCount,
        bestVariantId: bestResult.id,
        ...(runCompletedSuccessfully ? {} : { error: stopReason }),
      })
      const solutionLabel = completeCoverageMode
        ? `Suchraster vollständig geprüft · ${goodResultCount} gute Resultate`
        : (
            goodResultTargetReached
              ? `Adaptives Suchziel erreicht · ${goodResultCount} gute Resultate`
              : `Suchraum ausgeschöpft · ${goodResultCount}/${TARGET_GOOD_CONSTELLATION_RESULTS} gute Resultate`
          )
      const solarPassageLabel = best.candidate.maxSolarOrbitAngleDeg >= 360 ? 'Sonnenumrundung' : 'Sonnenpassage'
      setConstellationSearchStatus(
        `${solutionLabel} ${new Date(`${bestDate}T00:00:00Z`).toLocaleDateString('de-DE', { timeZone: 'UTC' })} - Start-Δv ${best.requiredInjectionDeltaVKmS.toFixed(2)} km/s - Zielkorrektur ${best.targetCorrectionDeltaVKmS.toFixed(2)} km/s - Zielrest ${best.targetAlignmentDeg.toFixed(1)}°${best.candidate.solarChanges > 0 ? ` - ${solarPassageLabel} ${best.candidate.maxSolarOrbitAngleDeg.toFixed(0)}°` : ''}${deltaVDeficitKmS > 0 ? ` - Δv-Defizit ${deltaVDeficitKmS.toFixed(2)} km/s` : ''}`,
      )
      logActivity({
        category: 'calculation',
        action: 'constellation-search-completed',
        status: runCompletedSuccessfully ? 'success' : 'rejected',
        details: {
          searchRunId,
          resultKind: completeCoverageMode
            ? 'complete-grid-evaluated'
            : goodResultTargetReached ? 'good-result-target-reached' : 'search-space-exhausted',
        },
        values: {
          iterations: solverIteration,
          bestDate,
          quality: best.quality,
          deltaVDeficitKmS,
          targetAlignmentDeg: best.targetAlignmentDeg,
          feasible: bestResult.flightReady,
          resultCount: rankedResults.length,
          flightReadyResultCount,
          goodResultCount,
          targetGoodResultCount: TARGET_GOOD_CONSTELLATION_RESULTS,
        },
      })
      setRouteCalculationTrace((current) => (
        current?.runId === searchRunId
          ? {
              ...current,
              bestDate,
              resultCount: rankedResults.length,
              flightReadyCount: flightReadyResultCount,
              goodResultCount,
              targetGoodResults: TARGET_GOOD_CONSTELLATION_RESULTS,
              stopReason,
              progressPercent: 100,
              progressMessage: stopReason,
            }
          : current
      ))
    } catch (error) {
      const rawMessage = error instanceof Error ? error.message : 'Konstellationssuche fehlgeschlagen.'
      const message = /failed to fetch/i.test(rawMessage)
        ? 'Backend nicht erreichbar oder waehrend der Berechnung neu gestartet. Bitte Serverstatus pruefen und den Solverlauf erneut starten.'
        : rawMessage
      setConstellationSearchStatus(message)
      setRouteCalculationTrace((current) => (
        current?.runId === searchRunId ? { ...current, error: message } : current
      ))
      logActivity({
        category: 'calculation',
        action: 'constellation-search-failed',
        status: 'error',
        message,
        details: { searchRunId },
      })
      if (searchRunId) {
        try {
          await persistRunUpdate({ status: 'failed', error: message })
        } catch {
          // The original persistence error is already visible in the UI.
        }
      }
    } finally {
      if (searchCancelledRef.current && searchRunId) {
        try {
          await persistRunUpdate({ status: 'cancelled', error: 'Solverlauf vom Nutzer abgebrochen.' })
        } catch {
          // The closed dialog must stay closed if persisting cancellation fails.
        }
      }
      searchRunningRef.current = false
      setConstellationSearchRunning(false)
      setRouteCalculationTrace((current) => (
        current?.runId === searchRunId ? { ...current, running: false } : current
      ))
    }
  }

  const loadPersistedCalculationRun = async (runId: string) => {
    setCalculationHistoryLoading(true)
    try {
      const response = await fetch(
        `/api/calculations/runs/${encodeURIComponent(runId)}`,
        { headers: activityRequestHeaders() },
      )
      const payload = await response.json() as RouteCalculationTrace & { error?: string }
      if (!response.ok) throw new Error(payload.error ?? `HTTP ${response.status}`)
      setRouteCalculationTrace(payload)
    } catch (error) {
      setConstellationSearchStatus(
        error instanceof Error
          ? `Gespeicherter Lauf: ${error.message}`
          : 'Gespeicherter Lauf konnte nicht geladen werden.',
      )
    } finally {
      setCalculationHistoryLoading(false)
    }
  }

  const applyConstellationResult = async (
    resultId: string,
    fallbackCandidate?: RouteCalculationCandidateTrace,
  ) => {
    const result = constellationResults.find((item) => item.id === resultId)
    if (!result && fallbackCandidate?.fullCorridorCheck && (
      fallbackCandidate.status === 'performance-valid'
      || fallbackCandidate.status === 'success'
      || fallbackCandidate.feasible === true
    )) {
      try {
        const response = await fetch(
          `/api/calculations/variants/${encodeURIComponent(resultId)}`,
          { headers: activityRequestHeaders() },
        )
        const persisted = await response.json() as PersistedCalculationVariant & { error?: string }
        if (!response.ok) throw new Error(persisted.error ?? `HTTP ${response.status}`)
        const restoredSections = (persisted.sections ?? [])
          .map((section, index) => parsePersistedRouteSection(section, routeSections[index]))
          .filter((section): section is RouteSectionDefinition => Boolean(section))
        if (restoredSections.length === 0) {
          throw new Error('Diese historische Variante enthaelt keine wiederherstellbaren Routenabschnitte.')
        }
        const restoredPoints = (persisted.routePoints ?? [])
          .map(persistedRoutePoint)
          .filter((point): point is { elapsedDays: number | null; positionKm: [number, number, number] } => Boolean(point))
        const totalFlightDays = Number.isFinite(persisted.totalFlightDays)
          ? persisted.totalFlightDays as number
          : Math.max(0, restoredPoints.length - 1)
        const trajectory = restoredPoints.length > 0
          ? restoredPoints.map((point, index) => ({
              elapsedDays: point.elapsedDays ?? (
                restoredPoints.length > 1
                  ? totalFlightDays * index / (restoredPoints.length - 1)
                  : 0
              ),
              positionKm: point.positionKm,
            }))
          : [{ elapsedDays: 0, positionKm: [0, 0, 0] as [number, number, number] }]
        const lastSection = restoredSections.at(-1)
        const waypointPosition = trajectory.at(-1)?.positionKm ?? [0, 0, 0] as [number, number, number]
        const restoredRoute: WaypointRouteResult = {
          calculationPersistence: {
            runId: persisted.calculationRunId,
            variantId: persisted.id,
          },
          startDate: persisted.date,
          totalFlightDays,
          waypoint: {
            id: lastSection?.targetId ?? 'target',
            name: persisted.sections?.at(-1)?.target_name || lastSection?.targetId || 'Ziel',
            encounterDay: totalFlightDays,
            flybyAltitudeKm: 0,
            positionKm: waypointPosition,
          },
          trajectory,
          warnings: (persisted.warnings ?? []).map((warning) => (
            typeof warning === 'string' ? warning : warning.message ?? 'Persistierte Warnung'
          )),
        }
        await fetch(
          `/api/calculations/runs/${encodeURIComponent(persisted.calculationRunId)}/variants/${encodeURIComponent(persisted.id)}`,
          {
            method: 'PATCH',
            headers: activityRequestHeaders({ 'Content-Type': 'application/json' }),
            body: JSON.stringify({ selected: true }),
          },
        )
        setSelectedConstellationResultId(persisted.id)
        onApplyPlannedSolution(persisted.date, restoredSections, restoredRoute)
        setConstellationSearchStatus(
          `Historische flugfaehige Loesung uebernommen ${new Date(`${persisted.date}T00:00:00Z`).toLocaleDateString('de-DE', { timeZone: 'UTC' })}`
          + ` · Zielrest ${(persisted.targetAlignmentDeg ?? fallbackCandidate.targetAlignmentDeg ?? 0).toFixed(1)}°`,
        )
        if (solverDialogOnly) onSolverDialogClose?.()
      } catch (reason) {
        setConstellationSearchStatus(
          `Historische Variante konnte nicht uebernommen werden: ${reason instanceof Error ? reason.message : String(reason)}`,
        )
      }
      return
    }
    if (!result) {
      setConstellationSearchStatus('Diese Variante ist nicht mehr im aktuellen Ergebnis-Speicher. Bitte den Solverlauf neu laden oder erneut starten.')
      return
    }
    setSelectedConstellationResultId(result.id)
    if (result.flightReady) {
      const runId = result.route.calculationPersistence?.runId
      if (runId) {
        void fetch(
          `/api/calculations/runs/${encodeURIComponent(runId)}/variants/${encodeURIComponent(result.id)}`,
          {
            method: 'PATCH',
            headers: activityRequestHeaders({ 'Content-Type': 'application/json' }),
            body: JSON.stringify({ selected: true }),
          },
        ).catch((reason: unknown) => {
          setConstellationSearchStatus(
            `Auswahl konnte nicht gespeichert werden: ${reason instanceof Error ? reason.message : String(reason)}`,
          )
        })
      }
      onApplyPlannedSolution(result.date, result.sections, result.route)
      if (solverDialogOnly) onSolverDialogClose?.()
    }
    const deficitKmS = Math.max(
      0,
      result.requiredInjectionDeltaVKmS
        + result.targetCorrectionDeltaVKmS
        - result.availableInjectionDeltaVKmS,
    ) + result.corridorInsertionDeficitKmS
    setConstellationSearchStatus(
      `${result.flightReady
        ? 'Flugfähige Lösung übernommen'
        : 'Geometrisch gültige Diagnose · nicht übernommen'} `
      + `${new Date(`${result.date}T00:00:00Z`).toLocaleDateString('de-DE', { timeZone: 'UTC' })}`
      + ` · Δv-Defizit ${deficitKmS.toFixed(2)} km/s`
      + ` · Zielrest ${result.targetAlignmentDeg.toFixed(1)}°`,
    )
    logActivity({
      category: 'calculation',
      action: 'constellation-result-selected',
      status: result.flightReady ? 'success' : 'rejected',
      values: {
        date: result.date,
        variantId: result.id,
        feasible: result.flightReady,
        flightReady: result.flightReady,
        corridorSatisfied: result.corridorSatisfied,
        collisionFree: result.collisionFree,
        requiredInjectionDeltaVKmS: result.requiredInjectionDeltaVKmS,
        availableInjectionDeltaVKmS: result.availableInjectionDeltaVKmS,
        targetCorrectionDeltaVKmS: result.targetCorrectionDeltaVKmS,
        corridorInsertionDeficitKmS: result.corridorInsertionDeficitKmS,
        quality: result.quality,
        deltaVDeficitKmS: deficitKmS,
        targetAlignmentDeg: result.targetAlignmentDeg,
      },
      details: {
        resultId: result.id,
        searchRunId: result.route.calculationPersistence?.runId ?? '',
        feedbackKind: 'user-selected-result',
      },
    })
  }

  useEffect(() => {
    if (!solverDialogOnly || !data || solverAutoStartedRef.current) return
    solverAutoStartedRef.current = true
    if (routeSections.length === 0) {
      setConstellationSearchStatus('Bitte zuerst eine Route mit mindestens einem Abschnitt anlegen.')
      return
    }
    void findBestConstellation()
  }, [data, solverDialogOnly])

  useEffect(() => {
    if (displayOnly && projection === 'corridor') setProjection('top')
  }, [displayOnly, projection])

  if (error) return solverDialogOnly
    ? <div className="solver-route-launcher"><div>{error}<button type="button" onClick={onSolverDialogClose}>Schließen</button></div></div>
    : <div className="status-message">{error}</div>
  if (!data) return solverDialogOnly
    ? <div className="solver-route-launcher"><div>Solver-Auswahl wird vorbereitet …</div></div>
    : <div className="status-message">2D-Orbitalplaner wird geladen …</div>

  if (solverDialogOnly) {
    return (
      <div className="solver-route-launcher">
        {!routeCalculationDialogOpen || !routeCalculationTrace
          ? <div>{constellationSearchStatus || 'Solver wird vorbereitet …'}</div>
          : (
            <RouteCalculationDialog
              trace={routeCalculationTrace}
              availableRuns={calculationRunHistory}
              historyLoading={calculationHistoryLoading}
              onRunSelect={(runId) => void loadPersistedCalculationRun(runId)}
              onClose={() => onSolverDialogClose?.()}
              selectionMode
              selectableCandidateIds={constellationResults.filter((result) => result.flightReady).map((result) => result.id)}
              onCandidateApply={applyConstellationResult}
            />
          )}
      </div>
    )
  }

  const invalidateConstellationResults = () => {
    setConstellationResults([])
    setSelectedConstellationResultId('')
  }
  const updateActiveRouteSection = (update: (section: RouteSectionDefinition) => RouteSectionDefinition) => {
    invalidateConstellationResults()
    onRouteSectionsChange((current) => current.map((section) => (
      section.id === activeRouteSectionId ? update(section) : section
    )))
  }
  const updateEntryCorridor: Dispatch<SetStateAction<EntryCorridorDefinition>> = (action) => {
    updateActiveRouteSection((section) => ({
      ...section,
      corridor: typeof action === 'function' ? action(section.corridor) : action,
    }))
  }
  const createSection = (section: RouteSectionDefinition) => {
    invalidateConstellationResults()
    onRouteSectionsChange((current) => [...current, section])
    onActiveRouteSectionChange(section.id)
  }
  const updateSection = (updatedSection: RouteSectionDefinition) => {
    invalidateConstellationResults()
    onRouteSectionsChange((current) => current.map((section) => (
      section.id === updatedSection.id ? updatedSection : section
    )))
    onActiveRouteSectionChange(updatedSection.id)
  }
  const deleteSection = (sectionId: string) => {
    invalidateConstellationResults()
    const deletedIndex = routeSections.findIndex((section) => section.id === sectionId)
    const nextActiveId = routeSections[deletedIndex + 1]?.id ?? routeSections[deletedIndex - 1]?.id
    onRouteSectionsChange((current) => current.filter((section) => section.id !== sectionId))
    if (sectionId === activeRouteSectionId) onActiveRouteSectionChange(nextActiveId ?? '')
  }
  const moveSection = (sectionId: string, direction: -1 | 1) => {
    invalidateConstellationResults()
    onRouteSectionsChange((current) => {
      const currentIndex = current.findIndex((section) => section.id === sectionId)
      const nextIndex = currentIndex + direction
      if (currentIndex < 0 || nextIndex < 0 || nextIndex >= current.length) return current
      const reordered = [...current]
      ;[reordered[currentIndex], reordered[nextIndex]] = [reordered[nextIndex], reordered[currentIndex]]
      return reordered
    })
  }
  const applyRouteIntent = (sectionId: string, intent: RoutePreviewIntent) => {
    invalidateConstellationResults()
    onRouteSectionsChange((current) => current.map((section) => {
      if (section.id !== sectionId) return section
      const nextPassage = { ...section.passage }
      if (intent === 'asymptotic-entry') {
        nextPassage.entryBehavior = 'ballistic'
        nextPassage.exitBehavior = 'tangential-accelerate'
      }
      if (intent === 'tangential-entry') {
        nextPassage.mode = nextPassage.mode === 'direct' ? 'partial-orbit' : nextPassage.mode
        nextPassage.orbitAngleDeg = nextPassage.orbitAngleDeg > 0 ? nextPassage.orbitAngleDeg : 45
        nextPassage.entryBehavior = 'tangential-prograde'
      }
      if (intent === 'accelerated-exit') {
        nextPassage.exitBehavior = 'tangential-accelerate'
      }
      if (intent === 'braking-entry') {
        nextPassage.entryBehavior = 'tangential-retrograde'
      }
      return {
        ...section,
        passage: nextPassage,
      }
    }))
  }
  const beginOrbitPan = (event: PointerEvent<HTMLDivElement>) => {
    if (event.button !== 0) return
    const plot = event.currentTarget
    orbitPanRef.current = {
      active: true,
      moved: false,
      pointerId: event.pointerId,
      startX: event.clientX,
      startY: event.clientY,
      scrollLeft: plot.scrollLeft,
      scrollTop: plot.scrollTop,
    }
    plot.setPointerCapture(event.pointerId)
    plot.classList.add('panning')
  }
  const moveOrbitPan = (event: PointerEvent<HTMLDivElement>) => {
    const pan = orbitPanRef.current
    if (!pan.active || pan.pointerId !== event.pointerId) return
    const dx = event.clientX - pan.startX
    const dy = event.clientY - pan.startY
    if (Math.hypot(dx, dy) > 3) pan.moved = true
    event.currentTarget.scrollLeft = pan.scrollLeft - dx
    event.currentTarget.scrollTop = pan.scrollTop - dy
  }
  const endOrbitPan = (event: PointerEvent<HTMLDivElement>) => {
    const pan = orbitPanRef.current
    if (!pan.active || pan.pointerId !== event.pointerId) return
    orbitPanRef.current = { ...pan, active: false }
    event.currentTarget.classList.remove('panning')
    if (pan.moved) {
      logActivity({
        category: 'ui',
        action: 'orbit-pan',
        values: {
          scrollLeft: Math.round(event.currentTarget.scrollLeft),
          scrollTop: Math.round(event.currentTarget.scrollTop),
        },
        details: { projection },
      })
    }
    if (event.currentTarget.hasPointerCapture(event.pointerId)) {
      event.currentTarget.releasePointerCapture(event.pointerId)
    }
  }

  return (
    <section className="view-panel two-d-planner" aria-labelledby="two-d-title">
      <div className="view-heading">
        <div>
          <p className="eyebrow">{displayOnly ? '2D Darstellung' : 'Interaktiver Berechnungsbereich'}</p>
          <h1 id="two-d-title">{displayOnly ? 'Das Sonnensystem in 2D' : 'Berechnung & Routenplanung'}</h1>
        </div>
        {!displayOnly && <aside className="ai-chat-panel" aria-label="Interaktiver KI-Chat fuer die 2D-Planung">
          <header>
            <span>KI-Chat</span>
            <small><b className="ai-chat-status">aktiv</b> · {projectionLabel} · {activeRouteSection ? `${activeRouteSection.originId} -> ${activeRouteSection.targetId}` : 'noch keine Route'}</small>
          </header>
          <div className="ai-chat-messages" aria-live="polite">
            {aiChatMessages.map((message) => (
              <div key={message.id} className={`ai-chat-message ${message.role}`}>
                <p>{message.text}</p>
                {message.basedOnSolverRunIds && message.basedOnSolverRunIds.length > 0 && (
                  <small>Basis: Solver-Lauf {message.basedOnSolverRunIds.join(', ')}</small>
                )}
                {message.proposedActions?.map((action, index) => (
                  <button key={`${action.type}-${index}`} type="button" onClick={() => applyAiAction(action)}>
                    Vorschlag übernehmen: {aiActionLabel(action)}
                  </button>
                ))}
                {message.role === 'assistant' && (
                  <button
                    type="button"
                    className="ai-chat-speech-button"
                    disabled={aiSpeechMessageId !== null}
                    onClick={() => void speakAiMessage(message)}
                    aria-label="KI-Antwort vorlesen"
                    title="KI-Antwort vorlesen"
                  >
                    Vorlesen
                  </button>
                )}
                {message.auditRunId && <small>KI-Audit: {message.auditRunId}</small>}
              </div>
            ))}
            {aiChatLoading && <div className="ai-chat-message assistant pending">Interaktions-KI antwortet …</div>}
          </div>
          {aiChatError && <p className="ai-chat-error" role="alert">{aiChatError}</p>}
          {aiAudioStatus && <p className="ai-chat-audio-status" aria-live="polite">{aiAudioStatus}</p>}
          <section
            className={`ai-plausibility-panel ${aiPlausibilityReport?.status ?? 'idle'}`}
            aria-label="Plausibilitaetspruefung"
          >
            <div>
              <span>Plausibilitaet</span>
              <small>
                {aiPlausibilityLoading
                  ? 'Pruefung laeuft...'
                  : aiPlausibilityReport
                    ? `${aiPlausibilityReport.status} · ${aiPlausibilityReport.displaySafe ? 'anzeigesicher' : 'gesperrt'}`
                    : 'noch kein Solver-Lauf'}
              </small>
            </div>
            {aiPlausibilityError && <p role="alert">{aiPlausibilityError}</p>}
            {aiPlausibilityReport && (
              <>
                <ul>
                  {aiPlausibilityReport.findings.slice(0, 3).map((finding) => (
                    <li key={finding.code}>{finding.message}</li>
                  ))}
                </ul>
                {aiPlausibilityReport.requiredFixes.length > 0 && (
                  <small>Fix: {aiPlausibilityReport.requiredFixes[0]}</small>
                )}
                <small>Audit: {aiPlausibilityReport.auditRunId}</small>
              </>
            )}
            {plannedRoute?.audit?.runId && (
              <button
                type="button"
                disabled={aiPlausibilityLoading}
                onClick={() => void runPlausibilityCheck()}
              >
                Erneut pruefen
              </button>
            )}
          </section>
          <section className="ai-calculation-panel" aria-label="Berechnungs-KI">
            <div>
              <span>Berechnung</span>
              <small>
                {aiCalculationLoading
                  ? 'Suchraum wird entworfen...'
                  : aiCalculationSuggestion
                    ? `${aiCalculationSuggestion.proposal.strategy} · ${aiCalculationSuggestion.proposal.candidateSeeds.length} Seeds`
                    : 'Suchraum-KI bereit'}
              </small>
            </div>
            {aiCalculationError && <p role="alert">{aiCalculationError}</p>}
            {aiCalculationSuggestion && (
              <>
                <p>{aiCalculationSuggestion.proposal.expectedImprovement || aiCalculationSuggestion.rationale}</p>
                <ul>
                  {aiCalculationSuggestion.proposal.searchWindows.slice(0, 2).map((window) => (
                    <li key={`${window.label}-${window.startDate}`}>
                      {window.label}: {window.startDate} bis {window.endDate}
                    </li>
                  ))}
                  {aiCalculationSuggestion.proposal.candidateSeeds.slice(0, 2).map((seed) => (
                    <li key={`${seed.startDate}-${seed.routeMode}`}>
                      Seed {seed.startDate} · {seed.routeMode} · Prioritaet {Math.round(seed.priority * 100)}%
                    </li>
                  ))}
                </ul>
                <small>Audit: {aiCalculationSuggestion.auditRunId}</small>
              </>
            )}
            <div className="ai-calculation-actions">
              <button
                type="button"
                disabled={aiCalculationLoading || routeSections.length === 0}
                onClick={() => void requestCalculationSuggestion()}
              >
                Suchraum vorschlagen
              </button>
              <button
                type="button"
                disabled={!aiCalculationSuggestion || constellationSearchRunning}
                onClick={() => {
                  setAiCalculationBiasActive(true)
                  void findBestConstellation()
                }}
              >
                Vorschlag mit Solver pruefen
              </button>
            </div>
          </section>
          <div className="ai-chat-suggestions" aria-label="Chat-Vorschlaege">
            {AI_CHAT_SUGGESTIONS.map((suggestion) => (
              <button key={suggestion} type="button" disabled={aiChatLoading} onClick={() => void sendAiChatMessage(suggestion)}>
                {suggestion}
              </button>
            ))}
          </div>
          <form
            className="ai-chat-form"
            onSubmit={(event) => {
              event.preventDefault()
              void sendAiChatMessage()
            }}
          >
            <input
              type="text"
              value={aiChatInput}
              onChange={(event) => setAiChatInput(event.target.value)}
              placeholder="Frage zur Mission stellen..."
              aria-label="Nachricht an den KI-Chat"
              disabled={aiChatLoading}
            />
            <button
              type="button"
              className="ai-chat-record-button"
              disabled={aiChatLoading}
              onClick={() => aiRecording ? stopAiRecording() : void startAiRecording()}
              aria-pressed={aiRecording}
              aria-label={aiRecording ? 'Audioaufnahme stoppen' : 'Audioaufnahme starten'}
              title={aiRecording ? 'Audioaufnahme stoppen' : 'Audioaufnahme starten'}
            >
              {aiRecording ? 'Stop' : 'Mic'}
            </button>
            <button type="submit" disabled={aiChatLoading}>Senden</button>
          </form>
        </aside>}
      </div>

      <div className="two-d-actionbar" role="toolbar" aria-label="2D-Ansichten">
        <div className="two-d-view-tabs" role="group" aria-label="Projektion">
          {!displayOnly && <button type="button" className={projection === 'corridor' ? 'active' : ''} aria-pressed={projection === 'corridor'} onClick={() => setProjection('corridor')}>Zielkorridor</button>}
          <button type="button" className={projection === 'side' ? 'active' : ''} aria-pressed={projection === 'side'} onClick={() => setProjection('side')}>Kantenansicht · Neigung</button>
          <button type="button" className={projection === 'top' ? 'active' : ''} aria-pressed={projection === 'top'} onClick={() => setProjection('top')}>Draufsicht · Bahnen</button>
        </div>
        {!displayOnly && <button
          type="button"
          className={`retrospective-search-button${retrospectiveSearchEnabled ? ' active' : ''}`}
          aria-pressed={retrospectiveSearchEnabled}
          disabled={constellationSearchRunning}
          onClick={() => setRetrospectiveSearchEnabled((enabled) => !enabled)}
          title="Historisches Missionsdatum bewusst als Start eines Was-wäre-wenn-Szenarios verwenden"
        >
          {retrospectiveSearchEnabled ? 'Rückblick aktiv' : 'Rückblick · Was wäre wenn'}
        </button>}
        {!displayOnly && <button
          type="button"
          className="best-constellation-button"
          disabled={!data || !moonCatalogue || routeSections.length === 0 || constellationSearchRunning}
          onClick={() => void findBestConstellation()}
        >
          {constellationSearchRunning ? 'Konstellationen werden geprüft …' : 'Beste mögliche Konstellation'}
        </button>}
        {!displayOnly && routeCalculationTrace
          ? (
            <button
              type="button"
              className="route-calculation-open-button"
              onClick={() => setRouteCalculationDialogOpen(true)}
            >
              {routeCalculationTrace.running
                ? `Analyse · ${Math.round(routeCalculationTrace.progressPercent ?? 0)}%`
                : routeCalculationTrace.error
                  ? 'Analyse · Fehler'
              : `Analyse · fertig (${routeCalculationTrace.candidates.length})`}
            </button>
          )
          : null}
        {!displayOnly && constellationResults.length > 0 && (
          <label className="constellation-result-select">
            <span>Variantenanalyse</span>
            <select
              aria-label="Geprüfte Konstellation auswählen"
              value={selectedConstellationResultId}
              onChange={(event) => applyConstellationResult(event.target.value)}
            >
              {constellationResults.map((result, index) => {
                const deficitKmS = Math.max(
                  0,
                  result.requiredInjectionDeltaVKmS
                    + result.targetCorrectionDeltaVKmS
                    - result.availableInjectionDeltaVKmS,
                ) + result.corridorInsertionDeficitKmS
                return (
                  <option key={result.id} value={result.id}>
                    {index + 1} · {new Date(`${result.date}T00:00:00Z`).toLocaleDateString('de-DE', { timeZone: 'UTC' })}
                    {' · '}{result.flightReady
                      ? 'flugfähig'
                      : result.hypotheticalInterstellarAsymptote
                        ? 'hypothetische Zielrichtung · 50 AE'
                        : `nicht flugfähig · Δv −${deficitKmS.toFixed(1)}`}
                  </option>
                )
              })}
            </select>
          </label>
        )}
        {(projection === 'side' || projection === 'top') && (
          <>
            <div className="orbit-zoom-control">
              <span>Zoom</span>
              <button
                type="button"
                aria-label="Zoom verkleinern"
                onClick={() => setOrbitZoom((value) => Math.max(1, Number((value - 0.2).toFixed(1))))}
              >
                -
              </button>
              <input
                type="range"
                min="1"
                max="5"
                step="0.1"
                value={orbitZoom}
                onChange={(event) => setOrbitZoom(event.target.valueAsNumber)}
              />
              <button
                type="button"
                aria-label="Zoom vergrößern"
                onClick={() => setOrbitZoom((value) => Math.min(5, Number((value + 0.2).toFixed(1))))}
              >
                +
              </button>
              <output>{Math.round(orbitZoom * 100)}%</output>
            </div>
            <output className={plannedMissionDate ? 'mission-epoch' : ''}>{epochLabel}</output>
          </>
        )}
      </div>
      {!displayOnly && constellationSearchStatus && <p className="constellation-search-status">{constellationSearchStatus}</p>}

      {projection === 'corridor' && !displayOnly
        ? (
          <div className="route-section-planner">
            {activeRouteSection
              ? <PlanetCorridorPlanner
                  planets={data.planets}
                  moons={moonCatalogue?.moons ?? []}
                  sun={data.sun}
                  originId={activeRouteSection.originId}
                  onOriginChange={(originId) => updateActiveRouteSection((section) => ({ ...section, originId }))}
                  waypointId={activeRouteSection.targetId}
                  onWaypointChange={(targetId) => updateActiveRouteSection((section) => ({ ...section, targetId }))}
                  definition={activeRouteSection.corridor}
                  onDefinitionChange={updateEntryCorridor}
                  deltaVMinusKmS={activeRouteSection.deltaVMinusKmS}
                  deltaVPlusKmS={activeRouteSection.deltaVPlusKmS}
                  onDeltaVMinusChange={(deltaVMinusKmS) => updateActiveRouteSection((section) => ({ ...section, deltaVMinusKmS }))}
                  onDeltaVPlusChange={(deltaVPlusKmS) => updateActiveRouteSection((section) => ({ ...section, deltaVPlusKmS }))}
                  sectionNumber={routeSections.findIndex((section) => section.id === activeRouteSectionId) + 1}
                  passage={activeRouteSection.passage}
                  sunToTargetDirection={sunToActiveTargetDirection}
                  actualEntryDirection={calculatedActiveRouteSection?.entryDirection ?? null}
                  entryFlightDirection={entryFlightDirection}
                  exitRadialDirection={calculatedActiveRouteSection?.corridor.exitAngleSelection?.desiredExitRadialDirection ?? null}
                  exitFlightDirection={exitFlightDirection}
                  passageNormalDirection={calculatedActiveRouteSection?.corridor.exitAngleSelection?.optimizedPassageNormal ?? null}
                  entrySourceName={routeObjectName(activeRouteSection.originId, data.planets, moonCatalogue?.moons ?? [])}
                  exitTargetName={linkedNextRouteSection
                    ? routeObjectName(linkedNextRouteSection.targetId, data.planets, moonCatalogue?.moons ?? [])
                    : null}
                  epochLabel={epochLabel}
                />
              : (
                <div className="route-project-empty" role="status">
                  <strong>Blanko-Projekt</strong>
                  <span>Noch keine Verbindung angelegt. Erstelle den ersten unabhängigen Routenabschnitt mit „+ Neu“.</span>
                </div>
              )}
            <RouteSectionList
              planets={data.planets}
              moons={moonCatalogue?.moons ?? []}
              sections={routeSections}
              activeSectionId={activeRouteSectionId}
              suggestedOriginId=""
              suggestedTargetId=""
              onCreate={createSection}
              onUpdate={updateSection}
              onEdit={onActiveRouteSectionChange}
              onPreview={setPreviewSectionId}
              onDelete={deleteSection}
              onMove={moveSection}
            />
          </div>
        )
        : (
          <>
            <div className="two-d-orbit-workspace">
              <div
                ref={orbitPlotRef}
                className={`plot-frame orbital-plot ${projection}`}
                style={{ '--orbit-zoom-width': `${orbitZoom * 100}%` } as CSSProperties}
                onPointerDown={beginOrbitPan}
                onPointerMove={moveOrbitPan}
                onPointerUp={endOrbitPan}
                onPointerCancel={endOrbitPan}
                onPointerLeave={endOrbitPan}
              >
                <svg
                  viewBox={viewBox}
                  role="group"
                  aria-label={orbitalProjection === 'top' ? 'Draufsicht der tatsächlichen Planetenbahnen' : 'Kantenansicht der tatsächlichen Bahnneigungen'}
                >
                  <defs>
                    <marker id="interstellar-direction-arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="5" markerHeight="5" orient="auto-start-reverse">
                      <path d="M 0 0 L 10 5 L 0 10 z" />
                    </marker>
                  </defs>
                  <rect x={-EXTENT} y={orbitalProjection === 'top' ? -EXTENT : -SIDE_HALF_HEIGHT} width={EXTENT * 2} height={orbitalProjection === 'top' ? EXTENT * 2 : SIDE_HALF_HEIGHT * 2} className="orbital-background" />
                  <line x1={-EXTENT} y1="0" x2={EXTENT} y2="0" className="ecliptic-line" />

                  {orbitalProjection === 'top' && orbitGeometry.map(({ planet, orbit }) => (
                    <path key={`orbit-${planet.id}`} d={pathFromPoints(orbit)} className="planet-orbit-path" style={{ stroke: planet.color }} />
                  ))}
                  {plannedRoutePoints.length > 1 && (
                    <path
                      d={pathFromPoints(plannedRoutePoints, orbitalProjection)}
                      className={`planned-route-path-2d${plannedRoutePlausibilitySafe ? ' feasible' : ' proposal'}`}
                    />
                  )}
                  {solarPassagePlot && (
                    <g className={`solar-passage-state-2d${solarPassagePlot.outbound ? '' : ' blocked'}`}>
                      <path d={solarPassagePlot.entryCorridorPath} className="solar-corridor entry" />
                      <path d={solarPassagePlot.exitCorridorPath} className="solar-corridor exit" />
                      <circle cx={solarPassagePlot.entry.x} cy={solarPassagePlot.entry.y} r=".17" className="solar-state entry" />
                      <circle cx={solarPassagePlot.periapsis.x} cy={solarPassagePlot.periapsis.y} r=".17" className="solar-state periapsis" />
                      <circle cx={solarPassagePlot.exit.x} cy={solarPassagePlot.exit.y} r=".17" className="solar-state exit" />
                      <text
                        x={solarPassagePlot.entryLabel.x}
                        y={solarPassagePlot.entryLabel.y}
                        textAnchor={solarPassagePlot.entryLabel.anchor}
                      >
                        Eintritt
                      </text>
                      <text
                        x={solarPassagePlot.exitLabel.x}
                        y={solarPassagePlot.exitLabel.y}
                        textAnchor={solarPassagePlot.exitLabel.anchor}
                      >
                        Austritt
                      </text>
                    </g>
                  )}
                  {!plannedRoute && routeSketchSegments.length > 0 && (
                    <g className="route-sketch-layer-2d">
                      {routeSketchSegments.map((segment) => (
                        <g key={`route-sketch-${segment.id}`}>
                          {segment.passageCorridors && (
                            <g className="route-sketch-corridors">
                              <path d={segment.passageCorridors.entryArc} className="entry" />
                              <path d={segment.passageCorridors.exitArc} className="exit" />
                            </g>
                          )}
                          <path
                            d={segment.path}
                            className={[
                              'route-sketch-path',
                              segment.hasPassageArc ? 'passage-arc' : '',
                              segment.outOfPlane ? 'out-of-plane' : '',
                            ].filter(Boolean).join(' ')}
                          />
                          <circle cx={segment.origin.x} cy={segment.origin.y} r=".16" className="route-sketch-node origin" />
                          <circle cx={segment.target.x} cy={segment.target.y} r=".18" className="route-sketch-node target" />
                          {segment.passageCorridors && (
                            <>
                              <circle
                                cx={segment.passageCorridors.entryPoint.x}
                                cy={segment.passageCorridors.entryPoint.y}
                                r=".11"
                                className="route-sketch-corridor-point entry"
                              />
                              <circle
                                cx={segment.passageCorridors.exitPoint.x}
                                cy={segment.passageCorridors.exitPoint.y}
                                r=".11"
                                className="route-sketch-corridor-point exit"
                              />
                            </>
                          )}
                        </g>
                      ))}
                    </g>
                  )}
                  {interstellarDirectionMarker && (
                    <g className="interstellar-direction-marker draft">
                      <line
                        x1={interstellarDirectionMarker.origin.x}
                        y1={interstellarDirectionMarker.origin.y}
                        x2={interstellarDirectionMarker.endpoint.x}
                        y2={interstellarDirectionMarker.endpoint.y}
                        markerEnd="url(#interstellar-direction-arrow)"
                      />
                      <text
                        x={interstellarDirectionMarker.endpoint.x + (interstellarDirectionMarker.direction.x >= 0 ? -.4 : .4)}
                        y={interstellarDirectionMarker.endpoint.y + (interstellarDirectionMarker.direction.y >= 0 ? -.45 : .65)}
                        textAnchor={interstellarDirectionMarker.direction.x >= 0 ? 'end' : 'start'}
                      >
                        Austritt → {interstellarDirectionMarker.targetName} · {interstellarDirectionMarker.source}
                      </text>
                    </g>
                  )}

                  <circle cx="0" cy="0" r="0.45" className="two-d-sun" />
                  {orbitalProjection === 'top' && <text x="0.7" y="-0.65" className="orbital-label">Sonne · Ekliptik 0°</text>}

                  {orbitGeometry.map(({ planet, position }, index) => {
                    const [x, y] = project(position, orbitalProjection)
                    const isSelected = planet.id === selectedPlanet?.id
                    const labelY = SIDE_LABEL_Y[index] ?? y
                    const markerRadius = planet.id === 'jupiter' || planet.id === 'saturn' ? 0.3 : 0.2
                    const selectPlanet = () => setSelectedPlanetId(planet.id)
                    return (
                      <g
                        key={`planet-${planet.id}`}
                        className={`two-d-planet-target ${isSelected ? 'selected' : ''}`}
                        role="button"
                        tabIndex={0}
                        aria-label={`${planet.name} auswählen`}
                        aria-pressed={isSelected}
                        onClick={(event) => {
                          if (orbitPanRef.current.moved) {
                            event.preventDefault()
                            event.stopPropagation()
                            orbitPanRef.current.moved = false
                            return
                          }
                          selectPlanet()
                        }}
                        onKeyDown={(event) => {
                          if (event.key === 'Enter' || event.key === ' ') {
                            event.preventDefault()
                            selectPlanet()
                          }
                        }}
                      >
                        <circle cx={x} cy={y} r="0.62" className="planet-hit-target" />
                        {isSelected && <circle cx={x} cy={y} r={markerRadius + 0.24} className="planet-selection-ring" />}
                        <circle cx={x} cy={y} r={markerRadius} fill={planet.color} className="two-d-planet" />
                        {orbitalProjection === 'top' && <text x={x + 0.38} y={y - 0.35} className="orbital-label">{planet.name}</text>}
                        {orbitalProjection === 'side' && (
                          <>
                            <line x1={x} y1={y} x2={x} y2={labelY} className="planet-label-leader" />
                            <text x={x + 0.28} y={labelY} className="orbital-label side-label">
                              {planet.name} · {(planet.inclinationDeg ?? 0).toFixed(1)}°
                            </text>
                          </>
                        )}
                      </g>
                    )
                  })}
                  {plannedRoute?.routeSections?.map((section) => {
                    const periapsis = plannedRoutePoints[section.periapsisIndex]
                    if (!periapsis || section.sectionType === 'interstellar-asymptote') return null
                    const [periapsisX, periapsisY] = project(periapsis, orbitalProjection)
                    return (
                      <g key={`route-state-${section.id}`} className="planned-route-states-2d">
                        <circle cx={periapsisX} cy={periapsisY} r=".2" className="periapsis" />
                      </g>
                    )
                  })}
                </svg>
              </div>

              {selectedPlanet && (
                <TwoDPlanetDetails
                  planet={selectedPlanet}
                  planets={data.planets}
                  moons={selectedMoons}
                  epochLabel={epochLabel}
                  onPlanetChange={setSelectedPlanetId}
                />
              )}
            </div>
            {!plannedRoute && routeSketchSegments.some((segment) => segment.passageCorridors) && (
              <div className="route-corridor-projection-legend" aria-label="Legende der projizierten Korridore">
                <span className="entry">Eintrittskorridor</span>
                <span className="exit">Austrittskorridor</span>
              </div>
            )}
            <p className="two-d-footnote">Kantenansicht: aktuelle Planetenpositionen gegen Ekliptikhöhe · Draufsicht: aktuelle Positionen auf realen J2000-Bahnen · Korridorpassagen sind zur Lesbarkeit schematisch vergrößert.</p>
          </>
        )}
      {previewSection && (
        <RoutePreviewDialog
          section={previewSection}
          calculatedSection={plannedRoute?.routeSections?.find((section) => section.id === previewSection.id) ?? null}
          previousSection={previewPreviousSection}
          nextSection={previewNextSection}
          planets={data.planets}
          moons={moonCatalogue?.moons ?? []}
          onClose={() => setPreviewSectionId(null)}
          onApply={(intent) => applyRouteIntent(previewSection.id, intent)}
        />
      )}
      {routeCalculationDialogOpen && routeCalculationTrace
        ? (
          <RouteCalculationDialog
            trace={routeCalculationTrace}
            availableRuns={calculationRunHistory}
            historyLoading={calculationHistoryLoading}
            onRunSelect={(runId) => void loadPersistedCalculationRun(runId)}
            onClose={() => setRouteCalculationDialogOpen(false)}
            selectableCandidateIds={constellationResults.filter((result) => result.flightReady).map((result) => result.id)}
            onCandidateApply={applyConstellationResult}
          />
        )
        : null}
    </section>
  )
}

type RoutePreviewIntent = 'asymptotic-entry' | 'tangential-entry' | 'accelerated-exit' | 'braking-entry'

const BEHAVIOR_LABELS: Record<RouteBoundaryBehavior, string> = {
  ballistic: 'ballistisch / asymptotisch',
  'tangential-prograde': 'tangential prograd',
  'tangential-retrograde': 'tangential retrograd',
  'tangential-accelerate': 'tangential beschleunigen',
  radial: 'radial',
}

function routeObjectName(objectId: string, planets: SolarSystemData['planets'], moons: MoonCatalogue['moons']) {
  if (objectId === 'sun') return 'Sonne'
  return planets.find((planet) => planet.id === objectId)?.name
    ?? moons.find((moon) => moon.id === objectId)?.name
    ?? ROUTE_INTERSTELLAR_SYSTEMS.find((target) => target.id === objectId)?.name
    ?? objectId
}

function isInterstellarRouteObject(objectId: string) {
  return ROUTE_INTERSTELLAR_SYSTEMS.some((target) => target.id === objectId)
}

type PreviewPoint = { x: number; y: number }
type CalculatedRouteSection = NonNullable<WaypointRouteResult['routeSections']>[number]

function pointOnCircle(center: PreviewPoint, radius: number, angleRad: number): PreviewPoint {
  return {
    x: center.x + Math.cos(angleRad) * radius,
    y: center.y + Math.sin(angleRad) * radius,
  }
}

function previewEntryAngle(section: RouteSectionDefinition) {
  const [x, y, z] = section.corridor.centerDirection
  const projectedY = (section.corridor.mainProjection ?? 'side') === 'top' ? y : z
  const length = Math.hypot(x, projectedY)
  if (length <= 0.0001) return Math.PI
  return Math.atan2(projectedY, x) + Math.PI
}

function previewPointOnRay(center: PreviewPoint, angleRad: number, distance: number): PreviewPoint {
  return {
    x: center.x + Math.cos(angleRad) * distance,
    y: center.y + Math.sin(angleRad) * distance,
  }
}

function mixPoint(start: PreviewPoint, end: PreviewPoint, t: number): PreviewPoint {
  return {
    x: start.x + (end.x - start.x) * t,
    y: start.y + (end.y - start.y) * t,
  }
}

function normalControlPoint(start: PreviewPoint, end: PreviewPoint, t: number, offset: number): PreviewPoint {
  const base = mixPoint(start, end, t)
  const dx = end.x - start.x
  const dy = end.y - start.y
  const length = Math.hypot(dx, dy) || 1
  return {
    x: base.x - dy / length * offset,
    y: base.y + dx / length * offset,
  }
}

function scaleVector(vector: PreviewPoint, factor: number): PreviewPoint {
  return { x: vector.x * factor, y: vector.y * factor }
}

function addPoint(point: PreviewPoint, vector: PreviewPoint): PreviewPoint {
  return { x: point.x + vector.x, y: point.y + vector.y }
}

function radialVector(angleRad: number): PreviewPoint {
  return { x: Math.cos(angleRad), y: Math.sin(angleRad) }
}

function normalizePreviewVector(vector: PreviewPoint) {
  const length = Math.hypot(vector.x, vector.y) || 1
  return { x: vector.x / length, y: vector.y / length }
}

function clampPointToCanvas(point: PreviewPoint, margin = 68): PreviewPoint {
  return {
    x: Math.min(970 - margin, Math.max(margin, point.x)),
    y: Math.min(600 - margin, Math.max(margin, point.y)),
  }
}

function previewRayEndpoint(origin: PreviewPoint, direction: PreviewPoint, margin = 68) {
  const normalizedDirection = normalizePreviewVector(direction)
  const minX = margin
  const maxX = 970 - margin
  const minY = margin
  const maxY = 600 - margin
  const distances = [
    normalizedDirection.x > 0.0001 ? (maxX - origin.x) / normalizedDirection.x : Number.POSITIVE_INFINITY,
    normalizedDirection.x < -0.0001 ? (minX - origin.x) / normalizedDirection.x : Number.POSITIVE_INFINITY,
    normalizedDirection.y > 0.0001 ? (maxY - origin.y) / normalizedDirection.y : Number.POSITIVE_INFINITY,
    normalizedDirection.y < -0.0001 ? (minY - origin.y) / normalizedDirection.y : Number.POSITIVE_INFINITY,
  ].filter((distance) => distance > 0)
  const distance = Math.min(...distances)
  return addPoint(origin, scaleVector(normalizedDirection, Number.isFinite(distance) ? distance : 0))
}

function interstellarPreviewDirection(targetId: string, projection: EntryCorridorDefinition['mainProjection']): PreviewPoint | null {
  const direction = interstellarTargetDirection(targetId)
  if (!direction) return null
  const [x, y, z] = direction
  return normalizePreviewVector({ x, y: (projection ?? 'side') === 'top' ? -y : -z })
}

function projectedPreviewDirection(
  direction: [number, number, number],
  projection: EntryCorridorDefinition['mainProjection'],
) {
  const [x, y, z] = direction
  return normalizePreviewVector({ x, y: (projection ?? 'side') === 'top' ? -y : -z })
}

function nextTargetPreviewVector(
  section: RouteSectionDefinition,
  nextSection: RouteSectionDefinition | null,
  calculatedSection: CalculatedRouteSection | null,
) {
  const calculatedDirection = calculatedSection?.corridor.exitAngleSelection?.desiredExitDirection
  if (calculatedDirection) {
    return projectedPreviewDirection(calculatedDirection, section.corridor.mainProjection)
  }
  if (!nextSection || nextSection.originId !== section.targetId) return normalizePreviewVector({ x: 1, y: 0 })
  if (nextSection.targetId === section.originId) return normalizePreviewVector({ x: -1, y: 0 })
  const stellarDirection = interstellarPreviewDirection(nextSection.targetId, section.corridor.mainProjection)
  if (stellarDirection) return stellarDirection
  const [x, y, z] = nextSection.corridor.centerDirection
  const projectedY = (section.corridor.mainProjection ?? 'side') === 'top' ? -y : -z
  const projected = normalizePreviewVector({ x, y: projectedY })
  return Math.hypot(projected.x, projected.y) > 0.0001 ? projected : normalizePreviewVector({ x: 1, y: 0 })
}

function tangentVector(angleRad: number, directionSign: number): PreviewPoint {
  return {
    x: -Math.sin(angleRad) * directionSign,
    y: Math.cos(angleRad) * directionSign,
  }
}

function sampledArcPath(center: PreviewPoint, radius: number, startAngle: number, endAngle: number) {
  const angleSpan = Math.abs(endAngle - startAngle)
  const steps = Math.max(8, Math.ceil(angleSpan / (Math.PI / 18)))
  return Array.from({ length: steps + 1 }, (_, index) => {
    const t = index / steps
    const point = pointOnCircle(center, radius, startAngle + (endAngle - startAngle) * t)
    return `${index === 0 ? 'M' : 'L'} ${point.x.toFixed(2)} ${point.y.toFixed(2)}`
  }).join(' ')
}

function routePreviewGeometry(
  section: RouteSectionDefinition,
  nextSection: RouteSectionDefinition | null,
  calculatedSection: CalculatedRouteSection | null,
) {
  const center = { x: 635, y: 310 }
  const hasLinkedExit = nextSection?.originId === section.targetId
  const isInterstellarTarget = isInterstellarRouteObject(section.targetId)
  const radius = 160
  const corridorRadius = 190
  const maxCorridorRadius = 214
  const minCorridorRadius = 166
  const innerCorridorRadius = 158
  const entryAngle = previewEntryAngle(section)
  const isFullOrbit = section.passage.mode === 'full-orbit'
  const requestedOrbitDeg = isFullOrbit
    ? 360
    : section.passage.mode === 'partial-orbit'
      ? clamp(section.passage.orbitAngleDeg, 1, MAX_PARTIAL_ORBIT_ANGLE_DEG)
      : 0
  const directionSign = section.passage.orbitDirection === 'prograde' ? 1 : -1
  const directExitAngle = entryAngle
  const exitVector = nextTargetPreviewVector(section, nextSection, calculatedSection)

  if (isInterstellarTarget) {
    const transitVector = normalizePreviewVector(scaleVector(radialVector(entryAngle), -1))
    const normalVector = { x: -transitVector.y, y: transitVector.x }
    const interstellarExitTarget = hasLinkedExit
      ? clampPointToCanvas(addPoint(center, scaleVector(exitVector, 348)))
      : clampPointToCanvas(addPoint(center, scaleVector(transitVector, 348)))
    const entry = addPoint(center, scaleVector(transitVector, -corridorRadius))
    const exit = addPoint(center, scaleVector(transitVector, corridorRadius))
    const entryInner = addPoint(center, scaleVector(transitVector, -innerCorridorRadius))
    const exitInner = addPoint(center, scaleVector(transitVector, innerCorridorRadius))
    const minStart = addPoint(entry, scaleVector(normalVector, -16))
    const minEnd = addPoint(exit, scaleVector(normalVector, -16))
    const maxStart = addPoint(entry, scaleVector(normalVector, 16))
    const maxEnd = addPoint(exit, scaleVector(normalVector, 16))
    const requestedExitAngle = Math.atan2(transitVector.y, transitVector.x)

    return {
      center,
      origin: { x: 105, y: center.y },
      exitTarget: interstellarExitTarget,
      targetHeadingEndpoint: interstellarExitTarget,
      hasLinkedExit,
      exitVector,
      exitAngleDeg: positiveAngleDeg(requestedExitAngle * 180 / Math.PI),
      requestedOrbitDeg,
      selectedOrbitDeg: requestedOrbitDeg,
      autoExtendedOrbitDeg: 0,
      selectionSource: 'Vorgabe',
      lineOfSightClear: true,
      bestApproximation: false,
      radius,
      entry,
      exit,
      entryInner,
      exitInner,
      entryPath: `M 105 ${center.y} L ${entry.x.toFixed(2)} ${entry.y.toFixed(2)}`,
      minBoundaryPath: `M ${minStart.x.toFixed(2)} ${minStart.y.toFixed(2)} L ${minEnd.x.toFixed(2)} ${minEnd.y.toFixed(2)}`,
      maxBoundaryPath: `M ${maxStart.x.toFixed(2)} ${maxStart.y.toFixed(2)} L ${maxEnd.x.toFixed(2)} ${maxEnd.y.toFixed(2)}`,
      passagePath: `M ${entry.x.toFixed(2)} ${entry.y.toFixed(2)} L ${exit.x.toFixed(2)} ${exit.y.toFixed(2)}`,
      exitPath: `M ${exit.x.toFixed(2)} ${exit.y.toFixed(2)} L ${interstellarExitTarget.x.toFixed(2)} ${interstellarExitTarget.y.toFixed(2)}`,
      corridorPath: `M ${minStart.x.toFixed(2)} ${minStart.y.toFixed(2)} L ${minEnd.x.toFixed(2)} ${minEnd.y.toFixed(2)} L ${maxEnd.x.toFixed(2)} ${maxEnd.y.toFixed(2)} L ${maxStart.x.toFixed(2)} ${maxStart.y.toFixed(2)} Z`,
    }
  }

  const linkedExitAngle = directionSign > 0
    ? Math.atan2(-exitVector.x, exitVector.y)
    : Math.atan2(exitVector.x, -exitVector.y)
  let targetedOrbitDeg = positiveAngleDeg(
    directionSign * (linkedExitAngle - entryAngle) * 180 / Math.PI,
  )
  while (targetedOrbitDeg + 0.0001 < requestedOrbitDeg) targetedOrbitDeg += 360
  const calculatedSelection = calculatedSection?.corridor.exitAngleSelection
  const solverSelectedOrbitDeg = Math.abs(
    calculatedSection?.selectedPassageAngleDeg
      ?? calculatedSelection?.selectedAngleDeg
      ?? 0,
  )
  const selectedOrbitDeg = section.passage.mode === 'direct'
    ? 0
    : hasLinkedExit && solverSelectedOrbitDeg + 0.0001 >= requestedOrbitDeg
      ? solverSelectedOrbitDeg
      : hasLinkedExit
        ? targetedOrbitDeg
        : requestedOrbitDeg
  const selectedExitAngle = entryAngle + directionSign * (selectedOrbitDeg * Math.PI / 180)
  const exitAngle = section.passage.mode === 'direct' ? directExitAngle : selectedExitAngle
  const corridorEndAngle = exitAngle
  const origin = { x: 105, y: center.y }
  const entry = pointOnCircle(center, corridorRadius, entryAngle)
  const exit = pointOnCircle(center, corridorRadius, exitAngle)
  const entryInner = pointOnCircle(center, innerCorridorRadius, entryAngle)
  const exitInner = pointOnCircle(center, innerCorridorRadius, exitAngle)
  const entryTangent = tangentVector(entryAngle, directionSign)
  const exitTangent = tangentVector(exitAngle, directionSign)
  const targetHeadingEndpoint = hasLinkedExit
    ? previewRayEndpoint(center, exitVector)
    : { x: 925, y: center.y }
  const exitTarget = hasLinkedExit
    ? previewRayEndpoint(exit, exitTangent)
    : targetHeadingEndpoint
  const asymptoteLift = section.passage.entryBehavior.includes('tangential') ? 0 : 10
  const entryControlA = { x: origin.x + 210, y: center.y + asymptoteLift }
  const entryControlB = addPoint(entry, scaleVector(entryTangent, -132))
  const exitStart = exit
  const exitControlA = addPoint(exitStart, scaleVector(exitTangent, 132))
  const exitControlB = addPoint(
    addPoint(exitTarget, scaleVector(exitTangent, -120)),
    scaleVector(radialVector(exitAngle), 24),
  )
  const optimumPath = section.passage.mode === 'direct'
    ? `M ${entry.x.toFixed(2)} ${entry.y.toFixed(2)} L ${exit.x.toFixed(2)} ${exit.y.toFixed(2)}`
    : sampledArcPath(center, corridorRadius, entryAngle, corridorEndAngle)
  const minBoundaryPath = sampledArcPath(center, minCorridorRadius, entryAngle, corridorEndAngle)
  const maxBoundaryPath = sampledArcPath(center, maxCorridorRadius, entryAngle, corridorEndAngle)
  const outerPath = maxBoundaryPath
  const innerPath = sampledArcPath(center, innerCorridorRadius, corridorEndAngle, entryAngle).replace(/^M/, 'L')
  return {
    center,
    origin,
    exitTarget,
    targetHeadingEndpoint,
    hasLinkedExit,
    exitVector,
    exitAngleDeg: positiveAngleDeg(exitAngle * 180 / Math.PI),
    requestedOrbitDeg,
    selectedOrbitDeg,
    autoExtendedOrbitDeg: Math.max(0, selectedOrbitDeg - requestedOrbitDeg),
    selectionSource: solverSelectedOrbitDeg > 0 ? 'Solver' : hasLinkedExit ? 'Vorschau' : 'Vorgabe',
    lineOfSightClear: calculatedSelection?.lineOfSightClear ?? true,
    bestApproximation: calculatedSelection?.bestApproximation ?? false,
    radius,
    entry,
    exit: exitStart,
    entryInner,
    exitInner,
    entryPath: `M ${origin.x} ${origin.y} C ${entryControlA.x} ${entryControlA.y}, ${entryControlB.x} ${entryControlB.y}, ${entry.x} ${entry.y}`,
    minBoundaryPath,
    maxBoundaryPath,
    passagePath: optimumPath,
    exitPath: `M ${exitStart.x} ${exitStart.y} C ${exitControlA.x} ${exitControlA.y}, ${exitControlB.x} ${exitControlB.y}, ${exitTarget.x} ${exitTarget.y}`,
    corridorPath: `${outerPath} ${innerPath} Z`,
  }
}

function RoutePreviewDialog({
  section,
  calculatedSection,
  previousSection,
  nextSection,
  planets,
  moons,
  onClose,
  onApply,
}: {
  section: RouteSectionDefinition
  calculatedSection: CalculatedRouteSection | null
  previousSection: RouteSectionDefinition | null
  nextSection: RouteSectionDefinition | null
  planets: SolarSystemData['planets']
  moons: MoonCatalogue['moons']
  onClose: () => void
  onApply: (intent: RoutePreviewIntent) => void
}) {
  const dialogRef = useRef<HTMLDialogElement>(null)
  const [instruction, setInstruction] = useState('')

  useEffect(() => {
    const dialog = dialogRef.current
    if (!dialog) return undefined
    dialog.showModal()
    return () => {
      if (dialog.open) dialog.close()
    }
  }, [])

  const originName = routeObjectName(section.originId, planets, moons)
  const targetName = routeObjectName(section.targetId, planets, moons)
  const hasRouteContext = Boolean(previousSection || nextSection)
  const exitName = nextSection?.originId === section.targetId
    ? routeObjectName(nextSection.targetId, planets, moons)
    : hasRouteContext
      ? 'Routenende'
      : 'freier Austritt'
  const preview = routePreviewGeometry(section, nextSection, calculatedSection)
  const exitLeadsToInterstellarTarget = Boolean(
    nextSection?.originId === section.targetId
      && isInterstellarRouteObject(nextSection.targetId),
  )
  const projectedTargetAxis = preview.exitVector.x < -0.0001
    ? '-x'
    : preview.exitVector.x > 0.0001
      ? '+x'
      : 'x=0'
  const passageText = section.passage.mode === 'full-orbit'
    ? 'volle Umrundung'
    : section.passage.mode === 'partial-orbit'
      ? `Teilumrundung ${section.passage.orbitAngleDeg.toFixed(0)}°`
      : 'direkte Passage'
  const applyInstruction = () => {
    const normalized = instruction.toLocaleLowerCase('de-DE')
    if (normalized.includes('asym')) onApply('asymptotic-entry')
    if (normalized.includes('tangent')) onApply('tangential-entry')
    if (normalized.includes('beschleun') || normalized.includes('erhöh')) onApply('accelerated-exit')
    if (normalized.includes('brems') || normalized.includes('retro')) onApply('braking-entry')
  }

  return (
    <dialog
      ref={dialogRef}
      className="route-preview-dialog"
      aria-labelledby="route-preview-title"
      onCancel={(event) => {
        event.preventDefault()
        onClose()
      }}
    >
      <header>
        <div>
          <small>Abschnittsvorschau · Quelle für 3D</small>
          <h2 id="route-preview-title">{originName} → {targetName}</h2>
        </div>
        <button type="button" className="wizard-close" aria-label="Vorschau schließen" onClick={onClose}>×</button>
      </header>
      <div className="route-preview-content">
        <svg viewBox="0 0 1000 620" className="route-preview-canvas editor-layout" role="img" aria-label="Vorschau vom Ursprung bis Austritt">
          <defs>
            <marker id="route-preview-arrow" markerWidth="8" markerHeight="8" refX="7" refY="4" orient="auto">
              <path d="M 0 0 L 8 4 L 0 8 Z" />
            </marker>
          </defs>
          <g className="corridor-coordinate-system" aria-hidden="true">
            <line x1="42" y1={preview.center.y} x2="965" y2={preview.center.y} className="coordinate-axis reference-plane" markerEnd="url(#route-preview-arrow)" />
            <line x1={preview.center.x} y1="586" x2={preview.center.x} y2="34" className="coordinate-axis" markerEnd="url(#route-preview-arrow)" />
            <text x="954" y={preview.center.y - 13} textAnchor="end" className="coordinate-axis-label">+x · Arbeitsachse</text>
            <text x={preview.center.x + 14} y="52" className="coordinate-axis-label">+z / +y · Korridorprojektion</text>
          </g>
          <text x="24" y="30" className="preview-note">Logische Abschnittsskizze · nicht maßstabsgetreu</text>
          <text x="24" y="50" className="preview-note">Startreferenz bleibt fix; Loop zeigt lokale Passage am Zielkörper</text>
          <circle cx={preview.origin.x} cy={preview.origin.y} r="22" className="preview-origin" />
          <text x={preview.origin.x} y={preview.origin.y + 44} textAnchor="middle">{originName}</text>
          <circle cx={preview.center.x} cy={preview.center.y} r={preview.radius} className="preview-target" />
          <text x={preview.center.x} y={preview.center.y + 7} textAnchor="middle">{targetName}</text>
          <text x={preview.center.x} y={preview.center.y + preview.radius + 28} textAnchor="middle" className="preview-note">lokal um {targetName}</text>
          <path d={preview.corridorPath} className="preview-corridor" />
          <path d={preview.minBoundaryPath} className="preview-min-boundary" />
          <path d={preview.maxBoundaryPath} className="preview-max-boundary" />
          <path d={preview.entryPath} className="preview-entry-path" />
          <path d={preview.passagePath} className="preview-passage-path" />
          {preview.hasLinkedExit && (
            <line
              x1={preview.center.x}
              y1={preview.center.y}
              x2={preview.targetHeadingEndpoint.x}
              y2={preview.targetHeadingEndpoint.y}
              className="preview-target-heading"
              markerEnd="url(#route-preview-arrow)"
            />
          )}
          <path d={preview.exitPath} className="preview-exit-path" />
          <circle cx={preview.entry.x} cy={preview.entry.y} r="5" className="preview-waypoint" />
          <circle cx={preview.exit.x} cy={preview.exit.y} r="5" className="preview-waypoint" />
          {preview.hasLinkedExit && <circle cx={preview.exitTarget.x} cy={preview.exitTarget.y} r="9" className="preview-exit-target" />}
          <text x={preview.entry.x - 8} y={preview.entry.y - 22} textAnchor="end">Eintritt</text>
          <text x={preview.exit.x + 12} y={preview.exit.y - 18}>lokaler Austrittspunkt</text>
          {preview.hasLinkedExit && (
            <text
              x={preview.targetHeadingEndpoint.x + (preview.exitVector.x >= 0 ? -10 : 10)}
              y={preview.targetHeadingEndpoint.y - 14}
              textAnchor={preview.exitVector.x >= 0 ? 'end' : 'start'}
              className="preview-target-direction-label"
            >
              Zielrichtung → {exitName} · {projectedTargetAxis}
            </text>
          )}
          <text x={preview.center.x + 118} y={preview.center.y - 4} className="preview-note">Optimum</text>
        </svg>
        <div className="route-preview-legend" aria-label="Legende der Abschnittsvorschau">
          <span><i className="entry" />Anflugbahn</span>
          <span><i className="passage" />Lokale Passage</span>
          <span><i className="prediction" />Prognosebahn zum zukünftigen Folgeziel</span>
          <span><i className="heading" />Zukünftige Zielrichtung</span>
          <span><i className="boundary" />Korridorgrenzen</span>
        </div>
        <dl className="route-preview-state">
          <div><dt>Passage</dt><dd>{passageText} · {section.passage.orbitDirection === 'prograde' ? 'prograd' : 'retrograd'}</dd></div>
          <div>
            <dt>Solver-Passage</dt>
            <dd>
              {preview.selectedOrbitDeg.toFixed(1)}°
              {preview.autoExtendedOrbitDeg > 0.05
                ? ` · +${preview.autoExtendedOrbitDeg.toFixed(1)}° automatisch`
                : ' · Vorgabe übernommen'}
              {' · '}{preview.selectionSource}
            </dd>
          </div>
          <div><dt>Eintritt</dt><dd>{BEHAVIOR_LABELS[section.passage.entryBehavior]}</dd></div>
          <div><dt>Austritt</dt><dd>{BEHAVIOR_LABELS[section.passage.exitBehavior]}</dd></div>
          <div><dt>Folgeziel</dt><dd>{exitName}</dd></div>
          {exitLeadsToInterstellarTarget && (
            <div>
              <dt>Globale Zielseite</dt>
              <dd>{projectedTargetAxis} · Austrittspunkt am Körper kann gegenüberliegen</dd>
            </div>
          )}
          <div>
            <dt>Austrittslage</dt>
            <dd>
              {preview.exitAngleDeg.toFixed(1)}° · +x-Bezug
              {preview.hasLinkedExit ? ' · gekoppelt' : ''}
              {preview.lineOfSightClear
                ? ' · freie Außentangente'
                : preview.bestApproximation
                  ? ' · gekrümmter Transfer'
                  : ' · Sicht ungeklärt'}
            </dd>
          </div>
        </dl>
        <section className="route-preview-ai" aria-labelledby="route-preview-ai-title">
          <h3 id="route-preview-ai-title">Interaktiv verfeinern</h3>
          <div className="route-preview-chips">
            <button type="button" onClick={() => onApply('asymptotic-entry')}>Eintritt asymptotisch</button>
            <button type="button" onClick={() => onApply('tangential-entry')}>Eintritt tangential</button>
            <button type="button" onClick={() => onApply('accelerated-exit')}>Austritt beschleunigen</button>
            <button type="button" onClick={() => onApply('braking-entry')}>Eintritt bremsend</button>
          </div>
          <label>
            <span>Planungsanweisung</span>
            <textarea
              value={instruction}
              onChange={(event) => setInstruction(event.target.value)}
              placeholder="z.B. Eintritt soll asymptotisch erfolgen, Austritt tangential beschleunigen"
            />
          </label>
          <button type="button" className="primary" onClick={applyInstruction}>Anweisung anwenden</button>
          <p>Diese Vorschau speichert die Passage direkt im Routenabschnitt. Die 3D-Gesamtberechnung verwendet dadurch denselben Abschnitt als Quelle.</p>
        </section>
      </div>
      <footer>
        <button type="button" className="primary" onClick={onClose}>Übernehmen</button>
      </footer>
    </dialog>
  )
}
