import fs from 'node:fs'
import path from 'node:path'

const cataloguePath = path.resolve('public/moons.json')
const data = JSON.parse(fs.readFileSync(cataloguePath, 'utf8'))
const earthNatural = data.moons.filter((moon) => moon.parentId === 'earth' && !String(moon.orbitSource ?? '').includes('celestrak'))

if (earthNatural.length !== 1 || earthNatural[0].name !== 'Moon') {
  throw new Error(`Expected only the natural Moon for Earth, found ${earthNatural.length}: ${earthNatural.slice(0, 5).map((moon) => moon.name).join(', ')}`)
}

const totalNatural = data.moons.filter((moon) => !String(moon.orbitSource ?? '').includes('celestrak')).length
if (totalNatural <= 0) {
  throw new Error('Natural moon list is empty after filtering out Celestrak objects.')
}

console.log(`Natural moon validation passed: Earth ${earthNatural.length} natural moon(s), total ${totalNatural}.`)
