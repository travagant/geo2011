# `opencode.json` с Coarena и Zro одновременно

## Главная ошибка: два объекта вместо одного

Так файл **не работает** — `python3 -m json.tool` выдаёт
`Extra data: line 18 column 1`:

```json
{ "$schema": "...", "provider": { "coarena": { ... } }, "model": "..." }
{ "provider": { "zro": { ... } } }
```

`opencode.json` — **один** JSON-объект. Провайдеры не склеиваются: оба живут
внутри одного словаря `provider`, через запятую.

## Правильный файл

`C:\Users\Skynet\.config\opencode\opencode.json` — целиком:

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
        "deepseek-v4-1-flash-1017": { "name": "DeepSeek V4.1 Flash 1017" }
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
  "small_model": "coarena/deepseek-v4-1-flash-1017"
}
```

Подставь только два ключа: `КЛЮЧ_COARENA` (без `Bearer`, без кавычек внутри) и
`КЛЮЧ_ZRO` (начинается с `sk-`).

## Что тут к чему

| Поле | Зачем |
|---|---|
| `provider` | один словарь; ключ словаря = имя провайдера |
| `coarena` / `zro` | эти имена попадают в префикс модели: `coarena/...`, `zro/...` |
| `npm` | один и тот же пакет: оба API OpenAI-совместимые |
| `baseURL` | обязаны оканчиваться на `/v1`, без `/chat/completions` |
| `models` | id модели **как его ждёт провайдер**, без префикса |
| `model` | что запустится по умолчанию |
| `small_model` | фон: заголовки сессий, мелкие задачи — дешёвый пул |

`small_model` смотрит на Coarena: заголовки сессий — редкие запросы, зато не тратят
куцый free-лимит Zro. Если лимит Coarena в минуту мешает — переведи на Zen:
`"small_model": "opencode/glm-5.3-flash-free"` (нужен `opencode auth login`).

> **Почему контекст 131072, а не 1048576.** `limit.context` — это не «сколько модель
> умеет», а порог, после которого OpenCode сжимает историю. С лимитом в 1M он
> практически не сжимает и на каждом ходе отправляет весь накопленный контекст:
> реальный прогон вырос до 3,5 млн входных токенов и съел free-лимит Zro за 5 минут.
> 128k держит ход дешёвым. Модель всё равно принимает 1M — поднять лимит можно,
> когда платишь пакетом, а не сидишь на free.

## Проверка по шагам

```powershell
$p="$env:USERPROFILE\.config\opencode\opencode.json"
python3 -m json.tool $p > $null; if ($?) { "JSON ок" }
```

```powershell
(Get-Content $p -Raw | ConvertFrom-Json).provider.PSObject.Properties.Name
```

Должно напечатать две строки: `coarena` и `zro`. Если одна — второй объект не склеился.

```powershell
opencode models | Select-String "coarena|zro"
```

Ждём три модели:
`coarena/deepseek-v4-1-flash-1017`, `zro/deepseek-v4.1-flash`, `zro/glm-5.3`.

## Запуск

```powershell
opencode                                            # Coarena (по умолчанию)
opencode --model zro/deepseek-v4.1-flash            # Zro, тот же промт
opencode --model zro/glm-5.3                        # второй кандидат для батла
```

## Частые ошибки

| Симптом | Причина |
|---|---|
| `Extra data: line N column 1` | два JSON-объекта в файле (твой случай) |
| `models` пусто | ключ модели с префиксом: `"coarena/deepseek-..."` вместо `"deepseek-..."` |
| `404` и в тексте `/v1/v1/` | лишний `/v1` в baseURL |
| `401 invalid_api_key` | ключ пустой, обрезан или вставлен вместе с кавычками |
| из двух провайдеров виден один | второй лежит **после** закрывающей `}` первого объекта |
| Coarena работает, Zro — нет | у Zro ключ ещё не создан (Account → API keys) |
