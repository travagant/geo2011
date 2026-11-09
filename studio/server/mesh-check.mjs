// Проверка модели перед импортом в движок: что реально лежит в GLB/glTF и что из этого
// станет проблемой в Unity или Godot. Считаем сами, по файлу, а не по словам генератора.
//
// Зачем: движок ругается не на «плохую» модель, а на конкретные вещи — нет UV, неравномерный
// масштаб узла, ненормализованные нормали, миллион дубликатов вершин. Всё это видно до импорта.
import { createHash } from 'node:crypto';

const MAGIC = 0x46546c67; // 'glTF'
const CHUNK_JSON = 0x4e4f534a;
const CHUNK_BIN = 0x004e4942;

/** Прочитать контейнер GLB: заголовок, JSON-чанк, BIN-чанк. Ошибки — текстом. */
export function readGlb(buffer) {
  if (!Buffer.isBuffer(buffer) || buffer.length < 20) throw Object.assign(new Error('Файл не GLB: меньше 20 байт'), { status: 400 });
  if (buffer.readUInt32LE(0) !== MAGIC || buffer.toString('ascii', 0, 4) !== 'glTF') {
    throw Object.assign(new Error('Файл не GLB: нет подписи glTF'), { status: 400 });
  }
  const version = buffer.readUInt32LE(4);
  if (version !== 2) throw Object.assign(new Error(`GLB версии ${version}: поддерживается только 2`), { status: 400 });
  const declared = buffer.readUInt32LE(8);
  if (declared !== buffer.length) {
    throw Object.assign(new Error(`GLB обрезан: в заголовке ${declared} байт, в файле ${buffer.length}`), { status: 400 });
  }
  let offset = 12, json = null, bin = null;
  while (offset + 8 <= buffer.length) {
    const length = buffer.readUInt32LE(offset);
    const type = buffer.readUInt32LE(offset + 4);
    const start = offset + 8;
    if (start + length > buffer.length) throw Object.assign(new Error('GLB: чанк выходит за пределы файла'), { status: 400 });
    const chunk = buffer.subarray(start, start + length);
    if (type === CHUNK_JSON && !json) json = chunk;
    else if (type === CHUNK_BIN && !bin) bin = chunk;
    offset = start + length + ((4 - (length % 4)) % 4);
  }
  if (!json) throw Object.assign(new Error('GLB: нет JSON-чанка'), { status: 400 });
  let doc;
  try {
    doc = JSON.parse(json.toString('utf8').replace(/\u0000+$/g, '').trim());
  } catch {
    throw Object.assign(new Error('GLB: JSON внутри файла не читается'), { status: 400 });
  }
  if (!doc || typeof doc !== 'object' || !doc.asset) throw Object.assign(new Error('GLB: нет обязательного поля asset'), { status: 400 });
  return { doc, bin, sha256: createHash('sha256').update(buffer).digest('hex') };
}

const COMPONENT = {
  5120: { bytes: 1, read: (b, o) => b.readInt8(o) },
  5121: { bytes: 1, read: (b, o) => b.readUInt8(o) },
  5122: { bytes: 2, read: (b, o) => b.readInt16LE(o) },
  5123: { bytes: 2, read: (b, o) => b.readUInt16LE(o) },
  5125: { bytes: 4, read: (b, o) => b.readUInt32LE(o) },
  5126: { bytes: 4, read: (b, o) => b.readFloatLE(o) },
};
const TYPES = { SCALAR: 1, VEC2: 2, VEC3: 3, VEC4: 4, MAT2: 4, MAT3: 9, MAT4: 16 };

/** Значения аксессора (только то, что нам нужно: VEC2/VEC3 float и индексы). */
export function readAccessor(doc, bin, index) {
  const accessor = doc.accessors?.[index];
  if (!accessor || !Number.isSafeInteger(accessor.count)) return null;
  if (accessor.bufferView === undefined) return { count: accessor.count, values: null, componentType: accessor.componentType };
  const view = doc.bufferViews?.[accessor.bufferView];
  if (!view || !bin) return { count: accessor.count, values: null, componentType: accessor.componentType };
  const component = COMPONENT[accessor.componentType];
  const width = TYPES[accessor.type];
  if (!component || !width) return { count: accessor.count, values: null, componentType: accessor.componentType };
  const stride = view.byteStride || component.bytes * width;
  const base = (view.byteOffset || 0) + (accessor.byteOffset || 0);
  const values = new Float64Array(accessor.count * width);
  for (let i = 0; i < accessor.count; i++) {
    const at = base + i * stride;
    if (at + component.bytes * width > bin.length) break;
    for (let c = 0; c < width; c++) values[i * width + c] = component.read(bin, at + c * component.bytes);
  }
  return { count: accessor.count, values, componentType: accessor.componentType, stride, width };
}

/** Уникальные вершины по позиции с точностью до 0.1 мм — сколько из них дубликаты. */
export function weldCount(accessor, tolerance = 1e-4) {
  if (!accessor?.values || accessor.width < 3) return null;
  const seen = new Set();
  const step = Math.max(tolerance, 1e-9);
  for (let i = 0; i < accessor.count; i++) {
    const x = Math.round(accessor.values[i * 3] / step);
    const y = Math.round(accessor.values[i * 3 + 1] / step);
    const z = Math.round(accessor.values[i * 3 + 2] / step);
    seen.add(`${x},${y},${z}`);
  }
  return { unique: seen.size, duplicates: accessor.count - seen.size };
}

/** Неравномерный масштаб узлов: в Unity после импорта ломает нормали и коллайдеры. */
export function unevenScales(doc) {
  const bad = [];
  for (const [i, node] of (doc.nodes || []).entries()) {
    if (!Array.isArray(node.scale) || node.scale.length !== 3) continue;
    const [x, y, z] = node.scale.map((v) => Math.abs(Number(v) || 0));
    const max = Math.max(x, y, z), min = Math.min(x, y, z);
    if (max > 0 && (max - min) / max > 0.01) bad.push({ node: i, name: node.name || `узел ${i}`, scale: node.scale });
  }
  return bad;
}

function findingsOf(report) {
  const out = [];
  const add = (level, code, text) => out.push({ level, code, text });
  if (!report.triangles) add('blocker', 'empty', 'В модели нет ни одного треугольника — импортировать нечего.');
  if (report.triangles > 2500000) add('blocker', 'too-heavy', `Треугольников ${report.triangles}: больше 2,5 млн, движок и видеокарта это не переварят.`);
  if (report.triangles > 100000) add('warn', 'heavy', `Треугольников ${report.triangles}: до 100 тыс. обычно лучше — это LOD-задача, а не блокер.`);
  if (!report.attributes.uv) add('warn', 'no-uv', 'Нет UV: текстура на модель не ляжет, в Unity материал будет пустой.');
  if (!report.attributes.normal) add('warn', 'no-normals', 'Нет нормалей: движок посчитает их сам, но на низкополигональной модели это видно швами.');
  if (report.weld && report.weld.duplicates > 0) {
    const ratio = report.weld.duplicates / Math.max(1, report.vertices);
    if (ratio > 0.1) add('warn', 'duplicates', `Дубликатов вершин ${report.weld.duplicates} (${Math.round(ratio * 100)}%): перед импортом стоит сварить — Unity и Godot сэкономят память.`);
    else add('info', 'duplicates', `Дубликатов вершин ${report.weld.duplicates} — немного, можно не трогать.`);
  }
  for (const node of report.unevenScales) {
    add('warn', 'uneven-scale', `Узел «${node.name}»: масштаб ${node.scale.map((v) => Number(v).toFixed(2)).join(' × ')} — неравномерный, в движке это ломает нормали и коллайдер.`);
  }
  if (report.rigged) add('info', 'rig', 'Есть скелет и веса — модель анимируется.');
  if (report.clips) add('info', 'clips', `Клипов анимации: ${report.clips}.`);
  if (!report.materials) add('info', 'no-material', 'Материалов нет: движок создаст свой по умолчанию.');
  if (!out.length) add('info', 'ok', 'Проверка пройдена: замечаний нет.');
  return out;
}

/**
 * Отчёт по модели.
 * @param {Buffer} buffer содержимое .glb
 * @returns {{sha256:string,triangles:number,vertices:number,uniqueVertices:number|null,weld:object|null,meshes:number,primitives:number,materials:number,textures:number,attributes:object,rigged:boolean,clips:number,unevenScales:object[],bounds:object|null,findings:object[],ok:boolean}}
 */
export function checkMesh(buffer) {
  const { doc, bin, sha256 } = readGlb(buffer);
  let triangles = 0, vertices = 0, primitives = 0, meshes = 0;
  let positionAccessor = null;
  const attributes = { uv: false, normal: false, tangent: false, color: false, skin: false };
  for (const mesh of doc.meshes || []) {
    meshes++;
    for (const primitive of mesh.primitives || []) {
      primitives++;
      const mode = primitive.mode ?? 4;
      if (mode !== 4) {
        throw Object.assign(new Error(`Примитив ${primitives}: режим ${mode}, а нужны треугольники (4)`), { status: 400 });
      }
      const attributesIndex = primitive.attributes || {};
      const position = doc.accessors?.[attributesIndex.POSITION];
      const indices = primitive.indices === undefined ? position : doc.accessors?.[primitive.indices];
      if (!Number.isSafeInteger(position?.count) || !Number.isSafeInteger(indices?.count)) {
        throw Object.assign(new Error('Примитив без аксессора POSITION или индексов'), { status: 400 });
      }
      if (indices.count % 3) throw Object.assign(new Error('Число индексов не кратно трём — это не треугольники'), { status: 400 });
      vertices += position.count;
      triangles += indices.count / 3;
      if (!positionAccessor && position.count > 0) positionAccessor = attributesIndex.POSITION;
      if (attributesIndex.TEXCOORD_0 !== undefined) attributes.uv = true;
      if (attributesIndex.NORMAL !== undefined) attributes.normal = true;
      if (attributesIndex.TANGENT !== undefined) attributes.tangent = true;
      if (attributesIndex.COLOR_0 !== undefined) attributes.color = true;
      if (attributesIndex.JOINTS_0 !== undefined && attributesIndex.WEIGHTS_0 !== undefined) attributes.skin = true;
    }
  }
  const positionValues = positionAccessor === null ? null : readAccessor(doc, bin, positionAccessor);
  const weld = positionValues && positionValues.count <= 400000 ? weldCount(positionValues) : null;
  const accessor = positionAccessor === null ? null : doc.accessors?.[positionAccessor];
  const bounds = accessor?.min && accessor?.max && accessor.min.length >= 3 && accessor.max.length >= 3
    ? { min: accessor.min.slice(0, 3).map(Number), max: accessor.max.slice(0, 3).map(Number) }
    : null;
  const report = {
    sha256,
    bytes: buffer.length,
    triangles,
    vertices,
    uniqueVertices: weld?.unique ?? null,
    weld,
    meshes,
    primitives,
    materials: (doc.materials || []).length,
    textures: (doc.textures || []).length,
    nodes: (doc.nodes || []).length,
    attributes,
    rigged: (doc.skins || []).length > 0,
    clips: (doc.animations || []).length,
    unevenScales: unevenScales(doc),
    bounds,
    generator: String(doc.asset?.generator || ''),
  };
  report.findings = findingsOf(report);
  report.ok = !report.findings.some((f) => f.level === 'blocker');
  return report;
}
