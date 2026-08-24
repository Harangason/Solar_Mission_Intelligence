export interface RankableRouteCandidate {
  id: string
  status: string
  fullCorridorCheck: boolean
  quality?: number
  feasible?: boolean
  geometryValid?: boolean
  corridorSatisfied?: boolean
  collisionFree?: boolean
  performanceEvaluated?: boolean
  requiredInjectionDeltaVKmS?: number
  availableInjectionDeltaVKmS?: number
  targetCorrectionDeltaVKmS?: number
  corridorInsertionDeficitKmS?: number
}

function finite(value: number | undefined) {
  return value !== undefined && Number.isFinite(value)
}

export function routeCandidateDeficit(candidate: RankableRouteCandidate) {
  if (
    !candidate.performanceEvaluated
    || !finite(candidate.requiredInjectionDeltaVKmS)
    || !finite(candidate.availableInjectionDeltaVKmS)
  ) return Number.POSITIVE_INFINITY
  return Math.max(
    0,
    candidate.requiredInjectionDeltaVKmS!
      + (candidate.targetCorrectionDeltaVKmS ?? 0)
      - candidate.availableInjectionDeltaVKmS!,
  ) + (candidate.corridorInsertionDeficitKmS ?? 0)
}

export function isSelectableRouteCandidate(candidate: RankableRouteCandidate) {
  return (
    candidate.fullCorridorCheck
    && candidate.status === 'performance-valid'
    && candidate.performanceEvaluated === true
    && candidate.feasible === true
    && candidate.geometryValid === true
    && candidate.corridorSatisfied === true
    && candidate.collisionFree === true
  )
}

function candidateTier(candidate: RankableRouteCandidate) {
  if (isSelectableRouteCandidate(candidate)) return 4
  if (candidate.fullCorridorCheck && candidate.status === 'success') return 3
  if (candidate.fullCorridorCheck && candidate.geometryValid) return 2
  if (candidate.status === 'running') return 1
  return 0
}

export function rankRouteCandidates<T extends RankableRouteCandidate>(candidates: T[]) {
  return [...candidates].sort((left, right) => (
    candidateTier(right) - candidateTier(left)
    || Number(right.fullCorridorCheck) - Number(left.fullCorridorCheck)
    || (right.quality ?? Number.NEGATIVE_INFINITY) - (left.quality ?? Number.NEGATIVE_INFINITY)
    || routeCandidateDeficit(left) - routeCandidateDeficit(right)
  ))
}
