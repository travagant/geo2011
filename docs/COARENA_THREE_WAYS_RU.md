# Coarena в OpenCode: три способа подключения

Твой ключ Coarena подходит к трём разным «дверям». Одна из них — Anthropic-совместимая,
и это лучший вариант для OpenCode. Выбери **один** способ и держи его в конфиге.

| Способ | baseURL | Формат id модели | Кому |
|---|---|---|---|
| **A. Anthropic-совместимый** (рекомендую) | `https://coarena.ai/api` | `deepseek-v4-1-flash-1017` | агентная работа, инструменты |
| **B. OpenAI-совместимый** | `https://coarena.ai/api/v1` | `deepseek-v4-1-flash-1017` | самый простой, уже настроен |
| **C. Встроенный провайдер `coarena`** | задан внутри OpenCode | `coarena/deepseek-v4-1-flash-1017` | если Zen/Coarena уже авторизованы |

---

## A. Anthropic-совместимый (лучший для агента)

Почему он лучше: OpenCode — агент, ему нужны инструменты и вызовы файлов. Через
Anthropic-протокол они идут нативно, без прослоек. Плюс так же работают Claude Code
и любые тулы, которые ждут `ANTHROPIC_BASE_URL`.

Файл `C:\Users\Skynet\.config\opencode\opencode.json` — целиком:

```json
{
  "$schema": "https://opencode.ai/config.json",
  "provider": {
    "coarena-anthropic": {
      "npm": "@ai-sdk/anthropic",
      "name": "Coarena (Anthropic API)",
      "options": {
        "baseURL": "https://coarena.ai/api",
        "apiKey": "ВСТАВЬ_КЛЮЧ"
      },
      "models": {
        "deepseek-v4-1-flash-1017": { "name": "DeepSeek V4.1 Flash 1017" }
      }
    }
  },
  "model": "coarena-anthropic/deepseek-v4-1-flash-1017"
}
```

Проверка, что ключ и протокол живые (в обход OpenCode):

```bash
curl -s https://coarena.ai/api/v1/chat/completions \
  -H "Authorization: Bearer ВСТАВЬ_КЛЮЧ" \
  -H "Content-Type: application/json" \
  -d '{"model":"deepseek-v4-1-flash-1017","messages":[{"role":"user","content":"ping"}],"max_tokens":16}'
```

---

## B. OpenAI-совместимый (то, что уже собрано)

Это вариант из `docs/QUICKSTART_COARENA_OPENCODE.md` — `npm: "@ai-sdk/openai-compatible"`,
`baseURL: https://coarena.ai/api/v1`. Работает, но агентские фичи (tools) идут через
эмуляцию, и часть ошибок Coarena отдаёт в формате OpenAI, который OpenCode показывает
менее понятно.

---

## C. Встроенный провайдер `coarena`

У OpenCode в каталоге `models.dev` есть провайдер `coarena` — тогда модели видны
как `coarena/<id>` и свой `provider`-блок не нужен:

```powershell
opencode auth login
```

→ выбрать **Coarena** → вставить ключ. Проверка:

```powershell
opencode models | Select-String coarena
```

Если в списке провайдеров Coarena нет — остаёшься на A или B, это нормально.

---

## Что означает блок, который ты прислал

```
OpenAI-compatible base URL: https://coarena.ai/api/v1
Anthropic-compatible base URL: https://coarena.ai/api
```

- **`/api/v1`** — путь для OpenAI-формата: `POST /api/v1/chat/completions`.
- **`/api`** — путь для Anthropic-формата: `POST /api/v1/messages`
  (у Anthropic SDK версия в пути, поэтому `baseURL` оканчивается на `/api`, а не на `/api/v1`).
- Ключ **один и тот же**, отличается только протокол.
- Список моделей смотреть по `https://coarena.ai/api/v1/models`, id писать **без**
  префикса провайдера.
- `POST https://coarena.ai/api/v1/feedback` — оценка ответа; из OpenCode это не
  вызывается, на настройку не влияет.

---

## Если A не заработал

| Симптом | Причина | Что делать |
|---|---|---|
| `404` и в тексте `/api/v1/v1/messages` | лишний `/v1` в baseURL | baseURL строго `https://coarena.ai/api` |
| `401 invalid_api_key` | ключ обрезан | создать новый ключ, вставить целиком |
| `403 model_api_terms_required` | не приняты условия | принять на странице ключей |
| `429 insufficient_quota` | дневной лимит (30 req/min, сброс 00:00 UTC) | ждать сброса |
| пустой список моделей | провайдер не загрузился | `opencode --print-config` и искать `coarena-anthropic` |

Быстрый откат на B: удалить блок `provider` из файла и оставить JSON из QUICKSTART.

---

## Проверка, что OpenCode реально видит Coarena

```powershell
opencode models | Select-String coarena
opencode --print-config | Select-String -Pattern "coarena|baseURL" -Context 0,1
```

Первая команда показывает id моделей, вторая — какой конфиг загружен и с каким
`baseURL`. Если `baseURL` в выводе отличается от того, что в файле, — правится не тот
файл: ищи проектный `opencode.json` в текущей папке (он перебивает глобальный).
