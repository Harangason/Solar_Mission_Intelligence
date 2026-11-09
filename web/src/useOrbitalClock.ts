import { useEffect, useState } from 'react'
import { advanceOrbitDays } from './orbitViewport'

/** Display time only. Never writes project dates, route state or solver inputs. */
export function useOrbitalClock(epochMs: number, enabled: boolean, autoPlay = false) {
  const [days, setDays] = useState(0)
  const [playing, setPlaying] = useState(() => autoPlay && !window.matchMedia('(prefers-reduced-motion: reduce)').matches)
  const [speed, setSpeed] = useState(10)
  useEffect(() => { setDays(0) }, [epochMs])
  useEffect(() => {
    if (!playing || !enabled) return
    let frame = 0
    let last = performance.now()
    let painted = last
    const tick = (now: number) => {
      if (document.hidden) { last = now; painted = now }
      else if (now - painted >= 40) {
        const delta = (now - last) / 1000
        setDays(current => advanceOrbitDays(current, delta, speed))
        last = now
        painted = now
      }
      frame = requestAnimationFrame(tick)
    }
    frame = requestAnimationFrame(tick)
    return () => cancelAnimationFrame(frame)
  }, [enabled, playing, speed])
  return { timestampMs: epochMs + days * 86_400_000, playing: playing && enabled, speed,
    toggle: () => setPlaying(current => !current), setSpeed,
    reset: () => { setDays(0); setPlaying(false) } }
}
