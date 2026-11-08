import assert from 'node:assert/strict'
import { trajectoryToWaypointRoute } from '../src/trajectoryToWaypointRoute.ts'
import { bodyPositionKmAt, useRouteEphemerides } from '../src/bodyEphemerides.ts'

const epoch = '2031-01-01T12:00:00Z'
const states = [
  { elapsedDays: 0, positionKm: [149597870.7, 0, 0], velocityKmS: [0, 29, 0], massKg: 10000, propellantKg: 8000, epochUtc: epoch },
  { elapsedDays: 2, positionKm: [150000000, 5011200, 0], velocityKmS: [2, 29, 0], massKg: 2000, propellantKg: 0, epochUtc: '2031-01-03T12:00:00Z' },
]
const plan = {
  schemaVersion: '2.0', mode: 'direct', input: { constraints: { maxTotalDeltaVKmS: 20 } },
  start: { type: 'orbit', bodyId: 'earth', date: epoch, ...states[0] },
  target: { type: 'body_orbit', bodyId: 'earth-moon', positionKm: states[1].positionKm },
  summary: { totalFlightDays: 2, feasible: true, vehicleValidated: false, vehicleFeasible: false, model: 'physical fixture', totalDeltaVKmS: 8 },
  trajectory: states, maneuvers: [{ type: 'CAPTURE', deltaVKmS: 8 }], warnings: ['vehicle missing'],
  validation: { collisionFree: true, stateContinuous: true },
  stateChain: { continuousPosition: true, exitStateFeedsNextSection: true },
  routeSections: [{ id: 'leg-1', originId: 'earth', targetId: 'earth-moon' }],
  bodyEphemerides: { epochUtc: epoch, tracks: {} }, segments: [{ id: 'leg-1', startIndex: 0, endIndex: 1 }],
}
const preview = trajectoryToWaypointRoute(plan)
assert.equal(preview.summary.feasibleWithConfiguredBurn, false, 'model-only proof must not become vehicle proof')
assert.equal(preview.genericTrajectoryPlan, plan)
assert.equal(preview.trajectory, states)
assert.equal(preview.bodyEphemerides, plan.bodyEphemerides)
assert.equal(preview.stateChain, plan.stateChain)
assert.equal(preview.startDate, epoch)
assert.equal(preview.trajectory[1].massKg, 2000)
assert.equal(preview.trajectory[1].velocityKmS[0], 2)
assert.equal(trajectoryToWaypointRoute({ ...plan, summary: { ...plan.summary, vehicleValidated: true, vehicleFeasible: true } }).summary.feasibleWithConfiguredBurn, true)
assert.equal(trajectoryToWaypointRoute({ ...plan, summary: { ...plan.summary, vehicleValidated: true, vehicleFeasible: false } }).summary.feasibleWithConfiguredBurn, false)

// An independent quadratic r(t)=t^2, v(t)=2t in seconds. Hermite interpolation
// must reproduce this trajectory; a linear line between samples gives 50, not 25.
useRouteEphemerides({ epochUtc: epoch, tracks: { earth: [
  { elapsedDays: 0, positionKm: [0, 0, 0], velocityKmS: [0, 0, 0] },
  { elapsedDays: 10 / 86400, positionKm: [100, 0, 0], velocityKmS: [20, 0, 0] },
] } })
assert.deepEqual(bodyPositionKmAt('earth', Date.parse(epoch) + 5000), [25, 0, 0])
assert.deepEqual(bodyPositionKmAt('earth', Date.parse(epoch) + 10000), [100, 0, 0])
assert.equal(bodyPositionKmAt('earth', Date.parse(epoch) - 1000), null)
assert.equal(bodyPositionKmAt('earth', Date.parse(epoch) + 11000), null)
assert.equal(bodyPositionKmAt('missing', Date.parse(epoch)), null)
useRouteEphemerides(undefined)
assert.equal(bodyPositionKmAt('earth', Date.parse(epoch)), null)
console.log('Trajectory bridge: full states, model/vehicle distinction, independent Hermite reference and no extrapolation PASS')
