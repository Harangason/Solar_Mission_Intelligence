import { bodyPositionKmAt } from './bodyEphemerides'
import * as THREE from 'three'

import type { MoonData } from './types'

const J2000_MS = Date.UTC(2000, 0, 1, 12)
const DAY_MS = 86_400_000

function epochTimestamp(epoch?: string) {
  const match = epoch?.match(/^(\d{4})-(\d{2})-(\d{2})(?:\.(\d+))?$/)
  if (!match) return J2000_MS
  const fraction = match[4] ? Number(`0.${match[4]}`) : 0
  return Date.UTC(Number(match[1]), Number(match[2]) - 1, Number(match[3])) + fraction * DAY_MS
}

function radians(value: number) {
  return value * Math.PI / 180
}

function solveEccentricAnomaly(meanAnomaly: number, eccentricity: number) {
  let eccentricAnomaly = meanAnomaly
  for (let iteration = 0; iteration < 7; iteration += 1) {
    eccentricAnomaly -= (
      eccentricAnomaly - eccentricity * Math.sin(eccentricAnomaly) - meanAnomaly
    ) / (1 - eccentricity * Math.cos(eccentricAnomaly))
  }
  return eccentricAnomaly
}

function stableFraction(value: string) {
  let hash = 2166136261
  for (const character of value) {
    hash ^= character.charCodeAt(0)
    hash = Math.imul(hash, 16777619)
  }
  return (hash >>> 0) / 4_294_967_295
}

export function moonDisplayDistance(
  moon: MoonData,
  index: number,
  moonCount: number,
  planetSize: number,
  knownDistances: number[],
) {
  // Mondsysteme werden separat und kompakt skaliert. So bleiben sie lesbar,
  // ohne im Sonnenmassstab weit vom zugehoerigen Planeten wegzudriften.
  // Der Innenrand liegt zugleich knapp ausserhalb von Saturns sichtbarem Ring.
  const inner = planetSize * 2.0 + 0.02
  const span = Math.min(0.42, Math.max(0.12, planetSize * 1.15))
  if (moon.semiMajorAxisKm && knownDistances.length === 1) {
    return inner + span * 0.55
  }
  if (!moon.semiMajorAxisKm) {
    return inner + span * (0.35 + 0.65 * ((index + 1) / Math.max(1, moonCount)))
  }
  const min = Math.log(Math.min(...knownDistances))
  const max = Math.log(Math.max(...knownDistances))
  const normalized = (Math.log(moon.semiMajorAxisKm) - min) / Math.max(0.01, max - min)
  return inner + span * normalized
}

export function moonPositionAt(
  moon: MoonData,
  timestampMs: number,
  displayDistance: number,
) {
  const precise = bodyPositionKmAt(moon.id,timestampMs), parent = bodyPositionKmAt(moon.parentId,timestampMs)
  if (precise && parent && moon.semiMajorAxisKm) {
    const relative = new THREE.Vector3(precise[0]-parent[0],precise[2]-parent[2],precise[1]-parent[1])
    return relative.multiplyScalar(displayDistance/moon.semiMajorAxisKm)
  }
  const fallbackPhase = stableFraction(moon.id) * Math.PI * 2
  const period = moon.orbitalPeriodDays ?? 80 + stableFraction(`${moon.id}-period`) * 900
  const elapsedDays = (timestampMs - epochTimestamp(moon.epoch)) / DAY_MS
  const meanAnomaly = radians(moon.meanAnomalyEpochDeg ?? 0)
    + elapsedDays * Math.PI * 2 / period
    + (moon.orbitSource ? 0 : fallbackPhase)
  const eccentricity = Math.min(moon.eccentricity ?? 0.04, 0.92)
  const eccentricAnomaly = solveEccentricAnomaly(meanAnomaly, eccentricity)
  const xPrime = displayDistance * (Math.cos(eccentricAnomaly) - eccentricity)
  const yPrime = displayDistance * Math.sqrt(1 - eccentricity ** 2) * Math.sin(eccentricAnomaly)
  const inclination = radians(moon.inclinationDeg ?? (stableFraction(`${moon.id}-i`) - 0.5) * 36)
  const node = radians(moon.ascendingNodeDeg ?? stableFraction(`${moon.id}-n`) * 360)
  const periapsis = radians(moon.argumentPeriapsisDeg ?? stableFraction(`${moon.id}-p`) * 360)

  const cosArgument = Math.cos(periapsis)
  const sinArgument = Math.sin(periapsis)
  const cosNode = Math.cos(node)
  const sinNode = Math.sin(node)
  const cosInclination = Math.cos(inclination)
  const sinInclination = Math.sin(inclination)
  const x = (cosArgument * cosNode - sinArgument * sinNode * cosInclination) * xPrime
    + (-sinArgument * cosNode - cosArgument * sinNode * cosInclination) * yPrime
  const y = (cosArgument * sinNode + sinArgument * cosNode * cosInclination) * xPrime
    + (-sinArgument * sinNode + cosArgument * cosNode * cosInclination) * yPrime
  const z = sinArgument * sinInclination * xPrime + cosArgument * sinInclination * yPrime
  return new THREE.Vector3(x, z, y)
}

export function moonOrbitVertices(moons: MoonData[], planetSize: number) {
  const knownDistances = moons.flatMap((moon) => moon.semiMajorAxisKm ? [moon.semiMajorAxisKm] : [])
  const vertices: number[] = []
  moons.forEach((moon, index) => {
    if (!moon.orbitSource) return
    const displayDistance = moonDisplayDistance(moon, index, moons.length, planetSize, knownDistances)
    const orbitEpoch = epochTimestamp(moon.epoch)
    let previous = moonPositionAt(moon, orbitEpoch, displayDistance)
    for (let step = 1; step <= 72; step += 1) {
      const periodMs = (moon.orbitalPeriodDays ?? 1) * DAY_MS
      const current = moonPositionAt(moon, orbitEpoch + periodMs * step / 72, displayDistance)
      vertices.push(previous.x, previous.y, previous.z, current.x, current.y, current.z)
      previous = current
    }
  })
  return new Float32Array(vertices)
}

export { DAY_MS }
