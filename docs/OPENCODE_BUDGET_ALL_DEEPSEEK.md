# Единый бюджетный режим для всех DeepSeek-пулов

Один файл `opencode.json`, в котором все пулы DeepSeek приведены к одному профилю
расхода. Плюс два места, где лимит ставится **вне** файла — Cloudflare и NVIDIA,
там его иначе не урезать.

## Профиль (одинаковый для всех пулов)

| Параметр | Значение | Почему именно так |
|---|---|---|
| `limit.context` | **131072** | 128k — рабочий размер диффа + два файла `d3tool`; при 1M ход раздувается до 140k и прогон съедает квоту |
| `limit.output` | **16384** | патч на 600 строк укладывается; 393k у DeepSeek — это потолок API, а не полезный объём |
| `compaction.auto` | `true` | без него сессия падает на переполнении |
| `compaction.background` | **`false`** | саммари пишется заранее и **платится, даже если не понадобился** — на free это чистые потери |
| `compaction.keep.tokens` | **8000** | дефолт 15000 оставляет половину бюджета под историю |
| `compaction.buffer` | **`"20%"`** | сжатие на ~105k вместо ~111k; запас на один большой вывод |
| `tool_output.max_lines` | **600** | главный источник раздувания: вывод тестов |
| `tool_output.max_bytes` | **16000** | то же в байтах |
| `agents.compaction.model` | Zen free (если есть) | саммари — полный контекст на выходе, самый дорогой запрос сессии |
| `steps` у агента `build` | **60** | предохранитель: прогон не уйдёт в бесконечный цикл |

## Файл целиком

```json
{
  "$schema": "https://opencode.ai/config.json",
  "provider": {
    "coarena": {
      "npm": "@ai-sdk/openai-compatible",
      "name": "Coarena",
      "options": {
        "baseURL": "https://coarena.ai/api/v1",
        "apiKey": "КЛЮЧ_COARENA"
      },
      "models": {
        "deepseek-v4-1-flash-1017": {
          "name": "DeepSeek V4.1 Flash (Coarena)",
          "limit": { "context": 131072, "output": 16384 }
        }
      }
    },
    "zro": {
      "npm": "@ai-sdk/openai-compatible",
      "name": "Zro",
      "options": {
        "baseURL": "https://zro.moonmath.ai/v1",
        "apiKey": "КЛЮЧ_ZRO"
      },
      "models": {
        "deepseek-v4.1-flash": {
          "name": "DeepSeek V4.1 Flash (Zro)",
          "limit": { "context": 131072, "output": 16384 }
        }
      }
    },
    "nvidia": {
      "npm": "@ai-sdk/openai-compatible",
      "name": "NVIDIA NIM",
      "options": {
        "baseURL": "https://integrate.api.nvidia.com/v1",
        "apiKey": "nvapi-КЛЮЧ_NVIDIA"
      },
      "models": {
        "deepseek-ai/deepseek-v4.1-flash": {
          "name": "DeepSeek V4.1 Flash (NVIDIA)",
          "limit": { "context": 131072, "output": 16384 }
        }
      }
    },
    "deepseek": {
      "name": "DeepSeek API",
      "models": {
        "deepseek-v4-flash": {
          "name": "DeepSeek V4.1 Flash (официальный)",
          "limit": { "context": 131072, "output": 16384 }
        },
        "deepseek-v4-pro": {
          "name": "DeepSeek V4 Pro",
          "limit": { "context": 131072, "output": 16384 }
        }
      }
    }
  },
  "model": "coarena/deepseek-v4-1-flash-1017",
  "compaction": {
    "auto": true,
    "background": false,
    "keep": { "tokens": 8000 },
    "buffer": "20%"
  },
  "tool_output": {
    "max_lines": 600,
    "max_bytes": 16000
  },
  "agents": {
    "build": { "steps": 60 }
  }
}
```

Если Zen не подключён — блок `agents` оставляй только со `steps`.
Если не подключён какой-то пул — удаляй его блок целиком, остальное не трогай.

## Id моделей: у каждого пула свой

Это первая причина `404` при смене пула — одна модель, четыре разных имени:

| Пул | id в запросе | Где задаётся |
|---|---|---|
| Coarena | `deepseek-v4-1-flash-1017` | в `opencode.json` |
| Zro | `deepseek-v4.1-flash` | в `opencode.json` |
| NVIDIA NIM | `deepseek-ai/deepseek-v4.1-flash` | в `opencode.json` |
| Официальный DeepSeek | `deepseek-v4-flash` | в каталоге, только `limit` |
| Ollama Cloud | `deepseek-v4.1-flash` | в каталоге, только `limit` |
| OpenCode Zen | `deepseek-v4.1-flash-free` | через `/connect` |

## Два пула, которые этот файл не урезает

**Cloudflare Workers AI.** У него свой id `@cf/deepseek-ai/deepseek-v4.1-flash-1017`
и лимит считается в «нейронах», а не в токенах. Контекст там не задаётся конфигом —
пул подходит только для коротких проверок, не для прогона по репозиторию.

**NVIDIA NIM.** `limit.output` в файле помогает от обрыва стрима, но дневной лимит
10 000 запросов и 40 rpm у него на стороне сервиса. Урезать их нельзя — только не
жечь зря, то есть тот же профиль плюс `steps`.

## Проверка, что режим применился

```powershell
$p="$env:USERPROFILE\.config\opencode\opencode.json"
python3 -m json.tool $p > $null; if ($?) { "JSON ок" }
(Get-Content $p -Raw | ConvertFrom-Json).compaction
```

Должно показать `background: False`, `keep.tokens: 8000`, `buffer: 20%`.

```powershell
opencode models | Select-String "deepseek"
```

В списке — только те пулы, которые реально настроены. Если пула нет, значит ключ
не задан: OpenCode не показывает модели провайдера без креденшела.

## Что это даёт в токенах

Оценка на прогоне задачи 3 (25 ходов, промт 1797 символов):

| Режим | Средний контекст | Вход за прогон | Zro free хватит? |
|---|---|---|---|
| дефолт 1M, без компакции | ~140k | ~3,5M | нет, 5 минут |
| 128k, дефолтная компакция 15% | ~70k | ~1,8M | впритык |
| **этот профиль** | ~45k | **~1,2M** | да |

## Если всё равно мало

Добавляй по одному, не всё сразу — каждое ухудшает качество:

| Рычаг | Как | Что теряешь |
|---|---|---|
| `context` 96k | в каждой модели | чаще сжатие, риск потерять начало диффа |
| `steps` 40 | `agents.build.steps` | агент может не дойти до последнего теста |
| reasoning `low` | `"options": {"reasoning_effort": "low"}` в модели | короче выход, грубее правки |
| `tool_output` 300 строк | `max_lines` | не увидишь хвост падения теста |
| отключить фоновые задачи | `agents.title.model` на Zen free | заголовки сессий на платном пуле |

Ниже 96k контекста для этой задачи не опускайся: промт (~1.8k символов) плюс
два файла `d3tool` дают ~40k, и сжатие начнёт резать по живому.
