import type { RouteSectionDefinition } from './routeSections'

interface GeometryTrajectoryPoint {
  elapsedDays: number
  positionKm: [number, number, number]
  velocityKmS?: [number, number, number]
}

interface GeometryRouteSection {
  id: string
  originId: string
  targetId: string
  entryIndex: number
  periapsisIndex: number
  exitIndex: number
  lambertEndpointResidualKm?: number
  targetConditionSatisfied?: boolean
  corridor: {
    entryInsideCorridor: boolean
  }
}

export interface GeometryRouteResult {
  trajectory: GeometryTrajectoryPoint[]
  routeSections?: GeometryRouteSection[]
  stateChain?: {
    continuousPosition?: boolean
    exitStateFeedsNextSection?: boolean
  }
  validation?: {
    collisionFree: boolean
  }
}

export interface RouteGeometryValidation {
  valid: boolean
  sectionOrderValid: boolean
  finiteTrajectory: boolean
  monotonicTime: boolean
  indicesValid: boolean
  stateContinuous: boolean
  endpointsReached: boolean
  collisionFree: boolean
  corridorsSatisfied: boolean
  maximumEndpointResidualKm: number
  errors: string[]
}

const ENDPOINT_TOLERANCE_KM = 100

function finiteVector(value: [number, number, number]) {
  return value.every(Number.isFinite)
}

export function validateRouteGeometry(
  requestedSections: RouteSectionDefinition[],
  result: GeometryRouteResult,
  requireCorridors: boolean,
): RouteGeometryValidation {
  const calculatedSections = result.routeSections ?? []
  const trajectory = result.trajectory ?? []
  const sectionOrderValid = (
    calculatedSections.length === requestedSections.length
    && calculatedSections.every((section, index) => (
      section.id === requestedSections[index]?.id
      && section.originId === requestedSections[index]?.originId
      && section.targetId === requestedSections[index]?.targetId
    ))
  )
  const finiteTrajectory = (
    trajectory.length >= 2
    && trajectory.every((point) => (
      Number.isFinite(point.elapsedDays) && finiteVector(point.positionKm)
      && point.velocityKmS !== undefined && finiteVector(point.velocityKmS)
    ))
  )
  const monotonicTime = trajectory.every((point, index) => (
    index === 0 || point.elapsedDays >= trajectory[index - 1].elapsedDays
  ))
  const indicesValid = calculatedSections.every((section, index) => (
    Number.isInteger(section.entryIndex)
    && Number.isInteger(section.periapsisIndex)
    && Number.isInteger(section.exitIndex)
    && section.entryIndex >= (index === 0 ? 0 : calculatedSections[index - 1].exitIndex)
    && section.entryIndex <= section.periapsisIndex
    && section.periapsisIndex <= section.exitIndex
    && section.exitIndex < trajectory.length
  ))
  const stateContinuous = (
    result.stateChain?.continuousPosition === true
    && result.stateChain?.exitStateFeedsNextSection === true
  )
  const endpointResiduals = calculatedSections.map(
    (section) => section.lambertEndpointResidualKm ?? Number.POSITIVE_INFINITY,
  )
  const maximumEndpointResidualKm = endpointResiduals.length > 0
    ? Math.max(...endpointResiduals)
    : Number.POSITIVE_INFINITY
  const endpointsReached = (
    endpointResiduals.length === requestedSections.length
    && endpointResiduals.every((residual) => (
      Number.isFinite(residual) && residual <= ENDPOINT_TOLERANCE_KM
    ))
    && calculatedSections.every((section) => section.targetConditionSatisfied === true)
  )
  const collisionFree = result.validation?.collisionFree === true
  const corridorsSatisfied = calculatedSections.every(
    (section) => section.corridor.entryInsideCorridor,
  )
  const errors: string[] = []
  if (!sectionOrderValid) errors.push('Die berechneten Teilstrecken entsprechen nicht der Nutzervorgabe.')
  if (!finiteTrajectory) errors.push('Die Trajektorie enthält ungültige oder zu wenige Punkte.')
  if (!monotonicTime) errors.push('Die Zeitrichtung der Trajektorie ist nicht monoton.')
  if (!indicesValid) errors.push('Die Abschnittsindizes sind nicht kontinuierlich geordnet.')
  if (!stateContinuous) errors.push('Der Austrittszustand wird nicht kontinuierlich weitergegeben.')
  if (!endpointsReached) errors.push('Mindestens ein Zielobjekt wurde geometrisch nicht erreicht.')
  if (!collisionFree) errors.push('Die Trajektorie kollidiert mit einem Himmelskörper.')
  if (requireCorridors && !corridorsSatisfied) errors.push('Mindestens ein vorgegebener Eintrittskorridor wurde verfehlt.')
  return {
    valid: errors.length === 0,
    sectionOrderValid,
    finiteTrajectory,
    monotonicTime,
    indicesValid,
    stateContinuous,
    endpointsReached,
    collisionFree,
    corridorsSatisfied,
    maximumEndpointResidualKm,
    errors,
  }
}
