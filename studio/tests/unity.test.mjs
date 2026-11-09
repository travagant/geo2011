// Unity-пакет (server/unity.mjs): что внутри .zip, как называются папки, что в README
// и что студия честно отказывается паковать (не-модели, не-текстуры, чужие id).
import test from 'node:test';
import assert from 'node:assert/strict';
import { promises as fs } from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { randomUUID } from 'node:crypto';
import JSZip from 'jszip';
import { buildUnityBundle, unityName, unityReadme, isTextureFile, unityModelExtension } from '../server/unity.mjs';
import { data, saveAsset } from '../server/storage.mjs';
import { buildGlb } from './helpers/glb.mjs';

/** Минимальный, но настоящий GLB: заголовок glTF, чтобы файл не был пустышкой. */
function glb(size = 512) {
  const buf = Buffer.alloc(size, 7);
  buf.write('glTF', 0, 'ascii');
  return buf;
}

async function tempDir() {
  return fs.mkdtemp(path.join(os.tmpdir(), 'unity-bundle-'));
}

async function modelAsset(name = 'Knight', extension = '.glb') {
  return saveAsset(glb(), extension, name);
}

test('unityName даёт имя папки без пробелов и служебных символов', () => {
  assert.equal(unityName('Knight of Death.glb'), 'Knight-of-Death');
  assert.equal(unityName('  боец   с копьём '), 'boec-s-kopem');
  assert.equal(unityName('Ёлка'), 'Elka');
  assert.equal(unityName(''), 'Asset');
  assert.equal(unityName('...'), 'Asset');
  assert.equal(unityName('a/b\\c:d*e'), 'a-b-c-d-e');
});

test('unityModelExtension пропускает модели и объясняет отказ для остального', () => {
  for (const ext of ['.glb', '.GLTF', '.obj']) assert.equal(unityModelExtension(ext), ext.toLowerCase());
  assert.throws(() => unityModelExtension('.fbx'), /уходит модель/);
  assert.throws(() => unityModelExtension(''), /уходит модель/);
});

test('isTextureFile: png/jpg/tga да, модель нет', () => {
  assert.ok(isTextureFile('.PNG') && isTextureFile('.jpg') && isTextureFile('.tga'));
  assert.equal(isTextureFile('.glb'), false);
});

test('пакет: Assets/<Name>/Models, манифест, README с шагами импорта', async () => {
  const dir = await tempDir();
  const asset = await modelAsset('Knight of Death');
  const result = await buildUnityBundle({ assetId: asset.id, dir });

  assert.equal(result.output, 'Assets/Knight-of-Death');
  assert.equal(result.name, 'Knight-of-Death-unity.zip');
  assert.match(result.url, /^\/api\/exports\/[0-9a-f-]{36}$/);

  const zip = await JSZip.loadAsync(await fs.readFile(path.join(dir, result.id + '.zip')));
  const names = Object.keys(zip.files).filter((n) => !zip.files[n].dir);
  assert.deepEqual(names.sort(), [
    'Assets/Knight-of-Death/Models/Knight-of-Death.glb',
    'Assets/Knight-of-Death/README.txt',
    'Assets/Knight-of-Death/d3studio.bundle.json',
  ]);

  // модель внутри побайтово та же, что лежит в студии
  const packed = await zip.file('Assets/Knight-of-Death/Models/Knight-of-Death.glb').async('nodebuffer');
  assert.equal(packed.length, 512);
  assert.equal(packed.subarray(0, 4).toString('ascii'), 'glTF');

  const manifest = JSON.parse(await zip.file('Assets/Knight-of-Death/d3studio.bundle.json').async('string'));
  assert.equal(manifest.tool, 'd3studio');
  assert.equal(manifest.asset.id, asset.id);
  assert.equal(manifest.asset.extension, '.glb');
  assert.equal(manifest.unity.importer, 'com.unity.cloud.gltfast');
  assert.equal(manifest.files.find((f) => f.role === 'manifest').path, 'Assets/Knight-of-Death/d3studio.bundle.json');
  assert.equal(manifest.files[0].sha256.length, 64);

  const readme = await zip.file('Assets/Knight-of-Death/README.txt').async('string');
  assert.match(readme, /glTFast/);
  assert.match(readme, /Scale Factor = 1/);
  assert.match(readme, /Assets\/Knight-of-Death/);
  assert.ok(!/undefined|\[object Object\]/.test(readme), 'в README не должно быть дыр в подстановке');

  await fs.rm(dir, { recursive: true, force: true });
});

test('текстуры едут в Textures/ рядом с моделью', async () => {
  const dir = await tempDir();
  const asset = await modelAsset('Statue');
  const skin = await saveAsset(Buffer.from('PNG-fake'), '.png', 'Statue skin');
  const result = await buildUnityBundle({ assetId: asset.id, textures: [skin.id], dir });
  const zip = await JSZip.loadAsync(await fs.readFile(path.join(dir, result.id + '.zip')));
  assert.ok(zip.file('Assets/Statue/Textures/Statue-skin.png'), 'текстура должна лежать в Textures/');
  const readme = await zip.file('Assets/Statue/README.txt').async('string');
  assert.match(readme, /Statue-skin\.png/);
  await fs.rm(dir, { recursive: true, force: true });
});

test('не-модель и не-текстура — понятная ошибка, а не пустой пакет', async () => {
  const dir = await tempDir();
  const sheet = await saveAsset(Buffer.from('<svg/>'), '.svg', 'Sheet');
  await assert.rejects(() => buildUnityBundle({ assetId: sheet.id, dir }), (e) => e.status === 400 && /уходит модель/.test(e.message));

  const asset = await modelAsset('Mech');
  const bad = await saveAsset(Buffer.from('{}'), '.json', 'Meta');
  await assert.rejects(() => buildUnityBundle({ assetId: asset.id, textures: [bad.id], dir }), (e) => e.status === 400 && /Не текстура/.test(e.message));

  await assert.rejects(() => buildUnityBundle({ assetId: randomUUID(), dir }), (e) => e.status === 404);
  await assert.rejects(() => buildUnityBundle({ assetId: '../storage', dir }), (e) => e.status === 400);
  await fs.rm(dir, { recursive: true, force: true });
});

test('unityReadme без текстур не врёт про них', () => {
  const text = unityReadme({ name: 'Rock', files: ['Rock.obj'], asset: { id: 'x', name: 'Rock' } });
  assert.match(text, /Rock\.obj/);
  assert.match(text, /текстур в пакете нет/);
  assert.ok(!text.includes('undefined'));
});

test('проверка модели едет внутрь пакета: треугольники и замечания в манифесте и README', async () => {
  const dir = await tempDir();
  const asset = await saveAsset(buildGlb({ uvs: false }), '.glb', 'Shield');
  const result = await buildUnityBundle({ assetId: asset.id, dir });
  const zip = await JSZip.loadAsync(await fs.readFile(path.join(dir, result.id + '.zip')));
  const manifest = JSON.parse(await zip.file('Assets/Shield/d3studio.bundle.json').async('string'));
  assert.equal(manifest.mesh.checked, true);
  assert.equal(manifest.mesh.triangles, 12);
  assert.equal(manifest.mesh.vertices, 8);
  assert.equal(manifest.mesh.attributes.uv, false);
  assert.ok(manifest.mesh.findings.some((f) => f.code === 'no-uv'));
  const readme = await zip.file('Assets/Shield/README.txt').async('string');
  assert.match(readme, /12 треугольников/);
  assert.match(readme, /Нет UV/);
  await fs.rm(dir, { recursive: true, force: true });
});

test('модель с блокером не упаковывается, а объясняет причину', async () => {
  const dir = await tempDir();
  const asset = await saveAsset(buildGlb({ positions: [], indices: [] }), '.glb', 'Empty');
  await assert.rejects(
    () => buildUnityBundle({ assetId: asset.id, dir }),
    (error) => error.status === 400 && /не годится для движка/.test(error.message) && /ни одного треугольника/.test(error.message),
  );
  await fs.rm(dir, { recursive: true, force: true });
});
