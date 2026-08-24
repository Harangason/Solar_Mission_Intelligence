export interface SunData {
  id: string
  name: string
  radiusKm: number
  color: string
  surfaceGravity: number
}

export interface PlanetData {
  id: string
  name: string
  massKg: number
  radiusKm: number
  temperatureK: number
  distanceAu: number
  orbitalPeriodDays: number
  surfaceGravity: number
  color: string
  hasRings: boolean
  eccentricity?: number
  inclinationDeg?: number
  meanLongitudeJ2000Deg?: number
  perihelionLongitudeDeg?: number
  ascendingNodeLongitudeDeg?: number
}

export interface MoonData {
  id: string
  name: string
  parentId: string
  provisionalDesignation: string | null
  semiMajorAxisKm?: number
  eccentricity?: number
  argumentPeriapsisDeg?: number
  meanAnomalyEpochDeg?: number
  inclinationDeg?: number
  ascendingNodeDeg?: number
  orbitalPeriodDays?: number
  epoch?: string
  orbitSource?: 'jpl-mean-elements'
}

export interface MoonCatalogue {
  generatedAt: string
  total: number
  withJplElements: number
  counts: Record<string, number>
  moons: MoonData[]
  source: {
    catalogue: string
    meanElements: string
  }
}

export type TimeMode = 'animation' | 'live'

export type MissionPhase =
  | 'EARTH_PARKING_ORBIT'
  | 'STAGE_SEPARATION'
  | 'EARTH_SWING_ORBIT'
  | 'SUNDIVER_TRANSFER'
  | 'SOLAR_APPROACH'
  | 'SOLAR_OBERTH_BURN'
  | 'PAYLOAD_SEPARATION'
  | 'ELECTRIC_SAIL_DEPLOYMENT'
  | 'ELECTRIC_SAIL_PROPULSION'
  | 'DEEP_SPACE_CRUISE'
  | 'MISSION_COMPLETE'

export interface VisualConfig {
  orbitScale: number
  inclinationScale: number
  planetScale: number
  smallPlanetScale: number
  giantPlanetScale: number
  probeScale: number
  saturnRingScale: number
  showPlanets: boolean
  showOrbits: boolean
  showTrajectory: boolean
  showStages: boolean
  showDetachedStages: boolean
  showBurn: boolean
  showSail: boolean
  highlightSensorTethers: boolean
  showLabels: boolean
  showVectors: boolean
  showForceVectors: boolean
  showScaleNotice: boolean
}

export type PropulsionType =
  | 'chemical' | 'solid_kick_stage' | 'solar_oberth' | 'ion' | 'hall'
  | 'nuclear_electric' | 'nuclear_thermal' | 'solar_sail' | 'electric_sail'
  | 'magnetic_sail' | 'fusion' | 'antimatter' | 'warp'

export type TechnologyReadiness =
  | 'operational' | 'demonstrated' | 'experimental'
  | 'conceptual' | 'speculative' | 'fictional'

export interface PropulsionModule {
  id: string
  name: string
  type: PropulsionType
  readiness: TechnologyReadiness
  enabled: boolean
  active: boolean
  dryMassKg: number
  propellantMassKg: number
  powerRequiredW: number
  directionMode: 'prograde' | 'retrograde' | 'radial_out' | 'radial_in' | 'custom_vector' | 'spin_plane_controlled'
  visualMode: 'engine_plume' | 'burn_marker' | 'sail_surface' | 'electric_tethers' | 'magnetic_field' | 'warp_bubble' | 'none'
  visualEnabled: boolean
  parameters: Record<string, number | string | boolean>
  warnings: string[]
}

export interface PropulsionReport extends PropulsionModule {
  activeSeconds: number
  peakThrustN: number
  propellantUsedKg: number
  propellantRemainingKg: number
  deltaVDeliveredKmS: number
  risk: 'low' | 'high' | 'unresolved'
}

export interface MissionConfig {
  startDate: string
  parkingOrbitAltitudeKm: number
  payloadMassKg: number
  carrierMassKg: number
  heatshieldMassKg: number
  propellantMassKg: number
  targetPerihelionAu: number
  oberthDeltaVKmS: number
  burnDurationSeconds: number
  engineIspSeconds: number
  separationDeltaVKmS: number
  launchStageEnabled: boolean
  carrierEnabled: boolean
  heatshieldEnabled: boolean
  kickStageEnabled: boolean
  missionYears: number
  electricSailEnabled: boolean
  tetherCount: number
  instrumentedTetherCount: number
  tetherLengthKm: number
  tetherVoltageKv: number
  spinRateRpm: number
  endMassesEnabled: boolean
  fiberCommunicationEnabled: boolean
  sensorNodesEnabled: boolean
  sailAccelerationMmS2: number
  heatshieldLimitWm2: number
  carrierDisposal: 'solar_orbit' | 'sun_impact' | 'safe_orbit'
  nBodyEnabled: boolean
  kalmanEnabled: boolean
  navigationCycleHours: number
  positionMeasurementNoiseKm: number
  velocityMeasurementNoiseKmS: number
  propulsionModules: PropulsionModule[]
  theoreticalPropulsionMode: boolean
}

export interface MissionEvent {
  elapsedDays: number
  phase: MissionPhase
  name: string
  description: string
  massKg: number
  speedKmS: number
  positionKm: [number, number, number]
  velocityKmS: [number, number, number]
  warningLevel: 'info' | 'warning' | 'error'
}

export interface TrajectoryPoint {
  elapsedDays: number
  positionKm: [number, number, number]
  velocityKmS: [number, number, number]
  phase: MissionPhase
  massKg: number
}

export interface MissionSummary {
  status: 'SUCCESS' | 'WARNING' | 'ABORT'
  totalFlightDays: number
  perihelionAu: number
  maxSolarFluxWm2: number
  preBurnSpeedKmS: number
  postBurnSpeedKmS: number
  achievedBurnDeltaVKmS: number
  propellantUsedKg: number
  payloadMassKg: number
  distanceAuByYear: Record<string, number>
  speedKmSByYear: Record<string, number>
  electricSailGainKmS: number
  navigationCycles: number
  positionUncertaintyKm: number
  velocityUncertaintyKmS: number
  maxPlanetaryPerturbationMmS2: number
  propulsionReport: PropulsionReport[]
  timeToSaturnDays: number | null
  timeToVoyagerDistanceDays: number | null
  warnings: string[]
}

export interface MissionResult {
  config: MissionConfig
  events: MissionEvent[]
  trajectory: TrajectoryPoint[]
  summary: MissionSummary
}

export interface SolarSystemData {
  sun: SunData
  planets: PlanetData[]
  scaleNotice: string
}

export type TrajectoryStartType = 'body' | 'orbit' | 'state_vector'
export type TrajectoryTargetType = 'body' | 'body_orbit' | 'flyby' | 'zone' | 'boundary' | 'direction' | 'state_vector'
export type TrajectoryOptimizationMode = 'minimum_energy' | 'minimum_time' | 'minimum_arrival_speed' | 'maximum_exit_speed' | 'minimum_delta_v' | 'balanced' | 'custom'
export type TrajectoryWaypointType = 'body_flyby' | 'solar_oberth' | 'deep_space_maneuver' | 'zone_crossing' | 'manual_point'

export interface GenericTrajectoryCandidate {
  id: string
  departureDate?: string
  arrivalDate?: string
  flightDays: number
  startBodyId?: string
  targetBodyId?: string
  waypointIds?: string[]
  c3Km2S2?: number
  departureVInfinityKmS?: number
  arrivalVInfinityKmS?: number
  requiredDeltaVKmS?: number
  totalDeltaVKmS?: number
  finalHeliocentricSpeedKmS?: number
  score: number
  feasible: boolean
  warnings: string[]
}

export interface GenericTrajectoryPlannerResult {
  mode: string
  input: Record<string, unknown>
  start: {
    type: string
    bodyId?: string
    date: string
    positionKm: [number, number, number]
    velocityKmS: [number, number, number]
  }
  target: {
    type: string
    bodyId?: string
    zoneId?: string
    boundaryId?: string
    direction?: [number, number, number]
    date?: string
    positionKm?: [number, number, number]
    distanceAU?: number
    innerRadiusAU?: number
    outerRadiusAU?: number
    radiusAU?: number
  }
  bestCandidate?: GenericTrajectoryCandidate
  candidates?: GenericTrajectoryCandidate[]
  guide: {
    mode: string
    nodes: Array<Record<string, unknown> & { id: string; kind: string }>
    legs: Array<Record<string, unknown> & { id: string; from: string; to: string }>
  }
  segments: Array<{ id: string; label: string; startIndex: number; endIndex: number }>
  trajectory: Array<{
    elapsedDays: number
    positionKm: [number, number, number]
    velocityKmS?: [number, number, number]
  }>
  summary: {
    totalFlightDays: number
    totalDeltaVKmS?: number
    requiredInjectionDeltaVKmS?: number
    c3Km2S2?: number
    departureVInfinityKmS?: number
    arrivalVInfinityKmS?: number
    finalHeliocentricSpeedKmS?: number
    targetReached: boolean
    targetReachedDate?: string
    targetReachedDistanceAU?: number
    targetAlignmentDeg?: number
    feasible: boolean
    model: string
  }
  warnings: string[]
  audit?: { runId: string; createdAtUtc?: string; logFile?: string; documentation?: string }
  legacyRoute?: unknown
  zoneEntryDate?: string | null
  zoneExitDate?: string | null
  zoneEntryDistanceAU?: number | null
  zoneExitDistanceAU?: number | null
}
