# Blender-аддон импорта/экспорта юнитов Disciples III

Порт старого плагина 3ds Max (`geo2011.dle`) на Python/Blender.
Спецификация форматов: [`../docs/FORMATS.md`](../docs/FORMATS.md).

## Установка

1. Скопируйте каталог `io_scene_d3unit` в каталог аддонов Blender
   (например `~/.config/blender/4.2/scripts/addons/`) или упакуйте в zip:
   ```bash
   cd blender
   zip -r io_scene_d3unit.zip io_scene_d3unit -x "*__pycache__*"
   ```
   и установите через `Edit > Preferences > Add-ons > Install...`.
2. Включите «Import-Export: Disciples III Unit (geo2011…)».

## Использование

* `File > Import > Disciples III Unit (.g)` — выберите `*.g` (или `*.scene`);
  рядом должны лежать `*.a`, `*.ac`, `*.scene`, `*.t` (одинаковый корень
  имени). Импортируются: арматура со скелетом (27 костей), по меш-объекту
  на секцию (тело + оружие) с весами, UV, сглаживанием, материалом и
  распакованной текстурой `.t`, экшен с полным 301 кадром (LINEAR),
  маркеры клипов Idle/Attack/… из `.ac`.
* `File > Export > Disciples III Unit (.g)` — активным объектом должна быть
  импортированная арматура. Пишутся `*.g`, `*.a` (из поз в кадрах сцены),
  копируется `*.ac`, обновляется `*.scene`. Экспорт без правок
  воспроизводит `.g`/`.a` байт-в-байт.

Требования: Blender 3.0+ (тестировалось на API 3.x/4.x/5.x; используется
`mesh.normals_split_custom_set`, `image.pixels.foreach_set`,
`Principled BSDF`).

## Быстрая проверка без Blender

```bash
python3 ../tests/test_pipeline.py
```

(proof: ключ↔basis round-trip, пересчёт bbox, байт-идентичная пересборка
`.g` и `.a` на образце `character_empire_inquisitor`).

## Структура

```
io_scene_d3unit/
  __init__.py   операторы импорта/экспорта, построение сцены Blender
  g3.py         .g reader/writer (байт-точная модель)
  a3.py         .a reader/writer
  t3.py         .t (DXT1/3/5 + mips) decoder/encoder
  scene.py      парсеры текстовых .scene / .ac
  unit.py       сборка юнита, математика 4x4/осей, пересчёт секций
```
