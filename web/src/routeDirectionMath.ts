import type { Vector3Tuple } from './targetAlignedProjection'

export function normalizedPhysicsDirectionFromScene(
  sceneDirection: Vector3Tuple,
): Vector3Tuple | null {
  const length = Math.hypot(...sceneDirection)
  if (length <= 1e-9) return null
  return [
    sceneDirection[0] / length,
    sceneDirection[2] / length,
    sceneDirection[1] / length,
  ]
}

export function physicsDirectionBetweenScenePositions(
  origin: Vector3Tuple,
  target: Vector3Tuple,
): Vector3Tuple | null {
  return normalizedPhysicsDirectionFromScene([
    target[0] - origin[0],
    target[1] - origin[1],
    target[2] - origin[2],
  ])
}

export function equatorialToEclipticPhysicsDirection(
  rightAscensionDeg: number,
  declinationDeg: number,
): Vector3Tuple {
  const rightAscension = rightAscensionDeg * Math.PI / 180
  const declination = declinationDeg * Math.PI / 180
  const obliquity = 23.43928 * Math.PI / 180
  const equatorialX = Math.cos(declination) * Math.cos(rightAscension)
  const equatorialY = Math.cos(declination) * Math.sin(rightAscension)
  const equatorialZ = Math.sin(declination)
  return normalizedPhysicsDirectionFromScene([
    equatorialX,
    -equatorialY * Math.sin(obliquity) + equatorialZ * Math.cos(obliquity),
    equatorialY * Math.cos(obliquity) + equatorialZ * Math.sin(obliquity),
  ]) ?? [1, 0, 0]
}

export function orthogonalizedScenePlaneNormal(
  radial: Vector3Tuple,
  preferredNormal: Vector3Tuple,
): Vector3Tuple | null {
  const radialLength = Math.hypot(...radial)
  if (radialLength <= 1e-9) return null
  const unitRadial: Vector3Tuple = radial.map(
    (component) => component / radialLength,
  ) as Vector3Tuple
  const projection = preferredNormal.reduce(
    (sum, component, index) => sum + component * unitRadial[index],
    0,
  )
  const normal: Vector3Tuple = preferredNormal.map(
    (component, index) => component - projection * unitRadial[index],
  ) as Vector3Tuple
  const length = Math.hypot(...normal)
  return length > 1e-9
    ? normal.map((component) => component / length) as Vector3Tuple
    : null
}

export function projectPhysicsDirectionToView(
  direction: Vector3Tuple,
  projection: 'top' | 'side',
): [number, number] | null {
  const projected: [number, number] = [
    direction[0],
    projection === 'top' ? -direction[1] : -direction[2],
  ]
  const length = Math.hypot(...projected)
  return length > 1e-9
    ? [projected[0] / length, projected[1] / length]
    : null
}
