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
    tr,
    yes_no,
)
from app.targets.local_target import resolve_local_root
from app.targets.webdav_target import WebDAVClient, validate_webdav_url
from app.telegram import media_helper as mh
from app.telegram.client_manager import canonical_id, display_name

log = logging.getLogger("migrator.bot.target")


async def targets_view(ctx: AppContext) -> tuple[str, list[list[Any]]]:
    lang = ctx.lang
    targets = await ctx.repo.list_targets()
    if not targets:
        text = tr(
            lang,
            "<b>🎯 Targets (Destinations)</b>\nNo targets configured yet.\n\n"
            "• Telegram: Add the Bot to the target group/channel (with post permission) and send <code>/bind</code> inside it (or inside a Forum Topic)\n"
            "• Local directory: /add_local\n• WebDAV: /add_webdav",
            "<b>🎯 目的地</b>\n还没有目的地。\n\n"
            "• Telegram：把 Bot 加入目标群/频道（需发言权限），在其中发送 <code>/bind</code>（可在 Topic 内发送）\n"
            "• 本地目录：/add_local\n• WebDAV：/add_webdav",
        )
        rows = [
            [
                Button.inline(tr(lang, "➕ Local", "➕ 本地"), b"tg:new:local"),
                Button.inline(tr(lang, "➕ WebDAV", "➕ WebDAV"), b"tg:new:webdav"),
            ],
            [back_button(b"m:home", lang=lang)],
        ]
        return text, rows
    lines = [tr(lang, "<b>🎯 Targets (Destinations)</b>", "<b>🎯 目的地</b>")]
    lines.extend(esc(target_label(target, lang)) for target in targets)
    buttons = [Button.inline(f"#{t.id} {t.name}"[:40], f"tg:v:{t.id}".encode()) for t in targets]
    rows = chunk_buttons(buttons, 2)
    rows.append(
        [
            Button.inline(tr(lang, "➕ Local", "➕ 本地"), b"tg:new:local"),
            Button.inline(tr(lang, "➕ WebDAV", "➕ WebDAV"), b"tg:new:webdav"),
        ]
    )
    rows.append([back_button(b"m:home", lang=lang)])
    return "\n".join(lines), rows


async def target_view(ctx: AppContext, target_id: int) -> tuple[str, list[list[Any]]]:
    lang = ctx.lang
    target = await ctx.repo.get_target(target_id)
    if target is None:
        return tr(lang, "Target does not exist.", "目的地不存在。"), [[back_button(b"tg:list", lang=lang)]]
    config = ctx.repo.decrypt_config(target.config)
    fallback = await ctx.repo.get_target(target.fallback_target_id) if target.fallback_target_id else None
    toggle_label = (
        tr(lang, "⏸ Disable", "⏸ 停用")
        if target.enabled
        else tr(lang, "▶️ Enable", "▶️ 启用")
    )
    rows = [[Button.inline(toggle_label, f"tg:en:{target.id}".encode())]]
    if target.type in {"webdav", "local"}:
        rows[0].append(Button.inline(tr(lang, "🧪 Test", "🧪 测试"), f"tg:test:{target.id}".encode()))
    if target.type == "telegram":
        rows.append([Button.inline(tr(lang, "🗄 Set Oversized Fallback", "🗄 设置超限备用"), f"tg:fb:{target.id}".encode())])
    if target.type == "webdav":
        rows.append([Button.inline(tr(lang, "🔑 Change Password", "🔑 修改密码"), f"tg:pw:{target.id}".encode())])
    rows.append([Button.inline(tr(lang, "🗑 Delete", "🗑 删除"), f"tg:del:{target.id}".encode()), back_button(b"tg:list", lang=lang)])
    return target_detail(target, config, fallback, lang), rows


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
        ctx.t(
            "Add the Bot to your target group/channel with permission to post messages, then:\n"
            "• Group: Send <code>/bind</code> directly in the group (or inside the target Forum Topic)\n"
            f"• Channel or anonymous admin: Post <code>/bind {code}</code> there (valid for 10 minutes)",
            "把 Bot 加入目标群组/频道并授予发言权限，然后：\n"
            "• 群组：你直接在群内（或目标 Topic 内）发送 <code>/bind</code>\n"
            f"• 频道或匿名管理员：在其中发送 <code>/bind {code}</code>（10 分钟内有效）",
        )
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
    lang = ctx.lang
    if created:
        note = tr(lang, f"Bound as target #{target.id} {target.name}", f"已绑定为目的地 #{target.id} {target.name}")
    else:
        note = tr(lang, f"Already bound as target #{target.id} {target.name}", f"该会话已是目的地 #{target.id} {target.name}")
    try:
        confirmation = await event.respond(f"✅ {note}", parse_mode=None)
        try:
            await ctx.clients.bot.delete_messages(entity, [confirmation.id], revoke=True)
        except Exception:
            pass
    except Exception as exc:
        note += tr(lang, f"\n⚠️ However, the Bot cannot post here: {exc}", f"\n⚠️ 但 Bot 无法在此发言：{exc}")
    await ctx.notifier.notify(
        f"🎯 {note} ({display_name(entity)}{f' · Topic {topic_id}' if topic_id else ''})",
        chat_id=admin_id,
    )


async def cmd_add_tg(event: Any, ctx: AppContext) -> None:
    parts = command_args(event).split()
    if not parts:
        await event.respond(
            ctx.t(
                "Usage: /add_tg <code>@username or -100ID</code> [topic_id]\n(Using /bind inside the target chat is recommended)",
                "用法：/add_tg <code>@用户名 或 -100ID</code> [topic_id]\n（Bot 写入时更推荐在目标会话里 /bind）",
            )
        )
        return
    topic_id = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else None
    writer = ctx.clients.writer
    try:
        entity = await ctx.clients.resolve(writer, parts[0])
    except Exception as exc:
        await event.respond(
            ctx.t(
                f"❌ Writer account cannot access {esc(parts[0])}: {esc(exc)}\nAdd the Bot to that chat and send /bind inside it.",
                f"❌ 写入账号无法访问 {esc(parts[0])}：{esc(exc)}\n请把 Bot 加入该会话并在其中发送 /bind。",
            )
        )
        return
    target, created = await create_telegram_target(ctx, entity, topic_id)
    prefix = ctx.t("✅ Added" if created else "Already exists", "✅ 已添加" if created else "已存在")
    await event.respond(
        f"{prefix}: {esc(target_label(target, ctx.lang))}",
        buttons=[[back_button(b"tg:list", lang=ctx.lang)]],
    )


def _local_wizard(ctx: AppContext) -> Wizard:
    lang = ctx.lang

    def parse_path(value: str) -> str:
        value = optional(value).strip("/")
        resolve_local_root(ctx.boot.archive_root, value)
        return value

    async def done(event: Any, values: dict[str, Any]) -> None:
        if await ctx.repo.get_target_by_name(values["name"]):
            await event.respond(
                tr(lang, "❌ Target name already exists.", "❌ 名称已存在。"),
                buttons=[[back_button(b"tg:list", lang=lang)]],
            )
            return
        root = resolve_local_root(ctx.boot.archive_root, values["path"])
        root.mkdir(parents=True, exist_ok=True)
        target = await ctx.repo.add_target(name=values["name"], type="local", config={"path": values["path"]})
        await event.respond(
            tr(
                lang,
                f"✅ Created {esc(target_label(target, lang))}\nContainer path: <code>{esc(root)}</code>",
                f"✅ 已创建 {esc(target_label(target, lang))}\n容器内路径：<code>{esc(root)}</code>",
            ),
            buttons=[[back_button(f"tg:v:{target.id}".encode(), tr(lang, "🎯 View Target", "🎯 查看目的地"), lang=lang), back_button(b"tg:list", lang=lang)]],
        )

    return Wizard(
        tr(lang, "New Local Target", "新建本地目的地"),
        [
            Step("name", tr(lang, "Target name:", "目的地名称："), name_value),
            Step(
                "path",
                tr(
                    lang,
                    f"Subdirectory under ARCHIVE_ROOT (<code>{esc(ctx.boot.archive_root)}</code>), or send <code>-</code> for root:",
                    f"ARCHIVE_ROOT（<code>{esc(ctx.boot.archive_root)}</code>）下的子目录，发送 <code>-</code> 表示根目录：",
                ),
                parse_path,
            ),
        ],
        done,
        lang=lang,
        back_data=b"tg:list",
    )


def _webdav_wizard(ctx: AppContext) -> Wizard:
    lang = ctx.lang

    def parse_url(value: str) -> str:
        try:
            return validate_webdav_url(value)
        except Exception as exc:
            raise ValueError(str(exc)) from exc

    async def done(event: Any, values: dict[str, Any]) -> None:
        if await ctx.repo.get_target_by_name(values["name"]):
            await event.respond(
                tr(lang, "❌ Target name already exists.", "❌ 名称已存在。"),
                buttons=[[back_button(b"tg:list", lang=lang)]],
            )
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
        await event.respond(
            tr(
                lang,
                f"✅ Created {esc(target_label(target, lang))}\nConnectivity test: {esc(result)}",
                f"✅ 已创建 {esc(target_label(target, lang))}\n连通性测试：{esc(result)}",
            ),
            buttons=[[back_button(f"tg:v:{target.id}".encode(), tr(lang, "🎯 View Target", "🎯 查看目的地"), lang=lang), back_button(b"tg:list", lang=lang)]],
        )

    return Wizard(
        tr(lang, "New WebDAV Target", "新建 WebDAV 目的地"),
        [
            Step("name", tr(lang, "Target name:", "目的地名称："), name_value),
            Step(
                "url",
                tr(
                    lang,
                    "WebDAV URL (supports https:// and internal http://, e.g. <code>http://alist:5244/dav</code>):",
                    "WebDAV 地址（支持 https:// 与内网 http://，如 <code>http://alist:5244/dav</code>）：",
                ),
                parse_url,
            ),
            Step("username", tr(lang, "Username (send <code>-</code> for anonymous):", "用户名（匿名发送 <code>-</code>）："), optional),
            Step(
                "password",
                tr(
                    lang,
                    "Password (message is deleted immediately; send <code>-</code> for none).\n"
                    "💡 <i>Tip: If your password contains <code>_</code>, <code>*</code>, or <code>~</code>, wrap it in backticks like <code>`password`</code> so Telegram does not strip symbols.</i>",
                    "密码（发送后消息会立即删除；无密码发送 <code>-</code>）。\n"
                    "💡 <i>提示：若密码含 <code>_</code>、<code>*</code>、<code>~</code> 等特殊符号，请用反引号包裹发送（如 <code>`密码`</code>），防止被 Telegram 转成斜体/粗体而吞掉符号。</i>",
                ),
                optional,
                secret=True,
                skip_if=lambda v: not v.get("username"),
            ),
            Step("base_path", tr(lang, "Subdirectory on server (optional, send <code>-</code> to skip):", "服务器上的子目录（可选，发送 <code>-</code> 跳过）："), optional),
            Step(
                "verify_tls",
                tr(lang, "Verify HTTPS TLS certificate? (y/n, choose n for self-signed):", "是否校验 HTTPS 证书？（y/n，自签名证书选 n）"),
                yes_no,
                skip_if=lambda v: not str(v.get("url", "")).startswith("https://"),
            ),
        ],
        done,
        lang=lang,
        back_data=b"tg:list",
    )


async def _test_webdav(ctx: AppContext, config: dict[str, Any]) -> str:
    try:
        async with WebDAVClient(config, ctx.boot) as dav:
            parts = [p for p in str(config.get("base_path") or "").split("/") if p]
            if parts:
                await dav.ensure_dirs(parts)
            return await dav.self_test()
    except Exception as exc:
        return ctx.t(f"Failed – {exc.__class__.__name__}: {exc}", f"失败 – {exc.__class__.__name__}: {exc}")


async def cmd_targets(event: Any, ctx: AppContext) -> None:
    text, buttons = await targets_view(ctx)
    await event.respond(text, buttons=buttons)


async def cmd_add_local(event: Any, ctx: AppContext) -> None:
    await ctx.state.wizards.start(event, _local_wizard(ctx))


async def cmd_add_webdav(event: Any, ctx: AppContext) -> None:
    await ctx.state.wizards.start(event, _webdav_wizard(ctx))


# ----------------------------------------------------------------- callbacks


async def on_target_button(event: Any, ctx: AppContext) -> None:
    lang = ctx.lang
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
        await event.answer(tr(lang, "Target does not exist", "目的地不存在"), alert=True)
        return

    if action == "en":
        await ctx.repo.update_target(target_id, enabled=not target.enabled)
        if target.enabled is False:
            ctx.worker.wake()
    elif action == "test":
        await event.answer(tr(lang, "Testing…", "测试中…"))
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
                result = f"OK ({root})"
            except Exception as exc:
                result = tr(lang, f"Failed – {exc}", f"失败 – {exc}")
        await event.respond(
            f"🧪 {esc(target.name)}: {esc(result)}",
            buttons=[[back_button(f"tg:v:{target_id}".encode(), lang=lang)]],
        )
        return
    elif action == "pw":
        async def save_password(evt: Any, values: dict[str, Any]) -> None:
            config = ctx.repo.decrypt_config(target.config)
            config["password"] = values["password"]
            await ctx.repo.update_target(target_id, config=config)
            test_res = await _test_webdav(ctx, config)
            await evt.respond(
                tr(
                    lang,
                    f"🔑 Password updated. Connectivity test: {esc(test_res)}",
                    f"🔑 已更新密码。连通性测试：{esc(test_res)}",
                ),
                buttons=[[back_button(f"tg:v:{target_id}".encode(), lang=lang)]],
            )

        await event.answer()
        await ctx.state.wizards.start(
            event,
            Wizard(
                tr(lang, f"Change Password for {target.name}", f"修改 {target.name} 的密码"),
                [
                    Step(
                        "password",
                        tr(
                            lang,
                            "New password (message is deleted immediately).\n"
                            "💡 <i>Tip: Wrap in backticks like <code>`password`</code> if it contains <code>_</code>, <code>*</code>, or <code>~</code>.</i>",
                            "新密码（消息会立即删除）。\n"
                            "💡 <i>提示：若密码含 <code>_</code>、<code>*</code>、<code>~</code> 等特殊符号，请用反引号包裹发送（如 <code>`密码`</code>）。</i>",
                        ),
                        optional,
                        secret=True,
                    )
                ],
                save_password,
                lang=lang,
                back_data=f"tg:v:{target_id}".encode(),
            ),
        )
        return
    elif action == "fb":
        archives = [t for t in await ctx.repo.list_targets() if t.type in {"local", "webdav"}]
        buttons = [Button.inline(target_label(t, lang)[:40], f"tg:fbs:{target_id}:{t.id}".encode()) for t in archives]
        rows = chunk_buttons(buttons, 1)
        rows.append(
            [
                Button.inline(tr(lang, "None (record only)", "无（仅记录）"), f"tg:fbs:{target_id}:0".encode()),
                back_button(f"tg:v:{target_id}".encode(), lang=lang),
            ]
        )
        await event.edit(
            tr(
                lang,
                f"Select oversized file fallback target for <b>{esc(target.name)}</b>:\nFiles exceeding Telegram's upload limit will be saved here instead.",
                f"为 <b>{esc(target.name)}</b> 选择超限备用目的地：\n超过 Telegram 上传上限的文件会改存到这里。",
            ),
            buttons=rows,
        )
        return
    elif action == "fbs":
        fallback_id = int(parts[3]) or None
        await ctx.repo.update_target(target_id, fallback_target_id=fallback_id)
    elif action == "del":
        await event.edit(
            tr(
                lang,
                f"Confirm deleting <b>{esc(target.name)}</b>?\nAll queued job records for this target will also be removed, and references in routes will be cleaned up.",
                f"确认删除 <b>{esc(target.name)}</b>？\n该目的地的全部队列记录也会删除，路由中的引用会被移除。",
            ),
            buttons=[
                [
                    Button.inline(tr(lang, "⚠️ Confirm Delete", "⚠️ 确认删除"), f"tg:delok:{target_id}".encode()),
                    back_button(f"tg:v:{target_id}".encode(), tr(lang, "Cancel", "取消"), lang=lang),
                ]
            ],
        )
        return
    elif action == "delok":
        await ctx.repo.delete_target(target_id)
        await ctx.watcher.refresh()
        text, buttons = await targets_view(ctx)
        await event.edit(tr(lang, "🗑 Deleted.\n\n", "🗑 已删除。\n\n") + text, buttons=buttons)
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
