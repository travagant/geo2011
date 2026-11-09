// Нативная часть (native/d3mesh, Rust) и её JS-двойник.
// Паритет проверяется автоматически, когда бинарник собран: npm run native:build.
import test from 'node:test';
import assert from 'node:assert/strict';
import { promises as fs } from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { verifyGlbBuffer, nativeVerify, hasNativeBinary, nativeBinaryPath } from '../server/native.mjs';
import { buildGlb } from './helpers/glb.mjs';

test('JS-проверка GLB: годный файл, размеры чанков и crc32', () => {
  const glb = buildGlb();
  const report = verifyGlbBuffer(glb);
  assert.equal(report.ok, true);
  assert.equal(report.bytes, glb.length);
  assert.equal(report.version, 2);
  assert.ok(report.json_chunk > 0);
  assert.ok(report.bin_chunk > 0);
  assert.match(report.crc32, /^0x[0-9a-f]{8}$/);
});

test('JS-проверка GLB: битые файлы объясняются', () => {
  assert.deepEqual(verifyGlbBuffer(Buffer.alloc(4)).ok, false);
  assert.match(verifyGlbBuffer(Buffer.alloc(4)).error, /не GLB/);
  const glb = buildGlb();
  assert.match(verifyGlbBuffer(glb.subarray(0, glb.length - 4)).error, /обрезан/);
  const wrong = Buffer.from(glb);
  wrong.writeUInt32LE(1, 4);
  assert.match(verifyGlbBuffer(wrong).error, /версии 1/);
});

test('Rust-бинарник даёт тот же ответ, что JS (пропускается, пока не собран)', async (t) => {
  if (!hasNativeBinary()) {
    t.skip(`нативный бинарник не собран (${nativeBinaryPath()}); сборка: npm run native:build`);
    return;
  }
  const dir = await fs.mkdtemp(path.join(os.tmpdir(), 'd3mesh-'));
  const file = path.join(dir, 'cube.glb');
  const glb = buildGlb();
  await fs.writeFile(file, glb);
  const native = nativeVerify(file);
  const js = verifyGlbBuffer(glb);
  assert.deepEqual(native, js);
  await fs.rm(dir, { recursive: true, force: true });
});
