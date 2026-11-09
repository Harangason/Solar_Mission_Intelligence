import { useMemo } from 'react'
import { moonPositionAt } from '../moonMath'
import { smallBodyOrbit, smallBodyPositionAt, type SmallBody } from '../smallBodies'
import type { MoonData } from '../types'
import type { Vector3 } from 'three'

const fraction = (seed: number) => { const value = Math.sin(seed) * 43758.5453; return value - Math.floor(value) }
const stars = Array.from({ length: 210 }, (_, i) => ({ x: fraction(i * 127.1 + 17), y: fraction(i * 311.7 + 53), radius: i % 13 === 0 ? 0.075 : 0.028 }))
const project = (p: Vector3, side: boolean) => [p.x, side ? -p.y : -p.z]
export function OrbitalBackdrop({ x, y, width, height }: { x: number; y: number; width: number; height: number }) {
  return <g aria-hidden="true" pointerEvents="none">
    <defs>
      <radialGradient id="orbit-nebula"><stop stopColor="#1c3c5c" stopOpacity=".55" /><stop offset="1" stopColor="#060e1d" stopOpacity="0" /></radialGradient>
      <radialGradient id="orbit-sun-glow"><stop stopColor="#ffdb80" stopOpacity=".35" /><stop offset="1" stopColor="#ffbb45" stopOpacity="0" /></radialGradient>
    </defs>
    <rect x={x} y={y} width={width} height={height} fill="#060e1d" />
    <ellipse cx={x + width * .68} cy={y + height * .28} rx={width * .6} ry={height * .72} fill="url(#orbit-nebula)" />
    {stars.map((star, index) => <circle key={index} cx={x + star.x * width} cy={y + star.y * height} r={star.radius} fill="#b6d3ed" opacity={index % 3 ? .35 : .7} />)}
    <circle r="2.2" fill="url(#orbit-sun-glow)" />
  </g>
}

export function OrbitalSmallBodies({ bodies, timestampMs, side }: { bodies: SmallBody[]; timestampMs: number; side: boolean }) {
  const paths = useMemo(() => bodies.map(body => ({ body, path: smallBodyOrbit(body).map((p, i) => `${i ? 'L' : 'M'}${project(p, side).join(' ')}`).join(' ') })), [bodies, side])
  return <g className="small-body-layer" aria-label="Asteroiden und Kometen">
    {paths.map(({ body, path }) => {
      const [x, y] = project(smallBodyPositionAt(body, timestampMs), side)
      const length = Math.hypot(x, y) || 1
      return <g key={body.id} data-body-id={body.id}>
        <path d={path} fill="none" stroke={body.color} strokeWidth=".055" strokeOpacity={body.kind === 'comet' ? '.3' : '.18'} strokeDasharray={body.kind === 'comet' ? '.2 .4' : undefined} />
        {body.kind === 'comet' && <line x1={x} y1={y} x2={x + x / length * 1.5} y2={y + y / length * 1.5} stroke={body.color} strokeWidth=".13" opacity=".6" />}
        <circle cx={x} cy={y} r={body.kind === 'comet' ? .18 : .13} fill={body.color}><title>{body.name} · JPL · Kepler-Näherung</title></circle>
        <text x={x + .35} y={y - .3} className={`orbital-label small-body-label ${body.kind}`}>{body.name.split(' (')[0]}</text>
      </g>
    })}
  </g>
}

export function OrbitalMoons({ moons, center, timestampMs, side }: { moons: MoonData[]; center: Vector3; timestampMs: number; side: boolean }) {
  const visible = useMemo(() => moons.filter(m => m.orbitSource && m.semiMajorAxisKm && m.orbitalPeriodDays).sort((a,b) => a.semiMajorAxisKm! - b.semiMajorAxisKm!).slice(0,16), [moons])
  const [cx, cy] = project(center, side)
  return <g className="orbital-moons" aria-label={`${visible.length} Monde mit Bahndaten`}>
    {visible.map((moon, index) => {
      const distance = 1 + index * .13
      const [x, y] = project(moonPositionAt(moon, timestampMs, distance), side)
      return <g key={moon.id}>
        {!side && <circle cx={cx} cy={cy} r={distance} fill="none" stroke="#b9d6e8" strokeWidth=".035" opacity=".35" />}
        <circle cx={cx+x} cy={cy+y} r=".095" fill="#e1efff"><title>{moon.name} · Abstand vergrößert</title></circle>
        {index < 4 && <text x={cx+x+.17} y={cy+y-.15} className="orbital-label moon-orbit-label">{moon.name}</text>}
      </g>
    })}
  </g>
}
