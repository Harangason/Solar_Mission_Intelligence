import { OrbitControls } from '@react-three/drei'
import { useThree } from '@react-three/fiber'
import { useEffect, useRef } from 'react'
import * as THREE from 'three'
import type { OrbitControls as OrbitControlsImpl } from 'three-stdlib'

type SystemCameraView = 'perspective' | 'top' | 'front' | 'side'
export type FocusedCameraView = SystemCameraView | 'sun-to-target' | 'sun-behind' | 'cross-axis'

export type CameraFocusRequest =
  | { kind: 'overview'; view: SystemCameraView; requestId: number }
  | {
      kind: 'planet'
      planetId: string
      requestId: number
      view?: FocusedCameraView
      preserveDistance?: boolean
    }
  | {
      kind: 'point'
      label: string
      position: [number, number, number]
      radius: number
      requestId: number
      view?: FocusedCameraView
      preserveDistance?: boolean
    }

interface PlanetCameraControlsProps {
  request: CameraFocusRequest
  focusPosition: THREE.Vector3 | null
  focusRadius: number
  navigationMode: 'rotate' | 'pan'
  enabled: boolean
}

const OVERVIEW_POSITIONS = {
  perspective: new THREE.Vector3(32, 54, 36),
  top: new THREE.Vector3(0, 78, 0),
  front: new THREE.Vector3(0, 0, 78),
  side: new THREE.Vector3(78, 0, 0),
} as const

const FOCUSED_VIEW_DIRECTIONS = {
  perspective: new THREE.Vector3(1, 0.65, 1).normalize(),
  top: new THREE.Vector3(0, 1, 0),
  front: new THREE.Vector3(0, 0, 1),
  side: new THREE.Vector3(1, 0, 0),
} as const

function getFocusedViewDirection(view: FocusedCameraView, focusPosition: THREE.Vector3) {
  if (view in FOCUSED_VIEW_DIRECTIONS) {
    return FOCUSED_VIEW_DIRECTIONS[view as SystemCameraView].clone()
  }

  // Radial axis: Sun (scene origin) -> focused object. A camera on the
  // negative radial side looks from the Sun toward the target; the positive
  // side puts the Sun behind the selected planet.
  const radialDirection = focusPosition.clone()
  if (radialDirection.lengthSq() < 1e-8) radialDirection.set(1, 0, 0)
  radialDirection.normalize()
  if (view === 'sun-to-target') return radialDirection.multiplyScalar(-1)
  if (view === 'sun-behind') return radialDirection

  const crossAxis = new THREE.Vector3().crossVectors(new THREE.Vector3(0, 1, 0), radialDirection)
  if (crossAxis.lengthSq() < 1e-8) {
    crossAxis.crossVectors(new THREE.Vector3(0, 0, 1), radialDirection)
  }
  return crossAxis.normalize()
}

export function PlanetCameraControls({
  request,
  focusPosition,
  focusRadius,
  navigationMode,
  enabled,
}: PlanetCameraControlsProps) {
  const controlsRef = useRef<OrbitControlsImpl>(null)
  const lastRequestId = useRef(-1)
  const lastPlanetPosition = useRef<THREE.Vector3 | null>(null)
  const { camera, size } = useThree()
  const lastAspect = useRef(0)
  const overviewMaxDistance = 5000
  const overviewFar = 10_000
  const planetMinDistance = Math.max(focusRadius * 1.12, 0.0015)
  const planetMaxDistance = Math.max(focusRadius * 5_000, 1_200)
  const planetFar = Math.max(focusRadius * 30_000, 6_000)

  useEffect(() => {
    const controls = controlsRef.current
    if (!controls) return

    const aspect = size.width / Math.max(1, size.height)
    const isNewRequest = request.requestId !== lastRequestId.current || lastAspect.current !== aspect
    lastAspect.current = aspect
    if (request.kind === 'overview') {
      if (isNewRequest) {
        controls.target.set(0, 0, 0)
        camera.up.set(0, request.view === 'top' ? 0 : 1, request.view === 'top' ? -1 : 0)
        camera.position.copy(OVERVIEW_POSITIONS[request.view]).multiplyScalar(Math.max(1, 1 / aspect))
        camera.near = 0.0001
        camera.far = overviewFar
        camera.updateProjectionMatrix()
        controls.update()
        lastPlanetPosition.current = null
      }
      lastRequestId.current = request.requestId
      return
    }

    if (!focusPosition) return
    if (isNewRequest) {
      const viewDirection = request.view
        ? getFocusedViewDirection(request.view, focusPosition)
        : camera.position.clone().sub(controls.target)
      if (viewDirection.lengthSq() < 1e-8) viewDirection.set(1, 0.55, 1)
      viewDirection.normalize()
      const currentDistance = camera.position.distanceTo(controls.target)
      const viewingDistance = request.preserveDistance
        ? THREE.MathUtils.clamp(currentDistance, planetMinDistance, planetMaxDistance)
        : Math.max(focusRadius * 7, 0.025)

      controls.target.copy(focusPosition)
      camera.up.set(0, request.view === 'top' ? 0 : 1, request.view === 'top' ? -1 : 0)
      camera.position.copy(focusPosition).addScaledVector(viewDirection, viewingDistance)
      camera.near = Math.max(focusRadius / 80, 0.00001)
      camera.far = planetFar
      camera.updateProjectionMatrix()
      controls.update()
    } else if (lastPlanetPosition.current) {
      // Keep a focused planet centered while mission time advances, without
      // destroying a manual orbit/pan offset chosen by the user.
      const motion = focusPosition.clone().sub(lastPlanetPosition.current)
      controls.target.add(motion)
      camera.position.add(motion)
      controls.update()
    }

    lastRequestId.current = request.requestId
    lastPlanetPosition.current = focusPosition.clone()
  }, [camera, focusPosition, focusPosition?.x, focusPosition?.y, focusPosition?.z, focusRadius, request, size.width, size.height])

  const focused = request.kind !== 'overview'
  const minimumDistance = focused ? planetMinDistance : 0.0015

  return (
    <OrbitControls
      ref={controlsRef}
      makeDefault
      minDistance={minimumDistance}
      maxDistance={focused ? planetMaxDistance : overviewMaxDistance}
      enabled={enabled}
      enableDamping
      enablePan
      enableZoom
      zoomToCursor
      screenSpacePanning
      panSpeed={1.2}
      rotateSpeed={0.7}
      zoomSpeed={1.45}
      mouseButtons={{
        LEFT: navigationMode === 'pan' ? THREE.MOUSE.PAN : THREE.MOUSE.ROTATE,
        MIDDLE: THREE.MOUSE.DOLLY,
        RIGHT: navigationMode === 'pan' ? THREE.MOUSE.ROTATE : THREE.MOUSE.PAN,
      }}
      touches={{
        ONE: navigationMode === 'pan' ? THREE.TOUCH.PAN : THREE.TOUCH.ROTATE,
        TWO: THREE.TOUCH.DOLLY_PAN,
      }}
    />
  )
}
