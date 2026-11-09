# GTX 1080 Ti: что сломано, почему и как это закрыть

Карта **видна**, CUDA **доступна**, и всё равно падает. Это не драйвер и не
поломка студии. Виновата одна строка установщика, которая на любую видеокарту
кладёт один и тот же PyTorch.

## Симптом

В логе ComfyUI:

```
Total VRAM 11264 MB, total RAM ... GB
pytorch version: 2.8.0+cu128
RuntimeError: CUDA error: no kernel image is available for execution on the device
```

`no kernel image is available` — это буквально «в вызванной CUDA-программе нет
машинного кода для этой карты». При этом `torch.cuda.is_available()` отвечает
`True`: проверка «есть ли GPU» проходит, а исполнять нечего. Отличить по
`/api/status`: `gpu.vram_total` заполнен, а `torch_vram_total: 0` — torch ни разу
не выделял память.

## Почему

| Что нужно | 1080 Ti (Pascal, sm_61) |
|---|---|
| Ядра в wheel'е torch | `cu128` собран под `sm_70…sm_120`. Кода для `sm_61` внутри нет вовсе |
| bf16 | появился в Ampere (sm_80). На Pascal таких инструкций нет, PyTorch их не эмулирует |
| flash-attn | требует Ampere+, под sm_61 не собирается |
| xformers `memory_efficient_attention` | на sm_61 отсутствует или нестабилен |

Нужен wheel, внутри которого есть `sm_61` — линейка **cu126**, последняя с
`sm_50…sm_120`. Никакая настройка студии этого не заменяет.

## Что именно исправлено в коде

`installer\Install.ps1`, функция `InstallComfyDeps`. Две правки:

1. Индекс PyTorch выбирается по compute capability карты, а не по факту «GPU есть»:

   ```powershell
   $torchIndex = if (!$hasGpu) { '.../whl/cpu' }
                 elseif ($cc -gt 0 -and $cc -lt 7.5) { '.../whl/cu126' }
                 else { '.../whl/cu128' }
   ```

   `$cc` берётся из `nvidia-smi --query-gpu=compute_cap`, а если драйвер стар и
   такого поля не отдаёт — по имени карты (`GTX 10xx` → 6.1).

2. torch ставится не только когда падает `import torch`. На Pascal импорт как
   раз проходит, поэтому старая проверка никогда не переставляла wheel. Теперь
   проверяется `sm_61` в `torch.cuda.get_arch_list()` — сборка обязана содержать
   код для карты, а не просто импортироваться. Иначе переустановка студии снова
   кладёт cu128 поверх рабочего cu126.

Обе правки — в `bridge\pascal.py`. Рядом кладётся `.bak`, повторный прогон
ничего не меняет. Сам wheel `pascal.py` не ставит: этим занимается
`cuda_check.py fix-torch`, который уже умеет гасить студию, ставить cu126,
перебирать варианты и проверять ядро на живой карте. Двух реализаций нет.

## Как запустить

Двойной клик по **`bridge\CUDA-fix-1080Ti.cmd`**. Он делает:

1. `pascal.py --auto` — находит студию (`D:\AstraForge`, `C:\AstraForge`, либо
   откуда запущено), исправляет `installer\Install.ps1`, печатает карту и
   индекс, который ей положен;
2. `CUDA-fix-torch.cmd` — ставит torch/torchvision/torchaudio с индекса `cu126`
   и проверяет результат матричным умножением на GPU;
3. `setx` для пользователя:
   - `PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True` — меньше фрагментации
     на 11 ГБ;
   - `TORCH_CUDA_ARCH_LIST=6.1` — под это должны собираться C++-расширения.

Проверить, что получилось, ничего не меняя:

```cmd
py -3 bridge\cuda_check.py check --studio D:\AstraForge
```

В отчёте (`bridge\cuda-report.txt`) нужна строка arch list с `sm_61` и вердикт
`[OK]`.

## Чего на этой карте не будет

- **Pixal3D / Trellis2.** `installer\pixal3d\pixal3d.ps1` жёстко требует
  `flash_attn-2.8.3` и `+cu128`, а веса идут в bf16
  (`ss_flow_img_dit_1_3B_64_bf16`, `slat_flow_img2shape_dit_1_3B_*_bf16`).
  flash-attn под sm_61 не собирается, bf16 на Pascal не исполняется. Это не
  «докрутить флаг», это другой класс карт. Для мешей остаётся путь Hunyuan3D.
- **SDXL на 1024 px.** 11 ГБ не хватает: держи 832–896 px, иначе OOM или
  выгрузка в RAM и минуты на картинку.
- **xformers-ускорение.** Если `ComfyUI-MVAdapter` упадёт на
  `memory_efficient_attention` — это отдельная правка в
  `mvadapter/models/attention_processor.py` (откат на `sdpa`). Скажи, сделаю.

## Если переустанавливаешь студию с нуля

Запусти `CUDA-fix-1080Ti.cmd` **после** установщика: он исправит `Install.ps1`
и поставит правильный torch. Либо исправь установщик заранее — тогда он сам
поставит cu126:

```cmd
py -3 bridge\pascal.py --patch-installer "D:\AstraForge\installer\Install.ps1"
```
