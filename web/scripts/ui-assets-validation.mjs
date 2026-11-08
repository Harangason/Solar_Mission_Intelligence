import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import { PLANET_TEXTURES } from '../src/planetTextures.ts'

for (const [planet, path] of Object.entries(PLANET_TEXTURES)) {
  const bytes = await readFile(new URL(`../public${path}`, import.meta.url))
  assert.equal(bytes.readUInt16BE(0), 0xffd8, `${planet}: valid JPEG signature`)
  assert.ok(bytes.length > 1000, `${planet}: non-empty texture`)
}
console.log(`PASS: ${Object.keys(PLANET_TEXTURES).length} referenced planet textures are available JPEGs`)
