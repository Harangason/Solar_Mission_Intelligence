import * as THREE from 'three'
import { useId } from 'react'
import { localEncounterRuns, type LocalEncounterGeometry } from '../localEncounterGeometry'

export function LocalEncounterPlot({ geometry, bodyName }: { geometry: LocalEncounterGeometry; bodyName: string }) {
  const runs = localEncounterRuns(geometry, 2.5)
  const marker = useId()
  const near = geometry.states.reduce((a, b) => Math.hypot(...a.position) < Math.hypot(...b.position) ? a : b)
  const x = new THREE.Vector3(...near.position).normalize()
  const normal = x.clone().cross(new THREE.Vector3(...near.velocity)).normalize()
  const y = normal.clone().cross(x).normalize()
  const scale = 95 / geometry.bodyRadiusKm
  const project = (p: [number, number, number]) => {
    const v = new THREE.Vector3(...p)
    return [500 + v.dot(x) * scale, 310 - v.dot(y) * scale]
  }
  return <svg viewBox="0 0 1000 620" className="route-preview-canvas editor-layout" role="img" aria-label={`Berechneter Nahbereich um ${bodyName}`}>
    <defs><marker id={marker} markerWidth="7" markerHeight="7" refX="6" refY="3" orient="auto"><path d="M0 0 L6 3 L0 6 Z" fill="#67dcff" /></marker></defs>
    <text x="24" y="30" className="preview-note">Berechnete Zustände · körperbezogen · Projektion in die Periapsisebene</text>
    <text x="24" y="50" className="preview-note">Nahbereich bis 2,5 Körperradien · Höhe min. {geometry.minimumAltitudeKm.toFixed(1)} km</text>
    <circle cx="500" cy="310" r="95" className="preview-target" />
    <text x="500" y="315" textAnchor="middle">{bodyName}</text>
    {runs.map((run, i) => <polyline key={i} points={run.map(s => project(s.position).join(',')).join(' ')} className="preview-passage-path" fill="none" />)}
    {runs.flatMap((run, index) => [run[0], run.at(-1)!].map((s, end) => {
      const [px, py] = project(s.position)
      const velocity = new THREE.Vector3(...s.velocity).normalize()
      return <line key={`${index}-${end}`} x1={px} y1={py} x2={px+velocity.dot(x)*55} y2={py-velocity.dot(y)*55} stroke="#67dcff" strokeWidth="2" markerEnd={`url(#${marker})`} />
    }))}
    {geometry.states.filter(s => s.phase.includes('ORBIT_INSERTION')).map((s, i) => {
      const [px, py] = project(s.position)
      return <g key={i}><circle cx={px} cy={py} r="5" fill="#ffae66" /><text x={px+12} y={py-14} className="preview-note">Einfangmanöver</text></g>
    })}
    {!runs.length && <text x="500" y="580" textAnchor="middle">Keine berechneten Zustände im Nahbereich.</text>}
    <text x="24" y="596" className="preview-note">Körper und Bahn im selben Maßstab · Projektion kann räumlich getrennte Punkte überlagern</text>
  </svg>
}
