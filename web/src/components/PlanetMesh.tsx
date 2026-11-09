import { Html, useTexture } from '@react-three/drei'
import { useFrame } from '@react-three/fiber'
import { useEffect, useRef, useState } from 'react'
import * as THREE from 'three'

import { planetPositionAt } from '../orbitalMath'
import { planetTextureUrl } from '../planetTextures'
import type { PlanetData } from '../types'

interface PlanetMeshProps {
  planet: PlanetData
  size: number
  timestampMs: number
  distanceScale: number
  inclinationScale: number
  ringScale: number
  showLabels: boolean
  showOverviewMarker?: boolean
  onSelect: (planet: PlanetData) => void
}

export function PlanetMesh({ planet, size, timestampMs, distanceScale, inclinationScale, ringScale, showLabels, showOverviewMarker = false, onSelect }: PlanetMeshProps) {
  const positionRef = useRef<THREE.Group>(null)
  const planetRef = useRef<THREE.Mesh>(null)
  const [hovered, setHovered] = useState(false)
  const texture = useTexture(planetTextureUrl(planet.id))

  useEffect(() => {
    texture.colorSpace = THREE.SRGBColorSpace
    texture.anisotropy = 8
    texture.needsUpdate = true
  }, [texture])

  useFrame((_, delta) => {
    const position = planetPositionAt(planet, timestampMs, distanceScale, inclinationScale)
    positionRef.current?.position.copy(position)
    if (planetRef.current) planetRef.current.rotation.y += delta * 0.35
  })

  return (
    <group ref={positionRef}>
      {showOverviewMarker && <mesh onClick={event => { event.stopPropagation(); onSelect(planet) }}>
        <sphereGeometry args={[Math.max(size, .15), 20, 20]} />
        <meshBasicMaterial color={planet.color} />
      </mesh>}
      {showOverviewMarker && <Html center position={[0, 0, 0]}>
        <button className="planet-overview-target" type="button" aria-label={`${planet.name} fokussieren`} onClick={() => onSelect(planet)}>
          <i style={{ background: planet.color }} />{showLabels && <span>{planet.name}</span>}
        </button>
      </Html>}
      <mesh
        ref={planetRef}
        onClick={(event) => {
          event.stopPropagation()
          onSelect(planet)
        }}
        onPointerEnter={() => setHovered(true)}
        onPointerLeave={() => setHovered(false)}
      >
        <sphereGeometry args={[size, 64, 64]} />
        <meshStandardMaterial
          map={texture}
          color="#ffffff"
          emissive={hovered ? planet.color : '#000000'}
          emissiveIntensity={hovered ? 0.18 : 0}
          roughness={0.9}
          metalness={0.01}
        />
      </mesh>
      <mesh
        onClick={(event) => {
          event.stopPropagation()
          onSelect(planet)
        }}
        onPointerEnter={() => setHovered(true)}
        onPointerLeave={() => setHovered(false)}
      >
        <sphereGeometry args={[Math.max(size, 0.075), 12, 12]} />
        <meshBasicMaterial transparent opacity={0} depthWrite={false} colorWrite={false} />
      </mesh>
      {planet.hasRings && (
        <mesh rotation={[Math.PI / 2.35, 0, 0]}>
          <ringGeometry args={[size * 1.35 * ringScale, size * 2.18 * ringScale, 128]} />
          <meshStandardMaterial color="#d8c9a4" side={THREE.DoubleSide} transparent opacity={0.72} roughness={0.92} />
        </mesh>
      )}
      {!showOverviewMarker && (hovered || showLabels) && (
        <Html center position={[0, size + 0.7, 0]}>
          <span className="planet-label">{planet.name}</span>
        </Html>
      )}
    </group>
  )
}
