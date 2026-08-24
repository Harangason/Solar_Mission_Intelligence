import assert from 'node:assert/strict'

import {
  isSelectableRouteCandidate,
  rankRouteCandidates,
} from '../src/routeCandidateSelection.ts'
import { routeSectionsBlockReason } from '../src/routeSectionValidation.ts'

const candidate = (overrides) => ({
  id: crypto.randomUUID(),
  status: 'rejected',
  fullCorridorCheck: true,
  quality: 0,
  feasible: false,
  geometryValid: true,
  corridorSatisfied: true,
  collisionFree: true,
  performanceEvaluated: true,
  requiredInjectionDeltaVKmS: 10,
  availableInjectionDeltaVKmS: 5,
  ...overrides,
})

const selectable = candidate({
  id: 'selectable',
  status: 'performance-valid',
  feasible: true,
  quality: 100,
})
const higherQualityButRejected = candidate({
  id: 'rejected',
  quality: 10_000,
})
const preflight = candidate({
  id: 'preflight',
  status: 'performance-valid',
  feasible: true,
  fullCorridorCheck: false,
  quality: 20_000,
})

assert.equal(isSelectableRouteCandidate(selectable), true)
assert.equal(isSelectableRouteCandidate(higherQualityButRejected), false)
assert.equal(isSelectableRouteCandidate(preflight), false)
assert.deepEqual(
  rankRouteCandidates([higherQualityButRejected, preflight, selectable]).map(({ id }) => id),
  ['selectable', 'rejected', 'preflight'],
)

const first = {
  id: 'earth-sun',
  originId: 'earth',
  targetId: 'sun',
  deltaVMinusKmS: 1,
  deltaVPlusKmS: 1,
}
const connected = {
  id: 'sun-jupiter',
  originId: 'sun',
  targetId: 'jupiter',
  deltaVMinusKmS: 1,
  deltaVPlusKmS: 1,
}
assert.equal(routeSectionsBlockReason([first, connected]), null)
assert.match(
  routeSectionsBlockReason([first, { ...connected, originId: 'mars' }]),
  /erwartet wird 'sun'/,
)
assert.match(
  routeSectionsBlockReason([first, { ...connected, id: first.id }]),
  /mehrfach/,
)

console.log('route selection logic: ok')
