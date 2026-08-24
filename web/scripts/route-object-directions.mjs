import assert from 'node:assert/strict'

import {
  equatorialToEclipticPhysicsDirection,
  orthogonalizedScenePlaneNormal,
  projectPhysicsDirectionToView,
  physicsDirectionBetweenScenePositions,
} from '../src/routeDirectionMath.ts'

const earthToMars = physicsDirectionBetweenScenePositions([1, 2, 3], [-4, 8, 5])
const marsToEarth = physicsDirectionBetweenScenePositions([-4, 8, 5], [1, 2, 3])
assert.ok(earthToMars)
assert.ok(marsToEarth)
assert.ok(Math.abs(Math.hypot(...earthToMars) - 1) < 1e-12)
earthToMars.forEach((component, index) => {
  assert.ok(Math.abs(component + marsToEarth[index]) < 1e-12)
})
assert.equal(physicsDirectionBetweenScenePositions([1, 2, 3], [1, 2, 3]), null)

const proxima = equatorialToEclipticPhysicsDirection(217.43, -62.68)
assert.ok(Math.abs(Math.hypot(...proxima) - 1) < 1e-12)

// A radial direction in the ecliptic must keep the ecliptic normal. The old
// reference × radial construction returned the z axis and tilted the orbit 90°.
const eclipticNormal = orthogonalizedScenePlaneNormal([1, 0, 0], [0, 1, 0])
assert.deepEqual(eclipticNormal, [0, 1, 0])
assert.deepEqual(projectPhysicsDirectionToView([0, 1, 0], 'top'), [0, -1])
assert.deepEqual(projectPhysicsDirectionToView([0, 0, 1], 'side'), [0, -1])

console.log('route object directions: ok')
