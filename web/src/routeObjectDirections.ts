import * as THREE from 'three'

import { ROUTE_INTERSTELLAR_SYSTEMS } from './interstellarTargets'
import { moonPositionAt } from './moonMath'
import { planetPositionAt } from './orbitalMath'
import {
  equatorialToEclipticPhysicsDirection,
  physicsDirectionBetweenScenePositions,
} from './routeDirectionMath'
import type { MoonData, PlanetData } from './types'
import type { Vector3Tuple } from './targetAlignedProjection'

const LOCAL_MOON_OFFSET = 0.03

export function interstellarTargetDirection(targetId: string): Vector3Tuple | null {
  const target = ROUTE_INTERSTELLAR_SYSTEMS.find((item) => item.id === targetId)
  if (!target) return null
  return equatorialToEclipticPhysicsDirection(
    target.rightAscensionDeg,
    target.declinationDeg,
  )
}

function routeObjectScenePosition(
  objectId: string,
  timestampMs: number,
  planets: PlanetData[],
  moons: MoonData[],
) {
  if (objectId === 'sun') return new THREE.Vector3()
  const planet = planets.find((item) => item.id === objectId)
  if (planet) return planetPositionAt(planet, timestampMs)
  const moon = moons.find((item) => item.id === objectId)
  if (!moon) return null
  const parent = planets.find((item) => item.id === moon.parentId)
  if (!parent) return null
  return planetPositionAt(parent, timestampMs).add(
    moonPositionAt(moon, timestampMs, LOCAL_MOON_OFFSET),
  )
}

export function directionBetweenRouteObjects(
  originId: string,
  targetId: string,
  originTimestampMs: number,
  targetTimestampMs: number,
  planets: PlanetData[],
  moons: MoonData[],
): Vector3Tuple | null {
  const stellarDirection = interstellarTargetDirection(targetId)
  if (stellarDirection) return stellarDirection
  const origin = routeObjectScenePosition(originId, originTimestampMs, planets, moons)
  const target = routeObjectScenePosition(targetId, targetTimestampMs, planets, moons)
  if (!origin || !target) return null
  return physicsDirectionBetweenScenePositions(
    [origin.x, origin.y, origin.z],
    [target.x, target.y, target.z],
  )
}
