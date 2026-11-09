# Где лежит наш материал (Google Drive)

Правило владельца 09.11.2026: **наше — в нашу папку, авторское — в авторскую**, чтобы не путать.

| Что | Папка Drive | id |
|---|---|---|
| **Наша студия d3studio** — сюда складываем всё, что сделали мы | `d3studio` (корень Диска) | `1ivq-sM76QfVbU10zCR2P2BFKEoR-8R73` |
| Авторская студия AstraForge — чужие патчи и чужие документы | `AstraForge` | `1-ENJRy-PqqK4izYx5avGLCgX7OKk3RGJ` |

Ссылка на нашу папку: <https://drive.google.com/drive/folders/1ivq-sM76QfVbU10zCR2P2BFKEoR-8R73>

Внутри нашей папки:

```
d3studio/            1ivq-sM76QfVbU10zCR2P2BFKEoR-8R73
  01-исходники/      1X1sdy-puezR6ywz4XEKcEKqG29IcGoa2   архивы кода студии (zip из git), по версиям
  02-документы/      1bsf6hAMSslCy_aOZk7T7nLsoYc5-vANo   README, DRIVE (текстом, читаются без распаковки)
  03-сборки/         1YF93-1wnAgMpboqNhrFhT7_L_vEuGgGU   готовые сборки и пакеты ассетов
```

## Что уже залито

| Файл | Куда | Что внутри |
|---|---|---|
| `d3studio-src-0.3.1.zip` (`1qeDdxfEKFJO2QVU_rkDPYjcqBYfZr-P8`) | `01-исходники` | коммит `724df4f`: `studio/` (основа — AstraForge 0.3.0, MIT), `d3studio/` (лёгкий режим), `docs/studio/` целиком (BASE, PLAN, DRIVE); без `node_modules`, `dist`, `__pycache__`, выгрузок `data/` |
| `README.md` (`1MR_mg7lQyPljkorrMC1ro7AXRx8Oh7_i`) | `02-документы` | описание студии: Godot и Unity, что переписано, как запускать |
| `DRIVE.md` (`1O87zmj0MKzNA2HDYD0DHiIdijRotJaAm`) | `02-документы` | это правило: наша папка отдельно от авторской, структура |
| `AUDIT-ASTRAFORGE-0.6.15.md` (`11wuvKJVZquhH2VV6E0GCxPpt4z7g-00E`) | `02-документы` | наш аудит последней версии автора: закрытый код, лицензии, что берём себе |
| `NATIVE.md` (`1PBUlV7VSjjxwl08tizXZ5tolw7mBNKAa`) | `02-документы` | нативная часть на Rust: что есть, как собрать, что дальше |
| `d3studio-src-0.3.2.zip` (`1RCKqaRU0VI9mkPQK4PmdtxYNyPIWjtJS`) | `01-исходники` | коммит `daa7931`: проверка модели перед импортом + `native/d3mesh` (Rust) |
| `d3studio-src-0.3.3.zip` (`1GnaWViXjnocYBvkRbe0muvAyX_2t1fCp`) | `01-исходники` | коммит `cc87f51`: Unity в интерфейсе, резолвер модели из Кузницы |
| `d3studio-src-0.3.4.zip` (`1BZdMfQhfNPG0tZGl1wNg6Hrn8pZMasyj`) | `01-исходники` | коммит `ffe0c15`: один сборщик пакетов для Unity и Godot |
| `d3studio-src-0.3.5.zip` (`1EWp7Vin9oPVbhQ65aieX7iQ1PgvkWsHo`) | `01-исходники` | коммит `c0eba11`: режим промпта `product` / `literal` |
| `d3studio-src-0.3.6.zip` (`1ZmgTNMwLIXgrjZVlF5bnHqelNGleqIzk`) | `01-исходники` | коммит `7985ffc`: где считает локальная модель, `keep_alive` 5m, выгрузка VRAM перед 3D |
| `LLM-GPU.md` (`1AZ307BUsaJqekGSfjHqNVFJyC6K56F8_`) | `02-документы` | почему локальная модель уходит на процессор и как проверить |
| `d3studio-src-0.3.7.zip` | `01-исходники` | коммит `6625a6c`: `POST /api/llm/probe` — вердикт, токены/с, время загрузки; кнопка «Проверить, где считается» |
| `PROMPTS.md` (`1ZCjYDtvcQtaOC4DyEbv_t4CjTlxdBcPb`) | `02-документы` | разбор промптов автора: цензуры нет, есть переписывание |

В **авторскую** папку `AstraForge` (не нашу) положено то, что принадлежит автору:

| Файл | id | Что |
|---|---|---|
| `AstraForge-0.6.15-source.tar.gz` | `1fXfOjxNpUSrh4sxXsxDqqSdAbFd5wNWw` | снимок исходников релиза v0.6.15 с codeload (1,9 МБ); код внутри = 0.3.0 |
| `AstraForge-releases-0.4.6-0.6.15.txt.gz` | `1f3j6hipFuSy7N1axg3z8AzZhPxfdS-lZ` | описания 33 выпусков из GitHub Releases |

`BASE.md` и `PLAN.md` отдельными файлами не залились: фильтр коннектора их отклоняет по содержимому
(не наша ошибка и не повод переписывать текст) — оба лежат внутри архива исходников в
`docs/studio/` и в репозитории.

## Как собирается архив исходников

```bash
cd /home/user/geo2011
git archive --format=tar HEAD studio d3studio docs/studio | tar -x -C /tmp/pkg/src
(cd /tmp/pkg/src && zip -q -9 -r /tmp/pkg/d3studio-src-<версия>.zip .)
```

Архив делается из коммита, а не из рабочего дерева: то, что не закоммичено, в Drive не уезжает.
`node_modules`, `dist`, `data/` (кэш, ассеты, выгрузки) в архив не попадают — они восстанавливаются
командами из `studio/README.md`.
