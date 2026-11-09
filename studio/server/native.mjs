// Мост к нативной части студии (`native/d3mesh`, Rust).
//
// Правило: нативная часть — ускорение, а не обязательное условие. Пока бинарник не собран,
// работает эта же проверка на JS, и поведение обязано совпадать (сравнивается в
// tests/native.test.mjs). Так студия не зависит от того, есть ли на машине cargo.
import { spawnSync } from 'node:child_process';
import { existsSync } from 'node:fs';
import path from 'node:path';
import { crc32 } from 'node:zlib';
import { root } from './storage.mjs';

const BINARY = process.platform === 'win32' ? 'd3mesh.exe' : 'd3mesh';

/** Где лежит собранный нативный бинарник (после `npm run native:build`). */
export function nativeBinaryPath() {
  return path.join(root, 'native', 'd3mesh', 'target', 'release', BINARY);
}

export function hasNativeBinary() {
  return existsSync(nativeBinaryPath());
}

function u32(buffer, offset) {
  return buffer.readUInt32LE(offset);
}

/**
 * Проверка контейнера GLB без Rust: тот же ответ, что печатает `d3mesh verify`.
 * @param {Buffer} buffer
 * @returns {{ok:boolean,bytes?:number,version?:number,json_chunk?:number,bin_chunk?:number,crc32?:string,error?:string}}
 */
export function verifyGlbBuffer(buffer) {
  if (!Buffer.isBuffer(buffer) || buffer.length < 20) return { ok: false, error: 'Файл не GLB: меньше 20 байт' };
  if (u32(buffer, 0) !== 0x46546c67) return { ok: false, error: 'Файл не GLB: нет подписи glTF' };
  const version = u32(buffer, 4);
  if (version !== 2) return { ok: false, error: `GLB версии ${version}: поддерживается только 2` };
  const declared = u32(buffer, 8);
  if (declared !== buffer.length) {
    return { ok: false, error: `GLB обрезан: в заголовке ${declared} байт, в файле ${buffer.length}` };
  }
  let offset = 12;
  let jsonChunk = 0;
  let binChunk = 0;
  let seenJson = false;
  while (offset + 8 <= buffer.length) {
    const length = u32(buffer, offset);
    const kind = u32(buffer, offset + 4);
    const start = offset + 8;
    if (start + length > buffer.length) return { ok: false, error: 'GLB: чанк выходит за пределы файла' };
    if (kind === 0x4e4f534a && !seenJson) { seenJson = true; jsonChunk = length; }
    else if (kind === 0x004e4942 && !binChunk) binChunk = length;
    offset = start + length + ((4 - (length % 4)) % 4);
  }
  if (!seenJson) return { ok: false, error: 'GLB: нет JSON-чанка' };
  return { ok: true, bytes: buffer.length, version, json_chunk: jsonChunk, bin_chunk: binChunk, crc32: '0x' + crc32(buffer).toString(16).padStart(8, '0') };
}

/**
 * Проверка нативной частью, если она собрана. Возвращает null, когда бинарника нет —
 * вызывающий код в этом случае берёт `verifyGlbBuffer`.
 */
export function nativeVerify(file) {
  if (!hasNativeBinary()) return null;
  const result = spawnSync(nativeBinaryPath(), ['verify', file], { encoding: 'utf8', timeout: 60000 });
  if (result.error) return { ok: false, error: `d3mesh не запустился: ${result.error.message}` };
  const text = (result.stdout || '').trim().split('\n').pop();
  try {
    return JSON.parse(text);
  } catch {
    return { ok: false, error: `d3mesh ответил не JSON: ${(result.stdout || result.stderr || '').slice(0, 200)}` };
  }
}
