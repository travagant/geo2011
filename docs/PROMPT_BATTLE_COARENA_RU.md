Ты — сильный инженер-отладчик. Репозиторий: https://github.com/travagant/geo2011 — Python 3, только stdlib, Blender не нужен. Это инструменты для Disciples III: `d3tool/` читает/пишет .g/.a/.scene/.ac/.t и glTF, `tests/` — проверки. Главный инвариант — побайтовое совпадение с эталонами (`tests/corpus_parity.py`, `tests/reverse_parity.py`).

Ниже три РЕАЛЬНЫХ дефекта, воспроизведённых мной на текущем HEAD. Ответь одним сообщением: для каждого — корневая причина, минимальный патч (unified diff, только stdlib) и команда проверки.

ЗАДАЧА 1. `python3 tests/test_d3tool.py` не запускает тесты.
Симптом: печатает 0 строк, exit 0. В файле есть `def main():` (~строка 3023), которая прогоняет 199 тестов и печатает «N/199 passed», но нет блока `if __name__ == "__main__":`. README обещает именно такой запуск.
Критерий: печатается «199/199 passed», при падении exit code 1.

ЗАДАЧА 2. `tests/reverse_parity.py` даёт DIFF на Neutrals/OrcKing (83/84 вместо 84/84).
Симптом: `weapon_neutrals_orcking_sword.t` — 349583 байт в экспорте против 1398139 в оригинале.
Цепочка, которую я проследил:
1) `parse_dds` (`d3tool/texture.py:232`) на `weapon_neutrals_orcking_sword.dds` бросает `unsupported DDS RGB format (bits=24, rmask=0xff0000)` — 24-битный заголовок над 32-битным payload, так его записал dis3tool.
2) По фолбэку (`d3tool/cli.py` ~465–478) экспорт копирует лежащий рядом `weapon_neutrals_orcking_sword.t` «verbatim» и предупреждает об этом.
3) Но затем `_carry_material_textures` (`d3tool/cli.py` ~1204+) вызывает `_shrink_texture` (`d3tool/cli.py:1117`), та видит у этого `.t` `gm_format=4` (несжатый 32-бит; `_T_FMT_32BBP = {4, 5}` в `texture.py:70`), перекодирует в DXT5 и перезаписывает файл — то есть байт-в-байт копию, только что принятую решением «не трогать», портит.
Проверка моей гипотезы: если замокать `_shrink_texture` в no-op, расхождений не остаётся вообще (все 10 файлов юнита совпадают). Значит причина именно в этом шаге, а не в самом `.dds`.
Критерий: `python3 tests/reverse_parity.py` → 84/84; новый тест фиксирует, что `.t`, скопированный verbatim из-за нечитаемого `.dds`, не перекодируется.

ЗАДАЧА 3. `tests/test_corpus.py` → 15/16: падает `test_reverse_export_every_gltf` на `Empire/Leader-Archmage/character_empire_leader-archmage_set1.gltf` с сообщением `.ac was not produced`.
Факты: в папке лежит один `character_empire_leader-archmage.ac` (и `.g`, `.scene` с тем же стемом), а glTF-вариантов три: `..._set1/2/3.gltf`. Экспорт пишет `.ac` от имени glTF, получается `..._set1.ac`, которого нет; сам же экспорт пишет: «character_empire_leader-archmage_set1.scene references character_empire_leader-archmage_set1.ac, which was not written».
Нужно: правило выбирать донора `.ac`/`.scene` по общему стему (`_setN` — вариант), не ломая ~45 юнитов, где `.ac` уже называется как glTF.
Критерий: test_corpus 16/16 и reverse_parity 84/84.

Требования: только stdlib; не менять `Empire/`, `Neutrals/`, `Profiles/`; сохранять байтовый паритет; не «улучшать» формат. Если считаешь, что причина другая — скажи прямо и покажи команду, которой это проверяется. В конце — список изменённых файлов и что осталось непроверенным.
