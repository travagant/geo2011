// Сборка нативной части. Запуск: npm run native:build
//
// Если cargo нет — говорим прямо, что делать, и не притворяемся, что собралось.
import { spawnSync } from 'node:child_process';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { existsSync } from 'node:fs';

const here = path.dirname(fileURLToPath(import.meta.url));
const project = path.join(here, '..', 'native', 'd3mesh');
const binary = path.join(project, 'target', 'release', process.platform === 'win32' ? 'd3mesh.exe' : 'd3mesh');

const probe = spawnSync('cargo', ['--version'], { encoding: 'utf8' });
if (probe.error) {
  console.error('cargo не найден. Установите Rust (https://rustup.rs), затем повторите: npm run native:build');
  console.error('Без нативной части студия работает: проверка GLB идёт на JS (server/native.mjs).');
  process.exit(2);
}

console.log(`cargo: ${probe.stdout.trim()}`);
const build = spawnSync('cargo', ['build', '--release', '--offline'], { cwd: project, stdio: 'inherit' });
if (build.status !== 0) {
  console.error('Сборка не прошла. Если cargo ругается на сеть — зависимости не нужны, проверьте --offline и версию Rust.');
  process.exit(build.status ?? 1);
}
if (!existsSync(binary)) {
  console.error(`Собралось, но бинарника нет по пути ${binary}`);
  process.exit(1);
}
console.log(`готово: ${binary}`);
