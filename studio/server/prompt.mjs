// Как текст пользователя превращается в промпт для SDXL.
//
// Это место раньше было спрятано внутри jobs.mjs/schema.mjs, и из-за него одна и та же
// формулировка давала разные картинки: текст не доходил до модели как есть. Здесь всё
// собрано в одном файле и покрыто тестами, чтобы поведение было видно, а не угадывалось.

/** Обёртка «предмет на белом фоне», которую базовая версия приклеивала к любому промпту. */
export const PRODUCT_PREFIX = 'single product photograph, exactly one object, one view only, ';
export const PRODUCT_SUFFIX = ', full object visible, isolated on solid white background, centered composition, clean silhouette';

/** Готовый негативный промпт: борется с сетками ракурсов, текстом и фоном. */
export const SDXL_NEGATIVE = 'contact sheet, collage, grid, tiles, panels, multiple visible views, '
  + 'different angles, variations, comparison, split screen, storyboard, text, watermark, cropped, '
  + 'multiple objects, blurry, scene, background, pedestal';

/** Параметры сэмплера, зашитые в базовой версии (не менять без причины — меняется вся серия). */
export const SDXL_SAMPLER = { steps: 25, cfg: 6, sampler_name: 'dpmpp_2m', scheduler: 'karras', width: 768, height: 768 };

/** SDXL понимает только английский: любой не-ASCII символ (кириллица, «—», кавычки-ёлочки) — повод для перевода. */
export function needsTranslation(prompt) {
  return /[^\x00-\x7F]/.test(String(prompt ?? ''));
}

/**
 * Собрать текст, который реально увидит CLIP.
 * @param {string} prompt текст пользователя (или его перевод)
 * @param {{mode?: 'product'|'literal'}} [options] product — обёртка как в базовой версии, literal — как написано
 * @returns {{text: string, mode: 'product'|'literal', wrapped: boolean}}
 */
export function buildSdxlPrompt(prompt, { mode = 'product' } = {}) {
  const text = String(prompt ?? '').trim();
  if (!text) throw new Error('Пустой промпт: SDXL нужно описание или картинка-концепт.');
  if (mode === 'literal') return { text, mode, wrapped: false };
  return { text: PRODUCT_PREFIX + text + PRODUCT_SUFFIX, mode: 'product', wrapped: true };
}

/** Русская подпись режима — для отчёта в задании и подсказок в интерфейсе. */
export function describePromptMode(mode) {
  return mode === 'literal'
    ? 'как написано (без перевода и без обёртки)'
    : 'как в базовой версии: перевод на английский + обёртка «предмет на белом фоне»';
}
