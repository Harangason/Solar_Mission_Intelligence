import { useEffect, useState } from 'react'
import { ellipticOrbitPoints, ellipticPositionAt } from './orbitalMath'

export interface SmallBody {
  id: string; name: string; kind: 'asteroid' | 'comet'; color: string
  epochJd: number; semiMajorAxisAu: number; eccentricity: number; inclinationDeg: number
  ascendingNodeDeg: number; argumentPerihelionDeg: number; meanAnomalyDeg: number; periodDays: number; source: string
}
export const smallBodyPositionAt = (body: SmallBody, timestampMs: number, scale = 5) =>
  ellipticPositionAt(body, timestampMs, scale)
export const smallBodyOrbit = (body: SmallBody, scale = 5) => ellipticOrbitPoints(body, scale)

export function useSmallBodies() {
  const [bodies, setBodies] = useState<SmallBody[]>([])
  const [error, setError] = useState(false)
  useEffect(() => {
    const controller = new AbortController()
    fetch('/small-bodies.json', { signal: controller.signal }).then(response => {
      if (!response.ok) throw new Error('Kleinkörperdaten fehlen')
      return response.json()
    }).then(payload => {
      if (!Array.isArray(payload.bodies)) throw new Error('Ungültiger Kleinkörperkatalog')
      setBodies(payload.bodies)
    }).catch(() => { if (!controller.signal.aborted) setError(true) })
    return () => controller.abort()
  }, [])
  return { bodies, error }
}
