from __future__ import annotations

import argparse
import asyncio
import json
import logging

from dotenv import load_dotenv

from afina_watch.config import load_config
from afina_watch.engine import Engine


def _setup_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )


def main() -> None:
    load_dotenv()
    p = argparse.ArgumentParser(prog="afina-watch")
    p.add_argument("--config", default="configs/watch.yaml")
    sub = p.add_subparsers(dest="cmd")
    sub.add_parser("run", help="живой монитор (по умолчанию)")

    bf = sub.add_parser("backfill", help="набрать архив за N дней (по умолчанию 7)")
    bf.add_argument("--days", type=int, default=None, help="окно догона, default из config.window.lookback_days")

    ar = sub.add_parser("archive", help="упаковать горячее окно в zip")
    ar.add_argument(
        "--close",
        action="store_true",
        help="закрыть текущее окно целиком, не ждать hot_days",
    )

    serve = sub.add_parser("serve", help="HTTP API поверх той же БД")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8091)
    args = p.parse_args()

    cfg = load_config(args.config)
    _setup_logging(cfg.app.log_level)
    engine = Engine(cfg)

    if args.cmd == "serve":
        import uvicorn
        from afina_watch.api.server import create_app

        uvicorn.run(create_app(cfg), host=args.host, port=args.port)
        return

    if args.cmd == "backfill":
        n = asyncio.run(engine.backfill(days=args.days))
        print(json.dumps({"ingested": n, "days": args.days or cfg.window.lookback_days}))
        return

    if args.cmd == "archive":
        info = asyncio.run(engine.archive(close_window=args.close))
        print(json.dumps(info, ensure_ascii=False, indent=2, default=str))
        return

    asyncio.run(engine.run())


if __name__ == "__main__":
    main()
