export interface TemporalGraphCandidate {
  timestamp: number
  score: number
}

export interface TemporalGraph<T extends TemporalGraphCandidate> {
  nodes: T[]
  neighbors: Map<number, Array<{ timestamp: number; costDays: number }>>
}

const DAY_MS = 86_400_000
const YEAR_DAYS = 365.25

export interface ConstellationSearchWindow {
  searchStartDay: number
  searchEndDay: number
  broadStepDays: number
  longestRelevantPeriodDays: number
  targetBroadSamples: number
}

export interface ConstellationSearchBudget {
  geometricShortlistLimit: number
  preflightSolverBudget: number
  fullValidationBudget: number
}

export interface AdaptiveLaunchWindowSelection<T extends TemporalGraphCandidate> {
  candidates: T[]
  localPeakCount: number
  qualityFloor: number
  qualifiedNodeCount: number
  coverageTarget: number
}

function clamp(value: number, minimum: number, maximum: number) {
  return Math.max(minimum, Math.min(maximum, value))
}

export function constellationSearchWindow(
  orbitalPeriodsDays: number[],
  routeSectionCount = 1,
): ConstellationSearchWindow {
  const relevantPeriods = orbitalPeriodsDays.filter((periodDays) => periodDays > 0)
  const longestRelevantPeriodDays = Math.max(YEAR_DAYS, ...relevantPeriods)
  const removedLookbackDays = Math.min(730, Math.ceil(longestRelevantPeriodDays / 2))
  const searchStartDay = 0
  const searchEndDay = Math.min(
    Math.ceil(60 * YEAR_DAYS),
    Math.max(Math.ceil(20 * YEAR_DAYS), Math.ceil(longestRelevantPeriodDays * 2.15))
      + removedLookbackDays,
  )
  const targetBroadSamples = clamp(
    1_800 + relevantPeriods.length * 600 + Math.max(1, routeSectionCount) * 500,
    2_400,
    12_000,
  )
  const searchSpanDays = searchEndDay - searchStartDay
  const broadStepDays = clamp(Math.round(searchSpanDays / targetBroadSamples), 1, 14)
  return {
    searchStartDay,
    searchEndDay,
    broadStepDays,
    longestRelevantPeriodDays,
    targetBroadSamples,
  }
}

export function constellationSearchBudget(
  geometricNodeCount: number,
  routeSectionCount: number,
  observedShortlistSize?: number,
): ConstellationSearchBudget {
  const routeComplexity = Math.max(1, routeSectionCount)
  const landscapeCoverage = observedShortlistSize ?? (
    Math.ceil(Math.sqrt(Math.max(1, geometricNodeCount)) * 2)
    + routeComplexity * 16
  )
  const geometricShortlistLimit = Math.min(
    geometricNodeCount,
    Math.max(routeComplexity * 8, landscapeCoverage),
  )
  const preflightSolverBudget = Math.min(
    geometricNodeCount,
    Math.max(
      geometricShortlistLimit,
      Math.ceil(geometricShortlistLimit * 2.5) + routeComplexity * 24,
    ),
  )
  const fullValidationBudget = Math.min(
    preflightSolverBudget,
    Math.max(
      routeComplexity * 8,
      Math.ceil(geometricShortlistLimit * 0.65) + routeComplexity * 8,
    ),
  )
  return { geometricShortlistLimit, preflightSolverBudget, fullValidationBudget }
}

function quantile(sortedValues: number[], fraction: number) {
  if (sortedValues.length === 0) return Number.NEGATIVE_INFINITY
  const index = Math.max(0, Math.min(sortedValues.length - 1, Math.floor((sortedValues.length - 1) * fraction)))
  return sortedValues[index]
}

/**
 * Select launch-window basins from the measured score landscape.
 *
 * There is intentionally no caller-supplied shortlist length. A sharply
 * separated landscape produces a compact set of local peaks; a broad or flat
 * launch window keeps correspondingly more dates for Lambert/solver checks.
 */
export function selectAdaptiveLaunchWindowCandidates<T extends TemporalGraphCandidate>(
  graph: TemporalGraph<T>,
  minimumSeparationDays: number,
  routeSectionCount = 1,
): AdaptiveLaunchWindowSelection<T> {
  if (graph.nodes.length === 0) {
    return {
      candidates: [], localPeakCount: 0,
      qualityFloor: Number.NEGATIVE_INFINITY,
      qualifiedNodeCount: 0, coverageTarget: 0,
    }
  }
  const nodeByTimestamp = new Map(graph.nodes.map((node) => [node.timestamp, node]))
  const localPeaks = graph.nodes.filter((node) => (
    (graph.neighbors.get(node.timestamp) ?? []).every((edge) => (
      node.score >= (nodeByTimestamp.get(edge.timestamp)?.score ?? Number.NEGATIVE_INFINITY)
    ))
  ))
  const ascendingScores = graph.nodes.map((node) => node.score).sort((left, right) => left - right)
  const median = quantile(ascendingScores, 0.5)
  const upperQuartile = quantile(ascendingScores, 0.75)
  const upperDecile = quantile(ascendingScores, 0.9)
  const bestScore = ascendingScores.at(-1) ?? upperDecile
  const upperSpread = Math.max(1e-9, upperDecile - median)
  const qualityFloor = Math.max(
    upperQuartile,
    bestScore - Math.max(1, upperSpread * (1.4 + Math.max(1, routeSectionCount) * 0.12)),
  )
  const qualified = graph.nodes
    .filter((node) => node.score >= qualityFloor)
    .sort((left, right) => right.score - left.score)
  const rankedPeaks = (localPeaks.length > 0 ? localPeaks : qualified)
    .filter((node) => node.score >= qualityFloor)
    .sort((left, right) => right.score - left.score)
  const qualifiedFraction = qualified.length / graph.nodes.length
  const coverageTarget = Math.min(
    graph.nodes.length,
    Math.max(
      rankedPeaks.length,
      Math.ceil(Math.sqrt(graph.nodes.length) * Math.max(1, routeSectionCount)),
      Math.ceil(graph.nodes.length * Math.min(0.35, Math.max(0.04, qualifiedFraction))),
    ),
  )
  const selected: T[] = []
  const primarySeparationMs = Math.max(1, minimumSeparationDays) * DAY_MS
  const refinedSeparationMs = Math.max(1, minimumSeparationDays / 4) * DAY_MS
  const addSeparated = (candidate: T, separationMs: number) => {
    if (selected.some((item) => item.timestamp === candidate.timestamp)) return
    if (selected.every((item) => Math.abs(item.timestamp - candidate.timestamp) >= separationMs)) {
      selected.push(candidate)
    }
  }
  for (const peak of rankedPeaks) addSeparated(peak, primarySeparationMs)
  for (const candidate of qualified) {
    addSeparated(candidate, refinedSeparationMs)
    if (selected.length >= coverageTarget) break
  }
  if (selected.length < coverageTarget) {
    for (const candidate of [...graph.nodes].sort((left, right) => right.score - left.score)) {
      if (!selected.some((item) => item.timestamp === candidate.timestamp)) selected.push(candidate)
      if (selected.length >= coverageTarget) break
    }
  }
  return {
    candidates: selected,
    localPeakCount: localPeaks.length,
    qualityFloor,
    qualifiedNodeCount: qualified.length,
    coverageTarget,
  }
}

export function buildTemporalCandidateGraph<T extends TemporalGraphCandidate>(
  candidates: T[],
  neighborSpan = 2,
): TemporalGraph<T> {
  const bestByTimestamp = new Map<number, T>()
  for (const candidate of candidates) {
    const current = bestByTimestamp.get(candidate.timestamp)
    if (!current || candidate.score > current.score) bestByTimestamp.set(candidate.timestamp, candidate)
  }
  const nodes = [...bestByTimestamp.values()].sort((left, right) => left.timestamp - right.timestamp)
  const neighbors = new Map<number, Array<{ timestamp: number; costDays: number }>>()
  for (const node of nodes) neighbors.set(node.timestamp, [])

  for (let index = 0; index < nodes.length; index += 1) {
    for (let offset = 1; offset <= neighborSpan; offset += 1) {
      const other = nodes[index + offset]
      if (!other) break
      const costDays = Math.abs(other.timestamp - nodes[index].timestamp) / DAY_MS
      neighbors.get(nodes[index].timestamp)?.push({ timestamp: other.timestamp, costDays })
      neighbors.get(other.timestamp)?.push({ timestamp: nodes[index].timestamp, costDays })
    }
  }
  return { nodes, neighbors }
}

export function dijkstraTemporalDistances<T extends TemporalGraphCandidate>(
  graph: TemporalGraph<T>,
  startTimestamp: number,
): Map<number, number> {
  const distances = new Map(graph.nodes.map((node) => [node.timestamp, Number.POSITIVE_INFINITY]))
  if (!distances.has(startTimestamp)) return distances
  distances.set(startTimestamp, 0)
  const pending = new Set(distances.keys())

  while (pending.size > 0) {
    let current: number | null = null
    let currentDistance = Number.POSITIVE_INFINITY
    for (const timestamp of pending) {
      const distance = distances.get(timestamp) ?? Number.POSITIVE_INFINITY
      if (distance < currentDistance) {
        current = timestamp
        currentDistance = distance
      }
    }
    if (current === null || !Number.isFinite(currentDistance)) break
    pending.delete(current)
    for (const edge of graph.neighbors.get(current) ?? []) {
      if (!pending.has(edge.timestamp)) continue
      const nextDistance = currentDistance + edge.costDays
      if (nextDistance < (distances.get(edge.timestamp) ?? Number.POSITIVE_INFINITY)) {
        distances.set(edge.timestamp, nextDistance)
      }
    }
  }
  return distances
}

export function selectDiverseGraphCandidates<T extends TemporalGraphCandidate>(
  graph: TemporalGraph<T>,
  limit: number,
  minimumSeparationDays: number,
): T[] {
  if (limit <= 0) return []
  const nodeByTimestamp = new Map(graph.nodes.map((node) => [node.timestamp, node]))
  const localPeaks = graph.nodes.filter((node) => (
    (graph.neighbors.get(node.timestamp) ?? []).every((edge) => (
      node.score >= (nodeByTimestamp.get(edge.timestamp)?.score ?? Number.NEGATIVE_INFINITY)
    ))
  ))
  const ranked = (localPeaks.length > 0 ? localPeaks : graph.nodes)
    .sort((left, right) => right.score - left.score)
  const selected: T[] = []
  const minimumSeparationMs = minimumSeparationDays * DAY_MS

  for (const candidate of ranked) {
    if (
      selected.every((other) => (
        Math.abs(other.timestamp - candidate.timestamp) >= minimumSeparationMs
      ))
    ) {
      selected.push(candidate)
    }
    if (selected.length >= limit) break
  }

  if (selected.length < limit) {
    for (const candidate of graph.nodes.sort((left, right) => right.score - left.score)) {
      if (!selected.some((item) => item.timestamp === candidate.timestamp)) selected.push(candidate)
      if (selected.length >= limit) break
    }
  }
  return selected
}

export function temporalRefinementNeighbors(
  timestamp: number,
  refinementLevel: number,
  broadStepDays: number,
): number[] {
  const refinementSteps = [
    Math.max(90, broadStepDays * 4),
    Math.max(30, broadStepDays * 2),
    Math.max(7, broadStepDays),
    1,
  ]
  const stepDays = refinementSteps[
    Math.min(refinementLevel, refinementSteps.length - 1)
  ]
  if (refinementLevel === 0) {
    const longStepDays = Math.max(270, broadStepDays * 12)
    return [
      timestamp - longStepDays * DAY_MS,
      timestamp - stepDays * DAY_MS,
      timestamp + stepDays * DAY_MS,
      timestamp + longStepDays * DAY_MS,
    ]
  }
  return [
    timestamp - stepDays * DAY_MS,
    timestamp + stepDays * DAY_MS,
  ]
}

export function selectTemporallyDiverseCandidates<T>(
  rankedCandidates: T[],
  timestampOf: (candidate: T) => number,
  limit: number,
  minimumSeparationDays: number,
): T[] {
  if (limit <= 0) return []
  const minimumSeparationMs = minimumSeparationDays * DAY_MS
  const selected: T[] = []
  for (const candidate of rankedCandidates) {
    const timestamp = timestampOf(candidate)
    if (selected.every((other) => (
      Math.abs(timestampOf(other) - timestamp) >= minimumSeparationMs
    ))) {
      selected.push(candidate)
    }
    if (selected.length >= limit) return selected
  }
  for (const candidate of rankedCandidates) {
    if (!selected.includes(candidate)) selected.push(candidate)
    if (selected.length >= limit) break
  }
  return selected
}
