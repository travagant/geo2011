// Пакет ассета для движка: один и тот же меш уезжает в Unity или Godot одной папкой
// с манифестом и инструкцией по импорту. Различия между движками — только раскладка
// папок и текст инструкции, поэтому общий код один, а профили — данные.
import JSZip from 'jszip';
import { createHash, randomUUID } from 'node:crypto';
import { promises as fs } from 'node:fs';
import path from 'node:path';
import { data, assetInfo, writeJson } from './storage.mjs';
import { resolveModelSource, MODEL_EXTENSIONS } from './model-source.mjs';
import { checkMesh } from './mesh-check.mjs';

/** Кириллица в латиницу: путь ассета должен переживать Windows, macOS и сборку под телефон. */
const TRANSLIT = {
  а: 'a', б: 'b', в: 'v', г: 'g', д: 'd', е: 'e', ё: 'e', ж: 'zh', з: 'z', и: 'i', й: 'y',
  к: 'k', л: 'l', м: 'm', н: 'n', о: 'o', п: 'p', р: 'r', с: 's', т: 't', у: 'u', ф: 'f',
  х: 'h', ц: 'c', ч: 'ch', ш: 'sh', щ: 'shch', ъ: '', ы: 'y', ь: '', э: 'e', ю: 'yu', я: 'ya',
  і: 'i', ї: 'yi', є: 'ye', ґ: 'g',
};

/** Имя ассета как имя папки в проекте движка: латиница/цифры/дефис. */
export function bundleName(name, fallback = 'Asset') {
  const base = String(name ?? '')
    .replace(/\.(glb|gltf|obj|zip)$/i, '')
    .replace(/[\u0400-\u04FF]/g, (ch) => {
      const low = ch.toLowerCase();
      const latin = TRANSLIT[low];
      if (latin === undefined) return '';
      return ch === low ? latin : latin.charAt(0).toUpperCase() + latin.slice(1);
    })
    .replace(/[^\w.-]+/g, '-')
    .replace(/-{2,}/g, '-')
    .replace(/^[-.]+|-+$/g, '')
    .slice(0, 60);
  return base || fallback;
}

export function isTextureFile(extension) {
  return ['.png', '.jpg', '.jpeg', '.tga', '.exr'].includes(String(extension).toLowerCase());
}

export function modelExtension(extension) {
  const ext = String(extension || '').toLowerCase();
  if (!MODEL_EXTENSIONS.includes(ext)) {
    throw Object.assign(
      new Error(`В движок уходит модель (${MODEL_EXTENSIONS.join(', ')}), а не ${ext || 'файл без расширения'}`),
      { status: 400 },
    );
  }
  return ext;
}

/** Профили движков: раскладка папок, расширение архива и то, что попадёт в манифест. */
export const ENGINES = {
  unity: {
    id: 'unity',
    title: 'Unity',
    root: 'Assets',
    models: 'Models',
    textures: 'Textures',
    suffix: '-unity.zip',
    meta: { min_version: '2022.3', importer: 'com.unity.cloud.gltfast', scale: '1 unit = 1 meter', forward: '-Z' },
    steps: [
      'Package Manager → + → Add package by name → com.unity.cloud.gltfast (glTFast).',
      'Без него .glb в проект не попадёт; .obj Unity импортирует сама.',
      (name) => `Распаковать папку ${name} в Assets/ своего проекта — путь станет Assets/${name}/.`,
      'Выделить модель в Project, в Inspector выставить Scale Factor = 1: студия считает в метрах, 1 юнит Unity = 1 метр.',
      'Материалы: glTFast создаёт их как Lit. Для URP/HDRP нажать Edit → Rendering → Materials → Convert Selected… или назначить свой шейдер.',
      'Текстуры из Textures/ перетащить в слоты материала, если glTFast не подхватил их из .glb.',
      'Проверить: модель стоит на нуле, высота совпадает с манифестом, лицо смотрит в -Z (вперёд в Unity).',
    ],
  },
  godot: {
    id: 'godot',
    title: 'Godot',
    root: 'Godot',
    models: 'models',
    textures: 'textures',
    suffix: '-godot.zip',
    meta: { min_version: '4.3', importer: 'built-in glTF importer', scale: '1 unit = 1 meter', forward: '-Z' },
    steps: [
      'Скопировать папку внутрь проекта Godot (в дереве FileSystem появится res://<папка>).',
      '.glb Godot импортирует сама: двойной щёлчок по файлу открывает сцену, её можно тащить в свою сцену как наследника.',
      'Масштаб оставить 1.0: модель в метрах, 1 юнит Godot = 1 метр.',
      'Материалы станут StandardMaterial3D; для мобильного рендера проверить, что шейдер совместим.',
      'Анимации лежат внутри импортированной сцены (AnimationPlayer + AnimationLibrary): выбрать клип по умолчанию.',
      'Коллизию Godot не создаёт: добавить StaticBody3D + CollisionShape3D вручную (или ConvexPolygonShape3D по мешу).',
      'Проверить: модель стоит на нуле, высота совпадает с манифестом, лицо смотрит в -Z (вперёд в Godot).',
    ],
  },
};

export function engineProfile(engine) {
  const profile = ENGINES[String(engine || '').toLowerCase()];
  if (!profile) throw Object.assign(new Error(`Неизвестный движок: ${engine}. Есть: ${Object.keys(ENGINES).join(', ')}`), { status: 400 });
  return profile;
}

async function hashFile(file) {
  const buffer = await fs.readFile(file);
  return { sha256: createHash('sha256').update(buffer).digest('hex'), bytes: buffer.length, buffer };
}

/** Текст инструкции, который едет внутрь пакета (его читают в движке, не в студии). */
export function engineReadme({ engine, name, files, asset, mesh }) {
  const profile = engineProfile(engine);
  const models = files.filter((f) => MODEL_EXTENSIONS.includes(path.extname(f)));
  const textures = files.filter((f) => isTextureFile(path.extname(f)));
  return [
    `${name} — пакет d3studio для ${profile.title}`,
    '',
    'Что внутри:',
    `  ${profile.models}/     ${models.join(', ') || '—'}`,
    `  ${profile.textures}/   ${textures.join(', ') || '— (текстур в пакете нет)'}`,
    '  d3studio.bundle.json   манифест: откуда модель, размеры, контрольные суммы',
    '',
    ...(mesh?.checked ? [
      `Проверка модели: ${mesh.triangles} треугольников, ${mesh.vertices} вершин`
        + (mesh.materials !== undefined ? `, материалов ${mesh.materials}` : '')
        + (mesh.rigged ? ', со скелетом' : '')
        + (mesh.clips ? `, клипов ${mesh.clips}` : ''),
      ...mesh.findings.filter((f) => f.level !== 'info').map((f) => `  ! ${f.text}`),
      ...(mesh.findings.some((f) => f.level !== 'info') ? [] : ['  замечаний нет']),
      '',
    ] : [`Проверка модели: не выполнялась (${mesh?.reason || 'файл не GLB'}).`, '']),
    `Как импортировать (${profile.title} ${profile.meta.min_version} и новее):`,
    ...profile.steps.map((step, i) => `  ${i + 1}. ${typeof step === 'function' ? step(name) : step}`),
    '',
    `Источник: ассет ${asset.id} (${asset.name}), собрано d3studio.`,
    'Файлы не пересобирались — это та же геометрия, что показывает студия.',
    '',
  ].join('\n');
}

/**
 * Собрать пакет для движка.
 * @param {{engine?: string, assetId: string, textures?: string[], name?: string, dir?: string}} options
 */
export async function buildEngineBundle({ engine = 'unity', assetId, textures = [], name, dir } = {}) {
  const profile = engineProfile(engine);
  const source = await resolveModelSource(assetId);
  const extension = modelExtension(source.extension);
  const asset = { id: assetId, name: source.name, extension };
  const folder = bundleName(name || source.name || assetId);
  const outDir = dir || path.join(data, 'exports');

  const entries = [{ abs: source.file, rel: `${profile.root}/${folder}/${profile.models}/${folder}${extension}` }];
  for (const textureId of textures) {
    if (!textureId || textureId === assetId) continue;
    const texture = await assetInfo(textureId);
    if (!isTextureFile(texture.extension)) {
      throw Object.assign(new Error(`Не текстура: ${texture.extension || 'без расширения'}`), { status: 400 });
    }
    entries.push({
      abs: path.join(data, 'assets', texture.id + texture.extension.toLowerCase()),
      rel: `${profile.root}/${folder}/${profile.textures}/${bundleName(texture.name || texture.id)}${texture.extension.toLowerCase()}`,
    });
  }

  const modelBuffer = await fs.readFile(source.file);
  let mesh = { checked: false, reason: 'не GLB' };
  if (extension !== '.obj') {
    let report = null;
    try {
      report = checkMesh(modelBuffer);
    } catch (error) {
      mesh = { checked: false, reason: error.message };
    }
    if (report) {
      if (!report.ok) {
        const blockers = report.findings.filter((f) => f.level === 'blocker').map((f) => f.text).join(' ');
        throw Object.assign(new Error(`Модель не годится для движка: ${blockers}`), { status: 400 });
      }
      mesh = {
        checked: true,
        triangles: report.triangles,
        vertices: report.vertices,
        uniqueVertices: report.uniqueVertices,
        materials: report.materials,
        textures: report.textures,
        attributes: report.attributes,
        rigged: report.rigged,
        clips: report.clips,
        bounds: report.bounds,
        findings: report.findings,
      };
    }
  }

  const files = [];
  const zip = new JSZip();
  for (const entry of entries) {
    const { sha256, bytes, buffer } = await hashFile(entry.abs);
    zip.file(entry.rel, buffer, { binary: true });
    files.push({ path: entry.rel, bytes, sha256 });
  }

  const readmePath = `${profile.root}/${folder}/README.txt`;
  zip.file(readmePath, engineReadme({ engine: profile.id, name: folder, files: files.map((f) => path.basename(f.path)), asset, mesh }), { binary: false });

  const manifestPath = `${profile.root}/${folder}/d3studio.bundle.json`;
  const manifest = {
    tool: 'd3studio',
    format: 1,
    engine: profile.id,
    created_at: new Date().toISOString(),
    [profile.id]: profile.meta,
    name: folder,
    asset: { id: asset.id, name: asset.name || '', extension },
    source: { origin: source.origin, version: source.version },
    mesh,
    files: [
      ...files,
      { path: readmePath, role: 'readme' },
      { path: manifestPath, role: 'manifest' },
    ],
  };
  zip.file(manifestPath, JSON.stringify(manifest, null, 2), { binary: false });

  const id = randomUUID();
  await fs.mkdir(outDir, { recursive: true });
  const zipPath = path.join(outDir, id + '.zip');
  const temp = zipPath + '.tmp';
  await fs.writeFile(temp, await zip.generateAsync({ type: 'nodebuffer', compression: 'DEFLATE', compressionOptions: { level: 6 } }));
  await fs.rename(temp, zipPath);

  const fileName = `${folder}${profile.suffix}`;
  await writeJson(path.join(outDir, id + '.meta.json'), { name: fileName, extension: '.zip', tool: `d3studio-${profile.id}` });
  const { bytes } = await hashFile(zipPath);
  return {
    id,
    engine: profile.id,
    name: fileName,
    url: '/api/exports/' + id,
    output: `${profile.root}/${folder}`,
    files: manifest.files,
    mesh: { checked: mesh.checked, triangles: mesh.triangles, vertices: mesh.vertices, findings: mesh.findings },
    bytes,
  };
}
