"""``python -m d3studio`` — start the local studio and open it in the browser."""
from __future__ import annotations

import argparse
import sys
import threading
import webbrowser

from . import __version__
from .api import create_app
from .config import Settings
from .store import Store


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="d3studio",
        description="Локальная студия ассетов: концепт на видеокарте → glTF → файлы Disciples 3.",
    )
    parser.add_argument("--version", action="version", version="d3studio " + __version__)
    subparsers = parser.add_subparsers(dest="command")
    serve = subparsers.add_parser("serve", help="запустить студию (по умолчанию)")
    serve.add_argument("--root", default=None, help="папка данных студии (по умолчанию ./studio-data)")
    serve.add_argument("--host", default="127.0.0.1", help="адрес (127.0.0.1 — только этот компьютер)")
    serve.add_argument("--port", type=int, default=8300, help="порт (0 — любой свободный)")
    serve.add_argument("--comfy", default=None, help="адрес сервера генерации, например http://127.0.0.1:8188")
    serve.add_argument("--checkpoint", default=None, help="чекпойнт для концептов")
    serve.add_argument("--open", action="store_true", dest="open_browser", help="открыть браузер")
    return parser


def serve(args) -> int:
    settings = Settings.load(args.root)
    if args.comfy:
        settings.comfy_url = args.comfy
    if args.checkpoint:
        settings.checkpoint = args.checkpoint
    if args.comfy or args.checkpoint:
        settings.save()

    store = Store(settings.db_path)
    project = store.ensure_project("Мой проект")
    try:
        app = create_app(settings, store=store, host=args.host, port=args.port)
    except OSError as exc:
        store.close()
        print(f"Не удалось занять {args.host}:{args.port} — {exc}. "
              f"Закройте другую студию или укажите другой --port.", file=sys.stderr)
        return 1
    url = app.url
    print(f"d3studio {__version__} — {url}")
    print(f"  данные:   {settings.root_path}")
    print(f"  движок:   {settings.comfy_url}")
    print(f"  проект:   {project['name']}")
    print("Остановить: Ctrl+C")
    if args.open_browser:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        app.httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nОстанавливаюсь…")
    finally:
        app.worker.stop()
        app.httpd.server_close()
        store.close()
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command in (None, "serve"):
        if args.command is None:
            args = parser.parse_args(["serve"])
        return serve(args)
    parser.error("Неизвестная команда: " + str(args.command))
    return 2


if __name__ == "__main__":
    sys.exit(main())
