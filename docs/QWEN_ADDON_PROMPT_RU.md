# Промт для прогона на Qwen: дожать Blender-аддон

## Модель и как запускать

```powershell
opencode --model opencode/qwen3.6-plus-free
```

`qwen3.6-plus-free` — **бесплатная** в Zen, 262 144 контекста / 65 536 выхода, tools есть.
Платная альтернатива, если free кончится: `opencode/qwen3.8-flash` ($0.15 / $0.47 за 1M,
1M контекста — но тогда обязательно поставь `limit` в конфиге).

Лимит для free-модели в `opencode.json` (добавить в блок `opencode.models`):

```json
{
  "opencode": {
    "models": {
      "qwen3.6-plus-free": {
        "name": "Qwen 3.6 Plus Free (Zen)",
        "limit": { "context": 131072, "output": 32768 }
      }
    }
  }
}
```

## Что прогонять и в каком порядке

Пять задач по аддону, все — **без Blender** (чистый Python, stdlib). Порядок важен:
каждая следующая опирается на предыдущую.

| # | Задача | Почему в этом порядке |
|---|---|---|
| 1 | Гард `__main__` в `test_d3tool.py` | без него не видно, что тесты вообще идут |
| 2 | Морфы через `extras`, не по имени bufferView | самая ценная: тихая потеря анимации |
| 3 | Мультиматериал: все слоты и все примитивы | самая мутная: правка в парсере glTF |
| 4 | `blender_manifest.toml` для Blender 4.2+ | изолирована, легко проверить |
| 5 | Операторы vdpack/char в аддоне | самая рискованная: требует bpy, тестировать нечем |

**Задачи 1–4 прогоняй на Qwen. Задачу 5 — нет**: она добавляет bpy-операторы,
а Blender в песочнице не запускается (`libXrender.so.1`), значит проверить её нечем.
Для неё нужен человек с Blender или отдельный прогон с `addon_import_test.py` как
единственной защитой.

## Промт (копировать целиком)

```
Репозиторий: https://github.com/travagant/geo2011, ветка main, HEAD bc059d1.
Python 3, только stdlib. Blender НЕ нужен — все пять задач проверяются без bpy.

Что это: d3tool/ — чистый Python (читает/пишет .g/.a/.scene/.ac/.t, генерирует glTF).
blender/io_scene_d3unit/ — Blender-аддон, тонкая оболочка над d3tool.

Инвариант: байтовый паритет с эталонами. Ни одна правка не должна его сломать.
Сейчас на HEAD: corpus_parity 84/84, reverse_parity 84/84, test_d3tool 200/200.

ЗАДАЧА 1 (начни с неё). tests/test_d3tool.py содержит def main(), но нет блока
`if __name__ == "__main__": sys.exit(main())` — поэтому `python3 tests/test_d3tool.py`
печатает ноль тестов и выходит с кодом 0, хотя README обещает 200 проверок.
Добавь гард. Докажи: до правки — "0 печатается, exit 0", после — "200/200, exit 0".
Файл tests/ менять можно ТОЛЬКО в этой задаче и только блоком запуска.

ЗАДАЧА 2. Морфы (shape keys) теряются молча. d3tool/gltf.py, функция
_morph_targets_raw (около строки 297) находит морф-стрим только по имени
bufferView вида "morph_<stream>_<k>" (строка ~323). Blender при пересохранении
glTF эти имена выбрасывает — значит после импорта в Blender и экспорта обратно
морф-анимация исчезает, а ошибки нет.
Проверка факта: Empire/Cleric, cloth-меш, 25 таргетов; Empire/Hierophant,
морф-база робы.
Нужно: путь, не зависящий от имён bufferView. Используй extras меша
(например d3unit_morph_stream) и/или порядок таргетов, чтобы восстановить
имя стрима. Старое поведение (по имени) сохранить как первый вариант.
Тест: импорт → пересохранение без имён → экспорт → число таргетов и имя
стрима те же. Файл теста — новый, tests/test_morph_extras.py.

ЗАДАЧА 3. Мультиматериальные части. Формат .g умеет material1_* (вторая
материальная строка того же блока атрибутов). В корпусе такие части есть:
Neutrals/DarkServant/character_neutrals_darkservant_lod (4 записи material1_),
Neutrals/Death. Сейчас:
 - d3tool/gltf.py строки 252, 415, 478 читают только mesh["primitives"][0] —
   вторая примитива меша с двумя материалами исчезает молча;
 - blender/io_scene_d3unit/exporter.py строки ~142 предупреждает и берёт
   material_slots[0].
Нужно: читать и писать все примитивы и все слоты; предупреждение оставить
только когда часть действительно не может нести второй материал.
Тест: DarkServant, число частей и materialN_diffuse после экспорта совпадают
с донором. Файл — tests/test_multimaterial.py.

ЗАДАЧА 4. Blender 4.2+ Extensions: нет blender_manifest.toml.
Добавь blender/io_scene_d3unit/blender_manifest.toml (schema_version 1.0.0,
id d3unit, version из bl_info = 1.1.51, blender_version_min 4.2.0, license,
теги). Добавь в release/build_addon.py флаг --extension, который собирает
extension-zip отдельно от legacy. Legacy zip должен остаться рабочим:
python3 release/build_addon.py по-прежнему пишет
release/d3unit-addon/io_scene_d3unit.zip.
Проверка без Blender: zip открывается, внутри blender_manifest.toml и все
модули аддона; tests/addon_zip_test.py проходит.

ЗАДАЧА 5 (НЕ ДЕЛАЙ, если не можешь проверить). Операторы «Pack .vdpack» и
«Unit profile (.char)» в аддоне. Требует bpy: без Blender проверить нечем.
Если берёшься — единственная защита tests/addon_import_test.py (импорт со
стаб-bpy). Если не уверен — пропусти и напиши, что пропустил.

ГРАНИЦЫ.
- Не менять Empire/, Neutrals/, Profiles/, Blender/ — это данные.
- Не менять существующие тесты, кроме гарда в задаче 1.
- Не ослаблять KNOWN в tests/test_corpus.py.
- Коммиты атомарные, сообщения по-русски.
- CHANGELOG.md обновить, версию в bl_info поднять до (1, 1, 52).

ПРОВЕРКА перед ответом (запускать без пайпа, иначе код выхода теряется):
  python3 tests/test_d3tool.py
  python3 tests/corpus_parity.py
  python3 tests/reverse_parity.py
  python3 tests/addon_import_test.py
  python3 tests/addon_zip_test.py
Ожидаемо: 200/200 и exit 0, 84/84, 84/84, PASS, PASS.
test_corpus.py сейчас 13/16 — это ИЗВЕСТНОЕ состояние (задача про .ac-донора,
не твоя), его не чинить и не упоминать как регрессию.

ОТВЕТ: по каждой задаче — что сделано, diff, числа до/после, что не проверено.
Не пересказывай код целиком.
```

## Длина

~4 300 символов. В лимит Zro/Coarena укладывается, но для Qwen free лучше
разбить на два прогона: 1–2 отдельно, 3–4 отдельно. Так каждый прогон короче
и ошибку проще локализовать.

## Как принимать работу

```powershell
git fetch origin
git worktree add --detach C:\tmp\qwen origin/main
cd C:\tmp\qwen
python tests\test_d3tool.py        # ждём 200/200, exit 0
python tests\corpus_parity.py      # 84/84
python tests\reverse_parity.py     # 84/84
python tests\addon_import_test.py  # PASS
python tests\addon_zip_test.py     # PASS
```

Плюс новые файлы `tests/test_morph_extras.py` и `tests/test_multimaterial.py` —
если Qwen их написал, прогнать отдельно.

## Ловушки

| Симптом | Что значит |
|---|---|
| `test_d3tool.py` печатает 0 тестов | задача 1 не сделана |
| `corpus_parity` 83/84 | сломали verbatim `.t` — откатывать |
| `reverse_parity` 83/84 | сломали обратный экспорт |
| Qwen правит `Empire/` или `Neutrals/` | отклонить целиком |
| Qwen добавил запись в `KNOWN` | отклонить: это ослабление теста |
| морфы «работают», но тест не добавлен | отклонить: нечем доказать |
