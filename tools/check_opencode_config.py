#!/usr/bin/env python3
"""Проверка opencode.json на типовые поломки.

Запуск:
    python tools/check_opencode_config.py "%USERPROFILE%\\.config\\opencode\\opencode.json"

Что проверяет:
  1. markdown-ссылки, приехавшие из чата: ](http...) и [https://...]
  2. экранирование \\_ (невалидный escape в JSON)
  3. что файл вообще парсится как JSON
  4. ключи верхнего уровня против схемы opencode.ai/config.json
  5. обязательные поля limit (context, output) у каждой модели
  6. model и agents.compaction.model ссылаются на существующие модели
  7. apiKey: непустой, без кириллицы (заголовок HTTP — только latin-1)
"""
import json
import re
import sys
from pathlib import Path

SCHEMA_KEYS = {
    "$schema", "shell", "model", "default_agent", "update", "share", "enterprise",
    "username", "timezone", "permissions", "policies", "agents", "snapshots", "watcher",
    "formatter", "lsp", "media", "tool_output", "mcp", "compaction", "skills",
    "commands", "instructions", "references", "websearch", "plugins", "worktree",
    "warming", "providers", "experimental", "provider", "agent", "permission",
    "attachment", "tools",
}


def clean_markdown(text: str) -> str:
    """Убирает то, что приклеивает чат: ](url), [url, \\_."""
    text = re.sub(r"\]\([^)]*\)", "", text)
    text = re.sub(r"\[(?=\s*\"?https?://)", "", text)
    return text.replace("\\_", "_")


def check(path: Path) -> int:
    problems = 0
    raw = path.read_text(encoding="utf-8")

    if re.search(r"\]\(http", raw) or re.search(r"\[\"?https?://", raw):
        print("[!] найдены markdown-ссылки — файл не запустится")
        problems += 1
    if "\\_" in raw:
        print(r"[!] найдено \_ — невалидный escape в JSON")
        problems += 1

    try:
        cfg = json.loads(clean_markdown(raw))
    except json.JSONDecodeError as e:
        print(f"[x] JSON битый даже после чистки: {e}")
        return problems + 1
    print("[ok] JSON валиден")

    unknown = [k for k in cfg if k not in SCHEMA_KEYS]
    if unknown:
        print(f"[!] ключи вне схемы: {unknown}")
        problems += 1
    else:
        print(f"[ok] ключи верхнего уровня: {sorted(cfg)}")

    providers = cfg.get("provider") or cfg.get("providers") or {}
    for pname, prov in providers.items():
        for mname, model in (prov.get("models") or {}).items():
            limit = model.get("limit") or {}
            if "context" not in limit or "output" not in limit:
                print(f"[!] {pname}/{mname}: в limit нет context и/или output")
                problems += 1
            else:
                print(f"[ok] {pname}/{mname}: {limit['context']}/{limit['output']}")

    def resolves(ref: str) -> bool:
        if "/" not in ref:
            return False
        p, m = ref.split("/", 1)
        return m in ((providers.get(p) or {}).get("models") or {})

    model = cfg.get("model")
    if isinstance(model, str):
        if resolves(model):
            print(f"[ok] model → {model}")
        else:
            print(f"[!] model → {model}: модели нет в конфиге")
            problems += 1

    comp = ((cfg.get("agents") or {}).get("compaction") or {}).get("model")
    if isinstance(comp, str):
        if resolves(comp):
            print(f"[ok] agents.compaction → {comp}")
        else:
            print(f"[!] agents.compaction → {comp}: модели нет в конфиге")
            problems += 1

    for pname, prov in providers.items():
        key = ((prov.get("options") or {}).get("apiKey") or "")
        if not key:
            continue
        if "КЛЮЧ" in key or "ВАШ" in key:
            print(f"[!] {pname}: ключ не заменён на настоящий")
            problems += 1
        elif any(ord(ch) > 255 for ch in key):
            print(f"[!] {pname}: в ключе нелатинские символы — HTTP-заголовок упадёт")
            problems += 1

    print()
    print("итог: без замечаний" if problems == 0 else f"итог: замечаний {problems}")
    return problems


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(2)
    sys.exit(1 if check(Path(sys.argv[1])) else 0)
