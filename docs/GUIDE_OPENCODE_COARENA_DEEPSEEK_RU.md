# DeepSeek V4.1 Flash от Coarena в OpenCode — пошаговый гайд

Что получится: OpenCode работает через бесплатный пул Coarena (`deepseek-v4-1-flash-1017`),
а бесплатный пул OpenCode Zen остаётся вторым независимым счётчиком на тот же день.
Ключевые слова: **Coarena** (шлюз, ключ там), **OpenCode** (клиент, конфиг тут).

---

## Шаг 0. Проверить код модели — одна команда

Бесплатные модели Coarena ответили ошибкой `payment_required`, когда запросили Claude.
Значит модель надо называть точно. Список публичный, ключ не нужен:

```bash
curl -s https://coarena.ai/api/v1/models | python3 -c "import sys,json;print('\n'.join(m['id'] for m in json.load(sys.stdin)['data']))"
```

Среди вывода должно быть `deepseek-v4-1-flash-1017`. Если его нет — модель переехала,
возьми ближайшую строку из ответа и подставляй её везде ниже вместо нашей.

## Шаг 1. Взять ключ Coarena

1. Открой https://coarena.ai/account/api/keys
2. Прими условия (без этого любые вызовы отвечают `403 model_api_terms_required`).
3. **Create key** → скопируй сразу: второй раз он не показывается.

Ключ никуда не коммить. Дальше он живёт либо в переменной окружения, либо в
`auth.json` OpenCode.

## Шаг 2. Проверить ключ ДО OpenCode

Так ты отделяешь «ключ/сеть» от «конфиг клиента». Подставь ключ и запусти:

```bash
export COARENA_API_KEY='вставь-ключ'
curl -s https://coarena.ai/api/v1/chat/completions \
  -H "Authorization: Bearer $COARENA_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"model":"deepseek-v4-1-flash-1017","messages":[{"role":"user","content":"ping"}],"max_tokens":16}'
```

Что увидишь и что это значит:

| Ответ | Значение | Что делать |
|---|---|---|
| `"choices":[{"...":"pong"` | всё хорошо | иди к шагу 3 |
| `invalid_api_key` (401) | ключ не тот / обрезан / отозван | перевыпусти на шаге 1 |
| `payment_required` (403) | это платная модель на free-аккаунте | сверь id с шагом 0 |
| `model_api_terms_required` (403) | не принял условия | шаг 1, пункт 2 |
| `insufficient_quota` (429) | дневной лимит | ждать 00:00 UTC |
| `conflicting_credentials` (401) | в `ANTHROPIC_API_KEY` лежит второй ключ Coarena | `unset ANTHROPIC_API_KEY` |

## Шаг 3. Установить OpenCode (если ещё нет)

Один из вариантов:

```bash
curl -fsSL https://opencode.ai/install | bash          # официальный скрипт
# или
npm i -g opencode-ai
# или
brew install sst/tap/opencode
```

Проверка: `opencode --version`.

## Шаг 4. Отдать ключ OpenCode

Два равнозначных способа, выбери один.

**4a. Через переменную окружения** (проще для разового прогона):

```bash
export COARENA_API_KEY='вставь-ключ'
```

Положи строку в `~/.zshrc` / `~/.bashrc`, чтобы жила между сессиями.

**4b. Через `auth.json`** (ключ не висит в окружении):

```bash
mkdir -p ~/.local/share/opencode
cat > ~/.local/share/opencode/auth.json <<'JSON'
{ "coarena": { "type": "api", "key": "вставь-ключ" } }
JSON
chmod 600 ~/.local/share/opencode/auth.json
```

Имя `coarena` здесь — это **id провайдера**, который ты объявишь на шаге 5. Оно должно
совпадать буква в букву.

Альтернатива без ручных файлов: `opencode auth login` → прокрутить до **Other** →
ввести id `coarena` → вставить ключ.

## Шаг 5. Создать конфиг провайдера

Файл: `~/.config/opencode/opencode.json` (глобальный; проектный `opencode.json` перебивает его —
не создавай проектный в публичном репозитории, иначе ключ утечёт в git).

```json
{
  "$schema": "https://opencode.ai/config.json",
  "provider": {
    "coarena": {
      "npm": "@ai-sdk/openai-compatible",
      "name": "Coarena (free pool)",
      "options": {
        "baseURL": "https://coarena.ai/api/v1",
        "apiKey": "{env:COARENA_API_KEY}"
      },
      "models": {
        "deepseek-v4-1-flash-1017": {
          "name": "DeepSeek V4.1 Flash 1017 (Coarena)",
          "limit": { "context": 128000, "output": 16384 }
        },
        "gemini-3-8-flash": {
          "name": "Gemini 3.8 Flash (Coarena)",
          "limit": { "context": 128000, "output": 16384 }
        },
        "gpt-6-luna": {
          "name": "GPT-6 Luna (Coarena)",
          "limit": { "context": 128000, "output": 16384 }
        }
      }
    }
  }
}
```

Разбор трёх вещей, на которых обычно спотыкаются:

- `baseURL` заканчивается на `/v1`. Путь `/chat/completions` SDK дописывает сам —
  если вписать его руками, получится `/v1/chat/completions/chat/completions` и 404.
- Ключи внутри `models` — это **ровно те id, что отдаёт API** (шаг 0), а не красивые подписи.
- `limit.context` — 128000, а не миллион: у Coarena входной кап считается по-другому, и
  заявить больше — значит получить 400 на длинной сессии раньше, чем клиент начнёт сжимать.
  `output` 16384 — реальный максимум ответа Coarena.

Если ты выбрал способ 4b (auth.json), строку `"apiKey"` из `options` можно убрать —
OpenCode возьмёт ключ из хранилища. Если оставить ссылку на пустую переменную, будет 401.

## Шаг 6. Проверить, что провайдер виден

```bash
opencode models | grep coarena
```

Ожидаемо три строки `coarena/deepseek-v4-1-flash-1017`, `coarena/gemini-3-8-flash`,
`coarena/gpt-6-luna`. Если пусто — конфиг лежит не там, либо `provider` объявлен
на уровне проекта и перебит другим файлом (`opencode --print-config` покажет, что
реально загружено).

## Шаг 7. Первый запуск именно на этой модели

```bash
cd ~/geo2011
opencode --model coarena/deepseek-v4-1-flash-1017
```

Внутри TUI: `/models` — переключение на ходу, `/model` — показать текущую.
Проверка «жив ли пул» одной фразой в чате: *«ответь одним словом: pong»*.

## Шаг 8. Разнести нагрузку по двум пулам

Смысл всей затеи: основная работа идёт в Coarena, а служебные вызовы (заголовки сессий,
сжатие контекста) не жгут лимит Zen. Добавь в тот же `opencode.json`:

```json
{
  "model": "coarena/deepseek-v4-1-flash-1017",
  "small_model": "opencode/glm-5.3-flash-free"
}
```

`small_model` — это как раз «фоновый» слот. Он может указывать и на Zen, и на Coarena —
лишь бы id существовал.

Важно про Zen: free-модели там **делят общий лимит**, поэтому упёршись в DeepSeek ты
одновременно теряешь `glm-5.3-flash-free`, `mimo-v2.6-flash-free` и `big-pickle`
(тир Nemotron считается отдельно). Coarena — независимый счётчик, живёт по своим правилам.

## Шаг 9. Ограничить прожорливость (экономия квоты)

Для наших задач вида «починить 3 дефекта» половина расхода — перечитывание больших файлов.
Две настройки в `opencode.json` помогают:

```json
{
  "autoupdate": false,
  "share": "disabled"
}
```

и в самом промте пиши: *«читай только `d3tool/texture.py` и `d3tool/cli.py:430–1250`,
остальное не открывай; после правки прогони одну команду теста»*.

## Шаг 10. Переключение, когда пул кончился

```bash
opencode --model coarena/gemini-3-8-flash        # другая модель того же пула
opencode --model opencode/glm-5.3-flash-free     # другой пул (Zen)
opencode --model coarena/deepseek-v4-1-flash-1017 # вернуться
```

Дневной лимит Coarena сбрасывается в **00:00 UTC** (01:00 по Варшаве). Zen — по своим
правилам, смотри `https://coarena.ai/api/v1/limits` и `/models` в Zen.

---

## Диагностика: что сломалось и где смотреть

| Симптом | Причина | Лечение |
|---|---|---|
| `opencode models` не показывает coarena | конфиг не загружен | проверь путь `~/.config/opencode/opencode.json`, запусти `opencode --print-config` |
| 404 на каждый запрос | в `baseURL` дописан `/chat/completions` | оставь только `https://coarena.ai/api/v1` |
| 401 `invalid_api_key` | ключ обрезан при копировании | перевыпусти ключ, вставь целиком |
| 403 `payment_required` | id модели платный на free | сверь id по шагу 0 |
| 403 `model_api_terms_required` | условия не приняты | прими на странице ключей |
| 429 `insufficient_quota` | дневной лимит | ждать 00:00 UTC или сменить пул |
| 400 «input too long» | больше 128k токенов в запросе | `/compact` или ограничь чтение файлов |
| модель «думает», но пусто в ответе | ответ пришёл в reasoning-блоке | не баг: `deepseek-v4-1-flash-1017` reasoning-модель, текст в `content` |
| всё работает, но медленно | GLM/Gemini в этом пуле медленнее DeepSeek | для правок кода держи DeepSeek, для ревью — GLM |

## Чек-лист (30 секунд перед прогоном)

- [ ] `curl .../models` отдаёт `deepseek-v4-1-flash-1017`
- [ ] `echo $COARENA_API_KEY` непустой (или ключ лежит в `auth.json`)
- [ ] `opencode models | grep coarena` — видит три модели
- [ ] `curl .../chat/completions` с `max_tokens:16` отвечает `pong`
- [ ] `unset ANTHROPIC_API_KEY` — иначе ключи конфликтуют
- [ ] запуск: `opencode --model coarena/deepseek-v4-1-flash-1017`

## Что НЕ делать

- Не класть `opencode.json` с ключом в репозиторий `geo2011` — он публичный.
- Не заводить второй аккаунт Zen ради обхода лимита: это нарушение правил, аккаунт забанят
  (ровно об этом предупредила арена).
- Не ставить `limit.context` больше 128000 — Coarena вернёт 400 раньше, чем сработает сжатие.
- Не ждать, что кэш промпта переедет между пулами: он живёт на стороне провайдера,
  при переключении контекст считается заново.
