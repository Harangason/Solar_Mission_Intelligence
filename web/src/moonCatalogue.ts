import type { MoonCatalogue, MoonData } from './types'

export function isNaturalMoon(moon: MoonData) {
  return !String(moon.orbitSource ?? '').toLowerCase().includes('celestrak')
}

export function sanitizeMoonCatalogue(catalogue: MoonCatalogue | null): MoonCatalogue | null {
  if (!catalogue) return null

  const naturalMoons = catalogue.moons.filter(isNaturalMoon)
  const planetIds = new Set<string>(Object.keys(catalogue.counts ?? {}))
  const counts: Record<string, number> = {}

  for (const planetId of planetIds) {
    if (planetId.toLowerCase().endsWith('artificial')) continue
    counts[planetId] = naturalMoons.filter((moon) => moon.parentId === planetId).length
  }

  for (const moon of naturalMoons) {
    counts[moon.parentId] = naturalMoons.filter((candidate) => candidate.parentId === moon.parentId).length
  }

  return {
    ...catalogue,
    counts,
    moons: naturalMoons,
  }
}
