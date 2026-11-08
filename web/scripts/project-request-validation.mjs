import assert from 'node:assert/strict'
import { createProject, deleteProject, listProjects, loadProject, updateProject } from '../src/projectStore.ts'

const originalFetch = globalThis.fetch
const calls = []
function reply(body, status = 200) {
  globalThis.fetch = async (url, init) => {
    calls.push({url, init})
    return new Response(body, {status})
  }
}
try {
  reply(JSON.stringify({projects: [{id: 'example'}]}))
  assert.equal((await listProjects())[0].id, 'example')
  assert.ok(calls.at(-1).init.signal instanceof AbortSignal, 'Requests must have a timeout signal')
  reply(JSON.stringify({id: 'test'}))
  await loadProject('test/id')
  assert.equal(calls.at(-1).url, '/api/projects/test%2Fid')
  await createProject('Mission', 'Description', {schemaVersion: 1})
  assert.equal(calls.at(-1).init.method, 'POST')
  assert.equal(JSON.parse(calls.at(-1).init.body).name, 'Mission')
  await updateProject('test', 'Updated', '', {schemaVersion: 1})
  assert.equal(calls.at(-1).init.method, 'PUT')
  reply(null, 204)
  await deleteProject('test/id')
  assert.equal(calls.at(-1).init.method, 'DELETE')
  assert.equal(calls.at(-1).url, '/api/projects/test%2Fid')
  reply(JSON.stringify({error: 'Projekt nicht gefunden'}), 404)
  await assert.rejects(loadProject('missing'), /Projekt nicht gefunden/)
  await assert.rejects(deleteProject('missing'), /Projekt nicht gefunden/)
  reply('<html>Service unavailable</html>', 503)
  await assert.rejects(listProjects(), /Ungültige Serverantwort.*503/)
  reply('null')
  await assert.rejects(loadProject('bad'), /keine gültigen Projektdaten/)
  globalThis.fetch = async () => { throw new TypeError('Failed to fetch') }
  await assert.rejects(listProjects(), /Projektserver nicht erreichbar/)
  globalThis.fetch = async () => { throw new DOMException('Timed out', 'TimeoutError') }
  await assert.rejects(createProject('Mission', '', {}), /nicht rechtzeitig/)
  console.log('PASS: project CRUD requests, request encoding, timeout signals, HTTP errors, invalid responses, offline and timeout recovery')
} finally {
  globalThis.fetch = originalFetch
}
