# Где ещё взять DeepSeek для OpenCode (кроме Coarena)

Проверено 2026-11-19 по каталогу `models.dev` (его OpenCode и читает) и по страницам провайдеров.

**Главное упрощение:** провайдеры из `models.dev` уже встроены в OpenCode. Достаточно
задать переменную окружения с ключом — и `opencode models` покажет модели. Свой блок
`provider` в `opencode.json` нужен только если хочешь переименовать модель или
подкрутить лимиты.

---

## 0. Без регистрации вообще — что выбрать, если телефон не подходит

| Пул | Телефон | Карта | Реально работает |
|---|---|---|---|
| **OpenCode Zen** (`opencode auth login`) | нет | нет | да, `opencode/deepseek-v4.1-flash-free` |
| Zro free (`zro.moonmath.ai`) | нет | нет | да, `deepseek-v4.1-flash`, дневная квота |
| NVIDIA NIM | **да, SMS** | нет | да, но без телефона не выдаёт ключ |
| Coarena | нет | нет | да, но лимит на сутки |
| Alibaba Model Studio | да (аккаунт) | карта для активации гранта | 1 млн токенов |
| Tooken Club | нет | да, пополнение | платно |

**Про временные номера:** сервисы одноразовых SMS не помогут — NVIDIA сверяет номер
с базой VoIP-диапазонов и лимитов на номер, а аккаунт с таким номером могут отозвать.
Обычный польский номер поддерживается и ничего обходить не нужно.

**Вывод:** если телефон подтверждать не хочешь — начинай с Zen, потом Zro.
NVIDIA NIM пропускай: у него SMS-верификация обязательная для новых ключей,
и это не формальность — на форуме NVIDIA отдельная тема с сотнями жалоб
(«verification has been challenging», «exceeded limits»). Обойти её легально нельзя;
поддержка (`help@build.nvidia.com`) разбирает случаи вручную и медленно.

---

## 1. OpenCode Zen — ноль настройки, уже внутри

```powershell
opencode auth login        # → OpenCode Zen
opencode models | Select-String deepseek
opencode --model opencode/deepseek-v4.1-flash-free
```

- id: `opencode/deepseek-v4.1-flash-free` (и `opencode/deepseek-v4-flash-free`)
- 200 000 контекст, 32 000 выход, tools, reasoning `low|high|max`
- бесплатно, но «limited time» и общий лимит аккаунта
- **плюс:** ничего не правишь в файлах, ключ хранится в OpenCode

## 2. NVIDIA NIM — самый стабильный бесплатный DeepSeek

### Регистрация (один раз, ~5 минут)

1. Открой https://build.nvidia.com/deepseek-ai/deepseek-v4.1-flash
2. Справа кнопка **Get API Key** → введи e-mail → **Next**.
3. Если NVIDIA-аккаунта нет — придумай пароль и подтверди почту. Ты автоматически
   становишься участником NVIDIA Developer Program (бесплатно, без карты).
4. Придёт подтверждение телефона (код по SMS) — NVIDIA требует это для новых ключей.
   Польша в списке поддерживаемых стран.
5. После входа снова открой страницу модели → **Get API Key** → появится окно с ключом
   вида `nvapi-...` → **Copy Key**. Ключ показывается один раз, сохрани его.

> **Телефон обязателен.** Без подтверждения номера кнопка выдачи ключа не работает:
> аккаунт остаётся «полуверифицированным», а баланс и ключи показываются нулевыми.
> NVIDIA признаёт, что верификация сломана для части стран и номеров, и разбирает
> такие случаи вручную через `help@build.nvidia.com` (нужны e-mail аккаунта,
> номера, которые пробовал, и скриншот ошибки). Если номер не подходит — не трать
> время, бери Zen или Zro из раздела 0.

Тот же ключ работает для всех моделей каталога, не только DeepSeek.

### Подключение к OpenCode (2 команды)

```powershell
setx NVIDIA_API_KEY "nvapi-ВАШ_КЛЮЧ"
```

`setx` записывает переменную навсегда. **После этого закрой PowerShell и открой заново** —
иначе текущее окно её не увидит.

```powershell
opencode models | Select-String nvidia
opencode --model nvidia/deepseek-ai/deepseek-v4.1-flash
```

Провайдер `nvidia` уже встроен в OpenCode (он берёт его из каталога `models.dev`),
поэтому `opencode.json` править не нужно.

### Проверка ключа в обход OpenCode

```powershell
curl.exe -s https://integrate.api.nvidia.com/v1/chat/completions -H "Authorization: Bearer nvapi-ВАШ_КЛЮЧ" -H "Content-Type: application/json" -d "{\"model\":\"deepseek-ai/deepseek-v4.1-flash\",\"messages\":[{\"role\":\"user\",\"content\":\"ping\"}],\"max_tokens\":16}"
```

Ответ с `"choices"` — ключ живой, дело только в конфиге.

### Если что-то не так

| Симптом | Причина | Что делать |
|---|---|---|
| `401` / `invalid api key` | ключ не скопирован целиком или отозван | создай новый на странице модели |
| `404 Function ... not found for account` | ключ выпущен, но модель не активирована для аккаунта | открой страницу модели и нажми **Get API Key** ещё раз |
| `429` | 40 запросов/мин или 10 000/день | подожди минуту, дневной лимит — до 00:00 UTC |
| `opencode models` пусто | переменная не видна процессу | перезапусти PowerShell; проверь `echo $env:NVIDIA_API_KEY` |
| регистрация не проходит | страна не поддерживается для SMS | нужен номер из поддерживаемой страны; Польша подходит |

### Ограничения

- 40 запросов в минуту, 10 000 в день на аккаунт
- контекст ~1 млн токенов, но **выход ограничен** (для агентной правки кода ставь
  `"limit": {"output": 16384}` в конфиге, если ответы обрываются)
- данные могут использоваться «для улучшения продуктов NVIDIA» — не гоняй туда секреты
- RTX/vRAM не нужны: модель крутится на DGX Cloud у NVIDIA, локально ничего не ставится

### Альтернатива: ключ прямо в конфиге

Если не хочешь трогать переменные окружения:

```json
{
  "provider": {
    "nvidia": {
      "npm": "@ai-sdk/openai-compatible",
      "name": "NVIDIA NIM",
      "options": {
        "baseURL": "https://integrate.api.nvidia.com/v1",
        "apiKey": "nvapi-ВАШ_КЛЮЧ"
      },
      "models": {
        "deepseek-ai/deepseek-v4.1-flash": {
          "name": "DeepSeek V4.1 Flash (NIM)",
          "limit": { "context": 1000000, "output": 16384 }
        }
      }
    }
  },
  "model": "nvidia/deepseek-ai/deepseek-v4.1-flash"
}
```

## 3. Zro (MoonMath) — тот же Flash, ЕС-шлюз, без обучения на данных

### Регистрация (без телефона и без карты)

1. Открой https://zro.moonmath.ai/get-started?plan=free
2. Войти можно **через GitHub**, **через Google** или по e-mail (вход по ссылке из письма).
   Пароль и телефон не нужны — счёт создаётся сразу.
3. В кабинете: **Account → API keys → Create key**. Ключ показывается **один раз**,
   только хеш хранится. Формат — `sk-...`. Скопируй и сохрани.
4. Бесплатный дневной лимит на DeepSeek V4.1 Flash заявлен на странице цен
   («Free daily usage on DeepSeek V4.1 Flash. Resets every day»). Если в кабинете
   предложит выбрать план — остаёшься на Free, ничего покупать не надо.

### Подключение к OpenCode

Через их CLI (настраивает на одну сессию, твой `opencode.json` не трогает):

```powershell
npm install -g @moonmath-ai/zro
zro login
zro launch opencode --model deepseek-v4.1-flash
```

Вручную — блок в `opencode.json` (официально рекомендованный Zro):

```json
{
  "provider": {
    "zro": {
      "npm": "@ai-sdk/openai-compatible",
      "options": {
        "baseURL": "https://zro.moonmath.ai/v1",
        "apiKey": "sk-ВАШ_КЛЮЧ"
      },
      "models": {
        "deepseek-v4.1-flash": {
          "name": "DeepSeek V4.1 Flash",
          "limit": { "context": 131072, "output": 16384 }
        },
        "glm-5.3": {
          "name": "GLM-5.3",
          "limit": { "context": 131072, "output": 16384 }
        }
      }
    }
  }
}
```

Запуск: `opencode --model zro/deepseek-v4.1-flash`

### Проверка ключа

```powershell
curl.exe -s https://zro.moonmath.ai/v1/chat/completions -H "Authorization: Bearer sk-ВАШ_КЛЮЧ" -H "Content-Type: application/json" -d "{\"model\":\"deepseek-v4.1-flash\",\"messages\":[{\"role\":\"user\",\"content\":\"ping\"}],\"max_tokens\":16}"
```

### Что важно

- контекст 1 048 576, выход 64 000, tools и reasoning есть
- запросы не сохраняются и не идут в обучение (заявлено в прайсе и в доках интеграций)
- инференс в ЕС/США, шлюз европейский
- платные планы — это месячные бюджеты ($20 Pro / $60 Max / $100 Ultra) или разовые пакеты от $5
  на 90 дней; для одного прогона хватает free, пакет нужен только если упрёшься
- бесплатный план — промо: в `docs/plans` его нет, поэтому проверяй остаток

### Почему free кончается за 5 минут

Агентный прогон — не чат. Один прогон по репозиторию: ~25 ходов по ~140k токенов
контекста = **3,5 млн входных токенов**. Дневной allowance Zro такого не рассчитан
(на чат хватает, на агента нет), отсюда «кончилось за пять минут».

Что раздувает расход и как лечить:

| Причина | Лечение |
|---|---|
| `limit.context` = 1M | поставить **131072**: OpenCode начнёт сжимать контекст вовремя |
| `small_model` указывает на Zro | перевести на Coarena — заголовки сессий едят тот же лимит |
| агент сам ищет файлы | давать `@файл` явно, а не «найди в репозитории» |
| повторный прогон с нуля | прогонять только диф, кэш префикса даёт $0.01 за 1M |

Цена того же прогона при разных настройках:

| Сценарий | Вход | Цена |
|---|---|---|
| контекст 1M, без кэша | 3,50M | $1,09 |
| контекст 1M, кэш Zro | 3,50M | $0,08 |
| контекст 128k, кэш | 1,97M | $0,05 |

Кэш снижает деньги, но не квоту: free-план считает токены, а не доллары.

### Где смотреть остаток лимита

| Способ | Команда / место | Что показывает |
|---|---|---|
| CLI (точнее всего) | `zro status` | `Plan Free`, полоса `64% left today · resets in 5h 12m` |
| Кабинет | zro.moonmath.ai → **Account** | план, остаток, история за 30 дней |
| Ключи | **Account → API keys** | лимит и срок каждого ключа отдельно |

```powershell
npm install -g @moonmath-ai/zro
zro login          # или: zro login --api-key sk-...
zro status
```

`zro status` читает `/api/cli/status` и для free-плана печатает процент остатка
на сегодня и время сброса. Денег для free не показывает — free расходуется в
процентах, не в долларах. Если увидел `Plan none` — ключ не подхватился или
аккаунт ещё не на free-плане.

> В документации Zro страницы про остаток нет — сама фича (`billing.free` →
> `remainingPercent`, `resetsAt`) появилась в CLI недавно. Если `zro status`
> пишет `Details unavailable`, значит сервер не отдал это поле; тогда смотри
> кабинет.

Сброс free-лимита — ежедневно, в кабинете указано точное время до сброса.
Понять, что лимит кончился, можно по ошибке `402` (spend quota или plan budget
exhausted) — это не `429`, ретраи не помогут, надо ждать сброса.

## 4. Alibaba Model Studio (DashScope, Сингапур) — 1 млн токенов на модель

```powershell
$env:DASHSCOPE_API_KEY = "..."
opencode --model alibaba/deepseek-v4.1-flash
```

- baseURL: `https://dashscope-intl.aliyuncs.com/compatible-mode/v1`
- id: `deepseek-v4.1-flash`, `deepseek-v4-pro`, `deepseek-v4-flash-0731`
- грант: 1 000 000 токенов на модель, действует 90 дней
- **важно:** новые гранты не выдаются на снимок `deepseek-v4.1-flash-1017` (тот, что в Coarena),
  но `deepseek-v4.1-flash` без даты — выдаётся
- в консоли включи **Free Quota Only** для модели, иначе после гранта пойдёт платное списание
- данные на обучение не идут

## 5. Cloudflare Workers AI — 10 000 neurons/день

- baseURL: `https://api.cloudflare.com/client/v4/accounts/{account_id}/ai/v1`
- нужны `CLOUDFLARE_ACCOUNT_ID` и токен с правами Workers AI
- в каталоге есть `@cf/deepseek-ai/deepseek-v4.1-flash-1017`
- часть моделей требует платного плана; проверяй пометку на странице pricing
- данные на обучение не идут

## 6. Ollama Cloud — дешёвый платный резерв

- baseURL: `https://ollama.com/v1`, ключ `OLLAMA_API_KEY`
- id `deepseek-v4.1-flash`, 1M контекст, цена ~$0.15/$0.60 за 1M (peak ×2 в рабочие часы)
- «бесплатный стартовый лимит» в анонсе есть, но в текущем прайсе не подтверждён

---

## 7. Tooken Club (tooken.club) — платный агрегатор, рубли

Единый OpenAI-совместимый шлюз: GPT, Claude, DeepSeek, GLM, Kimi, Gemini — один ключ,
один баланс, цены в рублях (от 5–6 ₽ за 1 млн токенов).

- baseURL: `https://tooken.club/v1`
- ключ вида `tc_live_...`, создаётся в кабинете после пополнения баланса
- модели берутся из каталога `https://tooken.club/models`, id пишется как есть:
  `deepseek-v4.1-flash-1017` (**та же версия, что бесплатно в Coarena**),
  `deepseek-v4-pro`, `glm-5.3-flash`, `kimi-k3.1-pro`, `claude-opus-5-5`, `gpt-6-luna`
- DeepSeek V4.1 Flash 1017: до 1 млн контекста, streaming, function tools, reasoning
- ошибки: `invalid_api_key`, `insufficient_quota`, `model_not_found`, `rate_limit_exceeded`,
  `upstream_error`, `upstream_timeout`; баланс резервируется под запрос и возвращается остаток
- в `models.dev` провайдера **нет**, поэтому нужен свой блок в `opencode.json`
- данные: политика хранения на сайте не описана → для репозитория с ключами/проприетарным
  кодом относись как к «третьему лицу» и не гоняй туда секреты

```json
{
  "provider": {
    "tooken": {
      "npm": "@ai-sdk/openai-compatible",
      "name": "Tooken Club",
      "options": {
        "baseURL": "https://tooken.club/v1",
        "apiKey": "tc_live_ВАШ_КЛЮЧ"
      },
      "models": {
        "deepseek-v4.1-flash-1017": { "name": "DeepSeek V4.1 Flash 1017" },
        "deepseek-v4-pro": { "name": "DeepSeek V4 Pro" },
        "glm-5.3-flash": { "name": "GLM 5.3 Flash" }
      }
    }
  }
}
```

Проверка ключа в обход OpenCode:

```bash
curl -s https://tooken.club/v1/chat/completions \
  -H "Authorization: Bearer tc_live_ВАШ_КЛЮЧ" \
  -H "Content-Type: application/json" \
  -d '{"model":"deepseek-v4.1-flash-1017","messages":[{"role":"user","content":"ping"}],"max_tokens":16}'
```

Запуск: `opencode --model tooken/deepseek-v4.1-flash-1017`

**Важно:** это платно и баланс кончается. Для длинного прогона (репозиторий ~325k токенов ×
несколько попыток) считай стоимость заранее: 325k входа ≈ 2 ₽ по цене 6 ₽/1 млн, но с
повторными попытками и рассуждениями выходит в разы больше.

---

## Что НЕ работает (не трать время)

| Вариант | Почему |
|---|---|
| OpenRouter `:free` | у DeepSeek-моделей не осталось ни одного бесплатного провайдера, `:free` отдаёт 404 |
| SambaNova | 20 запросов **в день**, только V3.1 |
| Groq | DeepSeek только distill-R1 70B, не Flash |
| FreeInference (Harvard) | бесплатный только `deepseek-v4-flash`, не 4.1; сервис исследовательский |
| Freebuff | это отдельный агент-приложение, API-ключ наружу не выдаёт |
| второй аккаунт Coarena/Zen | нарушение ToS, бан |

---

## Итоговый набор пулов для проекта

| Приоритет | Пул | Модель | Лимит | Данные |
|---|---|---|---|---|
| 1 | Coarena | `deepseek-v4-1-flash-1017` | 30 req/min, сброс 00:00 UTC | zero-retention |
| 2 | OpenCode Zen | `opencode/deepseek-v4.1-flash-free` | общий free-лимит | zero-retention |
| 3 | NVIDIA NIM | `deepseek-ai/deepseek-v4.1-flash` | 40 rpm / 10k в день | могут обучаться |
| 4 | Zro | `deepseek-v4.1-flash` | дневная квота | не обучаются |
| 5 | DeepSeek API (платно) | `deepseek-flash` | 1M контекст | zero-retention |
| 6 | Tooken Club (платно, ₽) | `deepseek-v4.1-flash-1017` | по балансу | не описано |

Практика: `opencode --model <пул>` переключает за секунду, конфиг менять не надо, если
провайдер уже в `models.dev`. Кэш промпта между пулами не переносится — на каждом новом
пуле первый запрос будет дороже/дольше.
