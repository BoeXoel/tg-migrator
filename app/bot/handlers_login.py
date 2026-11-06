"""Log the reading user account in from the Bot chat.

The login code is entered on an inline keypad, so it never appears as a
chat message (Telegram invalidates codes that are sent in messages).  The
two-step password message is deleted immediately after it is read.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any

from telethon import Button, TelegramClient, errors, events

from app.bot.auth import admin_only
from app.bot.context import AppContext
from app.bot.ui import command, esc
from app.telegram.client_manager import display_name

log = logging.getLogger("migrator.bot.login")

TIMEOUT_SECONDS = 600


@dataclass
class LoginSession:
    client: TelegramClient
    stage: str = "phone"  # phone | code | password
    phone: str = ""
    phone_code_hash: str = ""
    digits: str = ""
    started: float = field(default_factory=time.monotonic)

    @property
    def expired(self) -> bool:
        return time.monotonic() - self.started > TIMEOUT_SECONDS


def _keypad(digits: str) -> tuple[str, list[list[Any]]]:
    shown = " ".join("•" for _ in digits) or "—"
    rows = [
        [Button.inline(str(n), f"lg:d:{n}".encode()) for n in (1, 2, 3)],
        [Button.inline(str(n), f"lg:d:{n}".encode()) for n in (4, 5, 6)],
        [Button.inline(str(n), f"lg:d:{n}".encode()) for n in (7, 8, 9)],
        [Button.inline("⌫", b"lg:bk"), Button.inline("0", b"lg:d:0"), Button.inline("✅ 确认", b"lg:ok")],
        [Button.inline("取消登录", b"lg:x")],
    ]
    return f"请在下方键盘输入 Telegram 发给你的验证码：\n<b>{shown}</b>", rows


async def _finish(event: Any, ctx: AppContext, session: LoginSession) -> None:
    ctx.state.login.pop(event.sender_id, None)
    await ctx.clients.adopt_reader(session.client)
    me = ctx.clients.reader_me
    await event.respond(f"✅ 已登录读取账号：{esc(display_name(me))}（{me.id}）。监听与队列已启动。")


async def _abort(ctx: AppContext, user_id: int) -> None:
    session = ctx.state.login.pop(user_id, None)
    if session is not None and ctx.clients.reader is not session.client:
        try:
            await session.client.disconnect()
        except Exception:
            pass


async def cmd_login(event: Any, ctx: AppContext) -> None:
    if ctx.clients.reader is not None:
        await event.respond(
            f"已登录：{esc(display_name(ctx.clients.reader_me))}。如需切换账号，先发送 /logout。"
        )
        return
    await _abort(ctx, event.sender_id)
    client = ctx.clients.make_client(ctx.boot.user_session_name)
    await client.connect()
    if await client.is_user_authorized():
        session = LoginSession(client=client)
        await _finish(event, ctx, session)
        return
    ctx.state.login[event.sender_id] = LoginSession(client=client)
    await event.respond(
        "🔐 <b>登录读取账号</b>\n这个账号用于读取来源消息（需要已加入来源群组）。\n"
        "请发送手机号（国际格式，如 <code>+8613800000000</code>）。\n/cancel 取消"
    )


async def cmd_logout(event: Any, ctx: AppContext) -> None:
    if ctx.clients.reader is None:
        await event.respond("当前没有已登录的读取账号。")
        return
    await ctx.worker.pause("Reader logged out")
    await ctx.clients.logout_reader()
    await event.respond("👋 已退出读取账号并删除会话文件。队列已暂停，重新 /login 后发送 /resume。")


async def handle_login_text(event: Any, ctx: AppContext) -> bool:
    """Consume a private text message if a login is in progress."""

    session: LoginSession | None = ctx.state.login.get(event.sender_id)
    if session is None:
        return False
    if session.expired:
        await _abort(ctx, event.sender_id)
        await event.respond("登录已超时，请重新 /login。")
        return True
    text = (event.raw_text or "").strip()

    if session.stage == "phone":
        phone = "".join(ch for ch in text if ch.isdigit() or ch == "+")
        if len(phone) < 6:
            await event.respond("手机号格式不正确，请重新发送。")
            return True
        try:
            sent = await session.client.send_code_request(phone)
        except errors.PhoneNumberInvalidError:
            await event.respond("手机号无效，请重新发送。")
            return True
        except errors.FloodWaitError as exc:
            await _abort(ctx, event.sender_id)
            await event.respond(f"请求过于频繁，请 {exc.seconds} 秒后再试。")
            return True
        session.phone, session.phone_code_hash, session.stage = phone, sent.phone_code_hash, "code"
        prompt, buttons = _keypad("")
        await event.respond(prompt, buttons=buttons)
        return True

    if session.stage == "code":
        # Fallback: a code typed with separators, e.g. "1 2 3 4 5".
        digits = "".join(ch for ch in text if ch.isdigit())
        if digits:
            try:
                await event.delete()
            except Exception:
                pass
            session.digits = digits
            await _submit_code(event, ctx, session)
        else:
            await event.respond("请使用上方键盘输入验证码。")
        return True

    if session.stage == "password":
        try:
            await event.delete()
        except Exception:
            pass
        try:
            await session.client.sign_in(password=text)
        except errors.PasswordHashInvalidError:
            await event.respond("两步验证密码错误，请重新发送。")
            return True
        await _finish(event, ctx, session)
        return True
    return False


async def _submit_code(event: Any, ctx: AppContext, session: LoginSession) -> None:
    try:
        await session.client.sign_in(session.phone, session.digits, phone_code_hash=session.phone_code_hash)
    except errors.SessionPasswordNeededError:
        session.stage = "password"
        await event.respond("🔑 该账号开启了两步验证，请发送密码（消息读取后立即删除）。")
        return
    except (errors.PhoneCodeInvalidError, errors.PhoneCodeEmptyError):
        session.digits = ""
        prompt, buttons = _keypad("")
        await event.respond("验证码错误，请重新输入。\n" + prompt, buttons=buttons)
        return
    except errors.PhoneCodeExpiredError:
        await _abort(ctx, event.sender_id)
        await event.respond("验证码已过期，请重新 /login。")
        return
    await _finish(event, ctx, session)


async def on_keypad(event: Any, ctx: AppContext) -> None:
    session: LoginSession | None = ctx.state.login.get(event.sender_id)
    if session is None or session.stage != "code" or session.expired:
        await event.answer("登录会话已结束", alert=True)
        return
    action = event.data.decode().split(":")
    if action[1] == "d":
        if len(session.digits) < 8:
            session.digits += action[2]
    elif action[1] == "bk":
        session.digits = session.digits[:-1]
    elif action[1] == "x":
        await _abort(ctx, event.sender_id)
        await event.edit("已取消登录。")
        return
    elif action[1] == "ok":
        if len(session.digits) < 5:
            await event.answer("验证码至少 5 位", alert=True)
            return
        await event.edit("⏳ 正在验证…")
        await _submit_code(event, ctx, session)
        return
    prompt, buttons = _keypad(session.digits)
    await event.edit(prompt, buttons=buttons)


async def cancel_login(ctx: AppContext, user_id: int) -> bool:
    if user_id in ctx.state.login:
        await _abort(ctx, user_id)
        return True
    return False


def register(bot: TelegramClient, ctx: AppContext) -> None:
    bot.add_event_handler(admin_only(ctx, cmd_login), events.NewMessage(pattern=command("login")))
    bot.add_event_handler(admin_only(ctx, cmd_logout), events.NewMessage(pattern=command("logout")))
    bot.add_event_handler(admin_only(ctx, on_keypad), events.CallbackQuery(pattern=rb"^lg:"))
