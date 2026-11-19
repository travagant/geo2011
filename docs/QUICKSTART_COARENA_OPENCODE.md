# Быстрый старт: DeepSeek из Coarena в OpenCode (4 шага)

Всё, что нужно — **один файл** с конфигом, в который ты вставляешь ключ.
Никаких переменных окружения, никаких `auth.json`. Подробная версия с объяснениями —
`docs/GUIDE_OPENCODE_COARENA_DEEPSEEK_RU.md`, но она не обязательна.

---

## Шаг 1. Поставить OpenCode

```bash
curl -fsSL https://opencode.ai/install | bash
```

Windows (PowerShell):

```powershell
npm i -g @opencode/cli@latest
```

> Если `npm` ругается, что не найден, — сначала поставь Node.js LTS с https://nodejs.org,
> потом закрой и открой PowerShell заново.

Если `npm` ставит правами обычного пользователя и падает с `EACCES`/`EPERM`:

```powershell
npm i -g @opencode/cli@latest --prefix "$env:LOCALAPPDATA\opencode-cli"
& "$env:LOCALAPPDATA\opencode-cli\opencode.cmd" --version
```

Тогда запускать надо по полному пути (или добавить `%LOCALAPPDATA%\opencode-cli` в PATH).

Проверка — команда должна напечатать версию:

```powershell
opencode --version
```

Если PowerShell пишет `Имя "opencode" не распознано как имя командлета` — установка
не прошла или её папка не в PATH. Найди её и проверь оттуда:

```powershell
where.exe opencode
Get-ChildItem "$env:APPDATA\npm\opencode*"
```

Строка `...npm\opencode.cmd` означает, что пакет стоит, а PATH не подхватился:
закрой и открой PowerShell заново (или перезагрузись). Если `where.exe` пустой —
повтори `npm i -g @opencode/cli@latest` и посмотри, нет ли ошибки в выводе.

## Шаг 2. Взять ключ

https://coarena.ai/account/api/keys → принять условия → **Create key** → скопировать.

## Шаг 3. Создать файл конфига и вставить туда ключ

### macOS / Linux

Скопируй блок целиком в терминал, но **сначала замени `ВСТАВЬ_КЛЮЧ`** на свой ключ:

```bash
mkdir -p ~/.config/opencode
cat > ~/.config/opencode/opencode.json <<'EOF'
{
  "$schema": "https://opencode.ai/config.json",
  "provider": {
    "coarena": {
      "npm": "@ai-sdk/openai-compatible",
      "name": "Coarena",
      "options": {
        "baseURL": "https://coarena.ai/api/v1",
        "apiKey": "ВСТАВЬ_КЛЮЧ"
      },
      "models": {
        "deepseek-v4-1-flash-1017": { "name": "DeepSeek V4.1 Flash 1017" }
      }
    }
  },
  "model": "coarena/deepseek-v4-1-flash-1017"
}
EOF
```

### Windows (PowerShell)

```powershell
New-Item -ItemType Directory -Force "$env:USERPROFILE\.config\opencode" | Out-Null
notepad "$env:USERPROFILE\.config\opencode\opencode.json"
```

Блокнот спросит «создать файл?» → да. Вставь туда **тот же самый JSON** и сохрани.

> Куда именно: `~/.config/opencode/opencode.json` — это **папка конфига OpenCode**,
> а не папка проекта `geo2011`. Ключ должен лежать здесь, один раз на всю машину.

> **Две ловушки, на которых уже спотыкались**
>
> 1. Блоки `mkdir -p` / `cat > ... <<'EOF'` — это macOS/Linux. В PowerShell они
>    не работают и ничего не создают: файл придётся делать блокнотом (команда выше).
> 2. Не копируй JSON из отрендеренной страницы. В документах вида `[https://...](https://...)`
>    ссылка при копировании приезжает **вместе со скобками**. Такой JSON считается
>    валидным (`json.tool` молчит), но `baseURL` в нём — мусор, и OpenCode не
>    достучится до Coarena. В файле должны быть просто строки:
>    `"https://coarena.ai/api/v1"` и `"https://opencode.ai/config.json"`.

## Шаг 4. Запустить

```bash
cd ~/geo2011
opencode --model coarena/deepseek-v4-1-flash-1017
```

Windows:

```powershell
cd $env:USERPROFILE\geo2011
opencode --model coarena/deepseek-v4-1-flash-1017
```

(если `opencode` не найден — см. проверку в шаге 1)

Внутри напиши в чат: `ответь одним словом: pong`.
Если ответил — всё работает, можно давать ему промт из `docs/PROMPT_BATTLE_COARENA_ONE_SHOT.md`.

---

## Если не заработало — 5 проверок по порядку

**1. Файл вообще на месте и это валидный JSON**

```bash
cat ~/.config/opencode/opencode.json
python3 -m json.tool ~/.config/opencode/opencode.json > /dev/null && echo "JSON ок"
```

Если `json.tool` ругается — значит при вставке потерялась кавычка или запятая.
Самая частая причина: ключ вставили **вместо** кавычек, а не **внутрь**.

Windows — одна диагностика, которая сразу показывает, что в файле на самом деле:

```powershell
$p="$env:USERPROFILE\.config\opencode\opencode.json"; try { $j = Get-Content $p -Raw | ConvertFrom-Json } catch { "ФАЙЛ БИТЫЙ: $($_.Exception.Message)"; return }; "keys: " + (($j.PSObject.Properties.Name) -join ','); "baseURL: " + $j.provider.coarena.options.baseURL
```

Три возможных исхода:

| Что напечаталось | Что это значит | Что делать |
|---|---|---|
| `keys: $schema,provider,model` и `baseURL: https://coarena.ai/api/v1` | файл в порядке | иди к пункту 2 |
| ошибка `Не удается найти свойство "baseURL"` | ключа `provider` в файле нет — лежит другой JSON | шаг «лечение» ниже |
| `baseURL: [https://...](https://...)` | приехала markdown-ссылка | шаг «лечение» ниже |

**Лечение: одна команда, сама убирает скобки у ссылок**

```powershell
$p="$env:USERPROFILE\.config\opencode\opencode.json"; $t=Get-Content $p -Raw; $t=[regex]::Replace($t,'\]\([^)]*\)',''); $t=[regex]::Replace($t,'\[((?=\s*"?https?://))',''); [IO.File]::WriteAllText($p,$t); $j=Get-Content $p -Raw|ConvertFrom-Json; "keys: " + (($j.PSObject.Properties.Name) -join ','); "baseURL: " + $j.provider.coarena.options.baseURL
```

Она снимает `](...)` и ведущую `[` перед `http`, не трогая ключ и остальной файл.
Должно напечатать `keys: $schema,provider,model` и `baseURL: https://coarena.ai/api/v1`.

Если после неё всё равно `keys:` без `provider` — в файле чужая структура
(например, вставлен текст из чата, а не JSON). Тогда пересобери файл из литералов:

```powershell
$p="$env:USERPROFILE\.config\opencode\opencode.json"
$k="ВСТАВЬ_КЛЮЧ"
$json="{`n" +
'  "$schema": "https://opencode.ai/config.json",' + "`n" +
'  "provider": {' + "`n" +
'    "coarena": {' + "`n" +
'      "npm": "@ai-sdk/openai-compatible",' + "`n" +
'      "name": "Coarena",' + "`n" +
'      "options": {' + "`n" +
'        "baseURL": "https://coarena.ai/api/v1",' + "`n" +
'        "apiKey": "' + $k + '"' + "`n" +
'      },' + "`n" +
'      "models": {' + "`n" +
'        "deepseek-v4-1-flash-1017": { "name": "DeepSeek V4.1 Flash 1017" }' + "`n" +
'      }' + "`n" +
'    }' + "`n" +
'  },' + "`n" +
'  "model": "coarena/deepseek-v4-1-flash-1017"' + "`n" +
"}`n"
[IO.File]::WriteAllText($p,$json)
$j = Get-Content $p -Raw | ConvertFrom-Json
"keys: " + (($j.PSObject.Properties.Name) -join ',')
"baseURL: " + $j.provider.coarena.options.baseURL
```

В переменную `$k` подставляется **только ключ** — кавычки вокруг него уже стоят.

**2. OpenCode видит провайдера**

```bash
opencode models | grep coarena
```

Должна быть строка `coarena/deepseek-v4-1-flash-1017`.

- пусто → файл лежит не в том месте (проверь `~/.config/opencode/` именно с точкой в начале);
- есть `coarena`, но без модели → внутри `models` опечатка в id.

**3. Ключ живой — проверяем в обход OpenCode**

```bash
curl -s https://coarena.ai/api/v1/chat/completions \
  -H "Authorization: Bearer ВСТАВЬ_КЛЮЧ" \
  -H "Content-Type: application/json" \
  -d '{"model":"deepseek-v4-1-flash-1017","messages":[{"role":"user","content":"ping"}],"max_tokens":16}'
```

**4. Что означает ответ**

| Что видишь | Что делать |
|---|---|
| `"choices":[...]` | ключ и модель в порядке, проблема в конфиге — вернись к пункту 1–2 |
| `invalid_api_key` | ключ скопирован не целиком или отозван — создай новый |
| `payment_required` | в конфиге чужая (платная) модель — сверь id: `deepseek-v4-1-flash-1017` |
| `model_api_terms_required` | не принял условия на странице ключей |
| `insufficient_quota` | дневной лимит кончился, сброс в 01:00 по Варшаве |
| `conflicting_credentials` | в системе остался `ANTHROPIC_API_KEY` — Windows: `Remove-Item Env:ANTHROPIC_API_KEY` (macOS/Linux: `unset ANTHROPIC_API_KEY`) |

**5. Запуск всё ещё падает**

```bash
opencode --print-config | head -50
```

Покажет, какой конфиг реально загружен. Если в выводе нет `coarena` — значит
OpenCode читает другой файл (например, проектный `opencode.json` в текущей папке),
и его надо удалить.

---

## Три правила, чтобы не наступить на грабли

1. **Ключ — только в `~/.config/opencode/opencode.json`**, не в репозиторий `geo2011`.
   Он публичный.
2. **`baseURL` заканчивается на `/v1`** — без `/chat/completions`, иначе 404.
3. **Модель называетcя точно `deepseek-v4-1-flash-1017`** — тогда free-тариф её пустит.
