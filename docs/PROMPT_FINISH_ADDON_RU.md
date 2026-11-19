Ты — senior Python/Blender-разработчик. Доведи до релиза Blender-аддон из репозитория https://github.com/travagant/geo2011 (ветка main, текущая версия аддона 1.1.51-alpha).

Что это: инструменты для Disciples III (движок GM). `d3tool/` — чистый Python 3 (только stdlib): читает и пишет .g/.a/.scene/.ac/.t и генерирует dis3tool-совместимый glTF. `blender/io_scene_d3unit/` — аддон «Import/Export Disciples III Unit» (импорт юнита в арматуру с анимацией, экспорт обратно). `tests/` — 199 unit-тестов, побайтовые паритеты и Blender-тесты.

Главный инвариант — байтовый паритет с эталонами dis3tool. Сейчас: `tests/corpus_parity.py` 84/84 EXACT, `tests/reverse_parity.py` 83/84 EXACT, `tests/test_d3tool.py` 199/199. Любая правка обязана это сохранить; регрессия = провал задачи.

Сначала прочитай README.md, docs/FORMATS.md, CHANGELOG.md и прогони:
  python3 tests/test_d3tool.py
  python3 tests/corpus_parity.py
  python3 tests/reverse_parity.py
(Blender-тесты требуют bpy: python3 tests/blender_addon_test.py)

Задачи — по одной, каждая с тестом:

1. `tests/test_d3tool.py` содержит main(), но запуск `python3 tests/test_d3tool.py` печатает 0 тестов и выходит с кодом 0: нет блока `if __name__ == "__main__": sys.exit(main())`, хотя README этот запуск обещает. Починить и доказать, что печатается 199/199.

2. Морфы (shape keys) теряются. d3tool находит морф-стримы только по именам bufferView вида `morph_<stream>_<k>`, а Blender при пересохранении glTF эти имена выбрасывает — проверено: у Cleric cloth (25 target’ов) после снятия имён morph_name становится пустым, то есть морф-анимация теряется молча. Нужен путь, не зависящий от имён bufferView (extras/d3unit-метаданные и/или чтение shape_keys из Blender), чтобы импорт → Blender → экспорт сохранял морфы. Тесты: Cleric (cloth) и Hierophant (morph-база робы).

3. Мультиматериальные части. Экспорт предупреждает и берёт только `material_slots[0]` («a .g part carries one material»), а формат умеет `material1_*`: в корпусе 6 таких частей (например щит и тело в `Neutrals/OrcKing/*_lod.g`, и их `gobj` в `.scene` повторяют `material1_diffuse`). Плюс `load_gltf` читает только `primitives[0]` — вторая примитива Blender-меша с двумя материалами молча исчезает. Экспортировать все слоты и примитивы; предупреждение оставить только когда это действительно невозможно.

4. Путь «выпустить мод» из Blender. d3tool умеет `vdpack pack/validate` и `char set-id/validate`, но в аддоне этих команд нет. Добавить операторы: «Pack .vdpack» (папка юнита → архив с `index`) и «Unit profile (.char)», чтобы моддер не открывал терминал.

5. Blender 4.2+ Extensions: нет `blender_manifest.toml`. Добавить манифест и сборку extension-zip (флаг у `release/build_addon.py` или отдельный скрипт), legacy zip сохранить рабочим.

6. Добить известное ограничение: `Neutrals/OrcKing/weapon_neutrals_orcking_sword.dds/.t` — 24-битный DDS-заголовок над 32-битным payload, `parse_dds` отказывается, `.t` копируется как есть, из-за чего `reverse_parity` даёт DIFF Neutrals/OrcKing (83/84). Принять и починить такой файл при конвертации, довести паритет до 84/84.

Требования: только stdlib, без новых зависимостей; не менять данные `Empire/`, `Neutrals/`, `Profiles/`; не переписывать историю git, коммиты атомарные; обновить CHANGELOG.md и README.md; поднять версию в `bl_info`.

Готово, когда: все тесты зелёные, паритет 84/84 forward и 84/84 reverse, `python3 release/build_addon.py` собирает zip, новые тесты (морфы, мультиматериал, vdpack из Blender) проходят. В конце отчёт: что сделано, числа до/после, что осталось и почему.
