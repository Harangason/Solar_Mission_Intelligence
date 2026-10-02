import type { BodyEphemerides } from './types.ts'

let current: BodyEphemerides | undefined
export function useRouteEphemerides(value?: BodyEphemerides) { current = value }
// Display interpolation only: no extrapolation and no changes to solver states.
export function bodyPositionKmAt(bodyId: string, timestampMs: number): [number, number, number] | null {
  const samples = current?.tracks[bodyId]
  if (!current || !samples?.length) return null
  const day = (timestampMs - Date.parse(current.epochUtc)) / 86_400_000
  if (!Number.isFinite(day) || day < samples[0].elapsedDays - 1e-9 || day > samples.at(-1)!.elapsedDays + 1e-9) return null
  let lo = 0, hi = samples.length - 1
  while (hi - lo > 1) {
    const mid = (lo + hi) >> 1
    if (samples[mid].elapsedDays <= day) lo = mid
    else hi = mid
  }
  const a = samples[lo], b = samples[hi]
  if (Math.abs(day - a.elapsedDays) < 1e-10) return a.positionKm
  if (Math.abs(day - b.elapsedDays) < 1e-10) return b.positionKm
  const seconds = (b.elapsedDays - a.elapsedDays) * 86400
  if (seconds <= 0) return a.positionKm
  const t = (day - a.elapsedDays) / (b.elapsedDays - a.elapsedDays)
  const h00 = 2*t**3-3*t**2+1, h10=t**3-2*t**2+t, h01=-2*t**3+3*t**2, h11=t**3-t**2
  return a.positionKm.map((x,i) => h00*x + h10*seconds*a.velocityKmS[i] + h01*b.positionKm[i] + h11*seconds*b.velocityKmS[i]) as [number, number, number]
}
