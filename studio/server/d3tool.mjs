// glTF/GLB → файлы Disciples 3 через наш собственный `d3tool`.
//
// Это первое, чего у базовой версии 0.3.0 не было и быть не могло: её конвейер заканчивался
// GLB / Godot-пакетом, а нам нужен родной формат игры (.g, .scene, .ac, .a, .t). Конвертер уже
// есть в этом же репозитории и проверен на 84 юнитах байт в байт (`tests/reverse_parity.py`),
// поэтому студия не пишет формат сама, а зовёт `python -m d3tool export <glTF> -o <папка>`
// и показывает, что получилось.
//
// Python и папка репозитория настраиваются окружением (D3TOOL_PYTHON, D3TOOL_REPO), потому что
// на машине пользователя python может называться иначе, а репозиторий может лежать отдельно.
import { spawn } from 'node:child_process';
import { promises as fs } from 'node:fs';
import path from 'node:path';
import { randomUUID } from 'node:crypto';
import { root, data } from './storage.mjs';

export const MODEL_EXTENSIONS = ['.glb', '.gltf'];
export const DEFAULT_PYTHON = process.env.D3TOOL_PYTHON || 'python';
export const d3ExportsDir = path.join(data, 'd3exports');

/** Папка с пакетом `d3tool`: по умолчанию — репозиторий, в котором лежит студия. */
export function d3toolRepo() {
  return path.resolve(process.env.D3TOOL_REPO || path.join(root, '..'));
}

export function isModelFile(name) {
  return MODEL_EXTENSIONS.includes(path.extname(String(name || '')).toLowerCase());
}

/** Имя файла/папки без того, чем Windows запрещает пользоваться. */
export function safeName(name, fallback = 'unit') {
  const clean = String(name || '').replace(/[<>:"/\\|?*\x00-\x1f]/g, '_').replace(/^\.+/, '').replace(/\s+/g, ' ').trim().slice(0, 120);
  return clean || fallback;
}

/** Файл внутри папки экспорта: нельзя выйти за её пределы ни `..`, ни абсолютным путём. */
export function insideDir(dir, rel) {
  const base = path.resolve(dir);
  const target = path.resolve(base, String(rel || ''));
  if (target !== base && !target.startsWith(base + path.sep)) throw new Error('Файл вне папки экспорта');
  return target;
}

/** Все файлы папки, относительными путями с прямыми слэшами (для ссылок в интерфейсе). */
export async function listFiles(dir) {
  const found = [];
  async function walk(current) {
    for (const entry of await fs.readdir(current, { withFileTypes: true })) {
      const full = path.join(current, entry.name);
      if (entry.isDirectory()) await walk(full);
      else if (entry.isFile()) found.push(path.relative(dir, full).split(path.sep).join('/'));
    }
  }
  await walk(path.resolve(dir));
  return found.sort();
}

/**
 * Запуск `python -m d3tool export`. Возвращает {files, outDir, output, code}.
 * Ненулевой код, таймаут и отсутствие python — это ошибки с понятным текстом, а не «ошибка 500».
 */
export async function runD3Export(gltfPath, outDir, options = {}) {
  const {
    python = DEFAULT_PYTHON,
    repo = d3toolRepo(),
    timeoutMs = 30 * 60 * 1000,
    spawnFn = spawn,
    env = process.env,
  } = options;
  await fs.mkdir(outDir, { recursive: true });
  const args = ['-m', 'd3tool', 'export', String(gltfPath), '-o', String(outDir)];
  const result = await new Promise((resolve) => {
    let child;
    try {
      child = spawnFn(python, args, {
        cwd: repo,
        windowsHide: true,
        env: { ...env, PYTHONPATH: repo + (env.PYTHONPATH ? path.delimiter + env.PYTHONPATH : ''), PYTHONIOENCODING: 'utf-8' },
      });
    } catch (error) {
      resolve({ code: -1, output: '', error });
      return;
    }
    let output = '';
    let settled = false;
    const finish = (code, error) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      resolve({ code, output, error });
    };
    const timer = setTimeout(() => {
      try { child.kill(); } catch { /* уже убит */ }
      finish(-1, new Error(`d3tool не уложился в ${Math.round(timeoutMs / 1000)} с`));
    }, timeoutMs);
    child.stdout?.on('data', (chunk) => { if (output.length < 40000) output += chunk.toString(); });
    child.stderr?.on('data', (chunk) => { if (output.length < 40000) output += chunk.toString(); });
    child.on('error', (error) => finish(-1, error));
    child.on('close', (code) => finish(code ?? -1));
  });
  if (result.error || result.code !== 0) {
    const tail = String(result.output || '').trim().split(/\r?\n/).slice(-12).join('\n');
    const why = result.error?.code === 'ENOENT'
      ? `не найден Python (${python}). Укажите его в D3TOOL_PYTHON.`
      : (result.error?.message || `d3tool export завершился с кодом ${result.code}`);
    throw Object.assign(new Error(`Экспорт в Disciples 3 не удался: ${why}${tail ? '\n' + tail : ''}`), { output: result.output });
  }
  const files = await listFiles(outDir);
  if (!files.length) throw new Error('d3tool закончил, но не записал ни одного файла.');
  return { files, outDir, output: result.output, code: result.code };
}

/** Экспорт ассета в новую папку `data/d3exports/<id>/`; метаданные кладутся рядом для ссылок. */
export async function exportAsset(asset, options = {}) {
  if (!isModelFile(asset?.name)) throw new Error('В Disciples 3 экспортируется только .glb или .gltf');
  const source = path.join(data, 'assets', asset.id + path.extname(asset.name));
  await fs.access(source).catch(() => { throw Object.assign(new Error('Файл ассета не найден'), { status: 404 }); });
  const id = randomUUID();
  const outDir = path.join(d3ExportsDir, id, safeName(path.basename(asset.name, path.extname(asset.name))));
  const exported = await runD3Export(source, outDir, options);
  const meta = { id, assetId: asset.id, name: asset.name, dir: outDir, files: exported.files,
    output: String(exported.output || '').slice(-4000), createdAt: new Date().toISOString() };
  await fs.mkdir(d3ExportsDir, { recursive: true });
  await fs.writeFile(path.join(d3ExportsDir, id + '.json'), JSON.stringify(meta, null, 2), 'utf8');
  return meta;
}

export async function exportMeta(id) {
  if (!/^[0-9a-f-]{36}$/i.test(String(id || ''))) throw Object.assign(new Error('Неверный номер экспорта'), { status: 400 });
  const raw = await fs.readFile(path.join(d3ExportsDir, id + '.json'), 'utf8').catch(() => null);
  if (!raw) throw Object.assign(new Error('Экспорт не найден'), { status: 404 });
  return JSON.parse(raw);
}
