"""Admin-only access control. Non-admins get no response at all."""

from __future__ import annotations

import functools
import logging
from typing import Any, Awaitable, Callable

from telethon import errors

from app.bot.context import AppContext

log = logging.getLogger("migrator.bot")

Handler = Callable[[Any, AppContext], Awaitable[None]]


def admin_only(ctx: AppContext, handler: Handler, *, private_only: bool = True) -> Callable[[Any], Awaitable[None]]:
    """Wrap a handler: silently ignore everyone except ADMIN_IDS."""

    @functools.wraps(handler)
    async def wrapper(event: Any) -> None:
        if not ctx.is_admin(getattr(event, "sender_id", None)):
            return
        if private_only and not getattr(event, "is_private", True):
            return
        try:
            await handler(event, ctx)
        except errors.FloodWaitError as exc:
            log.warning("Bot reply hit FloodWait %ss", exc.seconds)
        except errors.MessageNotModifiedError:
            pass
        except Exception as exc:  # keep the Bot alive; report to the admin
            log.exception("Bot handler %s failed", handler.__name__)
            try:
                text = f"❌ 出错：{exc.__class__.__name__}: {exc}"
                if hasattr(event, "answer") and getattr(event, "data", None) is not None:
                    await event.answer(text[:190], alert=True)
                else:
                    await event.respond(text[:3500], parse_mode=None)
            except Exception:
                pass

    return wrapper
