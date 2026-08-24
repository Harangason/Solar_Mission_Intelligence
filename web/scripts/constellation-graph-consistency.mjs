import assert from 'node:assert/strict'

import {
  buildTemporalCandidateGraph,
  constellationSearchBudget,
  constellationSearchWindow,
  dijkstraTemporalDistances,
  selectDiverseGraphCandidates,
  selectAdaptiveLaunchWindowCandidates,
  selectTemporallyDiverseCandidates,
  temporalRefinementNeighbors,
} from '../src/constellationGraph.ts'

const dayMs = 86_400_000
const candidates = [
  { timestamp: 0, score: 2, label: 'a' },
  { timestamp: 10 * dayMs, score: 9, label: 'peak-a' },
  { timestamp: 20 * dayMs, score: 3, label: 'b' },
  { timestamp: 200 * dayMs, score: 8, label: 'peak-b' },
  { timestamp: 210 * dayMs, score: 2, label: 'c' },
]
const graph = buildTemporalCandidateGraph(candidates, 1)
const distances = dijkstraTemporalDistances(graph, 0)
assert.equal(distances.get(20 * dayMs), 20)
assert.equal(distances.get(210 * dayMs), 210)

const selected = selectDiverseGraphCandidates(graph, 2, 100)
assert.deepEqual(selected.map((candidate) => candidate.label), ['peak-a', 'peak-b'])

const rankedSolverCandidates = [
  { timestamp: 100 * dayMs, quality: 10, label: 'basin-a-best' },
  { timestamp: 102 * dayMs, quality: 9, label: 'basin-a-local' },
  { timestamp: 500 * dayMs, quality: 8, label: 'basin-b-best' },
  { timestamp: 900 * dayMs, quality: 7, label: 'basin-c-best' },
]
assert.deepEqual(
  selectTemporallyDiverseCandidates(
    rankedSolverCandidates,
    (candidate) => candidate.timestamp,
    3,
    180,
  ).map((candidate) => candidate.label),
  ['basin-a-best', 'basin-b-best', 'basin-c-best'],
)

assert.deepEqual(
  temporalRefinementNeighbors(100 * dayMs, 0, 20),
  [-170 * dayMs, 10 * dayMs, 190 * dayMs, 370 * dayMs],
)
assert.deepEqual(
  temporalRefinementNeighbors(100 * dayMs, 2, 20),
  [80 * dayMs, 120 * dayMs],
)

const earthWindow = constellationSearchWindow([365.25])
assert.ok(earthWindow.searchEndDay >= 20 * 365)
const jupiterWindow = constellationSearchWindow([365.25, 4332.59])
assert.ok(jupiterWindow.searchEndDay >= 2 * 4332.59)
assert.ok(jupiterWindow.searchEndDay > 2920)
assert.ok(jupiterWindow.broadStepDays <= 14)
const jupiterSampleCount = Math.floor(
  (jupiterWindow.searchEndDay - jupiterWindow.searchStartDay) / jupiterWindow.broadStepDays,
) + 1
assert.ok(jupiterSampleCount >= 2400)

const smallBudget = constellationSearchBudget(1000, 1)
const complexBudget = constellationSearchBudget(4000, 3)
assert.ok(smallBudget.geometricShortlistLimit >= 24)
assert.ok(complexBudget.geometricShortlistLimit > smallBudget.geometricShortlistLimit)
assert.ok(complexBudget.preflightSolverBudget > complexBudget.geometricShortlistLimit)
assert.ok(complexBudget.fullValidationBudget >= 12)

const adaptiveSharp = selectAdaptiveLaunchWindowCandidates(graph, 10, 1)
assert.ok(adaptiveSharp.candidates.length > 0)
assert.equal(adaptiveSharp.localPeakCount, 2)

const flatGraph = buildTemporalCandidateGraph(Array.from({ length: 400 }, (_, index) => ({
  timestamp: Date.UTC(2030, 0, 1) + index * 86_400_000,
  score: 100 + Math.sin(index / 20) * 0.01,
})))
const adaptiveFlat = selectAdaptiveLaunchWindowCandidates(flatGraph, 10, 1)
assert.ok(adaptiveFlat.candidates.length > adaptiveSharp.candidates.length)

function formatRoutePathLabel(sections) {
  const chains = []
  let currentChain = []
  for (const section of sections) {
    if (currentChain.length === 0) {
      currentChain = [section.originId, section.targetId]
    } else if (currentChain[currentChain.length - 1] === section.originId) {
      currentChain.push(section.targetId)
    } else {
      chains.push(currentChain.join(' → '))
      currentChain = [section.originId, section.targetId]
    }
  }
  if (currentChain.length > 0) chains.push(currentChain.join(' → '))
  return chains.join(' · ')
}
assert.equal(
  formatRoutePathLabel([
    { originId: 'earth', targetId: 'mars' },
    { originId: 'mars', targetId: 'earth' },
  ]),
  'earth → mars → earth',
)
assert.equal(
  formatRoutePathLabel([
    { originId: 'earth', targetId: 'mars' },
    { originId: 'venus', targetId: 'jupiter' },
  ]),
  'earth → mars · venus → jupiter',
)

console.log('constellation graph consistency: ok')
