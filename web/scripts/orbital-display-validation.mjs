import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { registerHooks } from 'node:module'
// Vite resolves this extensionless runtime import; mirror that resolution in Node.
const loader = registerHooks({ resolve(specifier, context, next) {
  return next(specifier === './bodyEphemerides' ? './bodyEphemerides.ts' : specifier, context)
} })
const { ellipticPositionAt, ellipticOrbitPoints, planetPositionAt, createOrbitPoints } = await import('../src/orbitalMath.ts')
loader.deregister()
import { fitOrbitViewport, advanceOrbitDays } from '../src/orbitViewport.ts'

const dayMs = 86400000
const circle = { epochJd: 2451545, semiMajorAxisAu: 1, eccentricity: 0, inclinationDeg: 0,
  ascendingNodeDeg: 0, argumentPerihelionDeg: 0, meanAnomalyDeg: 0, periodDays: 400 }
const epoch = (circle.epochJd - 2440587.5) * dayMs
const quarter = ellipticPositionAt(circle, epoch + 100 * dayMs)
assert.ok(Math.abs(quarter.x) < 1e-10 && Math.abs(quarter.z - 5) < 1e-10, 'Quarter orbit must advance 90 degrees')
const comet = { ...circle, semiMajorAxisAu: 18, eccentricity: .967 }
assert.ok(Math.abs(ellipticPositionAt(comet, epoch).length() - 5 * Math.sqrt(18 * (1 - .967))) < 1e-9, 'High-eccentricity perihelion must converge')
assert.ok(Math.abs(ellipticPositionAt(comet, epoch + 200 * dayMs).length() - 5 * Math.sqrt(18 * (1 + .967))) < 1e-9, 'Aphelion distance')
const { bodies } = JSON.parse(readFileSync(new URL('../public/small-bodies.json', import.meta.url)))
assert.equal(bodies.filter(b => b.kind === 'comet').length, 2)
assert.equal(bodies.filter(b => b.kind === 'asteroid').length, 3)
for (const body of bodies) {
  assert.ok(body.source.startsWith('https://ssd-api.jpl.nasa.gov/'))
  const t0 = (body.epochJd - 2440587.5) * dayMs
  const initial = ellipticPositionAt(body, t0)
  assert.ok(initial.distanceTo(ellipticPositionAt(body, t0 + body.periodDays * dayMs)) < 1e-7, body.name + ' period closure')
  assert.ok(initial.distanceTo(ellipticPositionAt(body, t0 + body.periodDays * dayMs / 4)) > .1, body.name + ' moves')
  for (const point of ellipticOrbitPoints(body)) {
    assert.ok(point.toArray().every(Number.isFinite), body.name + ' finite geometry')
    assert.ok(point.length() <= 5 * Math.sqrt(body.semiMajorAxisAu * (1 + body.eccentricity)) + 1e-8)
  }
}
const earth = { id: 'earth', distanceAu: 1, orbitalPeriodDays: 365.256 }
assert.ok(planetPositionAt(earth, epoch).distanceTo(planetPositionAt(earth, epoch + 365.256 * dayMs)) < 1e-8)
assert.ok(createOrbitPoints(earth)[0].distanceTo(createOrbitPoints(earth).at(-1)) < 1e-8)
for (const [width, height] of [[1000, 700], [340, 600], [1500, 400]]) {
  for (const side of [true, false]) {
    const box = fitOrbitViewport(width, height, 30, side)
    assert.ok(Math.abs(box.width / box.height - width / height) < 1e-10, 'No letterboxing or distortion')
    assert.ok(box.width >= 60 && box.height >= (side ? 10 : 60), 'Complete system remains in frame')
  }
}
assert.equal(advanceOrbitDays(2, .05, 10), 2.5)
assert.equal(advanceOrbitDays(2, 60, 10), 3, 'Returning to a background tab must not jump by months')
assert.equal(advanceOrbitDays(2, -1, 10), 2)
console.log('PASS: orbital periodicity, high-eccentricity comets, JPL catalogue, unchanged planet geometry, responsive aspect ratio and bounded display time')
