import { useFrame } from '@react-three/fiber'
import { useRef } from 'react'
import * as THREE from 'three'

// A continuous sky dome: no billboard edges, depth occlusion or image seams.
const vertexShader = `varying vec3 skyDirection;
void main() { skyDirection = normalize(position); gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0); }`
const fragmentShader = `varying vec3 skyDirection;
void main() {
  vec3 d = normalize(skyDirection);
  float latitude = dot(d, normalize(vec3(0.35, 0.83, -0.43)));
  float band = exp(-latitude * latitude * 18.0);
  float cloud = 0.52 + 0.18*sin(d.x*18.0 + sin(d.z*13.0)) + 0.13*sin(d.z*31.0+d.y*17.0);
  vec3 base = vec3(0.012, 0.023, 0.048);
  vec3 dust = mix(vec3(0.045, 0.07, 0.13), vec3(0.11, 0.075, 0.14), smoothstep(-0.7, 0.7, d.x));
  gl_FragColor = vec4(base + dust * band * cloud, 1.0);
}`
export function MilkyWayBackground() {
  const mesh = useRef<THREE.Mesh>(null)
  useFrame(({ camera }) => { mesh.current?.position.copy(camera.position) })
  return <mesh ref={mesh} renderOrder={-1000} frustumCulled={false} raycast={() => null}>
    <sphereGeometry args={[1500, 32, 20]} />
    <shaderMaterial vertexShader={vertexShader} fragmentShader={fragmentShader} side={THREE.BackSide} depthWrite={false} depthTest={false} toneMapped={false} fog={false} />
  </mesh>
}
