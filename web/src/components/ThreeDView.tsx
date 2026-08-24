import { GizmoHelper, GizmoViewport, Grid, PerformanceMonitor, Stars } from '@react-three/drei'
import { Canvas, type RootState } from '@react-three/fiber'
import {
  Suspense,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type Dispatch,
  type SetStateAction,
} from 'react'
import * as THREE from 'three'

import { activityRequestHeaders } from '../activityLog'
import { INTERSTELLAR_TARGETS } from '../interstellarTargets'
import { interstellarTargetPosition } from '../celestialCoordinates'
import { withCorridorFeasibility, type CorridorTargetPhysics } from '../corridorFeasibility'
import { DEFAULT_ENTRY_CORRIDOR, type EntryCorridorDefinition } from '../entryCorridorGeometry'
import { requestLaunchOptimization, requestSolarEnergyAssessment, type LaunchOptimizationResult, type SolarEnergyFeasibility } from '../launchOptimizer'
import { DEFAULT_MISSION_CONFIG, requestMissionSimulation, validateMissionConfig } from '../missionSimulation'
import { planetPositionAt } from '../orbitalMath'
import { appendPlaybackAuditEvent, startPlaybackAudit, type PlaybackEventType, type PlaybackStateSnapshot } from '../playbackAudit'
import type { RouteSectionDefinition } from '../routeSections'
import { routeSectionsBlockReason } from '../routeSectionValidation'
import { popSketchHistory, removeSketchSelection } from '../routeSketchState'
import type { GenericTrajectoryPlannerResult, MissionConfig, MissionResult, MoonCatalogue, MoonData, PlanetData, SolarSystemData, VisualConfig } from '../types'
import { MissionTrajectory } from './MissionTrajectory'
import { DirectSolarRoute, type DirectSolarRouteResult } from './DirectSolarRoute'
import { DraggableOverlayPanel } from './DraggableOverlayPanel'
import { EntryCorridorEditor } from './EntryCorridorEditor'
import { EntryCorridorMarker } from './EntryCorridorMarker'
import { FlybyFocusInset } from './FlybyFocusInset'
import { InterstellarTargets } from './InterstellarTargets'
import { MilkyWayBackground } from './MilkyWayBackground'
import { sanitizeMoonCatalogue } from '../moonCatalogue'
import { MoonSystem } from './MoonSystem'
import { Orbit } from './Orbit'
import { ParameterPanel } from './ParameterPanel'
import {
  PlanetCameraControls,
  type CameraFocusRequest,
  type FocusedCameraView,
} from './PlanetCameraControls'
import { PlannedWaypointRoute, type WaypointRouteResult } from './PlannedWaypointRoute'
import { PlanetMesh } from './PlanetMesh'
import { createRouteSketch, RoutePlanPreview, type RouteDrawTool, type RouteSketch, type RouteSketchSelection, type RouteTransformMode } from './RoutePlanPreview'
import { Sun } from './Sun'

const DEFAULT_VISUAL_CONFIG: VisualConfig = {
  orbitScale: 5,
  inclinationScale: 1,
  planetScale: 1,
  smallPlanetScale: 1,
  giantPlanetScale: 1,
  probeScale: 8,
  saturnRingScale: 1,
  showPlanets: true,
  showOrbits: true,
  showTrajectory: true,
  showStages: true,
  showDetachedStages: true,
  showBurn: true,
  showSail: true,
  highlightSensorTethers: true,
  showLabels: false,
  showVectors: false,
  showForceVectors: false,
  showScaleNotice: true,
}

const WEBGL_RENDERER_OPTIONS: THREE.WebGLRendererParameters = {
  antialias: true,
  alpha: false,
  depth: true,
  stencil: false,
  logarithmicDepthBuffer: true,
  powerPreference: 'high-performance',
  precision: 'highp',
  preserveDrawingBuffer: false,
}

const WEBGL_CAMERA = { position: [46, 38, 58] as [number, number, number], fov: 48, near: 0.0001, far: 2_000 }
type AimpointRole = 'entry' | 'periapsis' | 'exit' | 'periapsis_point'

function scaledRadius(planet: PlanetData, sunRadiusKm: number, visual: VisualConfig) {
  const readableSunReferenceRadius = 0.85
  return readableSunReferenceRadius * (planet.radiusKm / sunRadiusKm) * visual.planetScale
}

function pointAtDay(result: MissionResult, elapsedDays: number) {
  const points = result.trajectory
  let low = 0
  let high = points.length - 1
  while (low < high) {
    const middle = Math.ceil((low + high) / 2)
    if (points[middle].elapsedDays <= elapsedDays) low = middle
    else high = middle - 1
  }
  return points[low]
}

function calendarDateAfterDays(startDate: string, elapsedDays: number) {
  return new Date(new Date(`${startDate}T00:00:00Z`).getTime() + elapsedDays * 86_400_000).toISOString().slice(0, 10)
}

function formatMissionDate(isoDate: string) {
  return new Date(`${isoDate}T00:00:00Z`).toLocaleDateString('de-DE', { timeZone: 'UTC' })
}

interface ThreeDViewProps {
  routeSections: RouteSectionDefinition[]
  entryCorridor: EntryCorridorDefinition
  onEntryCorridorChange: Dispatch<SetStateAction<EntryCorridorDefinition>>
  waypointId: string
  onWaypointChange: Dispatch<SetStateAction<string>>
  plannedMissionDate: string | null
  onPlannedMissionDateChange: Dispatch<SetStateAction<string | null>>
  plannedRoute: WaypointRouteResult | null
  onPlannedRouteChange: Dispatch<SetStateAction<WaypointRouteResult | null>>
  onOpenRoutePlanner: () => void
  onOpenRouteSelector: () => void
  restoredMissionConfig: MissionConfig | null
  restoredVisualConfig: VisualConfig | null
  restoredMissionResult: MissionResult | null
  projectLoadToken: number
  onMissionConfigChange: Dispatch<SetStateAction<MissionConfig | null>>
  onVisualConfigChange: Dispatch<SetStateAction<VisualConfig | null>>
  onMissionResultChange: Dispatch<SetStateAction<MissionResult | null>>
}

function routeStateAtDay(route: WaypointRouteResult, elapsedDays: number): PlaybackStateSnapshot {
  const points = route.trajectory
  let low = 0
  let high = points.length - 1
  while (low < high) {
    const middle = Math.ceil((low + high) / 2)
    if (points[middle].elapsedDays <= elapsedDays) low = middle
    else high = middle - 1
  }
  const current = points[low]
  const next = points[Math.min(points.length - 1, low + 1)]
  if (!current) return { positionKm: [0, 0, 0] }
  const durationSeconds = Math.max(0, (next.elapsedDays - current.elapsedDays) * 86_400)
  const fraction = durationSeconds > 0
    ? Math.max(0, Math.min(1, (elapsedDays - current.elapsedDays) * 86_400 / durationSeconds))
    : 0
  const positionKm = current.positionKm.map(
    (component, index) => component + (next.positionKm[index] - component) * fraction,
  ) as [number, number, number]
  const velocityKmS = durationSeconds > 0
    ? current.positionKm.map(
      (component, index) => (next.positionKm[index] - component) / durationSeconds,
    ) as [number, number, number]
    : undefined
  return { positionKm, velocityKmS }
}

export function ThreeDView({
  routeSections,
  entryCorridor,
  onEntryCorridorChange,
  waypointId,
  onWaypointChange: setWaypointId,
  plannedMissionDate,
  onPlannedMissionDateChange: setPlannedMissionDate,
  plannedRoute,
  onPlannedRouteChange: setPlannedRoute,
  onOpenRoutePlanner,
  onOpenRouteSelector,
  restoredMissionConfig,
  restoredVisualConfig,
  restoredMissionResult,
  projectLoadToken,
  onMissionConfigChange,
  onVisualConfigChange,
  onMissionResultChange,
}: ThreeDViewProps) {
  const [data, setData] = useState<SolarSystemData | null>(null)
  const [moonCatalogue, setMoonCatalogue] = useState<MoonCatalogue | null>(null)
  const [selectedPlanet, setSelectedPlanet] = useState<PlanetData | null>(null)
  const [selectedMoon, setSelectedMoon] = useState<MoonData | null>(null)
  const [selectedObject, setSelectedObject] = useState('earth')
  const [loadError, setLoadError] = useState<string | null>(null)
  const [simulationError, setSimulationError] = useState<string | null>(null)
  const [visual, setVisual] = useState<VisualConfig>(DEFAULT_VISUAL_CONFIG)
  const [draft, setDraft] = useState<MissionConfig>(DEFAULT_MISSION_CONFIG)
  const [result, setResult] = useState<MissionResult | null>(null)
  const [missionResultVisible, setMissionResultVisible] = useState(false)
  const [elapsedDays, setElapsedDays] = useState(0)
  const [playing, setPlaying] = useState(false)
  const pendingMissionConfigRef = useRef<MissionConfig | null | undefined>(undefined)
  const pendingVisualConfigRef = useRef<VisualConfig | null | undefined>(undefined)
  const pendingMissionResultRef = useRef<MissionResult | null | undefined>(undefined)

  useEffect(() => {
    if (projectLoadToken === 0) return
    const nextMissionConfig = restoredMissionConfig ?? DEFAULT_MISSION_CONFIG
    const nextVisualConfig = restoredVisualConfig ?? DEFAULT_VISUAL_CONFIG
    pendingMissionConfigRef.current = nextMissionConfig
    pendingVisualConfigRef.current = nextVisualConfig
    pendingMissionResultRef.current = restoredMissionResult
    setDraft(nextMissionConfig)
    setVisual(nextVisualConfig)
    setResult(restoredMissionResult)
    setMissionResultVisible(false)
    setElapsedDays(0)
    setPlaying(false)
  }, [projectLoadToken])
  useEffect(() => {
    if (pendingMissionConfigRef.current !== undefined && pendingMissionConfigRef.current !== draft) return
    pendingMissionConfigRef.current = undefined
    onMissionConfigChange(draft)
  }, [draft, onMissionConfigChange])
  useEffect(() => {
    if (pendingVisualConfigRef.current !== undefined && pendingVisualConfigRef.current !== visual) return
    pendingVisualConfigRef.current = undefined
    onVisualConfigChange(visual)
  }, [onVisualConfigChange, visual])
  useEffect(() => {
    if (pendingMissionResultRef.current !== undefined && pendingMissionResultRef.current !== result) return
    pendingMissionResultRef.current = undefined
    onMissionResultChange(result)
  }, [onMissionResultChange, result])
  const [playbackAuditStatus, setPlaybackAuditStatus] = useState<'idle' | 'starting' | 'recording' | 'paused' | 'complete' | 'reset'>('idle')
  const [playbackAuditError, setPlaybackAuditError] = useState<string | null>(null)
  const playbackAuditIdRef = useRef<string | null>(null)
  const playbackAuditSequenceRef = useRef(0)
  const playbackAuditLastCheckpointDayRef = useRef(0)
  const playbackAuditLastSectionIdRef = useRef<string | null>(null)
  const [simulationSpeed, setSimulationSpeed] = useState(30)
  const [stepDays, setStepDays] = useState(1)
  const [showMoons, setShowMoons] = useState(true)
  const [navigationMode, setNavigationMode] = useState<'rotate' | 'pan'>('rotate')
  const [missionPlannerOpen, setMissionPlannerOpen] = useState(() => window.innerWidth >= 1_100)
  const [parameterPanelOpen, setParameterPanelOpen] = useState(() => window.innerWidth >= 1_440)
  const [selectedTargetId, setSelectedTargetId] = useState('')
  const [encounterDay, setEncounterDay] = useState(730)
  const [flybyAltitudeKm, setFlybyAltitudeKm] = useState(100_000)
  const [flybyMode, setFlybyMode] = useState<'acceleration' | 'observation'>('acceleration')
  const [highFidelityNBody, setHighFidelityNBody] = useState(false)
  const [entryCorridorEditorOpen, setEntryCorridorEditorOpen] = useState(false)
  const [aimpointEnabled, setAimpointEnabled] = useState(false)
  const [aimpointClockAngleDeg, setAimpointClockAngleDeg] = useState(0)
  const [aimpointScreenRadiusNorm, setAimpointScreenRadiusNorm] = useState(1)
  const [aimpointAltitudeKm, setAimpointAltitudeKm] = useState(100_000)
  const [aimpointRole, setAimpointRole] = useState<AimpointRole>('periapsis')
  const [routeError, setRouteError] = useState<string | null>(null)
  const [routeLoading, setRouteLoading] = useState(false)
  const [routeValidationPending, setRouteValidationPending] = useState(false)
  const observedPlannedRouteRef = useRef(plannedRoute)
  const [optimizationWindowDays, setOptimizationWindowDays] = useState(1_460)
  const [optimizationStartDate, setOptimizationStartDate] = useState(DEFAULT_MISSION_CONFIG.startDate)
  const [optimizationThreshold, setOptimizationThreshold] = useState(95)
  const [desiredSolarExitSpeedKmS, setDesiredSolarExitSpeedKmS] = useState(100)
  const [optimizationLoading, setOptimizationLoading] = useState(false)
  const [optimizationResult, setOptimizationResult] = useState<LaunchOptimizationResult | null>(null)
  const [optimizationPreflight, setOptimizationPreflight] = useState<SolarEnergyFeasibility | null>(null)
  const [autoReoptimize, setAutoReoptimize] = useState(false)
  const [recalculationMinutes, setRecalculationMinutes] = useState(5)
  const [showRouteDispersion, setShowRouteDispersion] = useState(false)
  const [dispersionWidth, setDispersionWidth] = useState(0.18)
  const [showRouteGuide, setShowRouteGuide] = useState(false)
  const [showAlternativeRoutes, setShowAlternativeRoutes] = useState(false)
  const [directSolarRoute, setDirectSolarRoute] = useState<DirectSolarRouteResult | null>(null)
  const [routePlanStatus, setRoutePlanStatus] = useState<'hidden' | 'review' | 'confirmed'>('hidden')
  const [routeSketch, setRouteSketch] = useState<RouteSketch | null>(null)
  const [routeDrawTool, setRouteDrawTool] = useState<RouteDrawTool>('move')
  const [routeTransformMode, setRouteTransformMode] = useState<RouteTransformMode>('translate')
  const [routeSketchSelection, setRouteSketchSelection] = useState<RouteSketchSelection>(null)
  const [routeSketchHistory, setRouteSketchHistory] = useState<RouteSketch[]>([])
  const [routeSketchDragging, setRouteSketchDragging] = useState(false)
  const routeSketchRef = useRef<RouteSketch | null>(null)
  const pendingRouteSketchRef = useRef<RouteSketch | null>(null)
  const routeSketchUpdateTimerRef = useRef<number | null>(null)
  const [rendererInfo, setRendererInfo] = useState<{ api: string; antialias: boolean; maxTextureSize: number } | null>(null)
  const [rendererDpr, setRendererDpr] = useState(1.2)
  const [rendererProfile, setRendererProfile] = useState<'stabil' | 'sparsam'>('stabil')
  const [activeInfoDrags, setActiveInfoDrags] = useState<Set<string>>(() => new Set())
  const [cameraFocusRequest, setCameraFocusRequest] = useState<CameraFocusRequest>({ kind: 'overview', view: 'perspective', requestId: 0 })
  const validationErrors = validateMissionConfig(draft)

  useEffect(() => {
    if (observedPlannedRouteRef.current === plannedRoute) return
    observedPlannedRouteRef.current = plannedRoute
    setRouteValidationPending(false)
  }, [plannedRoute])
  const setEntryCorridor: Dispatch<SetStateAction<EntryCorridorDefinition>> = useCallback((action) => {
    onEntryCorridorChange((current) => {
      const next = typeof action === 'function' ? action(current) : action
      const target = waypointId === 'sun'
        ? data?.sun
        : data?.planets.find((planet) => planet.id === waypointId)
      const targetPhysics: CorridorTargetPhysics = {
        radiusKm: target?.radiusKm,
        surfaceGravity: target?.surfaceGravity,
      }
      return withCorridorFeasibility(next, targetPhysics)
    })
  }, [data, onEntryCorridorChange, waypointId])
  const corridorBlocked = entryCorridor.enabled && Boolean(entryCorridor.blocked)
  const corridorRequiresDynamicCheck = corridorBlocked && routeSections.length > 0
  const corridorBlockMessage = entryCorridor.blockReasons?.join(' ')
    || 'Der Zielkorridor verletzt den Mindestabstand oder liegt auf der vom Ursprung abgewandten Seite.'
  const routeCalculationBlockReason = routeSectionsBlockReason(routeSections)

  useEffect(() => {
    if (!plannedMissionDate || draft.startDate === plannedMissionDate) return
    setDraft((current) => ({ ...current, startDate: plannedMissionDate }))
    setOptimizationStartDate(plannedMissionDate)
  }, [draft.startDate, plannedMissionDate])

  const handleInfoDragChange = useCallback((label: string, active: boolean) => {
    setActiveInfoDrags((current) => {
      const next = new Set(current)
      if (active) next.add(label)
      else next.delete(label)
      return next
    })
  }, [])
  const overlayDragActive = activeInfoDrags.size > 0
  const selectedSketchCircle = routeSketchSelection && (routeSketchSelection.kind === 'circle' || routeSketchSelection.kind === 'circle-radius')
    ? routeSketch?.circles.find((circle) => circle.id === routeSketchSelection.id) ?? null
    : null

  const configureWebGLRenderer = useCallback(({ gl }: RootState) => {
    gl.outputColorSpace = THREE.SRGBColorSpace
    gl.toneMapping = THREE.ACESFilmicToneMapping
    gl.toneMappingExposure = 1.08
    gl.shadowMap.enabled = false
    const attributes = gl.getContext().getContextAttributes()
    setRendererInfo({
      api: gl.capabilities.isWebGL2 ? 'WebGL 2' : 'WebGL 1',
      antialias: Boolean(attributes?.antialias),
      maxTextureSize: gl.capabilities.maxTextureSize,
    })
  }, [])

  const reduceRendererLoad = useCallback(() => {
    setRendererDpr(1)
    setRendererProfile('sparsam')
  }, [])

  const restoreRendererQuality = useCallback(() => {
    setRendererDpr(1.2)
    setRendererProfile('stabil')
  }, [])

  useEffect(() => {
    routeSketchRef.current = routeSketch
  }, [routeSketch])

  const rememberRouteSketch = useCallback(() => {
    const current = routeSketchRef.current
    if (current) setRouteSketchHistory((history) => [...history, current].slice(-100))
  }, [])

  const flushPendingRouteSketch = useCallback(() => {
    if (routeSketchUpdateTimerRef.current !== null) {
      window.clearTimeout(routeSketchUpdateTimerRef.current)
      routeSketchUpdateTimerRef.current = null
    }
    const next = pendingRouteSketchRef.current
    pendingRouteSketchRef.current = null
    if (!next) return
    routeSketchRef.current = next
    setRouteSketch(next)
  }, [])

  const clearPendingRouteSketch = useCallback(() => {
    if (routeSketchUpdateTimerRef.current !== null) window.clearTimeout(routeSketchUpdateTimerRef.current)
    routeSketchUpdateTimerRef.current = null
    pendingRouteSketchRef.current = null
  }, [])

  const handleRouteSketchChange = useCallback((next: RouteSketch, recordHistory = false) => {
    if (recordHistory) {
      flushPendingRouteSketch()
      rememberRouteSketch()
      routeSketchRef.current = next
      setRouteSketch(next)
      return
    }
    routeSketchRef.current = next
    pendingRouteSketchRef.current = next
    if (routeSketchUpdateTimerRef.current === null) {
      routeSketchUpdateTimerRef.current = window.setTimeout(() => {
        routeSketchUpdateTimerRef.current = null
        const pending = pendingRouteSketchRef.current
        pendingRouteSketchRef.current = null
        if (pending) setRouteSketch(pending)
      }, 32)
    }
  }, [flushPendingRouteSketch, rememberRouteSketch])

  const handleRouteSketchEditingChange = useCallback((editing: boolean) => {
    if (editing) rememberRouteSketch()
    else flushPendingRouteSketch()
    setRouteSketchDragging(editing)
  }, [flushPendingRouteSketch, rememberRouteSketch])

  useEffect(() => clearPendingRouteSketch, [clearPendingRouteSketch])

  const visibleMissionResult = missionResultVisible && routePlanStatus === 'hidden' ? result : null

  const applyGenericTrajectoryPlan = useCallback((trajectoryPlan: GenericTrajectoryPlannerResult) => {
    const legacy = trajectoryPlan.legacyRoute
    if (legacy && typeof legacy === 'object' && 'trajectory' in legacy && 'summary' in legacy) {
      setPlannedRoute(legacy as WaypointRouteResult)
    } else {
      const trajectory = trajectoryPlan.trajectory
      const finalPoint = trajectory.at(-1)
      const targetPosition = trajectoryPlan.target.positionKm ?? finalPoint?.positionKm ?? [0, 0, 0]
      const finalVelocity = finalPoint?.velocityKmS ?? [0, 0, 0]
      const finalSpeed = Math.hypot(...finalVelocity)
      const outgoingDirection = finalSpeed > 0
        ? finalVelocity.map((component) => component / finalSpeed) as [number, number, number]
        : [1, 0, 0] as [number, number, number]
      const minimumSolarRadiusKm = Math.min(...trajectory.map((point) => Math.hypot(...point.positionKm)))
      setPlannedRoute({
        startDate: trajectoryPlan.start.date,
        genericTarget: trajectoryPlan.target,
        totalFlightDays: trajectoryPlan.summary.totalFlightDays,
        warnings: trajectoryPlan.warnings,
        trajectory,
        segments: trajectoryPlan.segments,
        waypoint: {
          id: trajectoryPlan.target.bodyId ?? trajectoryPlan.target.zoneId ?? trajectoryPlan.target.boundaryId ?? trajectoryPlan.target.type,
          name: trajectoryPlan.target.bodyId ?? trajectoryPlan.target.zoneId ?? trajectoryPlan.target.boundaryId ?? trajectoryPlan.target.type,
          encounterDay: trajectoryPlan.summary.totalFlightDays,
          flybyAltitudeKm: 0,
          trajectoryIndex: Math.max(0, trajectory.length - 1),
          positionKm: targetPosition,
        },
        outgoingDirection,
        validation: {
          collisionFree: minimumSolarRadiusKm >= 696_340,
          minimumSolarRadiusKm,
          sunRadiusKm: 696_340,
          minimumSolarAltitudeKm: minimumSolarRadiusKm - 696_340,
        },
        summary: {
          flybyMode: 'multi-section',
          requiredInjectionDeltaVKmS: trajectoryPlan.summary.requiredInjectionDeltaVKmS ?? trajectoryPlan.summary.totalDeltaVKmS ?? 0,
          availableInjectionDeltaVKmS: draft.oberthDeltaVKmS,
          solarDepartureInjectionApplied: trajectoryPlan.summary.feasible,
          incomingExcessSpeedKmS: trajectoryPlan.summary.departureVInfinityKmS ?? 0,
          turnAngleDeg: 0,
          heliocentricSpeedBeforeKmS: Math.hypot(...trajectoryPlan.start.velocityKmS),
          heliocentricSpeedAfterKmS: trajectoryPlan.summary.finalHeliocentricSpeedKmS ?? finalSpeed,
          speedGainKmS: (trajectoryPlan.summary.finalHeliocentricSpeedKmS ?? finalSpeed) - Math.hypot(...trajectoryPlan.start.velocityKmS),
          targetCorrectionDeltaVKmS: trajectoryPlan.summary.arrivalVInfinityKmS ?? 0,
          targetInjectionApplied: trajectoryPlan.summary.targetReached,
          passiveTargeting: trajectoryPlan.summary.targetReached,
          courseChangeDeg: trajectoryPlan.summary.targetAlignmentDeg ?? 0,
          periapsisSpeedKmS: trajectoryPlan.summary.finalHeliocentricSpeedKmS ?? finalSpeed,
          observationWindowHours: 0,
          targetAlignmentDeg: trajectoryPlan.summary.targetAlignmentDeg ?? 0,
          feasibleWithConfiguredBurn: trajectoryPlan.summary.feasible,
          warnings: trajectoryPlan.warnings,
          model: trajectoryPlan.summary.model,
        },
        audit: trajectoryPlan.audit as WaypointRouteResult['audit'],
      })
    }
    setPlannedMissionDate(trajectoryPlan.start.date)
    setRouteValidationPending(false)
    setRoutePlanStatus('confirmed')
    setMissionResultVisible(false)
    setElapsedDays(0)
  }, [draft.oberthDeltaVKmS, setPlannedMissionDate, setPlannedRoute])

  const playbackEndDay = plannedRoute?.totalFlightDays
    ?? plannedRoute?.trajectory.at(-1)?.elapsedDays
    ?? visibleMissionResult?.summary.totalFlightDays
    ?? 0
  const plannedRouteIsExecutable = !plannedRoute || (
    plannedRoute.summary.feasibleWithConfiguredBurn && !routeValidationPending
  )
  const canPlay = playbackEndDay > 0 && routePlanStatus !== 'review' && plannedRouteIsExecutable
  const activeStartDate = plannedRoute?.startDate ?? visibleMissionResult?.config.startDate ?? draft.startDate

  useEffect(() => {
    const controller = new AbortController()
    Promise.all([
      fetch('/api/solar-system', { signal: controller.signal }),
      fetch('/moons.json', { signal: controller.signal }),
    ])
      .then(async ([planetResponse, moonResponse]) => {
        if (!planetResponse.ok || !moonResponse.ok) throw new Error(`HTTP ${planetResponse.status}/${moonResponse.status}`)
        const [solarData, moonData] = await Promise.all([
          planetResponse.json() as Promise<SolarSystemData>,
          moonResponse.json() as Promise<MoonCatalogue>,
        ])
        return [solarData, moonData] as const
      })
      .then(([solarData, moonData]) => {
        const sanitizedMoonCatalogue = sanitizeMoonCatalogue(moonData)
        setData(solarData)
        setMoonCatalogue(sanitizedMoonCatalogue)
        setSelectedPlanet(solarData.planets.find((planet) => planet.id === 'earth') ?? null)
      })
      .catch((requestError: Error) => {
        if (requestError.name !== 'AbortError') setLoadError(requestError.message)
      })
    return () => controller.abort()
  }, [])

  useEffect(() => {
    if (!playing || !canPlay) return undefined
    const timer = window.setInterval(() => {
      setElapsedDays((current) => Math.min(playbackEndDay, current + simulationSpeed / 10))
    }, 100)
    return () => window.clearInterval(timer)
  }, [canPlay, playbackEndDay, playing, simulationSpeed])

  useEffect(() => {
    const adaptSidebarsToViewport = () => {
      setMissionPlannerOpen(window.innerWidth >= 1_100)
      setParameterPanelOpen(window.innerWidth >= 1_440)
    }
    window.addEventListener('resize', adaptSidebarsToViewport)
    return () => window.removeEventListener('resize', adaptSidebarsToViewport)
  }, [])

  const selectedMoons = useMemo(
    () => selectedPlanet && moonCatalogue
      ? moonCatalogue.moons.filter((moon) => moon.parentId === selectedPlanet.id)
      : [],
    [moonCatalogue, selectedPlanet],
  )
  const currentPoint = useMemo(
    () => visibleMissionResult ? pointAtDay(visibleMissionResult, elapsedDays) : null,
    [elapsedDays, visibleMissionResult],
  )
  const currentRouteSegment = useMemo(() => {
    if (!plannedRoute?.segments?.length) return null
    const currentIndex = plannedRoute.trajectory.reduce(
      (selected, point, index) => point.elapsedDays <= elapsedDays ? index : selected,
      0,
    )
    return plannedRoute.segments.find((segment) => currentIndex >= segment.startIndex && currentIndex <= segment.endIndex)
      ?? plannedRoute.segments.at(-1)
      ?? null
  }, [elapsedDays, plannedRoute])
  const playbackStateAtDay = useCallback((day: number): PlaybackStateSnapshot => {
    if (plannedRoute?.trajectory.length) return routeStateAtDay(plannedRoute, day)
    if (visibleMissionResult?.trajectory.length) {
      const point = pointAtDay(visibleMissionResult, day)
      return {
        positionKm: point.positionKm,
        velocityKmS: point.velocityKmS,
        phase: point.phase,
        massKg: point.massKg,
      }
    }
    return { positionKm: [0, 0, 0] }
  }, [plannedRoute, visibleMissionResult])
  const playbackSectionAtDay = useCallback((day: number) => {
    if (plannedRoute?.trajectory.length && plannedRoute.segments?.length) {
      const pointIndex = plannedRoute.trajectory.reduce(
        (selected, point, index) => point.elapsedDays <= day ? index : selected,
        0,
      )
      return plannedRoute.segments.find(
        (segment) => pointIndex >= segment.startIndex && pointIndex <= segment.endIndex,
      ) ?? plannedRoute.segments.at(-1) ?? null
    }
    const phase = visibleMissionResult ? pointAtDay(visibleMissionResult, day).phase : undefined
    return phase ? { id: phase, label: phase.replaceAll('_', ' ') } : null
  }, [plannedRoute, visibleMissionResult])
  const appendPlaybackEvent = useCallback((
    eventType: PlaybackEventType,
    day: number,
    details: Record<string, unknown> = {},
  ) => {
    const playbackId = playbackAuditIdRef.current
    if (!playbackId) return
    const section = playbackSectionAtDay(day)
    playbackAuditSequenceRef.current += 1
    void appendPlaybackAuditEvent({
      playbackId,
      sequence: playbackAuditSequenceRef.current,
      eventType,
      missionDay: day,
      simulatedDateTimeUtc: new Date(
        new Date(`${activeStartDate}T00:00:00Z`).getTime() + day * 86_400_000,
      ).toISOString(),
      sectionId: section?.id,
      sectionLabel: section?.label,
      state: playbackStateAtDay(day),
      details,
    }).catch((error: Error) => setPlaybackAuditError(error.message))
  }, [activeStartDate, playbackSectionAtDay, playbackStateAtDay])
  const toggleMissionPlayback = useCallback(async () => {
    if (!canPlay || playbackAuditStatus === 'starting') return
    if (playing) {
      setPlaying(false)
      setPlaybackAuditStatus('paused')
      appendPlaybackEvent('paused', elapsedDays, { reason: 'user' })
      return
    }
    let startDay = elapsedDays
    if (elapsedDays >= playbackEndDay || playbackAuditStatus === 'complete' || playbackAuditStatus === 'reset') {
      startDay = 0
      setElapsedDays(0)
      playbackAuditIdRef.current = null
    }
    if (playbackAuditIdRef.current) {
      appendPlaybackEvent('resumed', startDay)
      setPlaybackAuditStatus('recording')
      setPlaying(true)
      return
    }
    setPlaybackAuditStatus('starting')
    setPlaybackAuditError(null)
    try {
      const lastSection = routeSections.at(-1)
      const started = await startPlaybackAudit({
        routeAuditRunId: plannedRoute?.audit?.runId,
        startDate: activeStartDate,
        playbackEndDay,
        originId: routeSections[0]?.originId ?? 'earth',
        targetId: lastSection?.targetId ?? plannedRoute?.waypoint.id ?? selectedTargetId,
        routeSectionIds: routeSections.map((section) => section.id),
        missionConfig: draft,
        routeSections,
        state: playbackStateAtDay(startDay),
      })
      playbackAuditIdRef.current = started.playbackId
      playbackAuditSequenceRef.current = 0
      playbackAuditLastCheckpointDayRef.current = startDay
      playbackAuditLastSectionIdRef.current = playbackSectionAtDay(startDay)?.id ?? null
      setPlaybackAuditStatus('recording')
      setPlaying(true)
    } catch (error) {
      setPlaybackAuditStatus('idle')
      setPlaybackAuditError(error instanceof Error ? error.message : 'Missionslauf-Log konnte nicht gestartet werden.')
    }
  }, [
    activeStartDate,
    appendPlaybackEvent,
    canPlay,
    draft,
    elapsedDays,
    playbackAuditStatus,
    playbackEndDay,
    playbackSectionAtDay,
    playbackStateAtDay,
    plannedRoute,
    playing,
    routeSections,
    selectedTargetId,
  ])
  const seekMissionPlayback = useCallback((day: number) => {
    const boundedDay = Math.max(0, Math.min(playbackEndDay, day))
    if (playing) setPlaying(false)
    if (playbackAuditIdRef.current) {
      appendPlaybackEvent('seek', boundedDay, { fromMissionDay: elapsedDays })
      setPlaybackAuditStatus('paused')
    }
    setElapsedDays(boundedDay)
  }, [appendPlaybackEvent, elapsedDays, playbackEndDay, playing])
  const resetMissionPlayback = useCallback(() => {
    if (playbackAuditIdRef.current && playbackAuditStatus !== 'complete') {
      appendPlaybackEvent('reset', elapsedDays, { reason: 'user' })
    }
    playbackAuditIdRef.current = null
    setPlaybackAuditStatus('reset')
    setElapsedDays(0)
    setPlaying(false)
  }, [appendPlaybackEvent, elapsedDays, playbackAuditStatus])
  const abortActivePlayback = useCallback((reason: string) => {
    if (playbackAuditIdRef.current && playbackAuditStatus !== 'complete') {
      appendPlaybackEvent('aborted', elapsedDays, { reason })
    }
    playbackAuditIdRef.current = null
    setPlaybackAuditStatus('idle')
    setPlaying(false)
  }, [appendPlaybackEvent, elapsedDays, playbackAuditStatus])

  useEffect(() => {
    if (!playing || !playbackAuditIdRef.current) return
    const section = playbackSectionAtDay(elapsedDays)
    const sectionChanged = Boolean(
      section?.id && section.id !== playbackAuditLastSectionIdRef.current,
    )
    const checkpointIntervalDays = Math.max(0.1, playbackEndDay / 500)
    const checkpointDue = (
      elapsedDays - playbackAuditLastCheckpointDayRef.current >= checkpointIntervalDays
      || elapsedDays >= playbackEndDay
    )
    if (!sectionChanged && !checkpointDue) return
    playbackAuditLastCheckpointDayRef.current = elapsedDays
    playbackAuditLastSectionIdRef.current = section?.id ?? null
    appendPlaybackEvent(
      sectionChanged ? 'section-entered' : 'checkpoint',
      elapsedDays,
    )
  }, [appendPlaybackEvent, elapsedDays, playbackEndDay, playbackSectionAtDay, playing])

  useEffect(() => {
    if (!canPlay || elapsedDays < playbackEndDay || !playing) return
    setPlaying(false)
    if (playbackAuditIdRef.current) {
      appendPlaybackEvent('target-reached', playbackEndDay, {
        targetId: routeSections.at(-1)?.targetId ?? plannedRoute?.waypoint.id ?? selectedTargetId,
      })
      setPlaybackAuditStatus('complete')
    }
  }, [
    appendPlaybackEvent,
    canPlay,
    elapsedDays,
    playbackEndDay,
    plannedRoute,
    playing,
    routeSections,
    selectedTargetId,
  ])
  const encounterPlanetRadius = useMemo(() => {
    if (!data) return 0.01
    const planet = data.planets.find((candidate) => candidate.id === waypointId)
    return planet ? scaledRadius(planet, data.sun.radiusKm, visual) : 0.01
  }, [data, visual, waypointId])
  const selectedTarget = INTERSTELLAR_TARGETS.find((target) => target.id === selectedTargetId)
  useEffect(() => {
    if (selectedTargetId || routeSections.length === 0) return
    const finalTargetId = routeSections.at(-1)?.targetId
    if (!finalTargetId || !INTERSTELLAR_TARGETS.some((target) => target.id === finalTargetId)) return
    setSelectedTargetId(finalTargetId)
  }, [routeSections, selectedTargetId])
  const timestampMs = new Date(activeStartDate).getTime() + elapsedDays * 86_400_000
  const focusedPlanet = cameraFocusRequest.kind === 'planet'
    ? data?.planets.find((planet) => planet.id === cameraFocusRequest.planetId) ?? null
    : null
  const focusedPlanetPosition = useMemo(
    () => {
      if (cameraFocusRequest.kind === 'point') return new THREE.Vector3(...cameraFocusRequest.position)
      return focusedPlanet
        ? planetPositionAt(focusedPlanet, timestampMs, visual.orbitScale, visual.inclinationScale)
        : null
    },
    [cameraFocusRequest, focusedPlanet, timestampMs, visual.inclinationScale, visual.orbitScale],
  )
  const focusedPlanetRadius = cameraFocusRequest.kind === 'point'
    ? cameraFocusRequest.radius
    : focusedPlanet && data
      ? scaledRadius(focusedPlanet, data.sun.radiusKm, visual)
      : 0.01
  const selectedTargetScenePosition = useMemo(() => {
    if (!selectedTarget) return undefined
    const cataloguePosition = interstellarTargetPosition(selectedTarget)
    const catalogueDistance = cataloguePosition.length()
    const displayDirection = cataloguePosition.normalize()
    displayDirection.y *= visual.inclinationScale
    displayDirection.normalize()
    // A catalogue target is a fixed sky direction. It must not jump when the
    // optimizer recommends another route or when a propagated preview ends.
    return displayDirection.multiplyScalar(catalogueDistance)
  }, [selectedTarget, visual.inclinationScale])
  const routePlanNodes = useMemo(() => {
    if (!data || !selectedTargetScenePosition) return null
    const earth = data.planets.find((planet) => planet.id === 'earth')
    const waypointPlanet = data.planets.find((planet) => planet.id === waypointId)
    if (!earth || !waypointPlanet) return null
    const activeStartDate = optimizationResult?.alternatives.gravityAssist.startDate ?? draft.startDate
    const activeEncounterDay = optimizationResult?.optimizedEncounterDay ?? encounterDay
    const startTimestamp = new Date(`${activeStartDate}T00:00:00Z`).getTime()
    return {
      earth: planetPositionAt(earth, startTimestamp, visual.orbitScale, visual.inclinationScale),
      sun: new THREE.Vector3(0, 0, 0),
      waypoint: planetPositionAt(waypointPlanet, startTimestamp + activeEncounterDay * 86_400_000, visual.orbitScale, visual.inclinationScale),
      target: selectedTargetScenePosition,
      waypointId: waypointPlanet.id,
      waypointName: waypointPlanet.name,
      waypointColor: waypointPlanet.color,
      waypointRadius: scaledRadius(waypointPlanet, data.sun.radiusKm, visual),
      encounterDay: activeEncounterDay,
      encounterDate: optimizationResult?.optimizedEncounterDate ?? calendarDateAfterDays(activeStartDate, activeEncounterDay),
    }
  }, [data, draft.startDate, encounterDay, optimizationResult, selectedTargetScenePosition, visual.inclinationScale, visual.orbitScale, visual.planetScale, waypointId])
  const requestedPlanNodes = useMemo(() => {
    if (!data || !optimizationResult) return undefined
    if (!optimizationResult.planComparison.startDateChanged && !optimizationResult.planComparison.encounterDayChanged) return undefined
    const earth = data.planets.find((planet) => planet.id === 'earth')
    const waypointPlanet = data.planets.find((planet) => planet.id === waypointId)
    if (!earth || !waypointPlanet) return undefined
    const requestedStartDate = optimizationResult.requestedPlan.startDate
    const requestedEncounterDay = optimizationResult.requestedPlan.encounterDay
    const requestedTimestamp = new Date(`${requestedStartDate}T00:00:00Z`).getTime()
    return {
      earth: planetPositionAt(earth, requestedTimestamp, visual.orbitScale, visual.inclinationScale),
      waypoint: planetPositionAt(waypointPlanet, requestedTimestamp + requestedEncounterDay * 86_400_000, visual.orbitScale, visual.inclinationScale),
      startDate: requestedStartDate,
      encounterDay: requestedEncounterDay,
      encounterDate: optimizationResult.requestedPlan.encounterDate,
    }
  }, [data, optimizationResult, visual.inclinationScale, visual.orbitScale, waypointId])

  const invalidateRoutePlan = () => {
    abortActivePlayback('route-invalidated')
    clearPendingRouteSketch()
    setEntryCorridorEditorOpen(false)
    setRoutePlanStatus('hidden')
    setRouteSketch(null)
    routeSketchRef.current = null
    setRouteSketchHistory([])
    setRouteSketchSelection(null)
    setRouteDrawTool('move')
    setRouteTransformMode('translate')
    setRouteSketchDragging(false)
    setPlannedMissionDate(null)
    setOptimizationPreflight(null)
  }

  const freshRouteSketch = () => routePlanNodes ? createRouteSketch(routePlanNodes) : null
  const beginRouteReview = () => {
    const sketch = freshRouteSketch()
    if (!sketch) return
    clearPendingRouteSketch()
    setRouteSketch(sketch)
    routeSketchRef.current = sketch
    setRouteSketchHistory([])
    setRouteSketchSelection(null)
    setRouteDrawTool('move')
    setRouteTransformMode('translate')
    setRouteSketchDragging(false)
    setEntryCorridorEditorOpen(false)
    setRoutePlanStatus('review')
    setPlannedRoute(null)
    setPlannedMissionDate(null)
    setDirectSolarRoute(null)
    setOptimizationResult(null)
    setRouteError(null)
  }
  const activateRouteDrawing = () => {
    setMissionPlannerOpen(true)
    abortActivePlayback('route-editing')
    setEntryCorridorEditorOpen(false)
    if (!routeSketch || routePlanStatus === 'hidden') {
      beginRouteReview()
      return
    }
    setRoutePlanStatus('review')
    setRouteSketchSelection(null)
    setRouteDrawTool('move')
    setRouteTransformMode('translate')
  }
  const resetRouteSketch = () => {
    const next = freshRouteSketch()
    if (next) handleRouteSketchChange(next, true)
    setRouteSketchSelection(null)
    setRouteDrawTool('move')
    setRouteTransformMode('translate')
  }
  const discardRouteSketch = () => {
    clearPendingRouteSketch()
    setRoutePlanStatus('hidden')
    setRouteSketch(null)
    routeSketchRef.current = null
    setRouteSketchHistory([])
    setRouteSketchSelection(null)
    setRouteDrawTool('move')
    setRouteTransformMode('translate')
    setRouteSketchDragging(false)
    setEntryCorridorEditorOpen(false)
  }
  const removeLastSketchElement = () => {
    const current = routeSketchRef.current
    if (!current) return
    let next = current
    if (current.lines.length > 0) next = { ...current, lines: current.lines.slice(0, -1) }
    else if (current.circles.length > 2) next = { ...current, circles: current.circles.slice(0, -1) }
    else {
      const lastControl = [...current.nodes].reverse().find((node) => !node.locked)
      if (lastControl) next = { ...current, nodes: current.nodes.filter((node) => node.id !== lastControl.id) }
    }
    if (next !== current) {
      handleRouteSketchChange(next, true)
      setRouteSketchSelection(null)
    }
  }

  const undoRouteSketch = useCallback(() => {
    const { previous, remaining } = popSketchHistory(routeSketchHistory)
    if (!previous) return
    routeSketchRef.current = previous
    setRouteSketch(previous)
    setRouteSketchHistory(remaining)
    setRouteSketchSelection(null)
    setRouteSketchDragging(false)
  }, [routeSketchHistory])

  const deleteSelectedSketchElement = useCallback(() => {
    const current = routeSketchRef.current
    const selection = routeSketchSelection
    if (!current || !selection) return
    const next = removeSketchSelection(current, selection)
    if (next !== current) handleRouteSketchChange(next, true)
    setRouteSketchSelection(null)
  }, [handleRouteSketchChange, routeSketchSelection])

  const setSelectedCircleRotationDeg = useCallback((axis: 0 | 1 | 2, degrees: number, relative = false) => {
    const current = routeSketchRef.current
    const selection = routeSketchSelection
    if (!current || !selection || (selection.kind !== 'circle' && selection.kind !== 'circle-radius') || !Number.isFinite(degrees)) return
    const selectedCircle = current.circles.find((circle) => circle.id === selection.id)
    if (!selectedCircle) return
    const rotation: [number, number, number] = [...selectedCircle.rotation]
    rotation[axis] = relative ? rotation[axis] + THREE.MathUtils.degToRad(degrees) : THREE.MathUtils.degToRad(degrees)
    handleRouteSketchChange({
      ...current,
      circles: current.circles.map((circle) => circle.id === selection.id ? { ...circle, rotation } : circle),
    }, true)
  }, [handleRouteSketchChange, routeSketchSelection])

  useEffect(() => {
    if (routePlanStatus !== 'review') return undefined
    const handleKeyDown = (event: KeyboardEvent) => {
      const target = event.target as HTMLElement | null
      if (target?.isContentEditable || target?.matches('input, textarea, select')) return
      if ((event.ctrlKey || event.metaKey) && !event.shiftKey && event.key.toLowerCase() === 'z') {
        event.preventDefault()
        undoRouteSketch()
      } else if (event.key === 'Delete') {
        event.preventDefault()
        deleteSelectedSketchElement()
      }
    }
    window.addEventListener('keydown', handleKeyDown)
    return () => window.removeEventListener('keydown', handleKeyDown)
  }, [deleteSelectedSketchElement, routePlanStatus, undoRouteSketch])

  const selectPlanet = (planet: PlanetData) => {
    setSelectedPlanet(planet)
    setSelectedObject(planet.id)
    setSelectedMoon(null)
    setCameraFocusRequest((current) => ({ kind: 'planet', planetId: planet.id, requestId: current.requestId + 1 }))
    // Inspecting or focusing another planet must never silently destroy an
    // open drawing or an already calculated route. The waypoint changes only
    // while no route workflow is active.
    if (planet.id !== 'earth' && routePlanStatus === 'hidden' && !plannedRoute) {
      setWaypointId(planet.id)
      invalidateRoutePlan()
      setPlannedRoute(null)
      setDirectSolarRoute(null)
      setOptimizationResult(null)
    }
  }
  const focusSelectedPlanet = () => {
    if (!selectedPlanet) return
    setCameraFocusRequest((current) => ({ kind: 'planet', planetId: selectedPlanet.id, requestId: current.requestId + 1 }))
  }
  const showSystemOverview = (view: 'perspective' | 'top' | 'front' | 'side' = 'perspective') => {
    setCameraFocusRequest((current) => ({ kind: 'overview', view, requestId: current.requestId + 1 }))
  }
  const showCameraView = (view: FocusedCameraView) => {
    if (cameraFocusRequest.kind === 'point') {
      setCameraFocusRequest((current) => current.kind === 'point'
        ? { ...current, view, preserveDistance: true, requestId: current.requestId + 1 }
        : current)
      return
    }
    if (
      cameraFocusRequest.kind === 'planet'
      && selectedPlanet
      && cameraFocusRequest.planetId === selectedPlanet.id
    ) {
      setCameraFocusRequest((current) => ({
        kind: 'planet',
        planetId: selectedPlanet.id,
        view,
        preserveDistance: true,
        requestId: current.requestId + 1,
      }))
      return
    }
    if (view !== 'sun-to-target' && view !== 'sun-behind' && view !== 'cross-axis') {
      showSystemOverview(view)
      return
    }
    const routeTargetPlanet = data?.planets.find((planet) => planet.id === waypointId)
      ?? selectedPlanet
    if (routeTargetPlanet) {
      setCameraFocusRequest((current) => ({
        kind: 'planet',
        planetId: routeTargetPlanet.id,
        view,
        preserveDistance: false,
        requestId: current.requestId + 1,
      }))
    }
  }
  const focusRouteWaypoint = () => {
    if (!routePlanNodes) return
    setCameraFocusRequest((current) => ({
      kind: 'point',
      label: `${routePlanNodes.waypointName}-Begegnung`,
      position: routePlanNodes.waypoint.toArray() as [number, number, number],
      radius: Math.max(routePlanNodes.waypointRadius, 0.24),
      requestId: current.requestId + 1,
    }))
  }
  const refocusCurrentObject = () => {
    if (cameraFocusRequest.kind === 'point') {
      setCameraFocusRequest((current) => current.kind === 'point'
        ? { ...current, preserveDistance: false, requestId: current.requestId + 1 }
        : current)
      return
    }
    focusSelectedPlanet()
  }
  const toggleEntryCorridorEditor = () => {
    const opening = !entryCorridorEditorOpen
    setEntryCorridorEditorOpen(opening)
    if (!opening) return
    setEntryCorridor((current) => ({ ...current, enabled: true }))
    setAimpointEnabled(false)
    setPlannedRoute(null)
    setPlannedMissionDate(null)
    focusRouteWaypoint()
  }
  const selectQuickObject = (objectId: string) => {
    if (!objectId) return
    const planet = data?.planets.find((candidate) => candidate.id === objectId)
    if (planet) {
      if (routePlanStatus === 'review' && planet.id === waypointId && routePlanNodes) {
        setSelectedPlanet(planet)
        setSelectedObject(planet.id)
        setSelectedMoon(null)
        focusRouteWaypoint()
        return
      }
      selectPlanet(planet)
      return
    }
    setSelectedObject(objectId)
    setSelectedMoon(null)
    if (objectId === 'sun') showSystemOverview('perspective')
  }
  const selectInterstellarTarget = (targetId: string) => {
    setSelectedTargetId(targetId)
    invalidateRoutePlan()
    setPlannedRoute(null)
    setDirectSolarRoute(null)
    setOptimizationResult(null)
    setOptimizationPreflight(null)
    clearPendingRouteSketch()
  }
  const applySimulation = async () => {
    try {
      abortActivePlayback('simulation-recalculated')
      setSimulationError(null)
      const nextResult = await requestMissionSimulation(draft)
      setResult(nextResult)
      setMissionResultVisible(true)
      setPlannedMissionDate(nextResult.config.startDate)
      setElapsedDays(0)
      setPlaying(false)
      setSimulationError(null)
    } catch (error) {
      setSimulationError(error instanceof Error ? error.message : 'Simulation fehlgeschlagen.')
    }
  }
  const aimpointPayload = useMemo(() => ({
    enabled: aimpointEnabled,
    clockAngleDeg: aimpointClockAngleDeg,
    screenRadiusNorm: aimpointScreenRadiusNorm,
    role: aimpointRole === 'periapsis_point' ? 'periapsis' : aimpointRole,
    altitudeKm: aimpointAltitudeKm,
  }), [
    aimpointEnabled,
    aimpointClockAngleDeg,
    aimpointScreenRadiusNorm,
    aimpointRole,
    aimpointAltitudeKm,
  ])
  const calculateWaypointRoute = async () => {
    if (!plannedRoute) {
      setRouteError(null)
      onOpenRouteSelector()
      return
    }
    if (!selectedTarget && routeSections.length === 0) {
      setRouteError('Bitte zuerst ein interstellares Ziel wählen.')
      return
    }
    if (routeCalculationBlockReason) {
      setRouteError(routeCalculationBlockReason)
      return
    }
    abortActivePlayback('route-recalculated')
    setRouteLoading(true)
    setRouteError(null)
    setDirectSolarRoute(null)
    setOptimizationResult(null)
    const sourceRoute = plannedRoute
    try {
      const response = await fetch('/api/route/simulate', {
        method: 'POST',
        headers: activityRequestHeaders({ 'Content-Type': 'application/json' }),
        body: JSON.stringify({
          mission: draft,
          visual,
          waypointId,
          encounterDay,
          flybyAltitudeKm,
          flybyMode,
          targetRightAscensionDeg: selectedTarget?.rightAscensionDeg,
          targetDeclinationDeg: selectedTarget?.declinationDeg,
          desiredSolarExitSpeedKmS,
          highFidelityNBody,
          flybyAimpoint: aimpointPayload,
          entryCorridor,
          routeSections,
          routeSketch,
          integrateSpacecraft: true,
          sourceRoute: {
            startDate: sourceRoute.startDate,
            auditRunId: sourceRoute.audit?.runId ?? null,
            persistenceRunId: sourceRoute.calculationPersistence?.runId ?? null,
            persistenceVariantId: sourceRoute.calculationPersistence?.variantId ?? null,
          },
        }),
      })
      const payload = await response.json() as WaypointRouteResult | { error?: string }
      if (!response.ok || 'error' in payload) throw new Error('error' in payload ? payload.error : `HTTP ${response.status}`)
      setPlannedRoute(payload as WaypointRouteResult)
      setPlannedMissionDate((payload as WaypointRouteResult).startDate)
      setRoutePlanStatus('confirmed')
      setElapsedDays((payload as WaypointRouteResult).trajectory[0]?.elapsedDays ?? 0)
      setPlaying(false)
      setSelectedObject('probe')
      setRouteValidationPending(false)
    } catch (error) {
      // Never destroy the selected solver geometry just because the chosen
      // spacecraft cannot fly it. Keep it as the visible reference and ask
      // for another validation after masses, stages or drives are adjusted.
      setPlannedRoute(sourceRoute)
      setPlannedMissionDate(sourceRoute.startDate)
      setRoutePlanStatus('confirmed')
      setRouteValidationPending(true)
      setRouteError(`Satellit kann die ausgewählte Route noch nicht fliegen: ${error instanceof Error ? error.message : 'Validierung fehlgeschlagen.'}`)
    } finally {
      setRouteLoading(false)
    }
  }
  const optimizeLaunchWindow = async () => {
    if (routePlanStatus !== 'confirmed') {
      setRouteError('Bitte zuerst den Hilfslinienplan prüfen und bestätigen.')
      return
    }
    if (!selectedTarget || optimizationLoading) {
      if (!selectedTarget) setRouteError('Bitte zuerst ein interstellares Ziel wählen.')
      return
    }
    setOptimizationLoading(true)
    setRouteError(null)
    try {
      const preflight = await requestSolarEnergyAssessment({
        mission: draft,
        desiredSolarExitSpeedKmS,
      })
      setOptimizationPreflight(preflight)
      if (!preflight.energeticallyReachable) {
        setOptimizationResult(null)
        setDirectSolarRoute(null)
        setShowAlternativeRoutes(false)
        setPlaying(false)
      }
      const optimized = await requestLaunchOptimization({
        mission: draft,
        waypointId,
        encounterDay,
        flybyAltitudeKm,
        flybyMode,
        flybyAimpoint: aimpointPayload,
        entryCorridor,
        targetRightAscensionDeg: selectedTarget.rightAscensionDeg,
        targetDeclinationDeg: selectedTarget.declinationDeg,
        desiredSolarExitSpeedKmS,
        searchStartDate: optimizationStartDate,
        searchWindowDays: optimizationWindowDays,
        confidenceThresholdPct: optimizationThreshold,
        maxIterations: 40,
        maxFullValidations: 8,
      })
      setOptimizationResult(optimized)
      if (!optimized.plausible) {
        // A rejected search minimum is still useful guidance: keep it visible
        // as an approach suggestion in the shared 2D/3D route state, but do
        // not promote its mission result as a validated flight.
        setEncounterDay(optimized.optimizedEncounterDay)
        setOptimizationStartDate(optimized.optimizedStartDate)
        setOptimizationWindowDays(optimized.optimizedSearchWindowDays)
        setDraft((current) => ({ ...current, startDate: optimized.optimizedStartDate }))
        setPlannedRoute(optimized.route)
        setPlannedMissionDate(optimized.optimizedStartDate)
        setDirectSolarRoute(null)
        setShowAlternativeRoutes(false)
        setShowRouteGuide(true)
        setElapsedDays(0)
        setPlaying(false)
        setRouteError('Noch nicht flugfähig, aber als Annäherungsvorschlag in 2D/3D übernommen. Passe Korridor, Datum oder Budget weiter an.')
        return
      }
      // Feed the converged boundary values back into the three coupled input
      // parameters. The result still retains the original requested plan for
      // comparison and audit, while a following run continues from the found
      // basin instead of restarting at the old date.
      setEncounterDay(optimized.optimizedEncounterDay)
      setOptimizationStartDate(optimized.optimizedStartDate)
      setOptimizationWindowDays(optimized.optimizedSearchWindowDays)
      setDraft((current) => ({ ...current, startDate: optimized.optimizedStartDate }))
      // The visible primary route is the gravity-assist route, so its epoch
      // and mission context must also drive the moving planets and HUD.
      setResult(optimized.mission)
      setMissionResultVisible(false)
      setPlannedRoute(optimized.route)
      setPlannedMissionDate(optimized.optimizedStartDate)
      setDirectSolarRoute(optimized.alternatives.directSolar.route)
      setElapsedDays(0)
      setPlaying(false)
      setSelectedObject('probe')
    } catch (error) {
      setRouteError(error instanceof Error ? error.message : 'Startfenster-Optimierung fehlgeschlagen.')
    } finally {
      setOptimizationLoading(false)
    }
  }

  useEffect(() => {
    if (!autoReoptimize) return undefined
    const timer = window.setInterval(() => { void optimizeLaunchWindow() }, Math.max(1, recalculationMinutes) * 60_000)
    return () => window.clearInterval(timer)
  }, [
    autoReoptimize,
    draft,
    desiredSolarExitSpeedKmS,
    encounterDay,
    flybyAltitudeKm,
    flybyMode,
    optimizationStartDate,
    entryCorridor,
    aimpointClockAngleDeg,
    aimpointEnabled,
    aimpointRole,
    aimpointScreenRadiusNorm,
    aimpointAltitudeKm,
    optimizationThreshold,
    optimizationWindowDays,
    recalculationMinutes,
    selectedTargetId,
    visual,
    waypointId,
  ])
  const resetAll = () => {
    abortActivePlayback('all-reset')
    clearPendingRouteSketch()
    const defaults = { ...DEFAULT_MISSION_CONFIG, startDate: new Date().toISOString().slice(0, 10) }
    setDraft(defaults)
    setOptimizationStartDate(defaults.startDate)
    setDesiredSolarExitSpeedKmS(100)
    setVisual(DEFAULT_VISUAL_CONFIG)
    setResult(null)
    setMissionResultVisible(false)
    setPlannedRoute(null)
    setDirectSolarRoute(null)
    setOptimizationResult(null)
    setRoutePlanStatus('hidden')
    setRouteSketch(null)
    routeSketchRef.current = null
    setRouteSketchHistory([])
    setRouteSketchSelection(null)
    setRouteDrawTool('move')
    setRouteTransformMode('translate')
    setRouteSketchDragging(false)
    setEntryCorridor(DEFAULT_ENTRY_CORRIDOR)
    setEntryCorridorEditorOpen(false)
    setElapsedDays(0)
    setPlaying(false)
    setSimulationError(null)
  }
  const saveSimulationPreset = () => {
    localStorage.setItem('solar-oberth-preset', JSON.stringify({ draft, visual }))
  }
  const loadSimulationPreset = () => {
    const stored = localStorage.getItem('solar-oberth-preset')
    if (!stored) return
    const preset = JSON.parse(stored) as { draft: MissionConfig; visual: VisualConfig }
    setDraft(preset.draft)
    setVisual(preset.visual)
    setOptimizationStartDate(preset.draft.startDate)
    invalidateRoutePlan()
    setResult(null)
    setMissionResultVisible(false)
    setPlannedRoute(null)
    setPlannedMissionDate(null)
    setDirectSolarRoute(null)
    setOptimizationResult(null)
    setElapsedDays(0)
    setPlaying(false)
  }

  if (loadError) return <p className="status-message">3D-Daten konnten nicht geladen werden: {loadError}</p>
  if (!data || !moonCatalogue) return <p className="status-message">Planeten- und Monddaten werden geladen …</p>

  return (
    <section
      className={`three-d-layout mission-layout ${missionPlannerOpen ? 'planner-open' : 'planner-collapsed'} ${parameterPanelOpen ? 'parameters-open' : 'parameters-collapsed'} ${routePlanStatus === 'review' ? 'drawing-active' : ''}`}
      aria-label="Interaktives Planeten- und Missionsmodell"
    >
      <button
        className="sidebar-toggle sidebar-toggle-left"
        type="button"
        aria-label={missionPlannerOpen ? 'Missionsplanung einklappen' : 'Missionsplanung ausklappen'}
        aria-expanded={missionPlannerOpen}
        title={missionPlannerOpen ? 'Missionsplanung einklappen' : 'Missionsplanung ausklappen'}
        onClick={() => setMissionPlannerOpen((open) => !open)}
      >
        <span aria-hidden="true">{missionPlannerOpen ? '‹' : '›'}</span>
      </button>
      <button
        className="sidebar-toggle sidebar-toggle-right"
        type="button"
        aria-label={parameterPanelOpen ? 'Objektdaten und Simulation einklappen' : 'Objektdaten und Simulation ausklappen'}
        aria-expanded={parameterPanelOpen}
        title={parameterPanelOpen ? 'Objektdaten und Simulation einklappen' : 'Objektdaten und Simulation ausklappen'}
        onClick={() => setParameterPanelOpen((open) => !open)}
      >
        <span aria-hidden="true">{parameterPanelOpen ? '›' : '‹'}</span>
      </button>
      <div className={`scene-wrap navigation-${navigationMode}`}>
        <Canvas
          camera={WEBGL_CAMERA}
          dpr={rendererDpr}
          gl={WEBGL_RENDERER_OPTIONS}
          onCreated={configureWebGLRenderer}
          fallback={<div className="webgl-fallback">WebGL konnte nicht initialisiert werden. Bitte Hardwarebeschleunigung im Browser aktivieren.</div>}
        >
          <color attach="background" args={['#02050d']} />
          <fog attach="fog" args={['#02050d', 95, 240]} />
          <PerformanceMonitor flipflops={3} onDecline={reduceRendererLoad} onIncline={restoreRendererQuality} onFallback={reduceRendererLoad} />
          <ambientLight intensity={0.18} />
          <hemisphereLight args={['#8fcfff', '#09030f', 0.22]} />
          <Stars radius={120} depth={60} count={2200} factor={3} saturation={0.25} fade speed={0.3} />
          <MilkyWayBackground />
          {routePlanStatus === 'review' && <Grid
            args={[180, 180]}
            position={[0, -0.025, 0]}
            cellSize={1}
            cellThickness={0.35}
            cellColor="#18334c"
            sectionSize={5}
            sectionThickness={0.7}
            sectionColor="#315f7e"
            fadeDistance={90}
            fadeStrength={1.4}
            side={THREE.DoubleSide}
          />}
          <Sun sun={data.sun} orbitScale={visual.orbitScale} sizeScale={visual.planetScale} />
          <InterstellarTargets
            targets={INTERSTELLAR_TARGETS}
            selectedId={selectedTargetId}
            onSelect={(target) => selectInterstellarTarget(target.id)}
            guideStart={routePlanNodes?.earth}
            selectedPositionOverride={selectedTargetScenePosition}
            hideGuide={Boolean(routePlanStatus !== 'hidden' || (showRouteGuide && (plannedRoute || directSolarRoute)))}
            onInfoDragChange={handleInfoDragChange}
          />
          {routePlanStatus === 'review' && routePlanNodes && routeSketch && (
            <RoutePlanPreview
              {...routePlanNodes}
              requestedPlan={requestedPlanNodes}
              confirmed={false}
              sketch={routeSketch}
              drawTool={routeDrawTool}
              transformMode={routeTransformMode}
              selection={routeSketchSelection}
              editable
              onFocusWaypoint={focusRouteWaypoint}
              onSketchChange={handleRouteSketchChange}
              onSelectionChange={setRouteSketchSelection}
              onEditingChange={handleRouteSketchEditingChange}
            />
          )}
          {entryCorridor.enabled && routePlanNodes && (routePlanStatus === 'review' || plannedRoute) && (
            <EntryCorridorMarker
              position={routePlanNodes.waypoint}
              radius={Math.max(routePlanNodes.waypointRadius * 1.55, 0.62)}
              definition={entryCorridor}
            />
          )}
          {data.planets.map((planet) => {
            const size = scaledRadius(planet, data.sun.radiusKm, visual)
            return (
              <group key={planet.id}>
                {visual.showOrbits && <Orbit planet={planet} distanceScale={visual.orbitScale} inclinationScale={visual.inclinationScale} />}
                {visual.showPlanets && !(routePlanStatus === 'review' && planet.id === waypointId) && (
                  <Suspense fallback={null}>
                    <PlanetMesh
                      planet={planet}
                      size={size}
                      timestampMs={timestampMs}
                      distanceScale={visual.orbitScale}
                      inclinationScale={visual.inclinationScale}
                      ringScale={visual.saturnRingScale}
                      showLabels={visual.showLabels}
                      onSelect={selectPlanet}
                    />
                  </Suspense>
                )}
                {showMoons && visual.showPlanets && selectedPlanet?.id === planet.id && selectedMoons.length > 0 && (
                  <MoonSystem
                    moons={selectedMoons}
                    planet={planet}
                    planetSize={size}
                    timestampMs={timestampMs}
                    distanceScale={visual.orbitScale}
                    inclinationScale={visual.inclinationScale}
                    onSelectMoon={setSelectedMoon}
                  />
                )}
              </group>
            )
          })}
          {visibleMissionResult && !plannedRoute && <MissionTrajectory result={visibleMissionResult} elapsedDays={elapsedDays} visual={visual} />}
          {plannedRoute && routePlanStatus !== 'review' && <PlannedWaypointRoute route={plannedRoute} orbitScale={visual.orbitScale} inclinationScale={visual.inclinationScale} elapsedDays={elapsedDays} showDispersion={showRouteDispersion} dispersionWidth={dispersionWidth} showNavigationGuide={showRouteGuide} encounterBodyRadius={encounterPlanetRadius} probeScale={visual.probeScale} targetPosition={selectedTargetScenePosition} onInfoDragChange={handleInfoDragChange} />}
          {showAlternativeRoutes && directSolarRoute && <DirectSolarRoute route={directSolarRoute} orbitScale={visual.orbitScale} inclinationScale={visual.inclinationScale} showNavigationGuide={showRouteGuide && routePlanStatus === 'hidden'} targetPosition={selectedTargetScenePosition} onInfoDragChange={handleInfoDragChange} />}
          <PlanetCameraControls
            request={cameraFocusRequest}
            focusPosition={focusedPlanetPosition}
            focusRadius={focusedPlanetRadius}
            navigationMode={navigationMode}
            enabled={!overlayDragActive && !routeSketchDragging && (routePlanStatus !== 'review' || routeDrawTool === 'move')}
          />
          <GizmoHelper alignment="bottom-right" margin={[86, 86]}>
            <GizmoViewport axisColors={['#ff5a67', '#72ff8f', '#68a8ff']} labelColor="#07101d" labels={['X', 'Y', 'Z']} />
          </GizmoHelper>
        </Canvas>

        {plannedRoute && routePlanStatus !== 'review' && <FlybyFocusInset route={plannedRoute} elapsedDays={elapsedDays} />}
        {entryCorridorEditorOpen && routePlanNodes && (
          <EntryCorridorEditor
            waypointName={routePlanNodes.waypointName}
            waypointColor={routePlanNodes.waypointColor}
            definition={entryCorridor}
            onChange={(definition) => {
              setEntryCorridor(definition)
              setPlannedRoute(null)
              setPlannedMissionDate(null)
            }}
            onClose={() => setEntryCorridorEditorOpen(false)}
          />
        )}

        <div className="webgl-renderer-status" aria-live="polite" title={rendererInfo ? `Maximale Texturgröße ${rendererInfo.maxTextureSize}px` : 'Renderer wird initialisiert'}>
          <span aria-hidden="true" />
          <strong>{rendererInfo?.api ?? 'WebGL'}</strong>
          <small>{rendererInfo ? `${rendererProfile === 'stabil' ? 'Stabilprofil' : 'Sparprofil'} · DPR ${rendererDpr.toFixed(1)} · AA ${rendererInfo.antialias ? 'an' : 'aus'} · ACES` : 'initialisiert …'}</small>
        </div>

        <div className="mission-hud">
          {routePlanStatus === 'review' ? <>
            <span className="mission-status warning">ENTWURF</span>
            <strong>Zeichenmodus aktiv</strong>
            <span>Werkzeug oben wählen · Entwurf bleibt erhalten</span>
          </> : plannedRoute ? <>
            <span className={`mission-status ${plannedRoute.summary.solarDepartureInjectionApplied && !routeValidationPending ? 'success' : 'warning'}`}>{routeValidationPending ? 'VALIDIEREN' : plannedRoute.summary.solarDepartureInjectionApplied ? 'ROUTE' : 'SOLLROUTE'}</span>
            <strong>{currentRouteSegment?.label ?? 'Wegpunktroute'}</strong>
            <span>Tag {elapsedDays.toFixed(1)} / {playbackEndDay.toFixed(0)}</span>
          </> : visibleMissionResult && currentPoint ? <>
            <span className={`mission-status ${visibleMissionResult.summary.status.toLowerCase()}`}>{visibleMissionResult.summary.status}</span>
            <strong>{currentPoint.phase.replaceAll('_', ' ')}</strong>
            <span>Tag {elapsedDays.toFixed(1)} / {visibleMissionResult.summary.totalFlightDays.toFixed(0)}</span>
          </> : <>
            <span className="mission-status">BEREIT</span>
            <strong>Noch keine Satellitenbahn berechnet</strong>
            <span>Parameter einstellen und Simulation starten</span>
          </>}
        </div>
        <div className="time-controls" role="group" aria-label="Schnellsteuerung">
          <label className="quick-object-search">
            <span>Objekt</span>
            <select aria-label="Objekt suchen" value="" onChange={(event) => selectQuickObject(event.target.value)}>
              <option value="" disabled>Suchen …</option>
              <optgroup label="Sonnensystem">
                <option value="sun">Sonne</option>
                {data.planets.map((planet) => <option key={planet.id} value={planet.id}>{planet.name}</option>)}
              </optgroup>
              <optgroup label="Mission">
                <option value="probe">Sonde</option>
                <option value="carrier">Solar-Oberth-Träger</option>
                <option value="sail">Electric Sail</option>
                <option value="energy_sources">Energiequellen</option>
              </optgroup>
            </select>
          </label>
          {routeSections.length === 0
            ? (
              <button className="quick-route-calculate" type="button" onClick={onOpenRoutePlanner}>
                Route anlegen
              </button>
            )
            : routePlanStatus !== 'review' && (
            <button
              className="quick-route-calculate"
              type="button"
              disabled={routeLoading || Boolean(routeCalculationBlockReason) || (corridorBlocked && !corridorRequiresDynamicCheck)}
              onClick={plannedRoute ? () => void calculateWaypointRoute() : onOpenRouteSelector}
            >
              {routeLoading ? 'Validiert …' : !plannedRoute ? 'Solver-Route auswählen' : routeValidationPending ? 'Route mit Satellit validieren' : 'Satellit neu validieren'}
            </button>
            )}
          <button
            className={routePlanStatus === 'review' ? 'active' : ''}
            type="button"
            disabled={!selectedTarget || routeLoading || !routePlanNodes}
            aria-pressed={routePlanStatus === 'review'}
            onClick={activateRouteDrawing}
          >
            {routePlanStatus === 'review' ? 'Zeichnen · aktiv' : routeSketch ? 'Entwurf bearbeiten' : 'Route zeichnen'}
          </button>
          {routePlanStatus === 'review' && routeSketch && <>
            <label className="quick-draw-select">
              <span>Werkzeug</span>
              <select
                aria-label="Zeichenwerkzeug"
                value={routeDrawTool}
                disabled={routeLoading}
                onChange={(event) => {
                  setRouteDrawTool(event.target.value as RouteDrawTool)
                  setRouteSketchSelection(null)
                }}
              >
                <option value="move">Auswählen</option>
                <option value="route-point">Stützpunkt</option>
                <option value="line">Linie</option>
                <option value="radius">Radius/Kreis</option>
              </select>
            </label>
            <label className="quick-draw-select">
              <span>3D</span>
              <select
                aria-label="3D-Transformation"
                value={routeTransformMode}
                disabled={routeLoading}
                onChange={(event) => {
                  setRouteDrawTool('move')
                  setRouteTransformMode(event.target.value as RouteTransformMode)
                }}
              >
                <option value="translate">Verschieben</option>
                <option value="rotate">Kreis drehen</option>
              </select>
            </label>
            <button type="button" disabled={routeLoading || routeSketchHistory.length === 0} title="Rückgängig" aria-label="Zeichnung rückgängig" onClick={undoRouteSketch}>↶</button>
            <button type="button" disabled={routeLoading || !routeSketchSelection} title="Auswahl löschen" aria-label="Auswahl aus Zeichnung löschen" onClick={deleteSelectedSketchElement}>⌫</button>
            <button
              className={entryCorridorEditorOpen ? 'active corridor-draw-action' : 'corridor-draw-action'}
              type="button"
              disabled={routeLoading || !routePlanNodes}
              aria-pressed={entryCorridorEditorOpen}
              onClick={toggleEntryCorridorEditor}
            >
              {entryCorridorEditorOpen ? 'Korridor · offen' : 'Korridor zeichnen'}
            </button>
            <button className="quick-route-calculate" type="button" disabled={routeLoading || Boolean(routeCalculationBlockReason) || (corridorBlocked && !corridorRequiresDynamicCheck)} onClick={plannedRoute ? () => void calculateWaypointRoute() : onOpenRouteSelector}>{routeLoading ? 'Validiert …' : !plannedRoute ? 'Solver-Route auswählen' : routeValidationPending ? 'Route mit Satellit validieren' : 'Satellit neu validieren'}</button>
          </>}
          {routePlanStatus !== 'review' && <>
            <button className={playing ? 'active' : ''} type="button" disabled={!canPlay || playbackAuditStatus === 'starting'} onClick={() => void toggleMissionPlayback()}>{playing ? 'Pause' : playbackAuditStatus === 'starting' ? 'Log startet …' : 'Mission abspielen'}</button>
            <button className={showMoons ? 'active' : ''} type="button" aria-pressed={showMoons} onClick={() => setShowMoons((visible) => !visible)}>Monde · {showMoons ? 'an' : 'aus'}</button>
            <button type="button" disabled={!canPlay} onClick={() => setSelectedObject('probe')}>Sonde</button>
          </>}
          <button className={navigationMode === 'pan' ? 'active' : ''} type="button" aria-pressed={navigationMode === 'pan'} onClick={() => setNavigationMode('pan')}>Ziehen</button>
          <button className={navigationMode === 'rotate' ? 'active' : ''} type="button" aria-pressed={navigationMode === 'rotate'} onClick={() => setNavigationMode('rotate')}>Drehen</button>
        </div>
        {routeCalculationBlockReason && routePlanStatus !== 'review' && (
          <div className="route-calculation-block-banner" role="status">
            {routeCalculationBlockReason}
          </div>
        )}
        <DraggableOverlayPanel
          className="target-controls"
          ariaLabel="Missionsplanung und Solver-Navigation"
          draggable={false}
          header={<div className="target-panel-title"><strong>Missionsplanung</strong><small>{routeSections.length === 0 ? 'Blanko-Projekt · keine Route' : `${selectedTarget?.name ?? 'Kein interstellares Ziel'} → ${data.planets.find((planet) => planet.id === waypointId)?.name ?? waypointId}`}</small></div>}
        >
          <div className="target-controls-body">
          <details className="target-control-section planner-simulation" open>
            <summary><span>Simulation</span><small>{canPlay ? `Tag ${elapsedDays.toFixed(1)}` : 'bereit'}</small></summary>
            <div className="target-control-section-content">
              <div className="planner-field-group">
                <strong className="planner-group-title">Zeitsteuerung</strong>
                <div className="transport-controls">
                  <button type="button" disabled={!canPlay || playbackAuditStatus === 'starting'} onClick={() => void toggleMissionPlayback()}>{playing ? 'Pause' : playbackAuditStatus === 'starting' ? 'Log startet …' : 'Play'}</button>
                  <button type="button" disabled={!canPlay} onClick={() => seekMissionPlayback(elapsedDays + stepDays)}>+ Schritt</button>
                  <button type="button" onClick={resetMissionPlayback}>Zeit zurück</button>
                </div>
                <div className={`playback-log-status playback-log-${playbackAuditStatus}`}>
                  <strong>Missionslauf-Log</strong>
                  <span>{playbackAuditStatus === 'recording' ? 'zeichnet auf' : playbackAuditStatus === 'paused' ? 'pausiert' : playbackAuditStatus === 'complete' ? 'Ziel erreicht · vollständig' : playbackAuditStatus === 'starting' ? 'wird angelegt' : playbackAuditStatus === 'reset' ? 'zurückgesetzt' : 'bereit'}</span>
                  {playbackAuditIdRef.current && <small>{playbackAuditIdRef.current}</small>}
                  <a href="/api/audit/latest-playback" target="_blank" rel="noreferrer">Letzten Lauf ansehen</a>
                  <a href="/api/audit/playback-log">JSONL herunterladen</a>
                </div>
                {playbackAuditError && <div className="validation-box" role="alert">Logfehler: {playbackAuditError}</div>}
                <label className="range-field">
                  <span>Missionstag<output>{elapsedDays.toFixed(1)} / {playbackEndDay.toFixed(1)}</output></span>
                  <input
                    type="range"
                    min="0"
                    max={Math.max(1, playbackEndDay)}
                    step="0.1"
                    value={Math.min(elapsedDays, Math.max(1, playbackEndDay))}
                    disabled={!canPlay}
                    onChange={(event) => seekMissionPlayback(event.target.valueAsNumber)}
                  />
                </label>
                <label className="range-field">
                  <span>Geschwindigkeit<output>{simulationSpeed.toLocaleString('de-DE')} Tage/s</output></span>
                  <input type="range" value={simulationSpeed} min="0.1" max="365" step="0.1" onChange={(event) => setSimulationSpeed(event.target.valueAsNumber)} />
                </label>
                <label className="parameter-field">
                  <span>Einzelschritt</span>
                  <select value={stepDays} onChange={(event) => setStepDays(Number(event.target.value))}>
                    <option value="0.000694">1 Minute</option>
                    <option value="0.041667">1 Stunde</option>
                    <option value="1">1 Tag</option>
                    <option value="7">1 Woche</option>
                    <option value="30">1 Monat</option>
                  </select>
                </label>
                <label className="parameter-field">
                  <span>Startdatum</span>
                  <input type="date" value={draft.startDate} onChange={(event) => { setDraft((current) => ({ ...current, startDate: event.target.value })); abortActivePlayback('spacecraft-configuration-changed'); setRouteValidationPending(Boolean(plannedRoute)); setOptimizationPreflight(null) }} />
                </label>
              </div>
              {(simulationError || validationErrors.length > 0) && <div className="validation-box" role="alert">{simulationError ?? validationErrors.join(' ')}</div>}
            </div>
          </details>
          <details className="target-control-section" open>
            <summary><span>Ziel & Vorbeiflug</span><small>{routePlanStatus === 'confirmed' ? 'Plan bestätigt' : routePlanStatus === 'review' ? 'Entwurf offen' : 'Eingabe'}</small></summary>
            <div className="target-control-section-content">
          <div className="planner-field-group">
          <strong className="planner-group-title">Zielgeometrie</strong>
          <label>
            <span>Interstellares Ziel</span>
            <select value={selectedTargetId} onChange={(event) => selectInterstellarTarget(event.target.value)}>
              <option value="">Kein Ziel</option>
              <optgroup label="Nahe Sternsysteme">
                {INTERSTELLAR_TARGETS.filter((target) => target.kind === 'stellar-system').map((target) => (
                  <option key={target.id} value={target.id}>{target.name} · {target.distanceLy.toLocaleString('de-DE')} Lj</option>
                ))}
              </optgroup>
              <optgroup label="Milchstraße">
                {INTERSTELLAR_TARGETS.filter((target) => target.kind === 'galactic-center').map((target) => (
                  <option key={target.id} value={target.id}>{target.name} · {target.distanceLy.toLocaleString('de-DE')} Lj</option>
                ))}
              </optgroup>
            </select>
          </label>
          {selectedTarget && <span>RA {selectedTarget.rightAscensionDeg.toFixed(2)}° · Dec {selectedTarget.declinationDeg.toFixed(2)}° · Klickbare Zielmarke im View</span>}
          <label>
            <span>Zielkörper des aktiven Abschnitts</span>
            <select aria-label="Zielkörper des aktiven Abschnitts" value={waypointId} onChange={(event) => { setWaypointId(event.target.value); invalidateRoutePlan(); setPlannedRoute(null); setDirectSolarRoute(null); setOptimizationResult(null) }}>
              {routeSections.length === 0 && <option value="" disabled>Kein Abschnitt angelegt</option>}
              <optgroup label="Sonnensystem">
                <option value="sun">Sonne</option>
                {data.planets.map((planet) => <option key={planet.id} value={planet.id}>{planet.name}</option>)}
              </optgroup>
              <optgroup label="Monde">
                {moonCatalogue.moons.filter((moon) => moon.semiMajorAxisKm && moon.orbitalPeriodDays).map((moon) => (
                  <option key={moon.id} value={moon.id}>
                    {data.planets.find((planet) => planet.id === moon.parentId)?.name ?? moon.parentId} · {moon.name}
                  </option>
                ))}
              </optgroup>
            </select>
          </label>
          </div>
          <div className="planner-field-group">
          <strong className="planner-group-title">Begegnung</strong>
          <label><span>Erste Begegnungsschätzung (Missionstag)</span><input type="number" min="500" max="7305" step="1" value={encounterDay} onChange={(event) => { setEncounterDay(event.target.valueAsNumber); invalidateRoutePlan(); setPlannedRoute(null) }} /></label>
          <span>Dieser Tag ist nur der Startwert der Suche. Das tatsächliche Begegnungsdatum wird aus Startfenster und Flugzeit berechnet.</span>
          <label><span>Vorbeiflughöhe</span><input type="number" min="100" step="1000" value={flybyAltitudeKm} onChange={(event) => { setFlybyAltitudeKm(event.target.valueAsNumber); invalidateRoutePlan(); setPlannedRoute(null) }} /></label>
          <label>
            <span>Vorbeiflugprofil</span>
            <select value={flybyMode} onChange={(event) => { setFlybyMode(event.target.value as 'acceleration' | 'observation'); invalidateRoutePlan(); setPlannedRoute(null) }}>
              <option value="acceleration">Beschleunigung maximieren</option>
              <option value="observation">Beobachtung / Zielkurs</option>
            </select>
          </label>
          </div>
          <div className="planner-field-group">
          <strong className="planner-group-title">Propagation</strong>
          <label>
            <span>Simultane N-Körper-Validierung</span>
            <input
              type="checkbox"
              checked={highFidelityNBody}
              onChange={(event) => {
                setHighFidelityNBody(event.target.checked)
                invalidateRoutePlan()
                setPlannedRoute(null)
              }}
            />
          </label>
          <span>Differentiell korrigierte DOP853-Propagation mit Sonne und allen acht Planeten; deutlich rechenintensiver.</span>
          </div>
          {routePlanStatus === 'hidden' && <button type="button" disabled={routeLoading || !selectedTarget} onClick={beginRouteReview}>Routenentwurf öffnen</button>}
          {routePlanStatus === 'review' && routeSketch && <div className="route-alternatives route-sketch-controls">
            <strong>Routenentwurf Erde → Sonne → {routePlanNodes?.waypointName ?? 'Wegpunkt'} → Ziel</strong>
            <span className="route-ok">Die gelben, gesperrten Anker liegen exakt auf Erde, Sonne, {routePlanNodes?.waypointName ?? 'Wegpunkt'} am Begegnungstag und Ziel. Ein aktivierter SOI-Korridor ersetzt das Planetenzentrum als Transferziel.</span>
            <span>Zeichenwerkzeug und 3D-Transformation bleiben während des gesamten Entwurfs oben in der Aktionsbox verfügbar. Element oder Griff anklicken und an den roten, grünen oder blauen Achsen bewegen; der helle Außengriff ändert den Kreisradius.</span>
            <span className={routeSketchSelection ? 'route-ok' : ''}>{routeSketchSelection ? `Ausgewählt: ${routeSketchSelection.kind === 'node' ? 'Stützpunkt' : routeSketchSelection.kind.startsWith('line') ? 'Linie' : routeSketchSelection.kind === 'circle-radius' ? 'Kreisradius' : 'Kreis'}` : 'Kein Element ausgewählt'} · Strg+Z: rückgängig · Entf: Auswahl löschen</span>
            <div className="entry-corridor-route-controls">
              <label className="optimizer-check">
                <span>SOI-Eintrittskorridor als Zielbereich</span>
                <input
                  type="checkbox"
                  checked={entryCorridor.enabled}
                  onChange={(event) => {
                    setEntryCorridor((current) => ({ ...current, enabled: event.target.checked }))
                    if (event.target.checked) setAimpointEnabled(false)
                    setPlannedRoute(null)
                  }}
                />
              </label>
              <button
                type="button"
                disabled={!entryCorridor.enabled}
                onClick={() => setEntryCorridorEditorOpen(true)}
              >
                Korridor mit Bögen zeichnen & zoomen
              </button>
              <span>
                ±{entryCorridor.horizontalHalfAngleDeg.toFixed(1)}° horizontal ·
                ±{entryCorridor.verticalHalfAngleDeg.toFixed(1)}° vertikal ·
                Drehung {entryCorridor.rotationDeg.toFixed(0)}°
              </span>
              {corridorBlocked && <span className="route-warning">{corridorRequiresDynamicCheck ? 'Schematischer Anflug gesperrt; die räumliche Abschnittskette führt eine dynamische Kollisionsprüfung aus: ' : 'Zielkorridor gesperrt: '}{corridorBlockMessage}</span>}
              {routeCalculationBlockReason && <span className="route-warning">{routeCalculationBlockReason}</span>}
            </div>
            {selectedSketchCircle && <div className="circle-orientation-controls">
              <strong>Kreisausrichtung im Raum</strong>
              {(['X', 'Y', 'Z'] as const).map((axis, index) => <label key={axis}>
                <span>{axis}-Winkel</span>
                <input
                  type="number"
                  step="1"
                  value={THREE.MathUtils.radToDeg(selectedSketchCircle.rotation?.[index] ?? 0).toFixed(1)}
                  onChange={(event) => setSelectedCircleRotationDeg(index as 0 | 1 | 2, event.target.valueAsNumber)}
                />
                <span>°</span>
              </label>)}
              <div className="route-sketch-secondary-actions">
                <button type="button" onClick={() => setSelectedCircleRotationDeg(0, 15, true)}>X +15°</button>
                <button type="button" onClick={() => setSelectedCircleRotationDeg(1, 15, true)}>Y +15°</button>
                <button type="button" onClick={() => setSelectedCircleRotationDeg(2, 15, true)}>Z +15°</button>
                <button type="button" onClick={() => {
                  const current = routeSketchRef.current
                  if (!current) return
                  handleRouteSketchChange({ ...current, circles: current.circles.map((circle) => circle.id === selectedSketchCircle.id ? { ...circle, rotation: [0, 0, 0] } : circle) }, true)
                }}>Winkel nullen</button>
              </div>
            </div>}
            <span>{routeSketch.nodes.filter((node) => !node.locked).length} Stützpunkte · {routeSketch.lines.length} Hilfslinien · {routeSketch.circles.length} Radien</span>
            <div className="route-sketch-secondary-actions">
              <button type="button" disabled={routeSketchHistory.length === 0} onClick={undoRouteSketch}>Rückgängig · Strg+Z</button>
              <button type="button" disabled={!routeSketchSelection} onClick={deleteSelectedSketchElement}>Auswahl löschen · Entf</button>
              <button type="button" onClick={removeLastSketchElement}>Letztes Element entfernen</button>
              <button type="button" onClick={resetRouteSketch}>Entwurf zurücksetzen</button>
            </div>
            <span>Der Entwurf verändert die visuelle Führung. Die anschließend berechnete Nominalbahn bleibt physikalisch und wird zwingend durch den festen Ephemeridenanker geführt.</span>
            <button className="ai-primary-action" type="button" disabled={routeLoading || Boolean(routeCalculationBlockReason) || (corridorBlocked && !corridorRequiresDynamicCheck)} onClick={() => { setRouteDrawTool('move'); setRouteSketchSelection(null); void calculateWaypointRoute() }}>{routeLoading ? 'Berechne komplexe Bahn …' : 'Entwurf übernehmen & Bahn physikalisch berechnen'}</button>
            <button type="button" onClick={discardRouteSketch}>Entwurf verwerfen</button>
          </div>}
          {routePlanStatus === 'confirmed' && <span className="route-ok">Routenplan bestätigt · Nach der Berechnung bleibt nur die Nominalbahn; Referenz und Streuung sind zuschaltbar.</span>}
          {routeError && <span className="route-warning">{routeError}</span>}
          <span className="planner-hint">Ein Klick auf einen Planeten setzt ihn ebenfalls als Wegpunkt.</span>
            </div>
          </details>
          <details className="target-control-section">
            <summary><span>Solver-Suche</span><small>{optimizationLoading ? 'prüft …' : optimizationPreflight && !optimizationPreflight.energeticallyReachable ? 'Ziel außerhalb Budget' : optimizationResult ? (optimizationResult.plausible ? 'flugfähig' : 'keine Freigabe') : 'optional'}</small></summary>
            <div className="target-control-section-content">
          <div className="ai-integrated-block">
          <div className="optimizer-divider"><strong>Numerische Randwertsuche</strong><span>vorwärts + rückwärts · Mehrpass 12/8</span></div>
          <span>Der Optimierer koppelt Sonnenaustritt, Ankunft und B-Plane direkt an das gewählte Ziel und den Vorbeiflug.</span>
          <label><span>Zielgeschwindigkeit Sonnenaustritt (km/s bei 1 AE)</span><input type="number" min="1" max="1000" step="1" value={desiredSolarExitSpeedKmS} onChange={(event) => { setDesiredSolarExitSpeedKmS(event.target.valueAsNumber); invalidateRoutePlan(); setPlannedRoute(null); setOptimizationResult(null) }} /></label>
          <label><span>Ausgangs-Start / Suchzentrum</span><input type="date" value={optimizationStartDate} onChange={(event) => { setOptimizationStartDate(event.target.value); invalidateRoutePlan() }} /></label>
          <label><span>Suchhorizont je Richtung (Tage)</span><input type="number" min="500" max="7305" step="1" value={optimizationWindowDays} onChange={(event) => { setOptimizationWindowDays(event.target.valueAsNumber); invalidateRoutePlan() }} /></label>
          <span>Startdatum, Begegnungstag und Horizont: bidirektional mit 100 → 10 → 5 → 1 Tagen · Grenzen 500 Tage bis 20 Jahre.</span>
          <label><span>Mindestkonfidenz</span><input type="number" min="90" max="99.9" step="0.5" value={optimizationThreshold} onChange={(event) => setOptimizationThreshold(event.target.valueAsNumber)} /></label>
          <button className="ai-primary-action" type="button" disabled={optimizationLoading || routeLoading || routePlanStatus !== 'confirmed' || corridorBlocked} onClick={() => void optimizeLaunchWindow()}>{optimizationLoading ? 'Optimierer koppelt Sonne, Jupiter und Ziel …' : routePlanStatus === 'confirmed' ? 'Route bidirektional optimieren' : 'Zuerst Routenplan bestätigen'}</button>
          <label className="optimizer-check"><span>Zyklisch neu rechnen</span><input type="checkbox" checked={autoReoptimize} onChange={(event) => setAutoReoptimize(event.target.checked)} /></label>
          {autoReoptimize && <label><span>Intervall (min)</span><input type="number" min="1" step="1" value={recalculationMinutes} onChange={(event) => setRecalculationMinutes(event.target.valueAsNumber)} /></label>}
          {optimizationPreflight && !optimizationPreflight.energeticallyReachable && !optimizationResult && (
            <div className="optimizer-result optimizer-result-blocked">
              <div className="optimizer-result-status">
                <strong>Harte Budgetgrenze erkannt</strong>
                <span>Die gewünschte Geschwindigkeit ist mit dem aktuellen Oberth-Impuls nicht erreichbar. Die Konstellationssuche läuft trotzdem weiter und ermittelt die beste geometrische Annäherung.</span>
              </div>
              <div className="optimizer-limit-card">
                <strong>Hauptursache · Zielgeschwindigkeit außerhalb des Antriebsbudgets</strong>
                <span>Gewünscht: {optimizationPreflight.desiredExitSpeedKmS.toFixed(1)} km/s bei 1 AE</span>
                <span>Mit {optimizationPreflight.availableOberthDeltaVKmS.toFixed(1)} km/s Oberth-Δv erreichbar: höchstens {optimizationPreflight.maximumExitSpeedWithAvailableBurnKmS.toFixed(1)} km/s</span>
                <span>Für das aktuelle Ziel erforderlich: mindestens {optimizationPreflight.minimumOberthDeltaVForDesiredSpeedKmS.toFixed(1)} km/s Oberth-Δv</span>
                <div className="optimizer-remedies">
                  <button
                    type="button"
                    onClick={() => {
                      setDesiredSolarExitSpeedKmS(Math.floor(optimizationPreflight.maximumExitSpeedWithAvailableBurnKmS))
                      invalidateRoutePlan()
                    }}
                  >
                    Ziel auf erreichbare Geschwindigkeit setzen
                  </button>
                  <button
                    type="button"
                    onClick={() => {
                      const requiredDeltaV = Math.ceil(optimizationPreflight.minimumOberthDeltaVForDesiredSpeedKmS * 10) / 10
                      setDraft((current) => ({ ...current, oberthDeltaVKmS: requiredDeltaV }))
                      invalidateRoutePlan()
                    }}
                  >
                    Erforderliches Oberth-Δv übernehmen
                  </button>
                </div>
              </div>
            </div>
          )}
          {optimizationResult && (
            <div className={`optimizer-result optimizer-result-${optimizationResult.plausible ? 'success' : 'blocked'}`}>
              <div className="optimizer-result-status">
                <strong>{optimizationResult.plausible ? 'Flugfähige Route gefunden' : 'Verbesserter Arbeitsvorschlag gefunden'}</strong>
                <span>{optimizationResult.plausible ? 'Vollmodell und Antriebsbudget sind erfüllt.' : 'Das beste Suchminimum ist in 2D/3D sichtbar und bildet den Ausgangspunkt der nächsten Iteration. Es ist noch nicht als flugfähige Mission validiert.'}</span>
              </div>
              {!optimizationResult.solarEnergyFeasibility.energeticallyReachable && (
                <div className="optimizer-limit-card">
                  <strong>Hauptursache · Zielgeschwindigkeit außerhalb des Antriebsbudgets</strong>
                  <span>Gewünscht: {optimizationResult.solarEnergyFeasibility.desiredExitSpeedKmS.toFixed(1)} km/s bei 1 AE</span>
                  <span>Mit {optimizationResult.solarEnergyFeasibility.availableOberthDeltaVKmS.toFixed(1)} km/s Oberth-Δv erreichbar: höchstens {optimizationResult.solarEnergyFeasibility.maximumExitSpeedWithAvailableBurnKmS.toFixed(1)} km/s</span>
                  <span>Für das aktuelle Ziel erforderlich: mindestens {optimizationResult.solarEnergyFeasibility.minimumOberthDeltaVForDesiredSpeedKmS.toFixed(1)} km/s Oberth-Δv</span>
                  <div className="optimizer-remedies">
                    <button
                      type="button"
                      onClick={() => {
                        setDesiredSolarExitSpeedKmS(Math.floor(optimizationResult.solarEnergyFeasibility.maximumExitSpeedWithAvailableBurnKmS))
                        setOptimizationResult(null)
                        invalidateRoutePlan()
                      }}
                    >
                      Ziel auf erreichbare Geschwindigkeit setzen
                    </button>
                    <button
                      type="button"
                      onClick={() => {
                        const requiredDeltaV = Math.ceil(optimizationResult.solarEnergyFeasibility.minimumOberthDeltaVForDesiredSpeedKmS * 10) / 10
                        setDraft((current) => ({ ...current, oberthDeltaVKmS: requiredDeltaV }))
                        setOptimizationResult(null)
                        invalidateRoutePlan()
                      }}
                    >
                      Erforderliches Oberth-Δv übernehmen
                    </button>
                  </div>
                </div>
              )}
              <div className="optimizer-check-summary">
                <span className={optimizationResult.geometryPlausible ? 'route-ok' : 'route-warning'}>{optimizationResult.geometryPlausible ? '✓ Gekoppelte Fluggeometrie bestanden' : '✕ Gekoppelte Fluggeometrie im Vollmodell nicht bestanden'}</span>
                <span className={optimizationResult.solarEnergyFeasibility.energeticallyReachable ? 'route-ok' : 'route-warning'}>{optimizationResult.solarEnergyFeasibility.energeticallyReachable ? '✓ Antriebsbudget bestanden' : '✕ Antriebsbudget nicht bestanden'}</span>
                <span>{optimizationResult.minimumConfidencePct.toFixed(1)} % numerische Konvergenz · {optimizationResult.evaluations} Kandidaten · {optimizationResult.fullValidationCandidates.length} Vollmodellprüfungen</span>
              </div>
              <details className="optimizer-technical-details">
                <summary>Technische Diagnose und Rechennachweis</summary>
                <span>Bestes Suchminimum: Start {optimizationResult.optimizedStartDate} · Begegnung {formatMissionDate(optimizationResult.optimizedEncounterDate)} · Missionstag {optimizationResult.optimizedEncounterDay.toFixed(1)}</span>
                <span>Suchbereich ±{optimizationResult.searchStrategy.exploredHorizonDays.toFixed(0)} Tage · Raster {optimizationResult.searchStrategy.refinementStepsDays.join(' → ')} Tage</span>
                <span>Jupiter-Randrest {(optimizationResult.bidirectionalSearch.jupiterMatch?.boundaryVelocityResidualKmS ?? 0).toFixed(3)} km/s · Zielfortschritt {optimizationResult.bidirectionalSearch.postFlybyTargetProgressMonotonic ? 'monoton' : 'nicht monoton'}</span>
                {!optimizationResult.plausible && optimizationResult.fullValidationCandidates.find((candidate) => candidate.startDate === optimizationResult.optimizedStartDate && Math.abs(candidate.encounterDay - optimizationResult.optimizedEncounterDay) < 0.01)?.rejectionReasons.map((reason) => <span className="route-warning" key={`optimum-${reason}`}>{reason}</span>)}
                <span>Navigator-Audit {optimizationResult.audit.runId} · <a href="/api/audit/latest-optimizer" target="_blank" rel="noreferrer">Entscheidungsweg</a> · <a href="/api/audit/optimizer-log">JSONL-Log</a></span>
              </details>
            </div>
          )}
          {optimizationResult?.alternatives && (
            <div className="route-alternatives">
              <strong>{optimizationResult.alternatives.recommendationFeasible ? `Flugfähige Empfehlung: ${optimizationResult.alternatives.recommended === 'gravityAssist' ? `${waypointId}-Swing-by` : 'direkter Solar-Oberth-Kurs'}` : 'Keine der beiden Varianten ist mit der aktuellen Konfiguration flugfähig.'}</strong>
              <details>
                <summary>Verglichene Varianten</summary>
                <span>A · Planet: Start {optimizationResult.alternatives.gravityAssist.startDate} · Zielrest {optimizationResult.alternatives.gravityAssist.route.summary.targetAlignmentDeg.toFixed(1)}° · {optimizationResult.alternatives.gravityAssist.feasible ? 'Δv ausreichend' : 'Δv nicht ausreichend'}</span>
                <span>B · Direkt: Start {optimizationResult.alternatives.directSolar.startDate} · Zielrest {optimizationResult.alternatives.directSolar.route.summary.finalTargetAlignmentDeg.toFixed(1)}° · Zielbreite {optimizationResult.alternatives.directSolar.route.summary.targetEclipticLatitudeDeg.toFixed(1)}° · {optimizationResult.alternatives.directSolar.feasible ? 'Δv ausreichend' : 'Δv nicht ausreichend'}</span>
              </details>
              {optimizationResult.alternatives.recommendationFeasible && <label className="optimizer-check"><span>Beide Routenvorschläge anzeigen</span><input type="checkbox" checked={showAlternativeRoutes} onChange={(event) => setShowAlternativeRoutes(event.target.checked)} /></label>}
            </div>
          )}
          </div>
            </div>
          </details>
          <details className="target-control-section">
            <summary><span>Ergebnis & Nachweis</span><small>{plannedRoute ? (plannedRoute.summary.feasibleWithConfiguredBurn ? 'erreichbar' : 'nicht erreichbar') : 'noch keine Route'}</small></summary>
            <div className="target-control-section-content">
          {plannedRoute?.uncertainty && (
            <div className="dispersion-controls">
              <label className="optimizer-check"><span>95-%-Streuung anzeigen</span><input type="checkbox" checked={showRouteDispersion} onChange={(event) => setShowRouteDispersion(event.target.checked)} /></label>
              <label className="optimizer-check"><span>Gestrichelte Routenführung</span><input type="checkbox" checked={showRouteGuide} onChange={(event) => setShowRouteGuide(event.target.checked)} /></label>
              <label><span>Sichtbare Korridorbreite</span><input type="range" min="0.08" max="1.5" step="0.02" value={dispersionWidth} onChange={(event) => setDispersionWidth(event.target.valueAsNumber)} /></label>
              <span>95-%-Radius am Wegpunkt: {plannedRoute.uncertainty.summary.waypointRadius95Km.toLocaleString('de-DE', { maximumFractionDigits: 0 })} km · Kalman-Zyklen: {plannedRoute.uncertainty.summary.navigationCycles.toLocaleString('de-DE')} · Korridorbreite nur visuell {dispersionWidth.toFixed(2)}</span>
            </div>
          )}
          {plannedRoute && (
            <span className={plannedRoute.summary.feasibleWithConfiguredBurn && !routeValidationPending ? 'route-ok' : 'route-warning'}>
              {routeValidationPending ? 'Solver-Route erhalten · Satellitenkonfiguration muss neu validiert werden' : plannedRoute.summary.feasibleWithConfiguredBurn ? 'Erreichbar' : 'Nicht erreichbar'} · Kurs-Δv {plannedRoute.summary.requiredInjectionDeltaVKmS.toFixed(2)} km/s · Swing-by {plannedRoute.summary.courseChangeDeg?.toFixed(1) ?? '–'}° · Geschwindigkeitsgewinn {plannedRoute.summary.speedGainKmS >= 0 ? '+' : ''}{plannedRoute.summary.speedGainKmS.toFixed(2)} km/s
            </span>
          )}
          {plannedRoute?.spacecraftIntegration && !routeValidationPending && (
            <span className="route-ok">
              Satellit integriert · Startmasse {plannedRoute.spacecraftIntegration.wetMassKg.toLocaleString('de-DE')} kg · Oberth {plannedRoute.spacecraftIntegration.achievedOberthDeltaVKmS.toFixed(2)}/{plannedRoute.spacecraftIntegration.requestedOberthDeltaVKmS.toFixed(2)} km/s · Antriebe {plannedRoute.spacecraftIntegration.enabledPropulsionModules.filter(Boolean).join(', ') || 'keine'}
            </span>
          )}
          {plannedRoute?.validation && (
            <span className={plannedRoute.validation.collisionFree ? 'route-ok' : 'route-warning'}>
              Sonnenkörper {plannedRoute.validation.collisionFree ? 'frei' : 'geschnitten'} · kleinster Abstand {plannedRoute.validation.minimumSolarRadiusKm.toLocaleString('de-DE', { maximumFractionDigits: 0 })} km · Höhe {plannedRoute.validation.minimumSolarAltitudeKm.toLocaleString('de-DE', { maximumFractionDigits: 0 })} km
            </span>
          )}
          {plannedRoute && !plannedRoute.summary.feasibleWithConfiguredBurn && (
            <span className="route-warning">Nur Sollroute: Mindestens ein Übergangs-Δv überschreitet das konfigurierte Budget. Die Mission kann deshalb nicht abgespielt werden.</span>
          )}
          {plannedRoute?.summary.warnings?.map((warning) => <span className="route-warning" key={`summary-warning-${warning}`}>⚠ {warning}</span>)}
          {plannedRoute?.warnings?.map((warning) => <span className="route-warning" key={`payload-warning-${warning}`}>⚠ {warning}</span>)}
          {plannedRoute?.solarBoundary && <span className={plannedRoute.solarBoundary.speedBoundaryReached ? 'route-ok' : 'route-warning'}>1-AE-Sonnenaustritt: {plannedRoute.solarBoundary.actualExitSpeedKmS.toFixed(2)} km/s · Ziel {plannedRoute.solarBoundary.desiredExitSpeedKmS?.toFixed(2) ?? '–'} km/s · erforderlicher Oberth-Vektor {plannedRoute.solarBoundary.requiredOberthVectorDeltaVKmS.toFixed(2)} km/s · Antriebs-Maximum {plannedRoute.solarBoundary.maximumExitSpeedWithAvailableBurnKmS.toFixed(2)} km/s · Mindest-Oberth-Δv fürs Ziel {plannedRoute.solarBoundary.minimumOberthDeltaVForDesiredSpeedKmS.toFixed(2)} km/s</span>}
          {plannedRoute?.transitionDiagnostics?.bidirectionalMatch && <span>Vorwärts/Rückwärts-Kopplung am Jupiter: Bedarf {plannedRoute.transitionDiagnostics.bidirectionalMatch.demandedTurnDeg.toFixed(2)}° / verfügbar {plannedRoute.transitionDiagnostics.bidirectionalMatch.maximumTurnDeg.toFixed(2)}° · Randrest {plannedRoute.transitionDiagnostics.bidirectionalMatch.boundaryVelocityResidualKmS.toFixed(3)} km/s · {plannedRoute.transitionDiagnostics.bidirectionalMatch.passiveMatch ? 'passiv geschlossen' : 'Korrektur erforderlich'}</span>}
          {plannedRoute && !plannedRoute.routeSections?.length && <span className={plannedRoute.summary.targetProgressMonotonic ? 'route-ok' : 'route-warning'}>{plannedRoute.summary.targetProgressMonotonic ? 'Zielbedingung erfüllt: Nach Jupiter verläuft kein Abschnitt mehr vom Ziel weg.' : 'Zielbedingung verletzt: Nach Jupiter besteht noch rückläufiger Zielfortschritt.'}</span>}
          {plannedRoute && !plannedRoute.summary.solarDepartureInjectionApplied && <span className="route-warning">Solarer Übergang nicht ausführbar: benötigt {plannedRoute.summary.requiredInjectionDeltaVKmS.toFixed(2)} km/s bei {(plannedRoute.summary.availableInjectionDeltaVKmS ?? draft.oberthDeltaVKmS).toFixed(2)} km/s Budget und {(plannedRoute.transitionDiagnostics?.burnToLambertDirectionChangeDeg ?? 0).toFixed(2)}° Richtungswechsel. Die gestrichelten Abschnitte sind eine zeitlich abspielbare Sollsimulation, keine freigegebene Flugbahn.</span>}
          {plannedRoute?.summary.flybyMode === 'observation' && <span>Beobachtungsfenster ≈ {plannedRoute.summary.observationWindowHours.toFixed(1)} h · Perizentrum {plannedRoute.summary.periapsisSpeedKmS.toFixed(2)} km/s · Zielabweichung {plannedRoute.summary.targetAlignmentDeg.toFixed(1)}°</span>}
          {plannedRoute?.transitionDiagnostics && <span>SOI-Übergang: Position gekoppelt · Geschwindigkeitsrest Eingang {(plannedRoute.transitionDiagnostics.entryVelocityResidualKmS * 1_000).toFixed(2)} m/s · Soll-Zielimpuls {(plannedRoute.transitionDiagnostics.exitTargetInjectionDeltaVKmS ?? 0).toFixed(2)} km/s / {(plannedRoute.transitionDiagnostics.exitTargetInjectionDirectionChangeDeg ?? 0).toFixed(2)}° · {plannedRoute.transitionDiagnostics.exitTargetInjectionApplied ? 'angewendet' : 'nicht verfügbar, daher nicht propagiert'}</span>}
          {plannedRoute?.transitionDiagnostics?.lambertSelection && <span>Lambert-Zweig: {plannedRoute.transitionDiagnostics.lambertSelection.motion} · Familie {plannedRoute.transitionDiagnostics.lambertSelection.revolutionFamily ?? 0} · Oberth→Lambert Richtung {(plannedRoute.transitionDiagnostics.burnToLambertDirectionChangeDeg ?? 0).toFixed(2)}° · Lambert→SOI Richtung {(plannedRoute.transitionDiagnostics.lambertToHyperbolaDirectionChangeDeg ?? 0).toFixed(4)}°</span>}
          {plannedRoute?.audit && <span>Rechennachweis {plannedRoute.audit.runId} · <a href="/api/audit/latest-route" target="_blank" rel="noreferrer">letzten Lauf prüfen</a> · <a href="/api/audit/route-log">JSONL-Log</a> · <a href="/api/audit/methods" target="_blank" rel="noreferrer">Methodendokument</a></span>}
            </div>
          </details>
          </div>
        </DraggableOverlayPanel>
        {(plannedRoute || visibleMissionResult) && <div className="phase-legend" aria-label="Farblegende">
          <span className="inbound">Sonnensturz</span><span className="burn">Oberth</span><span className="sail">Electric Sail</span><span className="cruise">Deep Space</span>
        </div>}
        {(plannedRoute || visibleMissionResult) && <div className={`phase-timeline ${plannedRoute ? 'route-timeline' : ''}`} aria-label="Missionstimeline">
          {(plannedRoute ? (
            plannedRoute.segments?.map((segment) => [segment.id, segment.label])
            ?? [
              ['earth-to-oberth', 'Erde → Sonne'],
              ['lambert-to-soi', 'Sonne → Jupiter'],
              ['jupiter-hyperbola', 'Jupiter-Swing-by'],
              ['post-flyby', 'Ausflug / Zielkurs'],
            ]
          ) : [
            ['EARTH', 'Start / Erdorbit'],
            ['SUNDIVER', 'Sonnensturz'],
            ['SOLAR_APPROACH', 'Perihel'],
            ['SOLAR_OBERTH', 'Oberth'],
            ['PAYLOAD', 'Trennung'],
            ['ELECTRIC_SAIL_DEPLOYMENT', 'Entfaltung'],
            ['ELECTRIC_SAIL_PROPULSION', 'E-Sail'],
            ['DEEP_SPACE', 'Deep Space'],
          ]).map(([phase, label]) => (
            <span className={plannedRoute ? currentRouteSegment?.id === phase ? 'active' : '' : currentPoint?.phase.includes(phase) ? 'active' : ''} key={phase}>{label}</span>
          ))}
        </div>}
        {aimpointEnabled && (
          <div className="aimpoint-overlay" aria-label="Aimpoint-Steuerung">
            <strong>Aimpoint im Planetenbild</strong>
            <label>
              <span>Rolle</span>
              <select
                value={aimpointRole}
                onChange={(event) => {
                  setAimpointRole(event.target.value as AimpointRole)
                  invalidateRoutePlan()
                  setPlannedRoute(null)
                }}
              >
                <option value="entry">Entry</option>
                <option value="periapsis">Periapsis (Standard)</option>
                <option value="exit">Exit</option>
              </select>
            </label>
            <label><span>Höhe</span><input type="number" min="0" step="1000" value={aimpointAltitudeKm} onChange={(event) => { setAimpointAltitudeKm(event.target.valueAsNumber); invalidateRoutePlan(); setPlannedRoute(null) }} /><small>km</small></label>
            <label><span>Uhrwinkel</span><input type="number" step="1" value={aimpointClockAngleDeg} onChange={(event) => { setAimpointClockAngleDeg(event.target.valueAsNumber); invalidateRoutePlan(); setPlannedRoute(null) }} /><small>°</small></label>
            <label><span>Scheibenradius</span><input type="number" min="0" max="1" step="0.05" value={aimpointScreenRadiusNorm} onChange={(event) => { setAimpointScreenRadiusNorm(Math.max(0, Math.min(1, event.target.valueAsNumber))); invalidateRoutePlan(); setPlannedRoute(null) }} /><small>0-1</small></label>
            <span>Aimpoint: {aimpointRole === 'periapsis' ? 'Periapsis' : aimpointRole === 'entry' ? 'Eintritt' : 'Austritt'} · {aimpointAltitudeKm.toLocaleString('de-DE', { maximumFractionDigits: 0 })} km · {aimpointClockAngleDeg.toFixed(0)}° · {aimpointScreenRadiusNorm.toFixed(2)}</span>
          </div>
        )}
        <div className="planet-view-actions" aria-label="Kamerafokus">
          <button type="button" onClick={() => showCameraView('perspective')}>3D-Perspektive</button>
          <button type="button" onClick={() => showCameraView('top')}>Draufsicht</button>
          <button type="button" onClick={() => showCameraView('front')}>Vorderansicht</button>
          <button type="button" onClick={() => showCameraView('side')}>Seitenansicht</button>
          <button
            type="button"
            aria-pressed={cameraFocusRequest.kind !== 'overview' && cameraFocusRequest.view === 'sun-to-target'}
            onClick={() => showCameraView('sun-to-target')}
          >
            Von Sonne zum Ziel
          </button>
          <button
            type="button"
            disabled={cameraFocusRequest.kind === 'overview'}
            aria-pressed={cameraFocusRequest.kind !== 'overview' && cameraFocusRequest.view === 'sun-behind'}
            onClick={() => showCameraView('sun-behind')}
          >
            Sonne dahinter
          </button>
          <button
            type="button"
            disabled={cameraFocusRequest.kind === 'overview'}
            aria-pressed={cameraFocusRequest.kind !== 'overview' && cameraFocusRequest.view === 'cross-axis'}
            onClick={() => showCameraView('cross-axis')}
          >
            Queransicht 90°
          </button>
          <button type="button" disabled={cameraFocusRequest.kind !== 'point' && !selectedPlanet} onClick={refocusCurrentObject}>
            {cameraFocusRequest.kind === 'point'
              ? `${cameraFocusRequest.label} fokussieren`
              : selectedPlanet
                ? `${selectedPlanet.name} fokussieren`
                : 'Planet fokussieren'}
          </button>
        </div>
        <div className="scene-help">XYZ-Gizmo unten rechts · Planet anklicken: Nahfokus · Mausrad: zoomen · Linke Taste: {navigationMode === 'pan' ? 'Ansicht ziehen' : 'Ansicht räumlich drehen'} · Rechte Taste: {navigationMode === 'pan' ? 'drehen' : 'ziehen'}</div>
      </div>

      <ParameterPanel
        planets={data.planets}
        moons={moonCatalogue.moons}
        moonCounts={moonCatalogue.counts}
        selectedPlanet={selectedPlanet}
        selectedObject={selectedObject}
        selectedMoons={selectedMoons}
        selectedMoon={selectedMoon}
        currentPoint={plannedRoute ? null : currentPoint}
        visual={visual}
        draft={draft}
        result={plannedRoute ? null : visibleMissionResult}
        elapsedDays={elapsedDays}
        canPlay={canPlay}
        energyDeficit={optimizationResult?.solarEnergyFeasibility ?? optimizationPreflight ?? undefined}
        onSelectPlanet={selectPlanet}
        onSelectObject={setSelectedObject}
        onSelectMoon={setSelectedMoon}
        onVisualChange={setVisual}
        onDraftChange={(nextDraft) => { setDraft(nextDraft); abortActivePlayback('spacecraft-configuration-changed'); setRouteValidationPending(Boolean(plannedRoute)); setOptimizationPreflight(null); setDirectSolarRoute(null); setOptimizationResult(null); setRouteError(null) }}
        onApplyTrajectoryPlan={applyGenericTrajectoryPlan}
      />
      {visual.showScaleNotice && (
        <p className="floating-scale-note">Orbitale Darstellung: {visual.orbitScale} × √AE · Neigungen vertikal ×{visual.inclinationScale} · Körperradien proportional zueinander · Missionsbahn RK4 / N-Körper</p>
      )}
    </section>
  )
}
