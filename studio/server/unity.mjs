// Пакет для Unity: из готовой модели студии собирается папка с .glb, текстурами,
// манифестом и инструкцией по импорту, всё это упаковывается в один .zip.
//
// Зачем отдельный файл: Godot-пакет у базовой версии был (шаг `godot_package` в
// forge-pipeline.mjs), Unity-пакета не было совсем. Формат папки —
// `Assets/d3studio/<Name>/...`, чтобы после распаковки в проект ничего не переименовывать.
import JSZip from 'jszip';
import { createHash, randomUUID } from 'node:crypto';
import { promises as fs } from 'node:fs';
import path from 'node:path';
import { data, assetInfo, writeJson } from './storage.mjs';
import { checkMesh } from './mesh-check.mjs';

/** Форматы, которые Unity (или glTFast) импортирует без сторонних конвертеров. */
export const UNITY_MODEL_EXTENSIONS = ['.glb', '.gltf', '.obj'];

/**
 * Кириллица в латиницу: папка в Unity должна переживать Windows, macOS и сборку под телефон,
 * поэтому имена ассетов («Боец с копьём») не тащим в путь как есть.
 */
const TRANSLIT = {
  а: 'a', б: 'b', в: 'v', г: 'g', д: 'd', е: 'e', ё: 'e', ж: 'zh', з: 'z', и: 'i', й: 'y',
  к: 'k', л: 'l', м: 'm', н: 'n', о: 'o', п: 'p', р: 'r', с: 's', т: 't', у: 'u', ф: 'f',
  х: 'h', ц: 'c', ч: 'ch', ш: 'sh', щ: 'shch', ъ: '', ы: 'y', ь: '', э: 'e', ю: 'yu', я: 'ya',
  і: 'i', ї: 'yi', є: 'ye', ґ: 'g',
};

/** Имя модели как имя папки в Unity: латиница/цифры/дефис, без пробелов и служебных символов. */
export function unityName(name, fallback = 'Asset') {
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

/** Куда класть текстуры: те же .png/.jpg, что лежат у нас как ассеты. */
export function isTextureFile(extension) {
  return ['.png', '.jpg', '.jpeg', '.tga', '.exr'].includes(String(extension).toLowerCase());
}

export function unityModelExtension(extension) {
  const ext = String(extension || '').toLowerCase();
  if (!UNITY_MODEL_EXTENSIONS.includes(ext)) {
    throw Object.assign(
      new Error(`В Unity уходит модель (${UNITY_MODEL_EXTENSIONS.join(', ')}), а не ${ext || 'файл без расширения'}`),
      { status: 400 },
    );
  }
  return ext;
}

async function hashFile(file) {
  const buf = await fs.readFile(file);
  return { sha256: createHash('sha256').update(buf).digest('hex'), bytes: buf.length };
}

/** Текст инструкции, которая едет внутрь пакета (её читают в Unity, не в студии). */
export function unityReadme({ name, files, asset, mesh }) {
  const models = files.filter((f) => UNITY_MODEL_EXTENSIONS.includes(path.extname(f)));
  const textures = files.filter((f) => isTextureFile(path.extname(f)));
  return [
    `${name} — пакет d3studio для Unity`,
    '',
    'Что внутри:',
    `  Models/     ${models.join(', ') || '—'}`,
    `  Textures/   ${textures.join(', ') || '— (текстур в пакете нет)'}`,
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
    'Как импортировать (Unity 2022.3 и новее):',
    '  1. Package Manager → + → Add package by name → com.unity.cloud.gltfast (glTFast).',
    '     Без него .glb в проект не попадёт; .obj Unity импортирует сама.',
    `  2. Распаковать папку ${name} в Assets/ своего проекта — путь станет Assets/${name}/.`,
    '  3. Выделить модель в Project, в Inspector выставить Scale Factor = 1:',
    '     студия считает в метрах, 1 юнит Unity = 1 метр.',
    '  4. Материалы: glTFast создаёт их как Lit. Для URP/HDRP нажать',
    '     Edit → Rendering → Materials → Convert Selected… (или назначить свой шейдер).',
    '  5. Текстуры из Textures/ перетащить в соответствующие слоты материала,',
    '     если glTFast не подхватил их автоматически (в .glb они уже вложены).',
    '  6. Проверить: модель стоит на нуле, высота совпадает с манифестом (height_m),',
    '     лицо смотрит в -Z (вперёд в Unity).',
    '',
    `Источник: ассет ${asset.id} (${asset.name}), собрано d3studio.`,
    'Файлы не пересобирались — это та же геометрия, что показывает студия.',
    '',
  ].join('\n');
}

/**
 * Собрать Unity-пакет из ассета студии.
 * @param {{assetId: string, textures?: string[], name?: string, dir?: string}} options
 * @returns {Promise<{id,name,url,output,files:{path,bytes,sha256}[],bytes}>}
 */
export async function buildUnityBundle({ assetId, textures = [], name, dir } = {}) {
  const asset = await assetInfo(assetId);
  const extension = unityModelExtension(asset.extension);
  const modelFile = path.join(data, 'assets', asset.id + extension);
  const folder = unityName(name || asset.name || asset.id);
  const outDir = dir || path.join(data, 'exports');

  const entries = [{ abs: modelFile, rel: `Assets/${folder}/Models/${folder}${extension}` }];
  for (const textureId of textures) {
    if (!textureId || textureId === assetId) continue;
    const texture = await assetInfo(textureId);
    if (!isTextureFile(texture.extension)) {
      throw Object.assign(new Error(`Не текстура: ${texture.extension || 'без расширения'}`), { status: 400 });
    }
    const file = path.join(data, 'assets', texture.id + texture.extension.toLowerCase());
    entries.push({ abs: file, rel: `Assets/${folder}/Textures/${unityName(texture.name || texture.id)}${texture.extension.toLowerCase()}` });
  }

  const modelBuffer = await fs.readFile(modelFile);
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
    const { sha256, bytes } = await hashFile(entry.abs);
    const buffer = await fs.readFile(entry.abs);
    zip.file(entry.rel, buffer, { binary: true });
    files.push({ path: entry.rel, bytes, sha256 });
  }

  const readmePath = `Assets/${folder}/README.txt`;
  const readme = unityReadme({ name: folder, files: files.map((f) => path.basename(f.path)), asset, mesh });
  zip.file(readmePath, readme, { binary: false });

  const manifestPath = `Assets/${folder}/d3studio.bundle.json`;
  const manifest = {
    tool: 'd3studio',
    format: 1,
    created_at: new Date().toISOString(),
    unity: { min_version: '2022.3', importer: 'com.unity.cloud.gltfast', scale: '1 unit = 1 meter', forward: '-Z' },
    name: folder,
    asset: { id: asset.id, name: asset.name || '', extension },
    mesh,
    files: [...files, { path: readmePath, role: 'readme' }, { path: manifestPath, role: 'manifest' }],
  };
  zip.file(manifestPath, JSON.stringify(manifest, null, 2), { binary: false });

  const id = randomUUID();
  await fs.mkdir(outDir, { recursive: true });
  const zipPath = path.join(outDir, id + '.zip');
  const temp = zipPath + '.tmp';
  await fs.writeFile(temp, await zip.generateAsync({ type: 'nodebuffer', compression: 'DEFLATE', compressionOptions: { level: 6 } }));
  await fs.rename(temp, zipPath);

  const fileName = `${folder}-unity.zip`;
  await writeJson(path.join(outDir, id + '.meta.json'), { name: fileName, extension: '.zip', tool: 'd3studio-unity' });
  const { bytes } = await hashFile(zipPath);
  return {
    id,
    name: fileName,
    url: '/api/exports/' + id,
    output: `Assets/${folder}`,
    files: [...files, { path: readmePath, role: 'readme' }, { path: manifestPath, role: 'manifest' }],
    mesh: { checked: mesh.checked, triangles: mesh.triangles, vertices: mesh.vertices, findings: mesh.findings },
    bytes,
  };
}
