"""Entry point.

    python main.py            # run the Bot-controlled service (default)
    python main.py serve      # same as above
    python main.py login      # terminal login of the reading user account
    python main.py check      # validate configuration and database access
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from app.config import load_bootstrap


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Telegram content migrator (Bot-controlled service)")
    parser.add_argument("command", nargs="?", default="serve", choices=("serve", "login", "check"))
    return parser.parse_args()


async def check() -> None:
    from app.bootstrap import build_context

    boot = load_bootstrap()
    ctx = await build_context(boot)
    try:
        print(f"Database : {ctx.database_label}")
        print(f"Admins   : {', '.join(str(i) for i in sorted(boot.admin_ids))}")
        print(f"Proxy    : {boot.proxy.describe()}")
        print(f"Archive  : {boot.archive_root}")
        print(f"Targets  : {len(await ctx.repo.list_targets())}")
        print(f"Routes   : {len(await ctx.repo.list_routes())}")
        print("Configuration OK")
    finally:
        await ctx.repo.engine.dispose()


def main() -> None:
    args = parse_args()
    try:
        if args.command == "login":
            from app.telegram.client_manager import interactive_cli_login

            asyncio.run(interactive_cli_login(load_bootstrap(require_bot=False)))
        elif args.command == "check":
            asyncio.run(check())
        else:
            from app.bootstrap import run_service

            asyncio.run(run_service(load_bootstrap()))
    except ValueError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        sys.exit(2)
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
