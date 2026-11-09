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
  02-документы/      1bsf6hAMSslCy_aOZk7T7nLsoYc5-vANo   README, разборы, планы (текстом, читаются без распаковки)
  03-сборки/         1YF93-1wnAgMpboqNhrFhT7_L_vEuGgGU   готовые сборки и пакеты ассетов
```

## Архивы исходников (`01-исходники`)

| Файл | id | Коммит | Что внутри |
|---|---|---|---|
| `d3studio-src-0.3.8.zip` | `1GRFKh92RpSBqyLQbL7EocfaHv5HRrNCT` | `65300ff` | **полный**: `studio/`, `d3studio/`, `docs/studio/` — код и все документы в одном файле |
| `d3studio-src-0.3.7.zip` | `1quxRJVJ1e6B9IdZXkkmdC_hincXpURtY` | `6625a6c` | `POST /api/llm/probe`, кнопка «Проверить, где считается» |
| `d3studio-src-0.3.6.zip` | `1ZmgTNMwLIXgrjZVlF5bnHqelNGleqIzk` | `7985ffc` | где считает локальная модель, `keep_alive` 5m, выгрузка VRAM перед 3D |
| `d3studio-src-0.3.5.zip` | `1EWp7Vin9oPVbhQ65aieX7iQ1PgvkWsHo` | `c0eba11` | режим промпта `product` / `literal` |
| `d3studio-src-0.3.4.zip` | `1BZdMfQhfNPG0tZGl1wNg6Hrn8pZMasyj` | `ffe0c15` | один сборщик пакетов для Unity и Godot |
| `d3studio-src-0.3.3.zip` | `1GnaWViXjnocYBvkRbe0muvAyX_2t1fCp` | `cc87f51` | Unity в интерфейсе, резолвер модели из Кузницы |
| `d3studio-src-0.3.2.zip` | `1RCKqaRU0VI9mkPQK4PmdtxYNyPIWjtJS` | `daa7931` | проверка модели перед импортом + `native/d3mesh` (Rust) |
| `d3studio-src-0.3.1.zip` | `1qeDdxfEKFJO2QVU_rkDPYjcqBYfZr-P8` | `724df4f` | первый полный снимок (код + `d3studio/` + `docs/studio/`) |

**Важно:** 0.3.2–0.3.7 собирались только из `studio/` — кода, без `docs/studio/`. Документы к ним
лежат отдельно в `02-документы`. Начиная с **0.3.8** архив полный: `studio/`, `d3studio/`,
`docs/studio/` — так и собираем дальше.

## Документы (`02-документы`)

| Файл | id | Что внутри |
|---|---|---|
| `README.md` | `1xMZVzze-7hhNOHpNq7Dmk6kEhL_4_CYH` | README студии (коммит `65300ff`): что умеет, как запускать, проверки, отличия от базы, лицензия |
| `DRIVE.md — где лежит наш материал.md` | этот файл | правило двух папок, полный перечень архивов и документов с id |
| `AUDIT-ASTRAFORGE-0.6.15.md` | `11wuvKJVZquhH2VV6E0GCxPpt4z7g-00E` | аудит последней версии автора: закрытый код, лицензии, что берём себе |
| `NATIVE.md — нативная часть на Rust.md` | `1PBUlV7VSjjxwl08tizXZ5tolw7mBNKAa` | `native/d3mesh`: что есть, как собрать, что дальше |
| `PROMPTS.md — промпты и цензура в студии автора.md` | `1ZCjYDtvcQtaOC4DyEbv_t4CjTlxdBcPb` | цензуры нет, есть переписывание промпта; места в коде |
| `LLM-GPU.md` | `1ShgdUwHY3DCvwSf-Lp0KJaNRWzpFwlSB` | почему локальная модель уходит на процессор, причины и проверки |
| `GPU-LLM.ru.md — проверка gpu-llm-1.md` | `1ig0yIsQadh8xVrxA8BtVGfOFBH74TFzT` | набор-проверка для студии автора: где считается модель, отчёт, откат |
| `ART2D-CHECKPOINT.ru.md — набор art2d-1.md` | `1lEreA3giKx7k-XRHC-yLSgpZ9yW-cwjA` | набор для студии автора: «2D» рисует выбранным SDXL-чекпойнтом |
| `BASE.md` | — | **фильтр коннектора не пропускает этот текст** (не наша ошибка, текст не переписываем); файл лежит внутри `d3studio-src-0.3.8.zip` и `0.3.1` в `docs/studio/` |
| `PLAN.md` | — | то же: внутри полного архива, отдельным файлом не заливается |

## Как собирается архив (с 0.3.8)

```bash
cd /home/user/geo2011
rm -rf /tmp/pkg && mkdir -p /tmp/pkg/src
git archive --format=tar HEAD studio d3studio docs/studio | tar -x -C /tmp/pkg/src
(cd /tmp/pkg/src && zip -q -9 -r /tmp/pkg/d3studio-src-<версия>.zip .)
```

Архив делается из коммита, а не из рабочего дерева: то, что не закоммичено, в Drive не уезжает.
`node_modules`, `dist`, выгрузки `data/` в архив не попадают — они восстанавливаются командами из
`studio/README.md`.

## Авторская папка (не наша)

| Файл | id | Что |
|---|---|---|
| `AstraForge-0.6.15-source.tar.gz` | `1fXfOjxNpUSrh4sxXsxDqqSdAbFd5wNWw` | снимок исходников релиза v0.6.15 с codeload (1,9 МБ); код внутри = 0.3.0 |
| `AstraForge-0.6.15.zip`, `AstraForge-update-0.6.15.zip` | `1GBZAu2QmjHARCuqO4PRtmzdN5TfW9ht4`, `1JaESEVURq12fEmRL5HCyh6Qu3Pd_QapO` | релиз и обновление автора |
| `AstraForge-releases-0.4.6-0.6.15.txt.gz` | `1f3j6hipFuSy7N1axg3z8AzZhPxfdS-lZ` | описания 33 выпусков из GitHub Releases |
| наборы `AstraForge-*.zip` (art2d-1, gpu-llm-1, vram-free-1, views-pascal-9, fixes-3, models-3, stab-4, memory-patch-1, bridge-2x) | см. папку | наши патчи для студии автора; описание каждого — в `02-документы` |

## Правила

* Новый материал по нашей студии — только в `d3studio`, ничего в авторскую папку.
* Каждый архив — из коммита, с номером версии и строкой в этом файле.
* Документы дублируются отдельными `.md`, чтобы читались с телефона без распаковки zip.
* Пароли, ключи (`data/vault.json`) и чужие рабочие копии на Диск не выкладываются.
