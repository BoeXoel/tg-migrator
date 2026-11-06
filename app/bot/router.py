"""Register all Bot handlers and dispatch free-form private messages."""

from __future__ import annotations

import logging
import re
from typing import Any

from telethon import TelegramClient, events

from app.bot import handlers_config, handlers_login, handlers_route, handlers_target, handlers_task
from app.bot.auth import admin_only
from app.bot.context import AppContext
from app.bot.ui import command, esc
from app.telegram.link_parser import parse_request

log = logging.getLogger("migrator.bot")

# "/dav/telegram" (a path typed into a wizard) is not a command.
_COMMAND_RE = re.compile(r"^/[A-Za-z_]+(?:@\w+)?(?:\s|$)")


async def cmd_cancel(event: Any, ctx: AppContext) -> None:
    cancelled = ctx.state.wizards.cancel(event.sender_id)
    cancelled = await handlers_login.cancel_login(ctx, event.sender_id) or cancelled
    cancelled = ctx.state.import_modes.pop(event.sender_id, None) is not None or cancelled
    await event.respond("已取消。" if cancelled else "当前没有进行中的操作。")


async def on_private_message(event: Any, ctx: AppContext) -> None:
    text = event.raw_text or ""
    if _COMMAND_RE.match(text):
        return  # commands have their own handlers
    if await handlers_login.handle_login_text(event, ctx):
        return
    if ctx.state.wizards.has(event.sender_id) and await ctx.state.wizards.handle(event):
        return
    document = event.message.document
    if document is not None:
        name = next((getattr(a, "file_name", "") for a in document.attributes if hasattr(a, "file_name")), "") or ""
        if event.sender_id in ctx.state.import_modes or name.lower().endswith((".yaml", ".yml")):
            await handlers_config.handle_import_file(event, ctx)
            return
    try:
        parsed = parse_request(text)
    except ValueError as exc:
        await event.respond(f"❌ {esc(exc)}")
        return
    if parsed is not None:
        await handlers_task.handle_link(event, ctx, parsed)
        return
    await event.respond("发送消息链接即可创建搬运任务，/help 查看全部功能。")


def register_all(bot: TelegramClient, ctx: AppContext) -> None:
    bot.parse_mode = "html"
    handlers_task.register(bot, ctx)
    handlers_target.register(bot, ctx)
    handlers_route.register(bot, ctx)
    handlers_config.register(bot, ctx)
    handlers_login.register(bot, ctx)
    bot.add_event_handler(admin_only(ctx, cmd_cancel), events.NewMessage(pattern=command("cancel")))
    bot.add_event_handler(admin_only(ctx, on_private_message), events.NewMessage(func=lambda e: e.is_private))
