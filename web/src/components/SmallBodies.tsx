import { Html, Line } from '@react-three/drei'
import { useMemo } from 'react'
import { smallBodyOrbit, smallBodyPositionAt, type SmallBody } from '../smallBodies'

export function SmallBodies({ bodies, timestampMs, scale, labels }: { bodies: SmallBody[]; timestampMs: number; scale: number; labels: boolean }) {
  const paths = useMemo(() => bodies.map(body => ({ body, points: smallBodyOrbit(body, scale) })), [bodies, scale])
  return <group>{paths.map(({ body, points }) => {
    const position = smallBodyPositionAt(body, timestampMs, scale)
    const tail = position.clone().addScaledVector(position.clone().normalize(), 1.6)
    return <group key={body.id}>
      <Line points={points} color={body.color} transparent opacity={body.kind === 'comet' ? .24 : .15} lineWidth={.65} />
      {body.kind === 'comet' && <Line points={[position, tail]} color={body.color} transparent opacity={.55} lineWidth={2} />}
      <mesh position={position}><sphereGeometry args={[body.kind === 'comet' ? .10 : .065, 12, 12]} /><meshBasicMaterial color={body.color} /></mesh>
      {labels && <Html center position={position.clone().addScalar(.18)} style={{ pointerEvents: 'none' }}><span className="small-body-label-3d">{body.name.split(' (')[0]}</span></Html>}
    </group>
  })}</group>
}
