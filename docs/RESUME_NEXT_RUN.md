# Продолжение прогона DeepSeek (следующий день)

Состояние на 2026-11-19 после `origin/main = bc059d1`:

| Задача батла | Статус |
|---|---|
| 1. Гард `__main__` в `tests/test_d3tool.py` | ✅ закрыта (`1ee3d93`), 200/200 |
| 2. OrcKing 24-bit `.dds` + verbatim `.t` | ✅ закрыта (`6e28a8a`), reverse_parity 84/84 |
| 3. Донор `.ac`/`.scene` по общему стему для `_setN` | ❌ не сделана, test_corpus 13/16 |

**Завтра нужен только промт по задаче 3.** Задачи 1–2 повторно не давать — они уже в `main`,
модель начнёт «чинить» рабочее.

---

## Промт (копировать целиком)

```
Репозиторий: https://github.com/travagant/geo2011 (ветка main, HEAD bc059d1).
Python 3, только stdlib, Blender не нужен.

ЗАДАЧА. `python3 tests/test_corpus.py` → 13/16. Три падения, корень один.

Факт, `d3tool/cli.py`, функция `_export`, около строки 863:

    src_ac = _unit_file_for(src_dir, base, ".ac")
    out_ac = os.path.join(out_dir, os.path.basename(src_ac)
                          if os.path.isfile(src_ac) else base + ".ac")

Для Empire/Leader-Archmage/character_empire_leader-archmage_set1.gltf
`_unit_file_for` возвращает character_empire_leader-archmage.ac (единственный
`.ac` в папке, стем без `_setN`). Экспорт кладёт `.ac` с именем донора, а тесты
ждут имя по glTF: character_empire_leader-archmage_set1.ac.

Падают:
- FAIL test_reverse_export_every_gltf: «.ac was not produced»
- ERROR test_leader_set_animation_adopts_sibling_donor: FileNotFoundError ..._set1.ac
- ERROR test_renamed_gltf_exports_battle_ready: FileNotFoundError out/angel_edit.ac

Нужно правило выбора ИМЕНИ выходного `.ac`. Если донор найден по общему стему,
но <base>.ac не существует — писать <base>.ac (имя по glTF), а содержимое
оставить байт-в-байт как у донора. Не ломать ~45 юнитов, где `.ac` уже назван
как glTF.

Второй ERROR важен: test_renamed_gltf_exports_battle_ready переименовывает glTF
(angel_edit.gltf) и ждёт angel_edit.ac — правило должно работать и для
произвольного переименования, а не только для `_setN`.

Требования: только stdlib; не менять Empire/, Neutrals/, Profiles/; сохранять
байтовый паритет; не менять тесты (правки только в d3tool/).

Критерий: test_corpus.py 16/16, reverse_parity.py 84/84, corpus_parity.py 84/84,
test_d3tool.py 200/200 и exit 0.

Ответ: корневая причина, минимальный unified diff, команда проверки, список
изменённых файлов и что осталось непроверенным.
```

Длина ~1900 символов — в лимит укладывается.

---

## Как проверить ответ (у себя)

```powershell
git fetch origin
git log --oneline -3 origin/main
git worktree add --detach C:\tmp\chk origin/main
cd C:\tmp\chk
python3 tests\test_corpus.py        # ждём 16/16
python3 tests\reverse_parity.py     # ждём 84/84
python3 tests\corpus_parity.py      # ждём 84/84
python3 tests\test_d3tool.py        # ждём 200/200, exit 0
```

Порядок важен: `test_corpus` — цель, остальные три — защита от регрессии.

## Что прислать для приёмки

1. Имя модели и номер промта (например: `DeepSeek V4.1 Flash, задача 3`).
2. Diff текстом, не скриншотом.
3. Вывод четырёх тестов (последние строки summary).
4. Список изменённых файлов.

## Ловушки, если сорвётся

| Симптом | Что проверить |
|---|---|
| `reverse_parity` 83/84 | правка снова трогает verbatim `.t` — откатывать |
| `test_corpus` 14–15/16 | правило покрыло `_setN`, но не переименование (`angel_edit`) |
| Модель правит `tests/` | отклонить: тесты в этой задаче — спецификация |
| Модель добавляет запись в `KNOWN` | отклонить: это ослабление теста, а не фикс |

## Лимиты

- Coarena free: 30 req/min, 2 параллельно, дневной лимит — сброс 00:00 UTC = **01:00 по Варшаве**.
- OpenCode Zen free: лимит на аккаунт, время сброса не публикуется.
- NVIDIA NIM: 40 rpm / 10 000 в день, сброс в 00:00 UTC.
