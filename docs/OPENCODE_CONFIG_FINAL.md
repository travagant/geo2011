# Финальный `opencode.json`: Coarena + Zro + Zen

Три пула, один бюджетный профиль. NVIDIA NIM и официальный DeepSeek API удалены —
их блоки из конфига выкинуты.

## Файл целиком

`C:\Users\Skynet\.config\opencode\opencode.json`:

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
    "opencode": {
      "name": "OpenCode Zen",
      "models": {
        "deepseek-v4.1-flash-free": {
          "name": "DeepSeek V4.1 Flash Free (Zen)",
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
    "build": { "steps": 60 },
    "compaction": { "model": "opencode/deepseek-v4.1-flash-free" }
  }
}
```

Подставить два ключа. Третий пул (Zen) ключа в файле не требует.

## Про блок `opencode`

Zen встроен в OpenCode, ключ хранится не в конфиге, а в самом OpenCode:

```powershell
opencode auth login      # → OpenCode Zen
```

Блок `provider.opencode` в файле нужен **только ради лимитов** — он не создаёт
провайдера, а переопределяет модель из каталога. Без него Zen работает, но с
контекстом 200k и без бюджетного потолка.

Если Zen не подключаешь — удаляй блок `opencode` и `agents.compaction`.

## Три пула, три запуска

```powershell
opencode                                            # Coarena (по умолчанию)
opencode --model zro/deepseek-v4.1-flash            # Zro
opencode --model opencode/deepseek-v4.1-flash-free  # Zen free
```

| Пул | id | Лимит | Роль |
|---|---|---|---|
| Coarena | `deepseek-v4-1-flash-1017` | 30 req/min, сброс 00:00 UTC | основной прогон |
| Zro | `deepseek-v4.1-flash` | дневная free-квота | второй прогон, кэш префикса |
| Zen | `deepseek-v4.1-flash-free` | общий на аккаунт | резерв, саммари |

## Что даёт профиль

| Настройка | Значение | Против дефолта |
|---|---|---|
| `limit.context` | 131072 | 1 048 576 / 200 000 |
| `limit.output` | 16384 | 393 216 / 32 000 |
| `compaction.background` | `false` | `true` — не платим за неиспользованное саммари |
| `compaction.keep.tokens` | 8000 | 15000 |
| `compaction.buffer` | `"20%"` | `"15%"` |
| `tool_output.max_lines` | 600 | 2000 |
| `tool_output.max_bytes` | 16000 | 51200 |
| `agents.build.steps` | 60 | без предела |

Прогон задачи 3 в этом режиме: **~1,2M входных токенов** вместо 3,5M.

## Проверка

```powershell
$p="$env:USERPROFILE\.config\opencode\opencode.json"
python3 -m json.tool $p > $null; if ($?) { "JSON ок" }
(Get-Content $p -Raw | ConvertFrom-Json).provider.PSObject.Properties.Name
```

Должно быть ровно три строки: `coarena`, `zro`, `opencode`.

```powershell
opencode models | Select-String "coarena|zro|opencode/deepseek"
```

Если пула нет в списке — не задан ключ: OpenCode не показывает модели провайдера
без креденшела.

## Что удалено и почему

| Пул | Причина удаления |
|---|---|
| NVIDIA NIM | ключ не выдаётся без SMS-верификации телефона |
| DeepSeek API (официальный) | платный, для задачи 3 не нужен |
| Tooken Club | платный, рублёвый, хранение данных не описано |
| Alibaba, Cloudflare, Ollama | не используются |

Документы по ним остались в репозитории как справка — из конфига они просто убраны.
