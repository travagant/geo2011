# Что прогнал DeepSeek: проверка `origin/main` после батла

Проверено 2026-11-19 на `origin/main` = `bc059d1` (worktree в `/tmp`, репозиторий не менялся).
Все прогоны — `python3`, только stdlib, корпус на месте.

## Коммиты, которых не было в базе `53f1c2e`

| Коммит | Время | Сообщение | Файлы |
|---|---|---|---|
| `1ee3d93` | 12:31 | test: run the self-test suite as a script | `tests/test_d3tool.py`, `d3tool/char.py`, `d3tool/texture.py` |
| `6e28a8a` | 12:45 | texture: read OrcKing's 24-bit header over a 32-bit payload | `d3tool/cli.py`, `d3tool/texture.py`, `tests/test_corpus.py`, `tests/test_d3tool.py` |
| `aa4f4ef` | 13:13 | `12312` (без описания) | `blender/` (importer/exporter/unitprops), `d3tool/anim.py`, `d3tool/gltf.py` |
| `bc059d1` | 14:17 | `2342` (без описания) | `d3tool/gltf.py` |

## Результаты прогонов

| Проверка | База `53f1c2e` | `origin/main` `bc059d1` | Вердикт |
|---|---|---|---|
| `tests/test_d3tool.py` как скрипт | 0 строк, exit 0 | **200/200 passed**, exit 0 | ✅ задача 1 закрыта |
| exit code при искусственном падении | 0 | **1** | ✅ критерий выполнен |
| `tests/corpus_parity.py` | 84/84 | **84/84 EXACT** | ✅ не сломано |
| `tests/reverse_parity.py` | 83/84 | **84/84 EXACT** | ✅ задача 2 закрыта |
| `tests/test_corpus.py` | 13/16 | **13/16** | ❌ задача 3 не сделана |
| `tests/anim_audit.py` | 94/94, 0 проблем | **94/94, 0 проблем** | ✅ |
| `tests/scenarios.py` | 4/4 | **4/4** | ✅ |
| `tests/addon_import_test.py` | PASS (1,1,51) | **PASS (1,1,51)** | ✅ версия не поднята |

## Задача 3 не решена

Причина та же, что в базе. `test_reverse_export_every_gltf` ждёт `character_empire_leader-archmage_set1.ac`
(имя по glTF), а `_export` кладёт `character_empire_leader-archmage.ac` — имя донора, взятое
из `_unit_file_for` (`d3tool/cli.py:622`). Фактический вывод экспорта:

```
character_empire_leader-archmage.ac   3801
character_empire_leader-archmage_set1.scene, _set1.g, _baseanims.a
```

Плюс два теста падают не по вине модели, а по той же причине:
`test_leader_set_animation_adopts_sibling_donor` и `test_renamed_gltf_exports_battle_ready`
ловят `FileNotFoundError` на ожидаемом `<base>_set1.ac`.

Обе проверки (FAIL и ERROR) были и до коммитов DeepSeek, то есть это не регрессия — просто
третья задача не взята.

## Коммиты 3 и 4 — без описания

`aa4f4ef` («12312») и `bc059d1` («2342») не соответствуют ни одной из трёх задач батла:

- `aa4f4ef` добавляет в аддон `_gltf_document` и `_morph_streams` (перенос морф-стримов в Blender),
  правит `d3tool/anim.py` и `gltf.py`;
- `bc059d1` вводит `_morph_trailing` — пересборку trailing-блока морф-таргетов с `strict=True`
  для мульти-стрим путей.

Ни один из них не приложен к задаче из `docs/PROMPT_BATTLE_COARENA_ONE_SHOT.md`, тестов на них нет,
сообщения не объясняют изменение. Это похоже на побочную работу, а не на ответ по батлу.

## Что ещё стоит знать

- `test_corpus.py` в `6e28a8a` не добавил новый тест, а **удалил** известное исключение
  (`unparsable_dds` для OrcKing). Это честно: `parse_dds` теперь читает файл (gm_format 5, 256×1024),
  и `test_import_every_asset` — PASS.
- В `tests/test_d3tool.py` появился один новый тест —
  `test_malformed_24bit_dds_header_with_a_32bit_payload` (199 → 200).
- Версия аддона осталась `(1, 1, 51)`.

## Итог по батлу

DeepSeek закрыл 2 из 3 задач, обе — с корректным обоснованием и паритетом.
Третья (выбор донора `.ac` по общему стему для `_setN`) не сделана: правка одна, в
`d3tool/cli.py` рядом с `out_ac`, — писать `<base>.ac`, когда `_unit_file_for` вернул файл
с другим стемом, но `<base>.ac` не существует. Тест при этом ждёт именно имя по glTF,
а `.ac` должен остаться байт-в-байт равным донору.
