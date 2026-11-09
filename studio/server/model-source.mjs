// Где лежит модель для ассета. В студии два хранилища, и это надо уважать:
//   data/assets/<id>.glb                       — то, что импортировано/загружено в студии;
//   <library>/assets/<id>/<версия>/... .glb    — то, что собрала Кузница (forge).
// Раньше экспорт понимал только первое, поэтому кнопка «Unity» не работала бы для обычного
// ассета из библиотеки. Здесь один резолвер на оба случая, с проверкой, что путь не вылез
// за папку ассета.
import { promises as fs, existsSync } from 'node:fs';
import path from 'node:path';
import { data, assetInfo, isId, readJson } from './storage.mjs';
import { defaultLibraryDir } from './forge-pipeline.mjs';

/** Форматы, которые мы умеем отдавать в движок и проверять. */
export const MODEL_EXTENSIONS = ['.glb', '.gltf', '.obj'];

export function libraryAssetsDir(libraryDir) {
  return path.join(path.resolve(libraryDir || process.env.ASTRA_FORGE_LIBRARY || defaultLibraryDir(data)), 'assets');
}

function inside(dir, file) {
  const base = path.resolve(dir) + path.sep;
  return path.resolve(file).startsWith(base);
}

function versionNumber(version) {
  const match = /^v?(\d+)/.exec(String(version || ''));
  return match ? Number(match[1]) : 0;
}

/** Новейшая завершённая версия ассета Кузницы и её итоговый файл нужного вида. */
function latestFromVersions(manifest) {
  return [...(manifest.versions || [])]
    .filter((v) => v && v.status === 'completed' && v.version)
    .sort((a, b) => versionNumber(b.version) - versionNumber(a.version));
}

const STATE_KEYS = ['glb', 'mesh', 'fbx'];

/**
 * Найти файл модели по id ассета — в студии или в библиотеке Кузницы.
 * @param {string} assetId
 * @param {{libraryDir?: string}} [options]
 * @returns {Promise<{file:string,extension:string,name:string,origin:'studio'|'library',version:string|null,bytes:number}>}
 */
export async function resolveModelSource(assetId, { libraryDir } = {}) {
  if (!isId(assetId)) throw Object.assign(new Error('Invalid ID'), { status: 400 });

  // 1) хранилище студии
  const studio = await assetInfo(assetId).catch(() => null);
  if (studio) {
    const extension = String(studio.extension || '').toLowerCase();
    if (!MODEL_EXTENSIONS.includes(extension)) {
      throw Object.assign(new Error(`В движок уходит модель (${MODEL_EXTENSIONS.join(', ')}), а не ${extension || 'файл без расширения'}`), { status: 400 });
    }
    const file = path.join(data, 'assets', studio.id + extension);
    const stat = await fs.stat(file).catch(() => null);
    if (!stat) throw Object.assign(new Error('Файл ассета пропал с диска'), { status: 404 });
    return { file, extension, name: studio.name || assetId, origin: 'studio', version: null, bytes: stat.size };
  }

  // 2) библиотека Кузницы
  const dir = path.join(libraryAssetsDir(libraryDir), assetId.toLowerCase());
  const manifest = await readJson(path.join(dir, 'asset.json'), null).catch(() => null);
  if (!manifest) throw Object.assign(new Error('Ассет не найден ни в студии, ни в библиотеке Кузницы'), { status: 404 });

  for (const version of latestFromVersions(manifest)) {
    const record = await readJson(path.join(dir, version.version, 'version.json'), null).catch(() => null);
    const final = record?.final || version.final || {};
    for (const key of STATE_KEYS) {
      const file = final[key];
      if (!file || !existsSync(file) || !inside(dir, file)) continue;
      const extension = path.extname(file).toLowerCase();
      if (!MODEL_EXTENSIONS.includes(extension)) continue;
      const stat = await fs.stat(file);
      return { file, extension, name: manifest.name || assetId, origin: 'library', version: version.version, bytes: stat.size };
    }
  }

  if (manifest.source?.file) {
    const file = path.join(dir, manifest.source.file);
    if (existsSync(file) && inside(dir, file)) {
      const extension = path.extname(file).toLowerCase();
      if (MODEL_EXTENSIONS.includes(extension)) {
        const stat = await fs.stat(file);
        return { file, extension, name: manifest.name || assetId, origin: 'library', version: null, bytes: stat.size };
      }
    }
  }

  throw Object.assign(
    new Error(`У ассета «${manifest.name || assetId}» нет готовой модели: соберите версию в Кузнице (нужен .glb, .gltf или .obj)`),
    { status: 409 },
  );
}
