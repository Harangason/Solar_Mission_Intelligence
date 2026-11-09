import type { WaypointRouteResult } from './components/PlannedWaypointRoute'

type Vec = [number, number, number]
export interface LocalEncounterState { position: Vec; velocity: Vec; day: number; phase: string }
export interface LocalEncounterGeometry {
  states: LocalEncounterState[]
  entry: LocalEncounterState
  exit: LocalEncounterState
  minimumAltitudeKm: number
  bodyRadiusKm: number
  feasible: boolean
  insertionDeltaVKmS: number | null
}

// Positions and velocities share the same inertial axes and epoch. Subtract
// BOTH body state components before drawing a planet-centred encounter.
export function localEncounterGeometry(
  route: WaypointRouteResult | null | undefined,
  sectionId: string | undefined,
  bodyRadiusKm = 0,
): LocalEncounterGeometry | null {
  const section = route?.routeSections?.find(item => item.id === sectionId)
  if (!route || !section) return null
  const track = route.bodyEphemerides?.tracks[section.targetId]
  if (section.targetId !== 'sun' && !track?.length) return null
  const bodyStates = new Map(track?.map(state => [state.elapsedDays, state]))
  const states: LocalEncounterState[] = []
  for (const sample of route.trajectory.slice(section.entryIndex, section.exitIndex + 1)) {
    const body = bodyStates.get(sample.elapsedDays)
    if ((!body && section.targetId !== 'sun') || !sample.velocityKmS) return null
    const position = sample.positionKm.map((x, i) => x - (body?.positionKm[i] ?? 0)) as Vec
    const velocity = sample.velocityKmS.map((x, i) => x - (body?.velocityKmS[i] ?? 0)) as Vec
    if (![...position, ...velocity].every(Number.isFinite)) return null
    states.push({ position, velocity, day: sample.elapsedDays, phase: sample.phase ?? '' })
  }
  if (states.length < 2) return null
  const minimumRadiusKm = Math.min(...states.map(s => Math.hypot(...s.position)))
  // Older catalogues omit moon radii. Recover the solver's radius from its
  // measured periapsis and altitude, never from the parent planet's radius.
  if (!(bodyRadiusKm > 0)) bodyRadiusKm = minimumRadiusKm - section.minimumAltitudeKm
  if (!(bodyRadiusKm > 0) || !Number.isFinite(bodyRadiusKm)) return null
  return { states, entry: states[0], exit: states.at(-1)!, bodyRadiusKm,
    feasible: route.summary?.feasibleWithConfiguredBurn === true,
    insertionDeltaVKmS: section.requiredPassageDeltaVKmS ?? null,
    minimumAltitudeKm: minimumRadiusKm - bodyRadiusKm }
}

// Do not reconnect across points outside the local field of view.
export function localEncounterRuns(geometry: LocalEncounterGeometry, radiusRatio = 4) {
  const runs: LocalEncounterState[][] = []
  let run: LocalEncounterState[] = []
  for (const state of geometry.states) {
    if (Math.hypot(...state.position) <= geometry.bodyRadiusKm * radiusRatio) run.push(state)
    else if (run.length) { runs.push(run); run = [] }
  }
  if (run.length) runs.push(run)
  return runs.filter(points => points.length > 1)
}

export function unitVelocity(state: LocalEncounterState): Vec | null {
  const speed = Math.hypot(...state.velocity)
  return speed > 0 ? state.velocity.map(v => v / speed) as Vec : null
}
