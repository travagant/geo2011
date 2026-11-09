// Мост в Disciples 3 (server/experimental/d3tool.mjs) — отключён от студии, но не потерян: запуск `python -m d3tool export`, список файлов,
// понятные ошибки и защита от выхода за папку экспорта. Поддельный пакет `d3tool` пишется
// во временную папку — настоящий репозиторий и настоящие юниты для этого не нужны.
import test from 'node:test';
import assert from 'node:assert/strict';
import { promises as fs } from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { randomUUID } from 'node:crypto';

import { runD3Export, listFiles, insideDir, safeName, isModelFile, exportAsset, exportMeta } from '../server/experimental/d3tool.mjs';
import { data } from '../server/storage.mjs';

const PY = process.env.PYTHON || (process.platform === 'win32' ? 'python' : 'python3');

/** Временный «пакет d3tool»: пишет те же файлы, что пишет настоящий экспорт. */
async function stubRepo({ files = ['unit.g', 'unit.scene', 'unit.ac', 'unit_lod.g', 'sub/deep.g'], code = 0, stderr = '' } = {}) {
  const repo = await fs.mkdtemp(path.join(os.tmpdir(), 'd3tool-stub-'));
  await fs.mkdir(path.join(repo, 'd3tool'), { recursive: true });
  await fs.writeFile(path.join(repo, 'd3tool', '__init__.py'), '', 'utf8');
  await fs.writeFile(path.join(repo, 'd3tool', '__main__.py'), [
    'import os, sys',
    'args = sys.argv[1:]',
    'out = args[args.index("-o") + 1]',
    `code = ${code}`,
    `stderr = ${JSON.stringify(stderr)}`,
    'if stderr:',
    '    sys.stderr.write(stderr)',
    `for name in ${JSON.stringify(files)}:`,
    '    full = os.path.join(out, name)',
    '    os.makedirs(os.path.dirname(full) or out, exist_ok=True)',
    '    with open(full, "w", encoding="utf-8") as handle:',
    '        handle.write("x")',
    'sys.stdout.write("reverse export ok: " + str(len(args)) + " args")',
    'sys.exit(code)',
  ].join('\n') + '\n', 'utf8');
  return repo;
}

test('имена и расширения: в Disciples 3 идёт только glTF/GLB, имя чистится', () => {
  assert.equal(isModelFile('knight.glb'), true);
  assert.equal(isModelFile('knight.GLTF'), true);
  assert.equal(isModelFile('concept.png'), false);
  assert.equal(safeName('knight: "sword"/1?.glb'), 'knight_ _sword__1_.glb');
  assert.equal(safeName('   '), 'unit');
  assert.equal(safeName(''), 'unit');
});

test('insideDir не выпускает за папку экспорта', () => {
  const dir = path.join(os.tmpdir(), 'd3-export-guard');
  assert.equal(insideDir(dir, 'unit.g'), path.join(dir, 'unit.g'));
  assert.equal(insideDir(dir, 'sub/deep.g'), path.join(dir, 'sub', 'deep.g'));
  assert.throws(() => insideDir(dir, '../secret.txt'), /вне папки/);
  assert.throws(() => insideDir(dir, '/etc/passwd'), /вне папки/);
});

test('listFiles обходит подпапки и отдаёт пути с прямыми слэшами', async () => {
  const dir = await fs.mkdtemp(path.join(os.tmpdir(), 'd3-list-'));
  try {
    await fs.mkdir(path.join(dir, 'lod'), { recursive: true });
    await fs.writeFile(path.join(dir, 'unit.g'), 'x');
    await fs.writeFile(path.join(dir, 'lod', 'unit_lod.g'), 'x');
    assert.deepEqual(await listFiles(dir), ['lod/unit_lod.g', 'unit.g']);
  } finally {
    await fs.rm(dir, { recursive: true, force: true });
  }
});

test('runD3Export зовёт python -m d3tool export и возвращает записанные файлы', async () => {
  const repo = await stubRepo();
  const out = await fs.mkdtemp(path.join(os.tmpdir(), 'd3-out-'));
  try {
    const gltf = path.join(out, 'knight.glb');
    await fs.writeFile(gltf, 'glTF');
    const result = await runD3Export(gltf, path.join(out, 'export'), { python: PY, repo });
    assert.deepEqual(result.files, ['sub/deep.g', 'unit.ac', 'unit.g', 'unit.scene', 'unit_lod.g']);
    assert.match(result.output, /reverse export ok/);
    assert.equal(result.code, 0);
    assert.ok((await fs.stat(path.join(out, 'export', 'unit.g'))).isFile());
  } finally {
    await fs.rm(repo, { recursive: true, force: true });
    await fs.rm(out, { recursive: true, force: true });
  }
});

test('ненулевой код d3tool — ошибка с хвостом вывода, а не «пустой экспорт»', async () => {
  const repo = await stubRepo({ code: 2, stderr: 'cannot read gltf: missing buffer' });
  const out = await fs.mkdtemp(path.join(os.tmpdir(), 'd3-out-'));
  try {
    const gltf = path.join(out, 'broken.glb');
    await fs.writeFile(gltf, 'nope');
    await assert.rejects(
      () => runD3Export(gltf, path.join(out, 'export'), { python: PY, repo }),
      (error) => /кодом 2/.test(error.message) && /cannot read gltf/.test(error.message),
    );
  } finally {
    await fs.rm(repo, { recursive: true, force: true });
    await fs.rm(out, { recursive: true, force: true });
  }
});

test('нет Python — говорят, что делать, а не падают молча', async () => {
  const out = await fs.mkdtemp(path.join(os.tmpdir(), 'd3-out-'));
  try {
    await assert.rejects(
      () => runD3Export(path.join(out, 'x.glb'), path.join(out, 'e'), { python: 'python-that-does-not-exist-42' }),
      (error) => /Python/.test(error.message) && /D3TOOL_PYTHON/.test(error.message),
    );
  } finally {
    await fs.rm(out, { recursive: true, force: true });
  }
});

test('экспорт без файлов — ошибка, а не пустая папка', async () => {
  const repo = await stubRepo({ files: [] });
  const out = await fs.mkdtemp(path.join(os.tmpdir(), 'd3-out-'));
  try {
    await assert.rejects(
      () => runD3Export(path.join(out, 'x.glb'), path.join(out, 'e'), { python: PY, repo }),
      /не записал ни одного файла/,
    );
  } finally {
    await fs.rm(repo, { recursive: true, force: true });
    await fs.rm(out, { recursive: true, force: true });
  }
});

test('exportAsset: ассет студии → папка data/d3exports/<id>, метаданные читаются обратно', async () => {
  const repo = await stubRepo();
  const id = randomUUID();
  const glb = path.join(data, 'assets', id + '.glb');
  await fs.mkdir(path.dirname(glb), { recursive: true });
  await fs.writeFile(glb, 'glTF');
  try {
    const meta = await exportAsset({ id, name: 'knight.glb', extension: '.glb' }, { python: PY, repo });
    assert.equal(meta.assetId, id);
    assert.ok(meta.files.includes('unit.g'));
    assert.match(meta.dir, /d3exports/);
    const again = await exportMeta(meta.id);
    assert.deepEqual(again.files, meta.files);
    await assert.rejects(() => exportAsset({ id, name: 'concept.png', extension: '.png' }), /только \.glb или \.gltf/);
    await assert.rejects(() => exportMeta('../../etc/passwd'), /Неверный номер/);
    await assert.rejects(() => exportMeta(randomUUID()), /не найден/);
    await fs.rm(path.join(meta.dir, '..'), { recursive: true, force: true });
    await fs.rm(meta.dir + '.json', { force: true }).catch(() => {});
    await fs.rm(path.join(data, 'd3exports', meta.id + '.json'), { force: true });
  } finally {
    await fs.rm(repo, { recursive: true, force: true });
    await fs.rm(glb, { force: true });
  }
});
