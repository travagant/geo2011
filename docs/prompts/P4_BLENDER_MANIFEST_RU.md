# Прогон P4: `blender_manifest.toml` для Blender 4.2+

Модель: `opencode --model opencode/qwen3.6-plus-free`, reasoning effort **`Low`**.

Задача изолированная: один новый файл, одна новая ветка сборки, один новый
тест. Проверяется **без Blender** — распаковкой zip и валидатором Blender,
который оказался чистым stdlib.

## Что я проверил в репозитории (агент это же увидит)

| Факт | Следствие для задачи |
|---|---|
| импорты в аддоне **уже относительные** (`from . import bridge`) | правка импортов не нужна |
| `preferences.py`: `bl_idname = __package__` | уже extension-совместимо |
| `bl_info` читается **только** в `__init__.py:26`, нигде не используется в коде | оставить как есть — проще и безопаснее |
| `.gitignore`: `release/d3unit-addon/*` + `!.../io_scene_d3unit.zip` | `d3unit-*.zip` уже игнорируется |
| `LICENSE` в репозитории **нет**, GitHub показывает `license: null` | лицензию выбирает человек, не агент |
| Blender при `bl_info` + манифесте: `del mod.bl_info`, работает по манифесту | конфликта нет, предупреждение только под `debug_python` |
| валидатор `bl_pkg/cli/blender_ext.py` — **чистый stdlib**, `tomllib` + `zipfile`, bpy не нужен | тест гоняется в песочнице |
| `build` и `validate` в Blender вызывают валидацию со `strict=True` (blender_ext.py:4820, :5171) | правила `tagline` и `permissions` действуют всегда |
| валидатор **не** проверяет префикс `SPDX:` — только непустоту строк | `SPDX:` ставим по документации, не по валидатору |

## Эталон проверен валидатором Blender

Этот TOML я прогнал через настоящий `pkg_manifest_from_archive_and_validate(...,
strict=True)` из `bl_pkg/cli/blender_ext.py` — вернулся `PkgManifest(...)`, то
есть манифест валиден. Агент должен прийти к тому же файлу.

Заодно проверил, что валидатор **ловит**: `version = "1.1"` (не семавер),
`id = "_d3unit"`, отсутствие `maintainer`, отсутствие `type`, а также
`tagline` с точкой на конце и лишние ключи в `permissions` — но только при
`strict=True`.

## Промт (копировать целиком)

```
Репозиторий: https://github.com/travagant/geo2011, ветка main, HEAD bc059d1.
Python 3.11, только stdlib (tomllib есть). Blender НЕ нужен — всё проверяется
распаковкой zip.

============ ЧАСТЬ 0. ПРАВИЛА ПРИЁМКИ (нарушение = работа не принята) ============

R1. Правь ТОЛЬКО эти файлы:
      blender/io_scene_d3unit/blender_manifest.toml   (новый)
      release/build_addon.py                          (новый флаг)
      tests/addon_manifest_test.py                    (новый)
      tests/addon_zip_test.py                         (только добавление
                                                       проверки, см. ЧАСТЬ 4)
    Не трогай: d3tool/**, tests/test_corpus.py, tests/test_d3tool.py,
    tests/corpus_parity.py, tests/reverse_parity.py, blender/io_scene_d3unit/*.py
    (кроме создания манифеста), README.md.

R2. Версию НЕ дублируй руками. В build_addon.py уже есть addon_version(),
    читающая bl_info["version"] через ast. Манифест в исходниках содержит
    version = "1.1.51" (это факт, его можно записать литералом), но сборщик
    ОБЯЗАН перезаписать его значением из addon_version() в staging-копии.
    Если они разойдутся — сборка падает с понятной ошибкой, а не тихо
    выпускает манифест со старой версией.

R3. Legacy-путь НЕ меняется. `python3 release/build_addon.py` без флагов даёт
    ровно тот же zip, что и сейчас: 29 файлов, без манифеста, без новых
    файлов внутри. Это проверяется тестом, а не «на глаз».

R4. ZIP собирается ВРУЧНУЮ через zipfile, а не `blender --command extension
    build`. Blender в CI нет.

R5. Верни бинарник как было: `release/d3unit-addon/io_scene_d3unit.zip` лежит
    в git. После своих проверок выполни
        git checkout -- release/d3unit-addon/io_scene_d3unit.zip
    Иначе в diff приедет бинарник на 254933 байта.

============ ЧАСТЬ 1. Манифест ============

Создай blender/io_scene_d3unit/blender_manifest.toml ровно с этими полями:

    schema_version = "1.0.0"
    id = "d3unit"
    version = "1.1.51"
    name = "Disciples III Unit"
    tagline = "Import and export Disciples III unit files"
    maintainer = "geo2011"
    type = "add-on"
    blender_version_min = "4.2.0"
    license = ["SPDX:GPL-3.0-or-later"]
    tags = ["Import-Export", "Game Engine"]

    [permissions]
    files = "Read and write the unit files you choose"

Проверено по исходникам Blender (scripts/addons_core/bl_pkg/cli/blender_ext.py),
не по памяти. Требования, которые легко нарушить:

- `id`: валидный Python-идентификатор, без `__`, не с `_` и не с `_` на конце.
- `tagline`: не длиннее 64 символов и НЕ заканчивается на пунктуацию —
  последний символ должен быть буквой/цифрой или закрывающей скобкой
  (`)`, `]`, `}`). Проверяется только при `strict=True` — а Blender и
  `build`, и `validate` вызывают валидацию именно со `strict=True`
  (blender_ext.py:4820 и :5171), так что правило действует всегда.
- `license`: непустой список непустых строк. ВАЖНО: сам валидатор префикс
  `SPDX:` НЕ проверяет (я проверил — `["GPL-3.0-or-later"]` без префикса
  проходит). `SPDX:` требует документация Blender, а не валидатор, поэтому
  ставим его сознательно: без префикса строка уходит в UI как есть.
- `permissions.files`: одна короткая фраза без точки на конце (как и
  tagline — «short single-sentence, up to 64 characters, no end punctuation»).
- `version`: семавер (`RE_MANIFEST_SEMVER`), строка, не кортеж.
- Все значения должны быть непустыми. Не хочешь поле — не пиши его вовсе.

Все перечисленные поля обязательные (`pkg_manifest_known_keys_and_types`
помечает их как required до комментария «Optional»). Не добавляй `website`,
`copyright`, `platforms`, `wheels` — их нет смысла выдумывать.

============ ЧАСТЬ 2. Сборка extension-zip ============

В release/build_addon.py добавь флаг:

    python3 release/build_addon.py             # как сейчас
    python3 release/build_addon.py --extension # + d3unit-1.1.51.zip

Требования к --extension:

1. Манифест кладётся в КОРЕНЬ архива рядом с __init__.py, а НЕ внутрь
   папки io_scene_d3unit/:
       blender_manifest.toml
       __init__.py
       bridge.py ...
       vendor/d3tool/...
   Проверено по Blender: `pkg_zipfile_detect_subdir_or_none` сначала ищет
   манифест в корне; папка допускается, но корень — канонический вид.
   Плоский архив без обёртки.
2. В манифест внутри архива подставляется addon_version() (R2).
   В исходнике на диске — литерал 1.1.51, чтобы файл был читаемым.
   Если литерал разошёлся с bl_info — SystemExit с текстом, где оба значения.
3. Имя архива: d3unit-<version>.zip в тот же DIST.
4. Legacy-zip не пересобирается иначе; оба могут собираться за один вызов.
5. `--zip-only` и `-o/--out` продолжают работать вместе с `--extension`.
6. Внутри extension-zip НЕТ __pycache__, *.pyc, .git, ничего лишнего.

============ ЧАСТЬ 3. Новый тест: tests/addon_manifest_test.py ============

Без bpy, exit 0 = PASS, exit 1 = FAIL. Стиль — как у остальных тестов в
tests/: функции `test_*`, `main()` в конце, вывод PASS/FAIL построчно.
Образец устройства смотри в tests/addon_zip_test.py.

Проверки:

  T1 манифест на диске парсится tomllib и содержит все обязательные поля
     из ЧАСТИ 1, с правильными типами;
  T2 tagline: len <= 64 и последний символ — буква/цифра или ) ] };
  T3 permissions: только известные ключи (files, network, clipboard, camera,
     microphone), значения — непустые строки <= 64 символов без точки на конце;
  T4 license: каждый элемент начинается с "SPDX:";
  T5 id: isidentifier(), без "__", не начинается и не кончается на "_";
  T6 version на диске == addon_version() из build_addon.py;
  T7 сборка без флагов: zip содержит ровно 29 файлов, ни одного
     с "manifest" в имени (регрессия legacy — R3);
  T8 сборка с --extension: архив d3unit-1.1.51.zip существует, манифест
     лежит в корне, версия внутри == 1.1.51, файлов столько же +1;
  T9 extension-zip содержит vendor/d3tool/__init__.py (аддон не потерял
     библиотеку при смене раскладки);
  T10 tools/check_blender_manifest.py на собранном d3unit-<version>.zip
      возвращает exit 0. Если валидатор недоступен — FAIL, а не SKIP:
      «проверить не смог» не равно «всё хорошо».

Собирай во временную папку через `-o <tmp> --zip-only`, в репозиторий не пиши.

============ ЧАСТЬ 4. tests/addon_zip_test.py ============

Добавь в существующий тест одну проверку — что legacy-zip по-прежнему
распаковывается и импортируется (он это уже делает). НЕ переписывай его.
Если добавлять нечего — не трогай файл вовсе.

============ ПРОВЕРКА (выполни и приложи вывод) ============

    python3 tests/addon_manifest_test.py
    python3 tests/test_d3tool.py        # должно остаться 200/200, exit 0
    python3 tests/corpus_parity.py      # 84/84
    python3 tests/reverse_parity.py     # 84/84
    python3 tests/test_corpus.py        # 13/16 — ТРИ падения это НЕ твои,
                                        # задача 3 не входит в эту работу
    git checkout -- release/d3unit-addon/io_scene_d3unit.zip
    git status --short

Финальный `git status` должен показывать только: новый манифест, новый
тест, изменённый build_addon.py. Никаких бинарников.

============ ОТВЕТ ============

1. unified diff всех изменённых/новых файлов;
2. вывод addon_manifest_test.py (строки PASS/FAIL);
3. вывод пяти команд проверки — коротко, последние строки;
4. таблица «поле манифеста → почему такое значение»;
5. что осталось непроверенным и почему (настоящую установку в Blender 4.2
   ты не делал — так и напиши).
```

## Что уже лежит в репозитории к моменту прогона

Коммит `arena/b8b2c736-geo2011` (в `main` этого ещё нет):

- `tools/vendor/blender_ext.py` — валидатор Blender, 220 КБ, GPL-2.0-or-later,
  взят из `blender/blender` как есть, без правок. Обновляется
  `python3 tools/check_blender_manifest.py --refresh`.
- `tools/check_blender_manifest.py` — обёртка, `strict=True` зашит.

Если прогон идёт на `main` (`bc059d1`), этих файлов там нет: промт тогда
требует `T10` пропустить, а проверку манифеста сделать вручную через
`fetch_page`/`gh api`. Поэтому гонять эту задачу лучше с ветки, где файлы есть.

## Длина

~4900 символов. В лимит Qwen (262 144 контекста) укладывается с запасом.

## Как я приму

```bash
# 1. наложить diff
python3 tests/addon_manifest_test.py     # ждём exit 0
python3 release/build_addon.py -o /tmp/x --zip-only
python3 release/build_addon.py -o /tmp/x --zip-only --extension
# 2. разложить оба архива и сверить
python3 -c "import zipfile;z=zipfile.ZipFile('/tmp/x/io_scene_d3unit.zip');print(len(z.namelist()))"   # 29
python3 -c "import zipfile,tomllib;z=zipfile.ZipFile('/tmp/x/d3unit-1.1.51.zip');m=tomllib.loads(z.read('blender_manifest.toml').decode());print(m['version'], len(z.namelist()))"
# 3. полная приёмка
python3 tools/acceptance.py              # всё зелёное, test_corpus 13/16
```

Плюс главное: **прогнать через валидатор самого Blender**. Он вшит в
репозиторий (`tools/vendor/blender_ext.py`, чистый stdlib, bpy не нужен),
обёртка — `tools/check_blender_manifest.py`:

```bash
python3 tools/check_blender_manifest.py /tmp/x/d3unit-1.1.51.zip   # exit 0 = PASS
python3 tools/check_blender_manifest.py blender/io_scene_d3unit/blender_manifest.toml
```

`exit 0` и `PASS:` = манифест пройдёт установку в Blender 4.2. Обёртка уже
проверена: эталонный TOML даёт `PASS`, а `tagline` с точкой, `version = "1.1"`,
`id = "_d3unit"` и отсутствие `maintainer` дают `FAIL` с текстом ошибки
Blender.

## Красные флаги

| Что увижу | Вердикт |
|---|---|
| манифест попал в legacy-zip | нарушен R3 — отклонить |
| манифест лежит в подпапке `io_scene_d3unit/` | допустимо, но отклонить: не канонический вид |
| версия в архиве не совпадает с `bl_info` | отклонить (R2) |
| `license = "GPL"` без `SPDX:` | отклонить |
| `tagline` кончается точкой | отклонить, Blender такой манифест не примет |
| правки в `d3tool/` или существующих тестах | отклонить (R1) |
| `--extension` переименовывает legacy-zip | отклонить |
| в diff приехал `io_scene_d3unit.zip` | отклонить, нарушен R5 |
| тест проверяет только наличие файла, не содержимое | слабо: требую T7, T8, T9 |

## Приёмка у тебя в PowerShell

```powershell
cd C:\path\to\geo2011
git fetch origin
git switch --detach origin/main        # или ветка с tools/vendor
# наложить diff от Qwen, затем:
python tools\check_blender_manifest.py blender\io_scene_d3unit\blender_manifest.toml
python tests\addon_manifest_test.py
python tools\acceptance.py
git checkout -- release/d3unit-addon/io_scene_d3unit.zip
git status --short
```

`tools\acceptance.py` гоняет пять проверок и печатает `ИТОГ: всё зелёное`.
`test_corpus` там ждёт **13/16** — три падения относятся к задаче про `.ac`,
это не регрессия.
