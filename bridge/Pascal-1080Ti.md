# GTX 1080 Ti: что сломано, почему и как это закрыть

Карта видна, CUDA доступна, и всё равно не работает. В AstraForge 0.6.12 дело не
в драйвере и не в студии: установщик **объявляет такую карту отсутствующей**, а
всё, что он на неё ставит, всё равно собрано не под неё.

## Симптом

Лог ComfyUI или движка:

```
Total VRAM 11264 MB, total RAM ... GB
pytorch version: 2.8.0+cu128
RuntimeError: CUDA error: no kernel image is available for execution on the device
```

Или наоборот — тише и хуже: в окне установщика «Видеокарта NVIDIA GeForce GTX
1080 Ti не подходит для генерации», ставится облегчённый набор, а в
`runtime\ComfyUI\.venv` оказывается **torch для процессора**, хотя карта на месте.

## Почему

В `installer\Install.ps1` (0.6.12) две строки решают всё:

```powershell
if ($s.cc -and (... -lt 7.5)) { $s.gpuOld = $s.gpu; $s.gpu = $null; $s.vram = 0 }
$torchIndex = if ($hasGpu) { 'https://download.pytorch.org/whl/cu128' } else { '.../cpu' }
```

1. Всё, что ниже compute 7.5, выбрасывается из `$s.gpu` — установщик считает,
   что NVIDIA нет, и ставит CPU-сборку.
2. Даже если карту оставить, индекс `cu128` собран под `sm_70…sm_120`. Pascal —
   это `sm_61`: `torch.cuda.is_available()` отвечает `True`, карта в списке, а
   первое же ядро падает с `no kernel image is available`.

Плюс третье место: движок генерации (`installer\engine\engine.ps1`) ставит
**скачанный заранее файл** `torch-2.8.0+cu128` из `installer\manifest.json` — тот
же неподходящий wheel, только во втором venv.

Всё это не «настройка», а выбор файла. `cu126` — последняя линейка, в которой
есть `sm_50…sm_120`, поэтому на такой карте работает именно она.

## Что исправляет `pascal.py`

Шесть правок, каждая с именем в отчёте, каждая идемпотентна, рядом остаётся
`.bak`. Якоря подобраны так, что правка **не находит строку — она пропускается**
с пометкой «другая версия установщика», а не портит файл.

`installer\Install.ps1`:

| правка | было | стало |
|---|---|---|
| индекс PyTorch | `hasGpu` → `cu128` | по compute capability: `< 7.5` → `cu126`, иначе `cu128` |
| признак «torch стоит» | `import torch` прошёл | плюс `sm_61` в `torch.cuda.get_arch_list()` |
| файл движка | всегда `EngineTorchFile` (cu128) | на Pascal не берётся |
| установка из индекса | `torch torchvision torchaudio` | `torch==2.8.0 torchvision==0.23.0 torchaudio==2.8.0` + `--reinstall-package` |
| проверка карты | `$s.gpu = $null` (карта «старая») | карта остаётся, помечается `$s.gpuPascal`, в окне предупреждение |

`installer\engine\engine.ps1`: на Pascal движок ставит torch `2.8.0+cu126` из
индекса вместо скачанного cu128-файла.

Почему `--reinstall-package`: по PEP 440 `torch==2.8.0` **удовлетворяется**
уже стоящим `2.8.0+cu128`, и uv на такой запрос ничего не переставляет. Без
этого флага починка выглядела бы успешной и не меняла ничего.

Сам wheel `pascal.py` не ставит: этим занимается `cuda_check.py fix-torch`
(гасит студию, ставит cu126, проверяет ядром на карте). Реализация одна.

## Как запустить

Двойной клик по **`bridge\CUDA-fix-1080Ti.cmd`**:

1. `pascal.py --auto` — находит студию, правит установщик, печатает карту и
   индекс, который ей положен;
2. `CUDA-fix-torch.cmd` — ставит `torch 2.8.0+cu126` в ComfyUI студии и
   проверяет матричным умножением на GPU;
3. `setx` для пользователя: `PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True`
   (меньше фрагментации на 11 ГБ) и `TORCH_CUDA_ARCH_LIST=6.1`.

Проверить, ничего не меняя:

```cmd
py -3 bridge\cuda_check.py check --studio D:\AstraForge
```

В `bridge\cuda-report.txt` нужна строка arch list с `sm_61` и вердикт `[OK]`.

## Чего на этой карте не будет

- **Pixal3D / Trellis2.** Установщик сам не предлагает их ниже compute 8.0
  (`installer\pixal3d\pixal3d.ps1`), и это правильно: там `flash_attn-2.8.3` и
  bf16-веса. flash-attn под `sm_61` не собирается, bf16 на Pascal не исполняется.
  Для мешей остаётся Hunyuan3D.
- **SDXL на 1024 px.** 11 ГБ не хватает: 832–896 px, иначе OOM или выгрузка в
  RAM и минуты на картинку.
- **xformers-ускорение.** Если `ComfyUI-MVAdapter` упадёт на
  `memory_efficient_attention` — это отдельная правка
  (`mvadapter/models/attention_processor.py`, откат на `sdpa`). Скажи, сделаю.

## Если ставишь студию заново

Запусти `CUDA-fix-1080Ti.cmd` **после** установщика: он исправит оба файла и
поставит правильный torch. Либо исправь установщик заранее — тогда он сам
поставит cu126 с самого начала:

```cmd
py -3 bridge\pascal.py --studio D:\AstraForge --auto
```
