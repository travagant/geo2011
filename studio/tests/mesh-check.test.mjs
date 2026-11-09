// Проверка модели перед импортом (server/mesh-check.mjs): что посчитано, что найдено,
// и как ведёт себя на битых файлах.
import test from 'node:test';
import assert from 'node:assert/strict';
import { checkMesh, readGlb, readAccessor, weldCount, unevenScales } from '../server/mesh-check.mjs';

/** Собрать настоящий GLB: JSON-чанк + BIN с позициями/нормалями/UV и индексами. */
function buildGlb({
  positions = cubePositions(),
  indices = cubeIndices(),
  uvs = true,
  normals = true,
  nodeScale,
  rig = false,
  clips = 0,
  materials = 1,
  textures = 1,
  mode,
  generator = 'test',
} = {}) {
  const bin = [];
  const views = [];
  const accessors = [];
  let offset = 0;
  const push = (data) => {
    const buffer = Buffer.from(data.buffer, data.byteOffset, data.byteLength);
    const pad = (4 - (offset % 4)) % 4;
    if (pad) { bin.push(Buffer.alloc(pad)); offset += pad; }
    views.push({ buffer: 0, byteOffset: offset, byteLength: buffer.length });
    bin.push(buffer);
    offset += buffer.length;
    return views.length - 1;
  };

  const positionView = push(Float32Array.from(positions));
  const min = [0, 1, 2].map((i) => Math.min(...positions.filter((_, k) => k % 3 === i)));
  const max = [0, 1, 2].map((i) => Math.max(...positions.filter((_, k) => k % 3 === i)));
  accessors.push({ bufferView: positionView, componentType: 5126, count: positions.length / 3, type: 'VEC3', min, max });
  const attributes = { POSITION: 0 };

  if (normals) {
    const data = new Float32Array(positions.length);
    for (let i = 0; i < data.length; i += 3) data[i + 1] = 1;
    accessors.push({ bufferView: push(data), componentType: 5126, count: positions.length / 3, type: 'VEC3' });
    attributes.NORMAL = accessors.length - 1;
  }
  if (uvs) {
    const data = new Float32Array((positions.length / 3) * 2);
    accessors.push({ bufferView: push(data), componentType: 5126, count: positions.length / 3, type: 'VEC2' });
    attributes.TEXCOORD_0 = accessors.length - 1;
  }
  if (rig) {
    const joints = new Uint8Array((positions.length / 3) * 4);
    const weights = new Float32Array((positions.length / 3) * 4);
    for (let i = 0; i < weights.length; i += 4) weights[i] = 1;
    accessors.push({ bufferView: push(joints), componentType: 5121, count: positions.length / 3, type: 'VEC4' });
    attributes.JOINTS_0 = accessors.length - 1;
    accessors.push({ bufferView: push(weights), componentType: 5126, count: positions.length / 3, type: 'VEC4' });
    attributes.WEIGHTS_0 = accessors.length - 1;
  }
  const indexData = positions.length / 3 > 65535 ? Uint32Array.from(indices) : Uint16Array.from(indices);
  accessors.push({ bufferView: push(indexData), componentType: indexData instanceof Uint32Array ? 5125 : 5123, count: indices.length, type: 'SCALAR' });

  const doc = {
    asset: { version: '2.0', generator },
    scene: 0,
    scenes: [{ nodes: [0] }],
    nodes: [{ name: 'Knight', mesh: 0, ...(nodeScale ? { scale: nodeScale } : {}) }],
    meshes: [{ primitives: [{ attributes, indices: accessors.length - 1, ...(mode === undefined ? {} : { mode }) }] }],
    accessors,
    bufferViews: views,
    buffers: [{ byteLength: offset }],
    materials: Array.from({ length: materials }, (_, i) => ({ name: `Mat${i}` })),
    textures: Array.from({ length: textures }, () => ({ source: 0 })),
    ...(rig ? { skins: [{ joints: [0], name: 'Armature' }] } : {}),
    ...(clips ? { animations: Array.from({ length: clips }, (_, i) => ({ name: `clip${i}`, channels: [], samplers: [] })) } : {}),
  };
  let json = Buffer.from(JSON.stringify(doc), 'utf8');
  json = Buffer.concat([json, Buffer.alloc((4 - (json.length % 4)) % 4, 0x20)]);
  let binBuffer = Buffer.concat(bin);
  binBuffer = Buffer.concat([binBuffer, Buffer.alloc((4 - (binBuffer.length % 4)) % 4)]);

  const jsonHeader = Buffer.alloc(8);
  jsonHeader.writeUInt32LE(json.length, 0);
  jsonHeader.writeUInt32LE(0x4e4f534a, 4);
  const binHeader = Buffer.alloc(8);
  binHeader.writeUInt32LE(binBuffer.length, 0);
  binHeader.writeUInt32LE(0x004e4942, 4);
  const total = 12 + 8 + json.length + 8 + binBuffer.length;
  const header = Buffer.alloc(12);
  header.write('glTF', 0, 'ascii');
  header.writeUInt32LE(2, 4);
  header.writeUInt32LE(total, 8);
  return Buffer.concat([header, jsonHeader, json, binHeader, binBuffer]);
}

function cubePositions() {
  return [
    -0.5, 0, -0.5, 0.5, 0, -0.5, 0.5, 1, -0.5, -0.5, 1, -0.5,
    -0.5, 0, 0.5, 0.5, 0, 0.5, 0.5, 1, 0.5, -0.5, 1, 0.5,
  ];
}
function cubeIndices() {
  return [
    0, 1, 2, 0, 2, 3, 4, 6, 5, 4, 7, 6, 0, 4, 5, 0, 5, 1,
    1, 5, 6, 1, 6, 2, 2, 6, 7, 2, 7, 3, 3, 7, 4, 3, 4, 0,
  ];
}

test('обычная модель: треугольники, вершины, материалы, UV, хеш', () => {
  const glb = buildGlb();
  const report = checkMesh(glb);
  assert.equal(report.triangles, 12);
  assert.equal(report.vertices, 8);
  assert.equal(report.materials, 1);
  assert.equal(report.textures, 1);
  assert.equal(report.attributes.uv, true);
  assert.equal(report.attributes.normal, true);
  assert.equal(report.sha256.length, 64);
  assert.equal(report.generator, 'test');
  assert.deepEqual(report.bounds, { min: [-0.5, 0, -0.5], max: [0.5, 1, 0.5] });
  assert.equal(report.ok, true);
  assert.ok(report.findings.some((f) => f.code === 'ok'), 'чистая модель должна получить «замечаний нет»');
});

test('вершины-дубликаты считаются и советуют сварку', () => {
  // 36 отдельных вершин вместо 8 с индексами — типичный результат «наивного» экспорта
  const positions = [];
  const indices = [];
  for (let t = 0; t < 12; t++) {
    for (let v = 0; v < 3; v++) {
      const src = cubeIndices()[t * 3 + v];
      positions.push(cubePositions()[src * 3], cubePositions()[src * 3 + 1], cubePositions()[src * 3 + 2]);
      indices.push(positions.length / 3 - 1);
    }
  }
  const report = checkMesh(buildGlb({ positions, indices }));
  assert.equal(report.vertices, 36);
  assert.equal(report.weld.unique, 8);
  assert.equal(report.weld.duplicates, 28);
  assert.ok(report.findings.some((f) => f.code === 'duplicates' && f.level === 'warn'));
});

test('нет UV и нормалей — предупреждения, а не блокер', () => {
  const report = checkMesh(buildGlb({ uvs: false, normals: false }));
  assert.equal(report.attributes.uv, false);
  const codes = report.findings.map((f) => f.code);
  assert.ok(codes.includes('no-uv'));
  assert.ok(codes.includes('no-normals'));
  assert.equal(report.ok, true);
});

test('неравномерный масштаб узла виден по имени', () => {
  const report = checkMesh(buildGlb({ nodeScale: [1, 2, 1] }));
  assert.equal(report.unevenScales.length, 1);
  assert.equal(report.unevenScales[0].name, 'Knight');
  assert.ok(report.findings.some((f) => f.code === 'uneven-scale' && /Knight/.test(f.text)));
  // равномерный масштаб — не находка
  assert.equal(checkMesh(buildGlb({ nodeScale: [2, 2, 2] })).unevenScales.length, 0);
});

test('риг и клипы попадают в отчёт', () => {
  const report = checkMesh(buildGlb({ rig: true, clips: 3 }));
  assert.equal(report.rigged, true);
  assert.equal(report.clips, 3);
  assert.equal(report.attributes.skin, true);
  const codes = report.findings.map((f) => f.code);
  assert.ok(codes.includes('rig') && codes.includes('clips'));
});

test('битые файлы объясняются текстом', () => {
  assert.throws(() => checkMesh(Buffer.alloc(4)), /не GLB/);
  const glb = buildGlb();
  assert.throws(() => checkMesh(glb.subarray(0, glb.length - 8)), /обрезан/);
  const lines = buildGlb({ mode: 1 });
  assert.throws(() => checkMesh(lines), /режим 1/);
  const broken = Buffer.from(glb);
  broken.writeUInt32LE(0xdeadbeef, 16); // испортить тип первого чанка
  assert.throws(() => checkMesh(broken), /JSON/);
});

test('readAccessor отдаёт значения аксессора', () => {
  const glb = buildGlb();
  const { doc, bin } = readGlb(glb);
  const positions = readAccessor(doc, bin, 0);
  assert.equal(positions.count, 8);
  assert.equal(positions.values[0], -0.5);
  assert.equal(positions.values[1], 0);
  assert.equal(positions.values[19], 1); // y вершины 6
});

test('readAccessor понимает byteStride (переплетённые вершины)', () => {
  // три вершины по 16 байт: xyz float + 4 байта мусора между ними
  const bin = Buffer.alloc(48);
  for (let i = 0; i < 3; i++) {
    bin.writeFloatLE(i, i * 16);
    bin.writeFloatLE(i + 10, i * 16 + 4);
    bin.writeFloatLE(0, i * 16 + 8);
    bin.writeUInt32LE(0xffffffff, i * 16 + 12);
  }
  const doc = {
    buffers: [{ byteLength: 48 }],
    bufferViews: [{ buffer: 0, byteOffset: 0, byteLength: 48, byteStride: 16 }],
    accessors: [{ bufferView: 0, componentType: 5126, count: 3, type: 'VEC3' }],
  };
  const read = readAccessor(doc, bin, 0);
  assert.deepEqual([...read.values], [0, 10, 0, 1, 11, 0, 2, 12, 0]);
});

test('weldCount и unevenScales работают сами по себе', () => {
  const values = new Float64Array([0, 0, 0, 0, 0, 0, 1, 0, 0]);
  assert.deepEqual(weldCount({ count: 3, values, width: 3 }), { unique: 2, duplicates: 1 });
  assert.equal(weldCount(null), null);
  const doc = { nodes: [{ name: 'a', scale: [1, 1, 1] }, { name: 'b', scale: [1, 3, 1] }, { name: 'c' }] };
  assert.deepEqual(unevenScales(doc).map((n) => n.name), ['b']);
});

test('пустая модель — блокер, а не «всё хорошо»', () => {
  const report = checkMesh(buildGlb({ positions: [0, 0, 0], indices: [0, 0, 0] }));
  assert.equal(report.triangles, 1);
  assert.equal(report.ok, true);
  const hollow = buildGlb({ positions: [], indices: [] });
  const empty = checkMesh(hollow);
  assert.equal(empty.triangles, 0);
  assert.equal(empty.ok, false);
  assert.ok(empty.findings.some((f) => f.code === 'empty' && f.level === 'blocker'));
});
