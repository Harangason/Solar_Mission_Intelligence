import assert from 'node:assert/strict'
import { localEncounterGeometry, localEncounterRuns, unitVelocity } from '../src/localEncounterGeometry.ts'

for (const [bodyId, radius] of [['earth', 6371], ['earth-moon', 1737.4], ['neptune', 24622]]) {
  const bodyVelocity = [30, 4, -2]
  const states = [0, 1, 2, 3].map(day => ({ elapsedDays: day,
    positionKm: [1e8 + day * 86400 * 30, day * 86400 * 4, -day * 86400 * 2], velocityKmS: bodyVelocity }))
  const route = {
    routeSections: [{ id: 'leg', targetId: bodyId, entryIndex: 0, exitIndex: 3, minimumAltitudeKm: 100 }],
    bodyEphemerides: { tracks: { [bodyId]: states } },
    trajectory: states.map((s, i) => ({ ...s,
      positionKm: [s.positionKm[0] + (i === 2 ? 5 * radius : radius + 100), s.positionKm[1], s.positionKm[2]],
      velocityKmS: [30, 11, -2], phase: 'BOUND_ORBIT' })),
  }
  const geometry = localEncounterGeometry(route, 'leg', bodyId === 'earth-moon' ? 0 : radius)
  assert.ok(Math.abs(geometry.bodyRadiusKm - radius) < 1e-6)
  assert.ok(Math.abs(geometry.minimumAltitudeKm - 100) < 1e-6)
  assert.deepEqual(geometry.entry.velocity, [0, 7, 0], 'Subtract body velocity, never use body-to-body heading')
  assert.deepEqual(unitVelocity(geometry.entry), [0, 1, 0])
  assert.equal(localEncounterRuns(geometry).length, 1, 'Do not bridge an excursion outside the local view')
  assert.equal(localEncounterRuns(geometry)[0].length, 2)
  assert.equal(localEncounterGeometry({ ...route, bodyEphemerides: undefined }, 'leg', radius), null,
    'Missing body states must not become an invented orbit')
}
console.log('Earth, Moon and Neptune: relative positions, relative velocities, radii and disconnected runs passed.')
