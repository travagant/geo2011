# Где лежит наш материал (Google Drive)

Правило владельца 09.11.2026: **наше — в нашу папку, авторское — в авторскую**, чтобы не путать.

| Что | Папка Drive | id |
|---|---|---|
| **Наша студия d3studio** — сюда складываем всё, что сделали мы | `d3studio` (корень Диска) | `1ivq-sM76QfVbU10zCR2P2BFKEoR-8R73` |
| Авторская студия AstraForge — чужие патчи и чужие документы | `AstraForge` | `1-ENJRy-PqqK4izYx5avGLCgX7OKk3RGJ` |

Ссылка на нашу папку: <https://drive.google.com/drive/folders/1ivq-sM76QfVbU10zCR2P2BFKEoR-8R73>

Внутри нашей папки:

```
d3studio/
  01-исходники/   — архивы кода студии (zip из git), по версиям
  02-документы/   — README, BASE, PLAN, DRIVE (текстом, читаются без распаковки)
  03-сборки/      — готовые сборки и пакеты ассетов, если понадобится отдать наружу
```

## Что уже залито

| Файл | Куда | Что внутри |
|---|---|---|
| `d3studio-src-0.3.1.zip` | `01-исходники` | `studio/` (основа — AstraForge 0.3.0, MIT), `d3studio/` (лёгкий режим), `docs/studio/`; без `node_modules`, `dist`, `__pycache__`, `data/` |
| `README.md`, `BASE.md`, `PLAN.md`, `DRIVE.md` | `02-документы` | описание студии, атрибуция MIT и что переписано, план по Godot/Unity, это правило про папки |

## Как собирается архив исходников

```bash
cd /home/user/geo2011
git archive --format=tar HEAD studio d3studio docs/studio | tar -x -C /tmp/pkg/src
(cd /tmp/pkg/src && zip -q -9 -r /tmp/pkg/d3studio-src-<версия>.zip .)
```

Архив делается из коммита, а не из рабочего дерева: то, что не закоммичено, в Drive не уезжает.
`node_modules`, `dist`, `data/` (кэш, ассеты, выгрузки) в архив не попадают — они восстанавливаются
командами из `studio/README.md`.
