import { bodyPositionKmAt } from './bodyEphemerides'
import * as THREE from 'three'

import type { PlanetData } from './types'

const DAY_MS = 86_400_000
const J2000_MS = Date.UTC(2000, 0, 1, 12)
const DISTANCE_SCENE_FACTOR = 5

const J2000_ELEMENTS: Record<string, [number, number, number, number, number]> = {
  mercury: [0.20563593, 7.00497902, 252.25032350, 77.45779628, 48.33076593],
  venus: [0.00677672, 3.39467605, 181.97909950, 131.60246718, 76.67984255],
  earth: [0.01671123, -0.00001531, 100.46457166, 102.93768193, 0],
  mars: [0.09339410, 1.84969142, -4.55343205, -23.94362959, 49.55953891],
  jupiter: [0.04838624, 1.30439695, 34.39644051, 14.72847983, 100.47390909],
  saturn: [0.05386179, 2.48599187, 49.95424423, 92.59887831, 113.66242448],
  uranus: [0.04725744, 0.77263783, 313.23810451, 170.95427630, 74.01692503],
  neptune: [0.00859048, 1.77004347, -55.12002969, 44.96476227, 131.78422574],
}

function orbitalElements(planet: PlanetData) {
  const fallback = J2000_ELEMENTS[planet.id]
  if (!fallback) throw new Error(`Keine Orbitaldaten für ${planet.id}`)
  return {
    eccentricity: planet.eccentricity ?? fallback[0],
    inclinationDeg: planet.inclinationDeg ?? fallback[1],
    meanLongitudeDeg: planet.meanLongitudeJ2000Deg ?? fallback[2],
    perihelionLongitudeDeg: planet.perihelionLongitudeDeg ?? fallback[3],
    ascendingNodeLongitudeDeg: planet.ascendingNodeLongitudeDeg ?? fallback[4],
  }
}

function degreesToRadians(value: number) {
  return value * Math.PI / 180
}

function normalizeDegrees(value: number) {
  return ((value + 180) % 360 + 360) % 360 - 180
}

function solveEccentricAnomaly(meanAnomaly: number, eccentricity: number) {
  let eccentricAnomaly = meanAnomaly
  for (let iteration = 0; iteration < 8; iteration += 1) {
    eccentricAnomaly -= (
      eccentricAnomaly - eccentricity * Math.sin(eccentricAnomaly) - meanAnomaly
    ) / (1 - eccentricity * Math.cos(eccentricAnomaly))
  }
  return eccentricAnomaly
}

function orbitalPlaneToEcliptic(planet: PlanetData, xPrime: number, yPrime: number) {
  const elements = orbitalElements(planet)
  const inclination = degreesToRadians(elements.inclinationDeg)
  const ascendingNode = degreesToRadians(elements.ascendingNodeLongitudeDeg)
  const argumentOfPerihelion = degreesToRadians(
    elements.perihelionLongitudeDeg - elements.ascendingNodeLongitudeDeg,
  )
  const cosArgument = Math.cos(argumentOfPerihelion)
  const sinArgument = Math.sin(argumentOfPerihelion)
  const cosNode = Math.cos(ascendingNode)
  const sinNode = Math.sin(ascendingNode)
  const cosInclination = Math.cos(inclination)
  const sinInclination = Math.sin(inclination)

  const x = (cosArgument * cosNode - sinArgument * sinNode * cosInclination) * xPrime
    + (-sinArgument * cosNode - cosArgument * sinNode * cosInclination) * yPrime
  const y = (cosArgument * sinNode + sinArgument * cosNode * cosInclination) * xPrime
    + (-sinArgument * sinNode + cosArgument * cosNode * cosInclination) * yPrime
  const z = sinArgument * sinInclination * xPrime + cosArgument * sinInclination * yPrime
  return new THREE.Vector3(x, z, y)
}

export function toScenePosition(
  positionAu: THREE.Vector3,
  distanceSceneFactor = DISTANCE_SCENE_FACTOR,
  inclinationScale = 1,
) {
  const distanceAu = positionAu.length()
  const displayDistance = distanceSceneFactor * Math.sqrt(distanceAu)
  const displayVector = positionAu.clone()
  displayVector.y *= inclinationScale
  return displayVector.normalize().multiplyScalar(displayDistance)
}

export function planetPositionAt(
  planet: PlanetData,
  timestampMs: number,
  distanceSceneFactor = DISTANCE_SCENE_FACTOR,
  inclinationScale = 1,
) {
  const precise = bodyPositionKmAt(planet.id, timestampMs)
  if (precise) return toScenePosition(new THREE.Vector3(precise[0]/149597870.7, precise[2]/149597870.7, precise[1]/149597870.7),distanceSceneFactor,inclinationScale)
  const elements = orbitalElements(planet)
  const daysSinceJ2000 = (timestampMs - J2000_MS) / DAY_MS
  const meanLongitude = elements.meanLongitudeDeg
    + (360 * daysSinceJ2000 / planet.orbitalPeriodDays)
  const meanAnomaly = degreesToRadians(
    normalizeDegrees(meanLongitude - elements.perihelionLongitudeDeg),
  )
  const eccentricAnomaly = solveEccentricAnomaly(meanAnomaly, elements.eccentricity)
  const xPrime = planet.distanceAu * (Math.cos(eccentricAnomaly) - elements.eccentricity)
  const yPrime = planet.distanceAu
    * Math.sqrt(1 - elements.eccentricity ** 2)
    * Math.sin(eccentricAnomaly)
  return toScenePosition(orbitalPlaneToEcliptic(planet, xPrime, yPrime), distanceSceneFactor, inclinationScale)
}

export function createOrbitPoints(
  planet: PlanetData,
  distanceSceneFactor = DISTANCE_SCENE_FACTOR,
  inclinationScale = 1,
) {
  const elements = orbitalElements(planet)
  return Array.from({ length: 257 }, (_, index) => {
    const eccentricAnomaly = (index / 256) * Math.PI * 2
    const xPrime = planet.distanceAu * (Math.cos(eccentricAnomaly) - elements.eccentricity)
    const yPrime = planet.distanceAu
      * Math.sqrt(1 - elements.eccentricity ** 2)
      * Math.sin(eccentricAnomaly)
    return toScenePosition(orbitalPlaneToEcliptic(planet, xPrime, yPrime), distanceSceneFactor, inclinationScale)
  })
}

export const ANIMATION_DAYS_PER_SECOND = 30
export { DAY_MS }
