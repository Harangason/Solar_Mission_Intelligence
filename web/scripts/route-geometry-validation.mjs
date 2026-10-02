import assert from 'node:assert/strict'

import { validateRouteGeometry } from '../src/routeGeometryValidation.ts'

const requestedSections = [
  { id: 'earth-sun', originId: 'earth', targetId: 'sun' },
  { id: 'sun-jupiter', originId: 'sun', targetId: 'jupiter' },
]
const validResult = {
  trajectory: [
    { elapsedDays: 0, velocityKmS: [0, 1, 0], positionKm: [1, 2, 3] },
    { elapsedDays: 100, velocityKmS: [0, 1, 0], positionKm: [4, 5, 6] },
    { elapsedDays: 200, velocityKmS: [0, 1, 0], positionKm: [7, 8, 9] },
  ],
  routeSections: [
    {
      id: 'earth-sun',
      originId: 'earth',
      targetId: 'sun',
      entryIndex: 0,
      periapsisIndex: 1,
      exitIndex: 1,
      targetConditionSatisfied: true, lambertEndpointResidualKm: 0.2,
      corridor: { entryInsideCorridor: true },
    },
    {
      id: 'sun-jupiter',
      originId: 'sun',
      targetId: 'jupiter',
      entryIndex: 1,
      periapsisIndex: 2,
      exitIndex: 2,
      targetConditionSatisfied: true, lambertEndpointResidualKm: 0.4,
      corridor: { entryInsideCorridor: true },
    },
  ],
  stateChain: { continuousPosition: true, exitStateFeedsNextSection: true },
  validation: { collisionFree: true },
}

const valid = validateRouteGeometry(requestedSections, validResult, true)
assert.equal(valid.valid, true)
assert.equal(valid.maximumEndpointResidualKm, 0.4)

const missingFirstSection = {
  ...validResult,
  routeSections: validResult.routeSections.slice(1),
}
const missing = validateRouteGeometry(requestedSections, missingFirstSection, true)
assert.equal(missing.valid, false)
assert.equal(missing.sectionOrderValid, false)

const reversedTime = {
  ...validResult,
  trajectory: [validResult.trajectory[1], validResult.trajectory[0]],
}
assert.equal(validateRouteGeometry(requestedSections, reversedTime, true).valid, false)

console.log('route geometry validation: ok')

assert.equal(validateRouteGeometry(requestedSections, {...validResult, stateChain: undefined}, true).valid, false)
assert.equal(validateRouteGeometry(requestedSections, {...validResult, validation: undefined}, true).valid, false)
assert.equal(validateRouteGeometry(requestedSections, {...validResult, trajectory: validResult.trajectory.map(({velocityKmS,...p}) => p)}, true).valid, false)
