"""Targets: Telegram chats/topics, local directories, WebDAV servers."""

from __future__ import annotations

import logging
from typing import Any

from telethon import Button, TelegramClient, events

from app.bot.auth import admin_only
from app.bot.context import AppContext
from app.bot.ui import (
    Step,
    Wizard,
    back_button,
    chunk_buttons,
    command,
    command_args,
    esc,
    name_value,
    optional,
    target_detail,
    target_label,
    yes_no,
)
from app.targets.local_target import resolve_local_root
from app.targets.webdav_target import WebDAVClient, validate_webdav_url
from app.telegram import media_helper as mh
from app.telegram.client_manager import canonical_id, display_name

log = logging.getLogger("migrator.bot.target")


async def targets_view(ctx: AppContext) -> tuple[str, list[list[Any]]]:
    targets = await ctx.repo.list_targets()
    if not targets:
        text = (
            "<b>🎯 目的地</b>\n还没有目的地。\n\n"
            "• Telegram：把 Bot 加入目标群/频道（需发言权限），在其中发送 /bind（可在 Topic 内发送）\n"
            "• 本地目录：/add_local\n• WebDAV：/add_webdav"
        )
        return text, [[Button.inline("📊 状态", b"m:status")]]
    lines = ["<b>🎯 目的地</b>"]
    lines.extend(esc(target_label(target)) for target in targets)
    buttons = [Button.inline(f"#{t.id} {t.name}"[:40], f"tg:v:{t.id}".encode()) for t in targets]
    rows = chunk_buttons(buttons, 2)
    rows.append([Button.inline("➕ 本地", b"tg:new:local"), Button.inline("➕ WebDAV", b"tg:new:webdav")])
    return "\n".join(lines), rows


async def target_view(ctx: AppContext, target_id: int) -> tuple[str, list[list[Any]]]:
    target = await ctx.repo.get_target(target_id)
    if target is None:
        return "目的地不存在。", [[back_button(b"tg:list")]]
    config = ctx.repo.decrypt_config(target.config)
    fallback = await ctx.repo.get_target(target.fallback_target_id) if target.fallback_target_id else None
    rows = [[Button.inline("⏸ 停用" if target.enabled else "▶️ 启用", f"tg:en:{target.id}".encode())]]
    if target.type in {"webdav", "local"}:
        rows[0].append(Button.inline("🧪 测试", f"tg:test:{target.id}".encode()))
    if target.type == "telegram":
        rows.append([Button.inline("🗄 设置超限备用", f"tg:fb:{target.id}".encode())])
    if target.type == "webdav":
        rows.append([Button.inline("🔑 修改密码", f"tg:pw:{target.id}".encode())])
    rows.append([Button.inline("🗑 删除", f"tg:del:{target.id}".encode()), back_button(b"tg:list")])
    return target_detail(target, config, fallback), rows


# ------------------------------------------------------------------ creators


async def _unique_name(ctx: AppContext, base: str) -> str:
    base = (base or "target")[:56]
    name, index = base, 2
    while await ctx.repo.get_target_by_name(name):
        name = f"{base}_{index}"
        index += 1
    return name


async def create_telegram_target(ctx: AppContext, entity: Any, topic_id: int | None) -> tuple[Any, bool]:
    chat_id = canonical_id(entity)
    existing = await ctx.repo.find_telegram_target(chat_id, topic_id)
    if existing:
        return existing, False
    title = display_name(entity)
    base = mh.safe_name(title, limit=48) or chat_id
    if topic_id:
        base += f"_t{topic_id}"
    target = await ctx.repo.add_target(
        name=await _unique_name(ctx, base),
        type="telegram",
        config={"chat_id": chat_id, "topic_id": topic_id, "title": title},
    )
    return target, True


async def cmd_bind_private(event: Any, ctx: AppContext) -> None:
    code = ctx.state.new_bind_code(event.sender_id)
    await event.respond(
        "把 Bot 加入目标群组/频道并授予发言权限，然后：\n"
        "• 群组：你直接在群内（或目标 Topic 内）发送 <code>/bind</code>\n"
        f"• 频道或匿名管理员：在其中发送 <code>/bind {code}</code>（10 分钟内有效）"
    )


async def on_bind_in_chat(event: Any, ctx: AppContext) -> None:
    """``/bind`` inside a group/channel. Authorised by sender or one-time code."""

    if event.is_private:
        return
    args = command_args(event)
    admin_id = event.sender_id if ctx.is_admin(event.sender_id) else None
    if admin_id is None and args:
        admin_id = ctx.state.use_bind_code(args)
    if admin_id is None:
        return  # silently ignore strangers
    entity = await event.get_chat()
    topic_id = mh.topic_of(event.message)
    target, created = await create_telegram_target(ctx, entity, topic_id)
    try:
        await event.delete()
    except Exception:
        pass
    note = f"已绑定为目的地 #{target.id} {target.name}" if created else f"该会话已是目的地 #{target.id} {target.name}"
    try:
        confirmation = await event.respond(f"✅ {note}", parse_mode=None)
        try:
            await ctx.clients.bot.delete_messages(entity, [confirmation.id], revoke=True)
        except Exception:
            pass
    except Exception as exc:
        note += f"\n⚠️ 但 Bot 无法在此发言：{exc}"
    await ctx.notifier.notify(f"🎯 {note}（{display_name(entity)}{f' · Topic {topic_id}' if topic_id else ''}）", chat_id=admin_id)


async def cmd_add_tg(event: Any, ctx: AppContext) -> None:
    parts = command_args(event).split()
    if not parts:
        await event.respond("用法：/add_tg <code>@username 或 -100ID</code> [topic_id]\n（Bot 写入时更推荐在目标会话里 /bind）")
        return
    topic_id = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else None
    writer = ctx.clients.writer
    try:
        entity = await ctx.clients.resolve(writer, parts[0])
    except Exception as exc:
        await event.respond(f"❌ 写入账号无法访问 {esc(parts[0])}：{esc(exc)}\n请把 Bot 加入该会话并在其中发送 /bind。")
        return
    target, created = await create_telegram_target(ctx, entity, topic_id)
    await event.respond(("✅ 已添加" if created else "已存在") + f"：{esc(target_label(target))}")


def _local_wizard(ctx: AppContext) -> Wizard:
    def parse_path(value: str) -> str:
        value = optional(value).strip("/")
        resolve_local_root(ctx.boot.archive_root, value)
        return value

    async def done(event: Any, values: dict[str, Any]) -> None:
        if await ctx.repo.get_target_by_name(values["name"]):
            await event.respond("❌ 名称已存在。")
            return
        root = resolve_local_root(ctx.boot.archive_root, values["path"])
        root.mkdir(parents=True, exist_ok=True)
        target = await ctx.repo.add_target(name=values["name"], type="local", config={"path": values["path"]})
        await event.respond(f"✅ 已创建 {esc(target_label(target))}\n容器内路径：<code>{esc(root)}</code>")

    return Wizard(
        "新建本地目的地",
        [
            Step("name", "目的地名称：", name_value),
            Step("path", f"ARCHIVE_ROOT（<code>{esc(ctx.boot.archive_root)}</code>）下的子目录，发送 - 表示根目录：", parse_path),
        ],
        done,
    )


def _webdav_wizard(ctx: AppContext) -> Wizard:
    def parse_url(value: str) -> str:
        try:
            return validate_webdav_url(value)
        except Exception as exc:
            raise ValueError(str(exc)) from exc

    async def done(event: Any, values: dict[str, Any]) -> None:
        if await ctx.repo.get_target_by_name(values["name"]):
            await event.respond("❌ 名称已存在。")
            return
        config = {
            "url": values["url"],
            "username": values.get("username", ""),
            "password": values.get("password", ""),
            "base_path": values.get("base_path", "").strip("/"),
            "verify_tls": values.get("verify_tls", True),
        }
        target = await ctx.repo.add_target(name=values["name"], type="webdav", config=config)
        result = await _test_webdav(ctx, config)
        await event.respond(f"✅ 已创建 {esc(target_label(target))}\n连通性测试：{esc(result)}")

    return Wizard(
        "新建 WebDAV 目的地",
        [
            Step("name", "目的地名称：", name_value),
            Step("url", "WebDAV 地址（支持 https:// 与内网 http://，如 <code>http://alist:5244/dav</code>）：", parse_url),
            Step("username", "用户名（匿名发送 -）：", optional),
            Step("password", "密码（发送后消息会立即删除；无密码发送 -）：", optional, secret=True,
                 skip_if=lambda v: not v.get("username")),
            Step("base_path", "服务器上的子目录（可选，发送 - 跳过）：", optional),
            Step("verify_tls", "是否校验 HTTPS 证书？（y/n，自签名证书选 n）", yes_no,
                 skip_if=lambda v: not str(v.get("url", "")).startswith("https://")),
        ],
        done,
    )


async def _test_webdav(ctx: AppContext, config: dict[str, Any]) -> str:
    try:
        async with WebDAVClient(config, ctx.boot) as dav:
            parts = [p for p in str(config.get("base_path") or "").split("/") if p]
            if parts:
                await dav.ensure_dirs(parts)
            return await dav.self_test()
    except Exception as exc:
        return f"失败 – {exc.__class__.__name__}: {exc}"


async def cmd_targets(event: Any, ctx: AppContext) -> None:
    text, buttons = await targets_view(ctx)
    await event.respond(text, buttons=buttons)


async def cmd_add_local(event: Any, ctx: AppContext) -> None:
    await ctx.state.wizards.start(event, _local_wizard(ctx))


async def cmd_add_webdav(event: Any, ctx: AppContext) -> None:
    await ctx.state.wizards.start(event, _webdav_wizard(ctx))


# ----------------------------------------------------------------- callbacks


async def on_target_button(event: Any, ctx: AppContext) -> None:
    parts = event.data.decode().split(":")
    action = parts[1]
    if action == "list":
        text, buttons = await targets_view(ctx)
        await event.edit(text, buttons=buttons)
        return
    if action == "new":
        wizard = _local_wizard(ctx) if parts[2] == "local" else _webdav_wizard(ctx)
        await event.answer()
        await ctx.state.wizards.start(event, wizard)
        return

    target_id = int(parts[2])
    target = await ctx.repo.get_target(target_id)
    if target is None:
        await event.answer("目的地不存在", alert=True)
        return

    if action == "en":
        await ctx.repo.update_target(target_id, enabled=not target.enabled)
        if target.enabled is False:
            ctx.worker.wake()
    elif action == "test":
        await event.answer("测试中…")
        config = ctx.repo.decrypt_config(target.config)
        if target.type == "webdav":
            result = await _test_webdav(ctx, config)
        else:
            try:
                root = resolve_local_root(ctx.boot.archive_root, config.get("path"))
                root.mkdir(parents=True, exist_ok=True)
                probe = root / ".tg-migrator-probe"
                probe.write_text("ok")
                probe.unlink()
                result = f"OK（{root}）"
            except Exception as exc:
                result = f"失败 – {exc}"
        await event.respond(f"🧪 {esc(target.name)}：{esc(result)}")
        return
    elif action == "pw":
        async def save_password(evt: Any, values: dict[str, Any]) -> None:
            config = ctx.repo.decrypt_config(target.config)
            config["password"] = values["password"]
            await ctx.repo.update_target(target_id, config=config)
            await evt.respond(f"🔑 已更新密码。测试：{esc(await _test_webdav(ctx, config))}")

        await event.answer()
        await ctx.state.wizards.start(
            event, Wizard(f"修改 {target.name} 的密码", [Step("password", "新密码（消息会立即删除）：", optional, secret=True)], save_password)
        )
        return
    elif action == "fb":
        archives = [t for t in await ctx.repo.list_targets() if t.type in {"local", "webdav"}]
        buttons = [Button.inline(target_label(t)[:40], f"tg:fbs:{target_id}:{t.id}".encode()) for t in archives]
        rows = chunk_buttons(buttons, 1)
        rows.append([Button.inline("无（仅记录）", f"tg:fbs:{target_id}:0".encode()), back_button(f"tg:v:{target_id}".encode())])
        await event.edit(
            f"为 <b>{esc(target.name)}</b> 选择超限备用目的地：\n超过 Telegram 上传上限的文件会改存到这里。",
            buttons=rows,
        )
        return
    elif action == "fbs":
        fallback_id = int(parts[3]) or None
        await ctx.repo.update_target(target_id, fallback_target_id=fallback_id)
    elif action == "del":
        await event.edit(
            f"确认删除 <b>{esc(target.name)}</b>？\n该目的地的全部队列记录也会删除，路由中的引用会被移除。",
            buttons=[[Button.inline("⚠️ 确认删除", f"tg:delok:{target_id}".encode()), back_button(f"tg:v:{target_id}".encode(), "取消")]],
        )
        return
    elif action == "delok":
        await ctx.repo.delete_target(target_id)
        await ctx.watcher.refresh()
        text, buttons = await targets_view(ctx)
        await event.edit("🗑 已删除。\n\n" + text, buttons=buttons)
        return

    text, buttons = await target_view(ctx, target_id)
    await event.edit(text, buttons=buttons)


def register(bot: TelegramClient, ctx: AppContext) -> None:
    bot.add_event_handler(admin_only(ctx, cmd_targets), events.NewMessage(pattern=command("targets")))
    bot.add_event_handler(admin_only(ctx, cmd_add_local), events.NewMessage(pattern=command("add_local")))
    bot.add_event_handler(admin_only(ctx, cmd_add_webdav), events.NewMessage(pattern=command("add_webdav")))
    bot.add_event_handler(admin_only(ctx, cmd_add_tg), events.NewMessage(pattern=command("add_tg")))
    bot.add_event_handler(admin_only(ctx, cmd_bind_private), events.NewMessage(pattern=command("bind"), func=lambda e: e.is_private))

    async def bind_in_chat(event: Any) -> None:
        try:
            await on_bind_in_chat(event, ctx)
        except Exception:
            log.exception("/bind failed")

    # Groups and channels: authorisation happens inside (admin sender or one-time code).
    bot.add_event_handler(bind_in_chat, events.NewMessage(pattern=command("bind"), func=lambda e: not e.is_private))
    bot.add_event_handler(admin_only(ctx, on_target_button), events.CallbackQuery(pattern=rb"^tg:"))
