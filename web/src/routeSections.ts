import { DEFAULT_ENTRY_CORRIDOR, type EntryCorridorDefinition } from './entryCorridorGeometry'

export type RoutePassageMode = 'direct' | 'partial-orbit' | 'full-orbit'
export type RoutePassageDirection = 'prograde' | 'retrograde'
export type RouteBoundaryBehavior = 'ballistic' | 'tangential-prograde' | 'tangential-retrograde' | 'tangential-accelerate' | 'radial'

export const MAX_PARTIAL_ORBIT_ANGLE_DEG = 1080

export interface RoutePassageDefinition {
  mode: RoutePassageMode
  orbitAngleDeg: number
  orbitDirection: RoutePassageDirection
  entryBehavior: RouteBoundaryBehavior
  exitBehavior: RouteBoundaryBehavior
}

export interface RouteSectionDefinition {
  id: string
  originId: string
  targetId: string
  corridor: EntryCorridorDefinition
  passage: RoutePassageDefinition
  deltaVMinusKmS: number
  deltaVPlusKmS: number
}

export const DEFAULT_ROUTE_PASSAGE: RoutePassageDefinition = {
  mode: 'direct',
  orbitAngleDeg: 0,
  orbitDirection: 'prograde',
  entryBehavior: 'ballistic',
  exitBehavior: 'ballistic',
}

export const DEFAULT_ROUTE_SECTION: RouteSectionDefinition = {
  id: 'route-section-1',
  originId: 'sun',
  targetId: 'jupiter',
  corridor: {
    ...DEFAULT_ENTRY_CORRIDOR,
    enabled: true,
  },
  passage: { ...DEFAULT_ROUTE_PASSAGE },
  deltaVMinusKmS: 0.5,
  deltaVPlusKmS: 0.5,
}

export function createRouteSection(originId: string, targetId: string): RouteSectionDefinition {
  return {
    ...DEFAULT_ROUTE_SECTION,
    id: `route-section-${crypto.randomUUID()}`,
    originId,
    targetId,
    corridor: { ...DEFAULT_ROUTE_SECTION.corridor },
    passage: { ...DEFAULT_ROUTE_PASSAGE },
  }
}

export function routePassage(section: RouteSectionDefinition): RoutePassageDefinition {
  return {
    ...DEFAULT_ROUTE_PASSAGE,
    ...section.passage,
  }
}

export function normalizeRouteSection(
  section: Partial<RouteSectionDefinition> | null | undefined,
  index = 0,
): RouteSectionDefinition {
  const normalizedPassage: RoutePassageDefinition = {
    ...DEFAULT_ROUTE_PASSAGE,
    ...(section?.passage ?? {}),
  }

  const normalizedCorridor = section?.corridor
    ? { ...DEFAULT_ENTRY_CORRIDOR, ...section.corridor }
    : { ...DEFAULT_ENTRY_CORRIDOR, enabled: true }

  const deltaVMinusKmS = Number.isFinite(section?.deltaVMinusKmS)
    ? Number(section?.deltaVMinusKmS)
    : DEFAULT_ROUTE_SECTION.deltaVMinusKmS
  const deltaVPlusKmS = Number.isFinite(section?.deltaVPlusKmS)
    ? Number(section?.deltaVPlusKmS)
    : DEFAULT_ROUTE_SECTION.deltaVPlusKmS

  return {
    ...DEFAULT_ROUTE_SECTION,
    ...section,
    id: section?.id ?? `route-section-${index + 1}`,
    originId: section?.originId ?? 'sun',
    targetId: section?.targetId ?? 'earth',
    corridor: normalizedCorridor,
    passage: normalizedPassage,
    deltaVMinusKmS,
    deltaVPlusKmS,
  }
}

export function normalizeRouteSections(
  sections: Array<Partial<RouteSectionDefinition> | null | undefined>,
): RouteSectionDefinition[] {
  return sections.map((section, index) => normalizeRouteSection(section, index))
}
