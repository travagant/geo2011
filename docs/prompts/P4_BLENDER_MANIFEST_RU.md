# Прогон: `blender_manifest.toml` для Blender 4.2+

Модель: `opencode --model opencode/qwen3.6-plus-free`

Задача изолированная: новый файл + флаг сборки. Проверяется без Blender —
распаковкой zip. Самая низкая цена ошибки из трёх оставшихся.

## Что уже есть в репозитории (важно агенту)

- `blender/io_scene_d3unit/` — 7 модулей, `bl_info["version"] = (1, 1, 51)`
- `release/build_addon.py` — собирает `release/d3unit-addon/io_scene_d3unit.zip`,
  список модулей в `ADDON_FILES`, версия читается из `bl_info` через `ast`
- `tests/addon_zip_test.py` — проверяет собранный zip (требует bpy, поэтому
  в песочнице падает — это норма, см. ниже)

## Промт (копировать целиком)

```
Репозиторий: https://github.com/travagant/geo2011, ветка main, HEAD bc059d1.
Python 3, только stdlib. Blender НЕ нужен: проверка — распаковка zip и ast-парсинг.

КОНТЕКСТ. Аддон лежит в blender/io_scene_d3unit/ (7 модулей), собирается
скриптом release/build_addon.py в release/d3unit-addon/io_scene_d3unit.zip.
Версия берётся из bl_info["version"] = (1, 1, 51) через ast, см. функцию
addon_version() в build_addon.py.

ПРОБЛЕМА. Начиная с Blender 4.2 аддон ставится как Extension и требует файл
blender_manifest.toml в корне пакета. Его нет — поэтому в 4.2+ аддон
устанавливается только legacy-путём.

ЗАДАЧА, две части.

ЧАСТЬ 1. Создать blender/io_scene_d3unit/blender_manifest.toml со схемой
Blender 4.2:
  schema_version = "1.0.0"
  id = "d3unit"
  version = "1.1.51"          # ровно то же, что bl_info["version"], строка
  name = "Disciples III Unit"
  tagline = не длиннее 64 символов, по-английски, про импорт/экспорт юнитов
  maintainer = "geo2011"
  type = "add-on"
  blender_version_min = "4.2.0"
  license = ["SPDX:GPL-3.0-or-later"]
  tags = ["Import-Export", "Game Engine"]   # до 2 тегов, из списка Blender
  [permissions]
  files = "Import and export unit files chosen by the user"
Не выдумывать поля, которых нет в схеме 1.0.0. Никаких network/files
разрешений, кроме files.

ЧАСТЬ 2. В release/build_addon.py добавить флаг --extension: он собирает
второй zip рядом с legacy, с тем же содержимым плюс blender_manifest.toml.
  python3 release/build_addon.py             # как сейчас: io_scene_d3unit.zip
  python3 release/build_addon.py --extension # ещё и d3unit-1.1.51.zip
Legacy-путь не ломать: без флага поведение и имя файла прежние, размер
и набор файлов те же.

Требования:
- версия в манифесте должна совпадать с bl_info, но НЕ дублироваться руками:
  читай её тем же addon_version(), что уже есть, и подставляй в toml;
- манифест копируется в staging наравне с модулями, значит добавь его в
  ADDON_FILES только если он должен попасть и в legacy zip. Он НЕ должен:
  Blender 4.2 legacy-установка на манифест не смотрит, а лишний файл меняет
  размер уже выпущенного zip. Значит отдельный список для extension-сборки.
- не менять d3tool/, tests/ (кроме случая, если addon_zip_test проверяет
  содержимое и падает — тогда добавь проверку манифеста отдельным тестом,
  существующий не трогай).

ПРОВЕРКА без Blender:
    python3 release/build_addon.py
    python3 -c "import zipfile;print(sorted(zipfile.ZipFile('release/d3unit-addon/io_scene_d3unit.zip').namelist()))"
    python3 release/build_addon.py --extension
    python3 -c "import zipfile,tomllib;z=zipfile.ZipFile('release/d3unit-addon/d3unit-1.1.51.zip');print([n for n in z.namelist() if 'manifest' in n]);print(tomllib.loads(z.read('io_scene_d3unit/blender_manifest.toml').decode()))"
Ожидаемо: legacy-zip без манифеста, 29 файлов (7 модулей + 21 файл d3tool +
README.txt), extension-zip с манифестом, toml парсится через tomllib,
version в нём = 1.1.51.

ВАЖНО: zip лежит в репозитории как артефакт релиза. Пересборка меняет его
размер даже без правок (251038 → 254933 байта: другие метки времени внутри
архива). Поэтому после проверки верни файл как было:
    git checkout -- release/d3unit-addon/io_scene_d3unit.zip
Иначе в diff попадёт бинарный артефакт, который к задаче не относится.

ЗАМЕЧАНИЕ про tests/addon_zip_test.py: он требует bpy и в песочнице падает
с ModuleNotFoundError — это ожидаемо, не считать провалом.

ОТВЕТ: unified diff, список полей манифеста, вывод двух сборок с размерами,
результат парсинга toml через tomllib, что осталось непроверенным
(реальную установку в Blender 4.2 ты не делал).
```

## Как я приму

1. Наложу diff, прогоню `tools/acceptance.py` — должно остаться зелёным.
2. Соберу оба zipa и проверю: legacy без манифеста, extension с ним.
3. Распарсю `blender_manifest.toml` через `tomllib` и сверю `version`
   с `bl_info`.
4. Проверю, что legacy-zip **не изменился** по составу файлов.

## Красные флаги

| Что увижу | Вердикт |
|---|---|
| манифест попал в legacy-zip | ломает выпущенный артефакт — отклонить |
| версия в toml не совпадает с `bl_info` | отклонить, должна читаться, а не дублироваться |
| поля вне схемы 1.0.0 | отклонить |
| `license = "GPL"` без SPDX | отклонить: Blender требует `SPDX:` |
| правки в `d3tool/` | отклонить, задача к нему не относится |
| `--extension` переименовывает legacy-zip | отклонить |
