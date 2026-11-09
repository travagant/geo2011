// Godot-пакет: та же модель и тот же манифест, что у Unity, но папка и инструкция — под Godot.
// Нужен потому, что профиль `godot` в Кузнице собирается Blender-шагом и требует запуска
// движка для проверки; а «взять готовую модель и положить в проект» должно работать сразу.
import { buildEngineBundle, engineReadme, ENGINES } from './engine-bundle.mjs';

export const GODOT_ENGINE = ENGINES.godot;

export function godotReadme(options) {
  return engineReadme({ engine: ENGINES.godot.id, ...options });
}

export async function buildGodotBundle(options = {}) {
  return buildEngineBundle({ ...options, engine: ENGINES.godot.id });
}
