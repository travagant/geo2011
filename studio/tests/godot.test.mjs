// Godot-пакет (server/godot.mjs + общий сборщик server/engine-bundle.mjs): раскладка папок,
// инструкция под Godot, отчёт проверки модели и отказы.
import test from 'node:test';
import assert from 'node:assert/strict';
import { promises as fs } from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import JSZip from 'jszip';
import { buildGodotBundle, godotReadme } from '../server/godot.mjs';
import { buildEngineBundle, engineProfile, ENGINES, bundleName } from '../server/engine-bundle.mjs';
import { saveAsset } from '../server/storage.mjs';
import { buildGlb } from './helpers/glb.mjs';

async function tempDir() {
  return fs.mkdtemp(path.join(os.tmpdir(), 'godot-bundle-'));
}

test('пакет Godot: папка Godot/<Имя>/models, текстуры, README и манифест', async () => {
  const dir = await tempDir();
  const model = await saveAsset(buildGlb({ clips: 2 }), '.glb', 'Боец с копьём');
  const skin = await saveAsset(Buffer.from('PNGDATA'), '.png', 'Боец кожа');
  const result = await buildGodotBundle({ assetId: model.id, textures: [skin.id], dir });

  assert.equal(result.engine, 'godot');
  assert.equal(result.name, 'Boec-s-kopem-godot.zip');
  assert.equal(result.output, 'Godot/Boec-s-kopem');

  const zip = await JSZip.loadAsync(await fs.readFile(path.join(dir, result.id + '.zip')));
  const names = Object.keys(zip.files).filter((n) => !zip.files[n].dir).sort();
  assert.deepEqual(names, [
    'Godot/Boec-s-kopem/README.txt',
    'Godot/Boec-s-kopem/d3studio.bundle.json',
    'Godot/Boec-s-kopem/models/Boec-s-kopem.glb',
    'Godot/Boec-s-kopem/textures/Boec-kozha.png',
  ]);

  const manifest = JSON.parse(await zip.file('Godot/Boec-s-kopem/d3studio.bundle.json').async('string'));
  assert.equal(manifest.engine, 'godot');
  assert.equal(manifest.godot.min_version, '4.3');
  assert.equal(manifest.godot.importer, 'built-in glTF importer');
  assert.equal(manifest.mesh.checked, true);
  assert.equal(manifest.mesh.clips, 2);
  assert.equal(manifest.files.find((f) => f.role === 'manifest').path, 'Godot/Boec-s-kopem/d3studio.bundle.json');

  const readme = await zip.file('Godot/Boec-s-kopem/README.txt').async('string');
  assert.match(readme, /пакет d3studio для Godot/);
  assert.match(readme, /Godot 4\.3 и новее/);
  assert.match(readme, /AnimationPlayer/);
  assert.match(readme, /CollisionShape3D/);
  assert.match(readme, /1 юнит Godot = 1 метр/);
  assert.ok(!/glTFast/.test(readme), 'инструкция Godot не должна отправлять в Unity-импортёр');
  await fs.rm(dir, { recursive: true, force: true });
});

test('инструкция Unity в Godot-пакет не попадает и наоборот', () => {
  const unity = ENGINES.unity;
  const godot = ENGINES.godot;
  assert.ok(unity.steps.some((s) => String(s).includes('glTFast')));
  assert.ok(!godot.steps.some((s) => String(s).includes('glTFast')));
  assert.equal(godot.root, 'Godot');
  assert.equal(unity.root, 'Assets');
  const text = godotReadme({ name: 'Rock', files: ['Rock.obj'], asset: { id: 'x', name: 'Rock' } });
  assert.match(text, /Коллизию Godot не создаёт/);
});

test('проверка модели едет в Godot-пакет и блокер останавливает сборку', async () => {
  const dir = await tempDir();
  const noUv = await saveAsset(buildGlb({ uvs: false }), '.glb', 'Sword');
  const result = await buildGodotBundle({ assetId: noUv.id, dir });
  const zip = await JSZip.loadAsync(await fs.readFile(path.join(dir, result.id + '.zip')));
  const manifest = JSON.parse(await zip.file('Godot/Sword/d3studio.bundle.json').async('string'));
  assert.ok(manifest.mesh.findings.some((f) => f.code === 'no-uv'));
  assert.equal(result.mesh.checked, true);

  const empty = await saveAsset(buildGlb({ positions: [], indices: [] }), '.glb', 'Empty');
  await assert.rejects(
    () => buildGodotBundle({ assetId: empty.id, dir }),
    (e) => e.status === 400 && /не годится для движка/.test(e.message),
  );
  await fs.rm(dir, { recursive: true, force: true });
});

test('неизвестный движок — понятный отказ, а имя ассета общее для обоих', () => {
  assert.throws(() => engineProfile('unreal'), (e) => e.status === 400 && /Неизвестный движок/.test(e.message));
  assert.equal(bundleName('Ёлка'), 'Elka');
  assert.throws(() => engineProfile(''), /Неизвестный движок/);
  return assert.rejects(() => buildEngineBundle({ engine: 'bevy', assetId: '00000000-0000-4000-8000-000000000000' }), /Неизвестный движок/);
});
