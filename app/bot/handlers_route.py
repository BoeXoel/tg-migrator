"""Routes: long-running source -> targets mappings with optional live watching."""

from __future__ import annotations

import json
import logging
from typing import Any

from telethon import Button, TelegramClient, events

from app.bot.auth import admin_only
from app.bot.context import AppContext
from app.bot.handlers_task import launch_scan
from app.bot.ui import (
    Step,
    Wizard,
    back_button,
    chunk_buttons,
    command,
    command_args,
    esc,
    name_value,
    target_id_list,
    target_label,
    tr,
    yes_no,
)
from app.config import get_setting, set_setting
from app.telegram.link_parser import parse_link, parse_request

log = logging.getLogger("migrator.bot.route")


def _parse_source(value: str) -> dict[str, Any]:
    value = value.strip()
    link = parse_link(value) if "t.me/" in value or value.startswith("tg://") else None
    if link is not None:
        return {"chat": link.chat, "topic_id": link.topic_id}
    parts = value.split()
    if not parts:
        raise ValueError("Please send a source (message link, @username, or -100ID)")
    topic = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else None
    return {"chat": parts[0], "topic_id": topic}


async def routes_view(ctx: AppContext) -> tuple[str, list[list[Any]]]:
    lang = ctx.lang
    routes = await ctx.repo.list_routes()
    if not routes:
        return (
            tr(
                lang,
                "<b>🔁 Watch Routes</b>\nNo routes configured yet. Use /add_route to create one: map one source to one or more targets with optional real-time watching.",
                "<b>🔁 路由</b>\n还没有路由。/add_route 新建：一个来源 → 多个目的地，可选实时监听。",
            ),
            [
                [Button.inline(tr(lang, "➕ New Route", "➕ 新建路由"), b"rt:new")],
                [back_button(b"m:home", lang=lang)],
            ],
        )
    lines = [tr(lang, "<b>🔁 Watch Routes</b>", "<b>🔁 路由</b>")]
    for route in routes:
        flags = ("👁" if route.watch_enabled else "") + ("" if route.enabled else "⏸")
        lines.append(f"#{route.id} {esc(route.name)} {flags}\n    {esc(route.source_title or route.source_chat_id)} → {route.target_ids}")
    buttons = [Button.inline(f"#{r.id} {r.name}"[:40], f"rt:v:{r.id}".encode()) for r in routes]
    rows = chunk_buttons(buttons, 2)
    rows.append([Button.inline(tr(lang, "➕ New Route", "➕ 新建路由"), b"rt:new")])
    rows.append([back_button(b"m:home", lang=lang)])
    return "\n".join(lines), rows


async def route_view(ctx: AppContext, route_id: int) -> tuple[str, list[list[Any]]]:
    lang = ctx.lang
    route = await ctx.repo.get_route(route_id)
    if route is None:
        return tr(lang, "Route does not exist.", "路由不存在。"), [[back_button(b"rt:list", lang=lang)]]
    targets = []
    for target_id in route.target_ids or []:
        target = await ctx.repo.get_target(int(target_id))
        targets.append(esc(target_label(target, lang)) if target else tr(lang, f"#{target_id} (deleted)", f"#{target_id}（已删除）"))
    overrides = json.dumps(route.overrides or {}, ensure_ascii=False)
    none_label = tr(lang, "(none)", "（无）")
    all_label = tr(lang, "All", "全部")
    watch_label = tr(lang, "Enabled" if route.watch_enabled else "Disabled", "开启" if route.watch_enabled else "关闭")
    state_label = tr(lang, "Enabled" if route.enabled else "Disabled", "启用" if route.enabled else "停用")
    text = "\n".join(
        [
            f"<b>🔁 #{route.id} {esc(route.name)}</b>",
            f"{tr(lang, 'Source', '来源')}: {esc(route.source_title)} <code>{esc(route.source_chat_id)}</code>",
            f"Topic: {route.source_topic_id or all_label}",
            f"{tr(lang, 'Targets', '目的地')}:\n  " + ("\n  ".join(targets) if targets else none_label),
            f"{tr(lang, 'Live watch', '实时监听')}: {watch_label} · {tr(lang, 'Watermark', '水位线')}: {route.last_seen_msg_id or '-'}",
            f"{tr(lang, 'Status', '状态')}: {state_label}",
            f"{tr(lang, 'Per-route overrides', '路由级覆盖')}: <code>{esc(overrides)}</code>",
        ]
    )
    rows = [
        [
            Button.inline(
                tr(lang, "👁 Disable Watch", "👁 关闭监听") if route.watch_enabled else tr(lang, "👁 Enable Watch", "👁 开启监听"),
                f"rt:w:{route.id}".encode(),
            ),
            Button.inline(
                tr(lang, "⏸ Disable", "⏸ 停用") if route.enabled else tr(lang, "▶️ Enable", "▶️ 启用"),
                f"rt:en:{route.id}".encode(),
            ),
        ],
        [
            Button.inline(tr(lang, "📜 Scan History", "📜 扫描历史"), f"rt:scan:{route.id}".encode()),
            Button.inline(tr(lang, "⏩ Catch Up Now", "⏩ 立即补齐"), f"rt:cu:{route.id}".encode()),
        ],
        [Button.inline(tr(lang, "🎯 Change Targets", "🎯 修改目的地"), f"rt:tg:{route.id}".encode())],
        [Button.inline(tr(lang, "🗑 Delete", "🗑 删除"), f"rt:del:{route.id}".encode()), back_button(b"rt:list", lang=lang)],
    ]
    return text, rows


async def _targets_prompt(ctx: AppContext) -> str:
    lang = ctx.lang
    targets = await ctx.repo.list_targets()
    listing = "\n".join(esc(target_label(t, lang)) for t in targets) or tr(lang, "(no targets yet)", "（还没有目的地）")
    return tr(lang, f"Target ID(s), comma-separated:\n{listing}", f"目的地编号，逗号分隔：\n{listing}")


async def _route_wizard(ctx: AppContext) -> Wizard:
    lang = ctx.lang

    async def done(event: Any, values: dict[str, Any]) -> None:
        if await ctx.repo.get_route_by_name(values["name"]):
            await event.respond(
                tr(lang, "❌ Route name already exists.", "❌ 名称已存在。"),
                buttons=[[back_button(b"rt:list", lang=lang)]],
            )
            return
        try:
            await ctx.scanner.preflight_targets(values["targets"])
            source = await ctx.scanner.resolve_source(values["source"]["chat"])
        except Exception as exc:
            await event.respond(
                f"❌ {esc(exc)}",
                buttons=[[back_button(b"rt:list", lang=lang)]],
            )
            return
        route = await ctx.repo.add_route(
            name=values["name"],
            source_chat_id=source.chat_id,
            source_title=source.title[:255],
            source_topic_id=values["source"]["topic_id"],
            target_ids=values["targets"],
            watch_enabled=values["watch"],
        )
        if route.watch_enabled:
            await ctx.watcher.catch_up(route)  # sets the starting watermark
            await ctx.watcher.refresh()
        text, buttons = await route_view(ctx, route.id)
        header = tr(
            lang,
            "✅ Route created. Tap 'Scan History' below to backfill existing messages.\n\n",
            "✅ 路由已创建。历史消息请点「扫描历史」。\n\n",
        )
        await event.respond(header + text, buttons=buttons)

    return Wizard(
        tr(lang, "New Watch Route", "新建路由"),
        [
            Step("name", tr(lang, "Route name:", "路由名称："), name_value),
            Step(
                "source",
                tr(
                    lang,
                    "Source: message link (Forum Topic supported), @username, or <code>-100ID [topic_id]</code>:",
                    "来源：消息链接（可带 Topic）、@用户名，或 <code>-100ID [topic_id]</code>：",
                ),
                _parse_source,
            ),
            Step("targets", await _targets_prompt(ctx), target_id_list),
            Step("watch", tr(lang, "Enable live watching for new messages? (y/n):", "开启实时监听新消息？（y/n）"), yes_no),
        ],
        done,
        lang=lang,
        back_data=b"rt:list",
    )


async def cmd_routes(event: Any, ctx: AppContext) -> None:
    text, buttons = await routes_view(ctx)
    await event.respond(text, buttons=buttons)


async def cmd_add_route(event: Any, ctx: AppContext) -> None:
    if ctx.clients.reader is None:
        await event.respond(ctx.t("❌ Reader account is not logged in. Send /login first.", "❌ 读取账号尚未登录，请先 /login。"))
        return
    await ctx.state.wizards.start(event, await _route_wizard(ctx))


async def cmd_route_set(event: Any, ctx: AppContext) -> None:
    parts = command_args(event).split(None, 2)
    if len(parts) < 3 or not parts[0].lstrip("#").isdigit():
        await event.respond(
            ctx.t(
                "Usage: /route_set <route_id> <setting_key> <value>\nExample: <code>/route_set 1 transfer.include.document true</code>",
                "用法：/route_set 路由ID 设置项 值\n例：<code>/route_set 1 transfer.include.document true</code>",
            )
        )
        return
    route = await ctx.repo.get_route(int(parts[0].lstrip("#")))
    if route is None:
        await event.respond(ctx.t("Route does not exist.", "路由不存在。"))
        return
    path, raw = parts[1], parts[2]
    try:
        updated = set_setting(ctx.store.current, path, raw)
    except (KeyError, ValueError, TypeError) as exc:
        await event.respond(f"❌ {esc(exc)}")
        return
    overrides = dict(route.overrides or {})
    overrides[path] = get_setting(updated, path)
    await ctx.repo.update_route(route.id, overrides=overrides)
    await event.respond(
        ctx.t(
            f"✅ Route #{route.id} override: <code>{esc(path)} = {esc(overrides[path])}</code>",
            f"✅ 路由 #{route.id} 覆盖：<code>{esc(path)} = {esc(overrides[path])}</code>",
        )
    )


async def cmd_route_unset(event: Any, ctx: AppContext) -> None:
    parts = command_args(event).split()
    if len(parts) < 2 or not parts[0].lstrip("#").isdigit():
        await event.respond(ctx.t("Usage: /route_unset <route_id> <setting_key>", "用法：/route_unset 路由ID 设置项"))
        return
    route = await ctx.repo.get_route(int(parts[0].lstrip("#")))
    if route is None:
        await event.respond(ctx.t("Route does not exist.", "路由不存在。"))
        return
    overrides = dict(route.overrides or {})
    removed = overrides.pop(parts[1], None)
    await ctx.repo.update_route(route.id, overrides=overrides)
    await event.respond(
        ctx.t(
            "✅ Override removed." if removed is not None else "Override key was not set.",
            "✅ 已移除覆盖。" if removed is not None else "该覆盖项不存在。",
        )
    )


async def on_route_button(event: Any, ctx: AppContext) -> None:
    lang = ctx.lang
    parts = event.data.decode().split(":")
    action = parts[1]
    if action == "list":
        text, buttons = await routes_view(ctx)
        await event.edit(text, buttons=buttons)
        return
    if action == "new":
        await event.answer()
        if ctx.clients.reader is None:
            await event.respond(tr(lang, "❌ Reader account is not logged in. Send /login first.", "❌ 读取账号尚未登录，请先 /login。"))
            return
        await ctx.state.wizards.start(event, await _route_wizard(ctx))
        return

    route_id = int(parts[2])
    route = await ctx.repo.get_route(route_id)
    if route is None:
        await event.answer(tr(lang, "Route does not exist", "路由不存在"), alert=True)
        return

    if action == "w":
        route = await ctx.repo.update_route(route_id, watch_enabled=not route.watch_enabled)
        if route.watch_enabled and ctx.clients.reader is not None:
            await ctx.watcher.catch_up(route)
        await ctx.watcher.refresh()
    elif action == "en":
        await ctx.repo.update_route(route_id, enabled=not route.enabled)
        await ctx.watcher.refresh()
    elif action == "cu":
        if not route.watch_enabled:
            await event.answer(tr(lang, "Available only when live watch is enabled", "仅对开启监听的路由可用"), alert=True)
            return
        await event.answer(tr(lang, "Catching up…", "补齐中…"))
        stats = await ctx.watcher.catch_up(route)
        await event.respond(
            tr(
                lang,
                f"⏩ Route #{route.id} catch-up: queued {stats.enqueued} · duplicates {stats.duplicates}",
                f"⏩ 路由 #{route.id} 补齐：新入队 {stats.enqueued} · 重复 {stats.duplicates}",
            )
        )
    elif action == "scan":
        async def start_scan(evt: Any, values: dict[str, Any]) -> None:
            start, end = values["range"]
            task = await ctx.repo.create_task(
                kind="route_scan",
                route_id=route.id,
                source_chat_id=route.source_chat_id,
                source_title=route.source_title,
                source_topic_id=route.source_topic_id,
                start_id=start,
                end_id=end,
                target_ids=list(route.target_ids or []),
                created_by=evt.sender_id,
                notify_chat_id=evt.chat_id,
                status="scanning",
            )
            message = await evt.respond(tr(lang, f"🔎 Scanning task #{task.id}…", f"🔎 任务 #{task.id} 扫描中…"))
            launch_scan(ctx, task.id, evt.chat_id, message.id)

        await event.answer()
        await ctx.state.wizards.start(
            event,
            Wizard(
                tr(lang, f"Scan History for Route {route.name}", f"扫描路由 {route.name} 的历史消息"),
                [
                    Step(
                        "range",
                        tr(
                            lang,
                            "Message ID range (e.g. <code>1-5000</code>), or send two message links / a range link:",
                            "消息 ID 范围，如 <code>1-5000</code>，或发送两条消息链接 / 带范围的链接：",
                        ),
                        _parse_range,
                    )
                ],
                start_scan,
                lang=lang,
                back_data=f"rt:v:{route_id}".encode(),
            ),
        )
        return
    elif action == "tg":
        async def save_targets(evt: Any, values: dict[str, Any]) -> None:
            try:
                await ctx.scanner.preflight_targets(values["targets"])
            except Exception as exc:
                await evt.respond(
                    f"❌ {esc(exc)}",
                    buttons=[[back_button(f"rt:v:{route_id}".encode(), lang=lang)]],
                )
                return
            await ctx.repo.update_route(route_id, target_ids=values["targets"])
            await ctx.watcher.refresh()
            text, buttons = await route_view(ctx, route_id)
            await evt.respond(text, buttons=buttons)

        await event.answer()
        await ctx.state.wizards.start(
            event,
            Wizard(
                tr(lang, f"Change Targets for {route.name}", f"修改 {route.name} 的目的地"),
                [Step("targets", await _targets_prompt(ctx), target_id_list)],
                save_targets,
                lang=lang,
                back_data=f"rt:v:{route_id}".encode(),
            ),
        )
        return
    elif action == "del":
        await event.edit(
            tr(
                lang,
                f"Confirm deleting route <b>{esc(route.name)}</b>? (Already queued jobs are not affected)",
                f"确认删除路由 <b>{esc(route.name)}</b>？（已入队的任务不受影响）",
            ),
            buttons=[
                [
                    Button.inline(tr(lang, "⚠️ Confirm Delete", "⚠️ 确认删除"), f"rt:delok:{route_id}".encode()),
                    back_button(f"rt:v:{route_id}".encode(), tr(lang, "Cancel", "取消"), lang=lang),
                ]
            ],
        )
        return
    elif action == "delok":
        await ctx.repo.delete_route(route_id)
        await ctx.watcher.refresh()
        text, buttons = await routes_view(ctx)
        await event.edit(tr(lang, "🗑 Deleted.\n\n", "🗑 已删除。\n\n") + text, buttons=buttons)
        return

    text, buttons = await route_view(ctx, route_id)
    await event.edit(text, buttons=buttons)


def _parse_range(value: str) -> tuple[int, int]:
    value = value.strip()
    if "t.me/" in value or value.startswith("tg://"):
        parsed = parse_request(value)
        if parsed is None:
            raise ValueError("Unrecognized message link")
        return parsed.start, parsed.end
    cleaned = value.replace("–", "-").replace("—", "-").replace("~", "-").replace(" ", "")
    if "-" in cleaned:
        left, right = cleaned.split("-", 1)
        if left.isdigit() and right.isdigit():
            return min(int(left), int(right)), max(int(left), int(right))
    if cleaned.isdigit():
        return int(cleaned), int(cleaned)
    raise ValueError("Format: start_id-end_id, e.g. 1-5000")


def register(bot: TelegramClient, ctx: AppContext) -> None:
    bot.add_event_handler(admin_only(ctx, cmd_routes), events.NewMessage(pattern=command("routes")))
    bot.add_event_handler(admin_only(ctx, cmd_add_route), events.NewMessage(pattern=command("add_route")))
    bot.add_event_handler(admin_only(ctx, cmd_route_set), events.NewMessage(pattern=command("route_set")))
    bot.add_event_handler(admin_only(ctx, cmd_route_unset), events.NewMessage(pattern=command("route_unset")))
    bot.add_event_handler(admin_only(ctx, on_route_button), events.CallbackQuery(pattern=rb"^rt:"))
