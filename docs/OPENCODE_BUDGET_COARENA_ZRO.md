# Бюджетный `opencode.json`: Coarena + Zro

Один файл, оба пула, расход урезан в 3–4 раза. Целиком — ниже.

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
          "name": "DeepSeek V4.1 Flash 1017",
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
        },
        "glm-5.3": {
          "name": "GLM-5.3 (Zro)",
          "limit": { "context": 131072, "output": 16384 }
        }
      }
    }
  },
  "model": "coarena/deepseek-v4-1-flash-1017",
  "compaction": {
    "auto": true,
    "keep": { "tokens": 8000 },
    "buffer": "20%"
  },
  "tool_output": {
    "max_lines": 600,
    "max_bytes": 16000
  }
}
```

Подставить два ключа. Больше ничего менять не нужно.

## Что тут экономит

| Настройка | Значение | Против дефолта | Экономия |
|---|---|---|---|
| `limit.context` | 131072 | 1 048 576 | главное: ход не раздувается до 1M |
| `limit.output` | 16384 | 64000 | потолок ответа, обрезки нет — 16k хватает на патч |
| `compaction.buffer` | `"20%"` | наследует дефолт | сжатие включается за 26k до края, а не в упор |
| `compaction.keep.tokens` | 8000 | без лимита | после сжатия тащится 8k, а не весь хвост |
| `tool_output.max_lines` | 600 | 2000 | **вывод тестов не заливает контекст** |
| `tool_output.max_bytes` | 16000 | 51200 | то же, в байтах |

## Почему это работает

Расход считается по каждому ходу, а не за сессию: бот отправляет **весь** контекст целиком на каждый шаг. Значит платишь `средний_контекст × число_ходов`.

Три вещи раздувают средний контекст:

1. **`limit.context` = 1M.** OpenCode считает это порогом сжатия, а не «сколько модель умеет». При 1M он не сжимает почти до конца — и каждый ход летит накопленное.
2. **`tool_output` по умолчанию 2000 строк / 50 КБ.** Один прогон `test_corpus.py` или `anim_audit` забивает контекст вывода, который потом тащится на каждом следующем ходу.
3. **Фоновые задачи.** При не заданном `small_model` (и в v2 это деприкейт) заголовки и саммари идут на основную модель — на Coarena, что правильно: free-лимит Zro не тратится.

Оценка того же прогона (25 ходов, задача 3) до и после:

| Настройка | Средний контекст | Вход за прогон |
|---|---|---|
| было (1M, tool_output по умолчанию) | ~140k | ~3,5M токенов |
| стало (128k, сжатие 20%, вывод 600 строк) | ~45–60k | ~1,2–1,5M токенов |

На Zro с кэшем префикса это $0,02–0,03 за прогон вместо $0,08 — плюс сам прогон укладывается в free-квоту, потому что ходы больше не растут.

## Проверка

```powershell
$p="$env:USERPROFILE\.config\opencode\opencode.json"
python3 -m json.tool $p > $null; if ($?) { "JSON ок" }
(Get-Content $p -Raw | ConvertFrom-Json).PSObject.Properties.Name
```

Ожидаемые ключи верхнего уровня:
`$schema, provider, model, compaction, tool_output` — плюс может появиться `small_model`, если остался с прошлой версии.

```powershell
opencode models | Select-String "coarena|zro"
```

Три модели. Дальше:

```powershell
opencode                                    # Coarena
opencode --model zro/deepseek-v4.1-flash    # Zro
```

## Если нужно ещё дешевле

Добавляй точечно, не всё сразу:

| Рычаг | Как | Цена |
|---|---|---|
| reasoning `low` | в модель Zro: `"options": { "reasoning_effort": "low" }` | выход короче в 2–3 раза, качество ниже |
| потолок ходов | агент `build`: `"steps": 60` | агент остановится сам, не дойдя до конца |
| контекст 64k | `"context": 65536` | сжатие чаще, риск потерять детали диффа |
| без free-моделей | `$env:OPENCODE_DISABLE_FREE_MODELS="1"` | в списке не будет Zen free |

`limit.context` ниже 64k для этой задачи не ставь: промт + два файла `d3tool` уже дают ~40k, сжатие начнёт резать по живому.

## Важно про формат

В схеме `opencode.ai/config.json` поле `provider` помечено **deprecated**: в v2 современный вид — `providers` (множественное число), где вместо `npm` пишут `package`, а `options.baseURL`/`apiKey` уезжают в `settings`. Твой формат работает и поддерживается, но при переходе на новую мажорную версию файл придётся переписать:

```json
{
  "providers": {
    "zro": {
      "package": "@ai-sdk/openai-compatible",
      "settings": {
        "baseURL": "https://zro.moonmath.ai/v1",
        "apiKey": "КЛЮЧ_ZRO"
      },
      "models": {
        "deepseek-v4.1-flash": {
          "name": "DeepSeek V4.1 Flash",
          "limit": { "context": 131072, "output": 16384 }
        }
      }
    }
  }
}
```

Пока всё работает — не трогай. Это на будущее, чтобы знать, почему после `opencode upgrade` может перестать читаться.
