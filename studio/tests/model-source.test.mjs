// Резолвер модели (server/model-source.mjs): студия, библиотека Кузницы, версии, отказы.
import test from 'node:test';
import assert from 'node:assert/strict';
import { promises as fs } from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { randomUUID } from 'node:crypto';
import { resolveModelSource } from '../server/model-source.mjs';
import { saveAsset, data } from '../server/storage.mjs';
import { buildGlb } from './helpers/glb.mjs';

async function libraryAsset({ name = 'Knight', source = true, versions = [] } = {}) {
  const library = await fs.mkdtemp(path.join(os.tmpdir(), 'library-'));
  const id = randomUUID();
  const dir = path.join(library, 'assets', id.toLowerCase());
  await fs.mkdir(path.join(dir, 'source'), { recursive: true });
  const manifest = {
    id,
    name,
    kind: 'humanoid',
    source: source ? { type: 'upload', file: 'source/source.glb' } : { type: 'generation', params: {} },
    versions: [],
    pipeline: [],
  };
  if (source) await fs.writeFile(path.join(dir, 'source', 'source.glb'), buildGlb());
  for (const version of versions) {
    await fs.mkdir(path.join(dir, version.version), { recursive: true });
    const file = path.join(dir, version.version, 'model.glb');
    await fs.writeFile(file, buildGlb());
    await fs.writeFile(path.join(dir, version.version, 'version.json'), JSON.stringify({ version: version.version, final: { glb: file } }));
    manifest.versions.push({ version: version.version, status: version.status || 'completed' });
  }
  await fs.writeFile(path.join(dir, 'asset.json'), JSON.stringify(manifest));
  return { library, id, dir };
}

test('ассет студии: берётся из data/assets', async () => {
  const asset = await saveAsset(buildGlb(), '.glb', 'Shield');
  const source = await resolveModelSource(asset.id);
  assert.equal(source.origin, 'studio');
  assert.equal(source.extension, '.glb');
  assert.equal(source.name, 'Shield');
  assert.ok(source.bytes > 0);
  await fs.rm(path.join(data, 'assets', asset.id + '.glb'), { force: true });
  await fs.rm(path.join(data, 'assets', asset.id + '.json'), { force: true });
});

test('ассет Кузницы: исходник без версий', async () => {
  const { library, id } = await libraryAsset({ name: 'Orc' });
  const source = await resolveModelSource(id, { libraryDir: library });
  assert.equal(source.origin, 'library');
  assert.equal(source.version, null);
  assert.equal(source.name, 'Orc');
  assert.match(source.file, /source\.glb$/);
});

test('ассет Кузницы: берётся новейшая завершённая версия, а не исходник', async () => {
  const { library, id } = await libraryAsset({ versions: [{ version: 'v001' }, { version: 'v002' }, { version: 'v003', status: 'failed' }] });
  const source = await resolveModelSource(id, { libraryDir: library });
  assert.equal(source.version, 'v002');
  assert.match(source.file, /v002[\\/]model\.glb$/);
});

test('не-модель в студии и пустой ассет Кузницы объясняются текстом', async () => {
  const sheet = await saveAsset(Buffer.from('<svg/>'), '.svg', 'Sheet');
  await assert.rejects(() => resolveModelSource(sheet.id), (e) => e.status === 400 && /уходит модель/.test(e.message));
  await assert.rejects(() => resolveModelSource(randomUUID()), (e) => e.status === 404);
  await assert.rejects(() => resolveModelSource('../etc'), (e) => e.status === 400);

  const { library, id } = await libraryAsset({ source: false });
  await assert.rejects(() => resolveModelSource(id, { libraryDir: library }), (e) => e.status === 409 && /соберите версию/.test(e.message));
  await fs.rm(library, { recursive: true, force: true });
});

test('путь за папкой ассета не принимается', async () => {
  const { library, id, dir } = await libraryAsset({ source: false });
  const outside = path.join(path.dirname(dir), 'stolen.glb');
  await fs.writeFile(outside, buildGlb());
  const manifestPath = path.join(dir, 'asset.json');
  const manifest = JSON.parse(await fs.readFile(manifestPath, 'utf8'));
  manifest.source = { type: 'upload', file: outside };
  await fs.writeFile(manifestPath, JSON.stringify(manifest));
  await assert.rejects(() => resolveModelSource(id, { libraryDir: library }), (e) => e.status === 409);
  await fs.rm(library, { recursive: true, force: true });
});
