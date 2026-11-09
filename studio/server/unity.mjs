// Unity-пакет: тонкая обёртка над общим сборщиком (server/engine-bundle.mjs).
// Имена операций оставлены прежними — на них завязаны маршруты и тесты.
import { buildEngineBundle, bundleName, modelExtension, isTextureFile, engineReadme, ENGINES } from './engine-bundle.mjs';
import { MODEL_EXTENSIONS } from './model-source.mjs';

/** Форматы, которые Unity (или glTFast) импортирует без сторонних конвертеров. */
export const UNITY_MODEL_EXTENSIONS = MODEL_EXTENSIONS;

export const unityName = bundleName;
export const unityModelExtension = modelExtension;
export { isTextureFile };

/** Текст инструкции для Unity (без сборки архива — нужен и тестам, и пакету). */
export function unityReadme(options) {
  return engineReadme({ engine: ENGINES.unity.id, ...options });
}

/** Собрать Unity-пакет из ассета студии или Кузницы. */
export async function buildUnityBundle(options = {}) {
  return buildEngineBundle({ ...options, engine: ENGINES.unity.id });
}
