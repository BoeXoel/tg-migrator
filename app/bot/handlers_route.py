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
        raise ValueError("请发送来源（链接、@用户名 或 -100ID）")
    topic = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else None
    return {"chat": parts[0], "topic_id": topic}


async def routes_view(ctx: AppContext) -> tuple[str, list[list[Any]]]:
    routes = await ctx.repo.list_routes()
    if not routes:
        return (
            "<b>🔁 路由</b>\n还没有路由。/add_route 新建：一个来源 → 多个目的地，可选实时监听。",
            [[Button.inline("➕ 新建路由", b"rt:new")]],
        )
    lines = ["<b>🔁 路由</b>"]
    for route in routes:
        flags = ("👁" if route.watch_enabled else "") + ("" if route.enabled else "⏸")
        lines.append(f"#{route.id} {esc(route.name)} {flags}\n    {esc(route.source_title or route.source_chat_id)} → {route.target_ids}")
    buttons = [Button.inline(f"#{r.id} {r.name}"[:40], f"rt:v:{r.id}".encode()) for r in routes]
    rows = chunk_buttons(buttons, 2)
    rows.append([Button.inline("➕ 新建路由", b"rt:new")])
    return "\n".join(lines), rows


async def route_view(ctx: AppContext, route_id: int) -> tuple[str, list[list[Any]]]:
    route = await ctx.repo.get_route(route_id)
    if route is None:
        return "路由不存在。", [[back_button(b"rt:list")]]
    targets = []
    for target_id in route.target_ids or []:
        target = await ctx.repo.get_target(int(target_id))
        targets.append(esc(target_label(target)) if target else f"#{target_id}（已删除）")
    overrides = json.dumps(route.overrides or {}, ensure_ascii=False)
    text = "\n".join(
        [
            f"<b>🔁 #{route.id} {esc(route.name)}</b>",
            f"来源：{esc(route.source_title)} <code>{esc(route.source_chat_id)}</code>",
            f"Topic：{route.source_topic_id or '全部'}",
            "目的地：\n  " + ("\n  ".join(targets) if targets else "（无）"),
            f"实时监听：{'开启' if route.watch_enabled else '关闭'} · 水位线：{route.last_seen_msg_id or '-'}",
            f"状态：{'启用' if route.enabled else '停用'}",
            f"路由级覆盖：<code>{esc(overrides)}</code>",
        ]
    )
    rows = [
        [
            Button.inline("👁 关闭监听" if route.watch_enabled else "👁 开启监听", f"rt:w:{route.id}".encode()),
            Button.inline("⏸ 停用" if route.enabled else "▶️ 启用", f"rt:en:{route.id}".encode()),
        ],
        [Button.inline("📜 扫描历史", f"rt:scan:{route.id}".encode()), Button.inline("⏩ 立即补齐", f"rt:cu:{route.id}".encode())],
        [Button.inline("🎯 修改目的地", f"rt:tg:{route.id}".encode())],
        [Button.inline("🗑 删除", f"rt:del:{route.id}".encode()), back_button(b"rt:list")],
    ]
    return text, rows


async def _targets_prompt(ctx: AppContext) -> str:
    targets = await ctx.repo.list_targets()
    listing = "\n".join(esc(target_label(t)) for t in targets) or "（还没有目的地）"
    return f"目的地编号，逗号分隔：\n{listing}"


async def _route_wizard(ctx: AppContext) -> Wizard:
    async def done(event: Any, values: dict[str, Any]) -> None:
        if await ctx.repo.get_route_by_name(values["name"]):
            await event.respond("❌ 名称已存在。")
            return
        try:
            await ctx.scanner.preflight_targets(values["targets"])
            source = await ctx.scanner.resolve_source(values["source"]["chat"])
        except Exception as exc:
            await event.respond(f"❌ {esc(exc)}")
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
        await event.respond("✅ 路由已创建。历史消息请点「扫描历史」。\n\n" + text, buttons=buttons)

    return Wizard(
        "新建路由",
        [
            Step("name", "路由名称：", name_value),
            Step("source", "来源：消息链接（可带 Topic）、@用户名，或 <code>-100ID [topic_id]</code>", _parse_source),
            Step("targets", await _targets_prompt(ctx), target_id_list),
            Step("watch", "开启实时监听新消息？（y/n）", yes_no),
        ],
        done,
    )


async def cmd_routes(event: Any, ctx: AppContext) -> None:
    text, buttons = await routes_view(ctx)
    await event.respond(text, buttons=buttons)


async def cmd_add_route(event: Any, ctx: AppContext) -> None:
    if ctx.clients.reader is None:
        await event.respond("❌ 读取账号尚未登录，请先 /login。")
        return
    await ctx.state.wizards.start(event, await _route_wizard(ctx))


async def cmd_route_set(event: Any, ctx: AppContext) -> None:
    parts = command_args(event).split(None, 2)
    if len(parts) < 3 or not parts[0].lstrip("#").isdigit():
        await event.respond("用法：/route_set 路由ID 设置项 值\n例：<code>/route_set 1 transfer.include.document true</code>")
        return
    route = await ctx.repo.get_route(int(parts[0].lstrip("#")))
    if route is None:
        await event.respond("路由不存在。")
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
    await event.respond(f"✅ 路由 #{route.id} 覆盖：<code>{esc(path)} = {esc(overrides[path])}</code>")


async def cmd_route_unset(event: Any, ctx: AppContext) -> None:
    parts = command_args(event).split()
    if len(parts) < 2 or not parts[0].lstrip("#").isdigit():
        await event.respond("用法：/route_unset 路由ID 设置项")
        return
    route = await ctx.repo.get_route(int(parts[0].lstrip("#")))
    if route is None:
        await event.respond("路由不存在。")
        return
    overrides = dict(route.overrides or {})
    removed = overrides.pop(parts[1], None)
    await ctx.repo.update_route(route.id, overrides=overrides)
    await event.respond("✅ 已移除覆盖。" if removed is not None else "该覆盖项不存在。")


async def on_route_button(event: Any, ctx: AppContext) -> None:
    parts = event.data.decode().split(":")
    action = parts[1]
    if action == "list":
        text, buttons = await routes_view(ctx)
        await event.edit(text, buttons=buttons)
        return
    if action == "new":
        await event.answer()
        if ctx.clients.reader is None:
            await event.respond("❌ 读取账号尚未登录，请先 /login。")
            return
        await ctx.state.wizards.start(event, await _route_wizard(ctx))
        return

    route_id = int(parts[2])
    route = await ctx.repo.get_route(route_id)
    if route is None:
        await event.answer("路由不存在", alert=True)
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
            await event.answer("仅对开启监听的路由可用", alert=True)
            return
        await event.answer("补齐中…")
        stats = await ctx.watcher.catch_up(route)
        await event.respond(f"⏩ 路由 #{route.id} 补齐：新入队 {stats.enqueued} · 重复 {stats.duplicates}")
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
            message = await evt.respond(f"🔎 任务 #{task.id} 扫描中…")
            launch_scan(ctx, task.id, evt.chat_id, message.id)

        await event.answer()
        await ctx.state.wizards.start(
            event,
            Wizard(
                f"扫描路由 {route.name} 的历史消息",
                [Step("range", "消息 ID 范围，如 <code>1-5000</code>，或发送两条消息链接 / 带范围的链接：", _parse_range)],
                start_scan,
            ),
        )
        return
    elif action == "tg":
        async def save_targets(evt: Any, values: dict[str, Any]) -> None:
            try:
                await ctx.scanner.preflight_targets(values["targets"])
            except Exception as exc:
                await evt.respond(f"❌ {esc(exc)}")
                return
            await ctx.repo.update_route(route_id, target_ids=values["targets"])
            await ctx.watcher.refresh()
            text, buttons = await route_view(ctx, route_id)
            await evt.respond(text, buttons=buttons)

        await event.answer()
        await ctx.state.wizards.start(
            event, Wizard(f"修改 {route.name} 的目的地", [Step("targets", await _targets_prompt(ctx), target_id_list)], save_targets)
        )
        return
    elif action == "del":
        await event.edit(
            f"确认删除路由 <b>{esc(route.name)}</b>？（已入队的任务不受影响）",
            buttons=[[Button.inline("⚠️ 确认删除", f"rt:delok:{route_id}".encode()), back_button(f"rt:v:{route_id}".encode(), "取消")]],
        )
        return
    elif action == "delok":
        await ctx.repo.delete_route(route_id)
        await ctx.watcher.refresh()
        text, buttons = await routes_view(ctx)
        await event.edit("🗑 已删除。\n\n" + text, buttons=buttons)
        return

    text, buttons = await route_view(ctx, route_id)
    await event.edit(text, buttons=buttons)


def _parse_range(value: str) -> tuple[int, int]:
    value = value.strip()
    if "t.me/" in value or value.startswith("tg://"):
        parsed = parse_request(value)
        if parsed is None:
            raise ValueError("无法识别链接")
        return parsed.start, parsed.end
    cleaned = value.replace("–", "-").replace("—", "-").replace("~", "-").replace(" ", "")
    if "-" in cleaned:
        left, right = cleaned.split("-", 1)
        if left.isdigit() and right.isdigit():
            return min(int(left), int(right)), max(int(left), int(right))
    if cleaned.isdigit():
        return int(cleaned), int(cleaned)
    raise ValueError("格式：起始ID-结束ID，例如 1-5000")


def register(bot: TelegramClient, ctx: AppContext) -> None:
    bot.add_event_handler(admin_only(ctx, cmd_routes), events.NewMessage(pattern=command("routes")))
    bot.add_event_handler(admin_only(ctx, cmd_add_route), events.NewMessage(pattern=command("add_route")))
    bot.add_event_handler(admin_only(ctx, cmd_route_set), events.NewMessage(pattern=command("route_set")))
    bot.add_event_handler(admin_only(ctx, cmd_route_unset), events.NewMessage(pattern=command("route_unset")))
    bot.add_event_handler(admin_only(ctx, on_route_button), events.CallbackQuery(pattern=rb"^rt:"))
