"""YAML backup (export) and restore (import) of the configuration."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from app import __version__
from app.config import dataclass_to_dict, settings_from_sections
from app.db.models import TARGET_TYPES
from app.db.repository import Repository
from app.engine.filters import validate_rule
from app.settings_store import SettingsStore
from app.targets.local_target import resolve_local_root
from app.targets.webdav_target import validate_webdav_url

REDACTED = "<redacted>"
FORMAT_VERSION = 2


@dataclass
class ImportReport:
    targets_added: int = 0
    targets_updated: int = 0
    targets_removed: int = 0
    routes_added: int = 0
    routes_updated: int = 0
    routes_removed: int = 0
    filters: int | None = None
    settings: bool = False
    warnings: list[str] = field(default_factory=list)

    def summary(self) -> str:
        lines = [
            f"设置：{'已更新' if self.settings else '未变更'}",
            f"目的地：新增 {self.targets_added} · 更新 {self.targets_updated} · 删除 {self.targets_removed}",
            f"路由：新增 {self.routes_added} · 更新 {self.routes_updated} · 删除 {self.routes_removed}",
            f"过滤规则：{'未变更' if self.filters is None else f'替换为 {self.filters} 条'}",
        ]
        lines.extend(f"⚠️ {warning}" for warning in self.warnings)
        return "\n".join(lines)


async def export_yaml(repo: Repository, store: SettingsStore, *, include_secrets: bool = False) -> bytes:
    targets = await repo.list_targets()
    names = {target.id: target.name for target in targets}
    target_items = []
    for target in targets:
        config = repo.decrypt_config(target.config)
        if not include_secrets:
            config = {key: (REDACTED if key == "password" and value else value) for key, value in config.items()}
        item: dict[str, Any] = {"name": target.name, "type": target.type, "enabled": target.enabled, "config": config}
        if target.fallback_target_id:
            item["fallback"] = names.get(target.fallback_target_id)
        target_items.append(item)

    route_items = []
    for route in await repo.list_routes():
        route_items.append(
            {
                "name": route.name,
                "source_chat_id": route.source_chat_id,
                "source_title": route.source_title,
                "source_topic_id": route.source_topic_id,
                "targets": [names[int(i)] for i in route.target_ids or [] if int(i) in names],
                "watch_enabled": route.watch_enabled,
                "last_seen_msg_id": route.last_seen_msg_id,
                "overrides": route.overrides or {},
                "enabled": route.enabled,
            }
        )

    document = {
        "version": FORMAT_VERSION,
        "app_version": __version__,
        "exported_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "secrets_included": include_secrets,
        "settings": dataclass_to_dict(store.current),
        "targets": target_items,
        "routes": route_items,
        "filter_rules": [
            {"type": rule.rule_type, "pattern": rule.pattern, "enabled": rule.enabled}
            for rule in await repo.list_filters()
        ],
    }
    header = (
        "# TG Migrator configuration backup\n"
        "# Restore by sending this file to the Bot (/import).\n"
        + ("# WARNING: contains plaintext secrets. Store it safely.\n" if include_secrets else "# Secrets are redacted.\n")
    )
    return (header + yaml.safe_dump(document, allow_unicode=True, sort_keys=False)).encode("utf-8")


def _deep_merge(base: dict[str, Any], incoming: dict[str, Any]) -> dict[str, Any]:
    result = dict(base)
    for key, value in incoming.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def _validate_target(item: dict[str, Any], archive_root: Path) -> None:
    name = item.get("name")
    if not name or not isinstance(name, str):
        raise ValueError("Every target needs a name")
    if item.get("type") not in TARGET_TYPES:
        raise ValueError(f"Target {name}: type must be one of {', '.join(TARGET_TYPES)}")
    config = item.get("config") or {}
    if not isinstance(config, dict):
        raise ValueError(f"Target {name}: config must be a mapping")
    if item["type"] == "telegram" and not config.get("chat_id"):
        raise ValueError(f"Target {name}: telegram targets need config.chat_id")
    if item["type"] == "local":
        resolve_local_root(archive_root, config.get("path"))
    if item["type"] == "webdav":
        validate_webdav_url(str(config.get("url") or ""))


async def import_yaml(
    content: bytes, repo: Repository, store: SettingsStore, *, archive_root: Path, replace: bool = False
) -> ImportReport:
    try:
        document = yaml.safe_load(content.decode("utf-8")) or {}
    except (yaml.YAMLError, UnicodeDecodeError) as exc:
        raise ValueError(f"Invalid YAML: {exc}") from exc
    if not isinstance(document, dict):
        raise ValueError("The YAML root must be a mapping")
    version = document.get("version", FORMAT_VERSION)
    if version != FORMAT_VERSION:
        raise ValueError(f"Unsupported backup version {version!r} (expected {FORMAT_VERSION})")

    report = ImportReport()

    # Validate everything before writing anything.
    new_settings = None
    if "settings" in document:
        current = dataclass_to_dict(store.current)
        incoming = document.get("settings") or {}
        if not isinstance(incoming, dict):
            raise ValueError("settings must be a mapping")
        merged = _deep_merge(current, incoming)
        new_settings = settings_from_sections(merged, strict=True)
    targets = document.get("targets") or []
    routes = document.get("routes") or []
    if not isinstance(targets, list) or not isinstance(routes, list):
        raise ValueError("targets and routes must be lists")
    for item in targets:
        if not isinstance(item, dict):
            raise ValueError("Each target must be a mapping")
        _validate_target(item, archive_root)
    rules = document.get("filter_rules")
    if rules is not None:
        if not isinstance(rules, list):
            raise ValueError("filter_rules must be a list")
        for rule in rules:
            validate_rule(str(rule.get("type")), str(rule.get("pattern") or ""))

    # Settings
    if new_settings is not None:
        await store.replace(new_settings)
        report.settings = True

    # Targets (two passes so fallbacks can reference any target by name)
    existing = {target.name: target for target in await repo.list_targets()}
    for item in targets:
        config = dict(item.get("config") or {})
        current = existing.get(item["name"])
        if config.get("password") in (None, "", REDACTED):
            config.pop("password", None)
            previous = repo.decrypt_config(current.config).get("password") if current is not None else None
            if previous:
                config["password"] = previous
            elif item["type"] == "webdav" and config.get("username"):
                report.warnings.append(f"Target {item['name']}: password missing, set it again in the Bot")
        if current is None:
            existing[item["name"]] = await repo.add_target(
                name=item["name"], type=item["type"], config=config, enabled=bool(item.get("enabled", True))
            )
            report.targets_added += 1
        else:
            existing[item["name"]] = await repo.update_target(
                current.id, type=item["type"], config=config, enabled=bool(item.get("enabled", True))
            )
            report.targets_updated += 1
    for item in targets:
        fallback_name = item.get("fallback")
        fallback = existing.get(fallback_name) if fallback_name else None
        if fallback_name and fallback is None:
            report.warnings.append(f"Target {item['name']}: fallback {fallback_name} not found")
        await repo.update_target(existing[item["name"]].id, fallback_target_id=fallback.id if fallback else None)

    # Routes
    existing_routes = {route.name: route for route in await repo.list_routes()}
    for item in routes:
        if not isinstance(item, dict) or not item.get("name") or not item.get("source_chat_id"):
            raise ValueError("Each route needs name and source_chat_id")
        target_ids = []
        for name in item.get("targets") or []:
            target = existing.get(name)
            if target is None:
                report.warnings.append(f"Route {item['name']}: target {name} not found")
            else:
                target_ids.append(target.id)
        values = dict(
            source_chat_id=str(item["source_chat_id"]),
            source_title=str(item.get("source_title") or ""),
            source_topic_id=item.get("source_topic_id"),
            target_ids=target_ids,
            watch_enabled=bool(item.get("watch_enabled", False)),
            overrides=item.get("overrides") or {},
            enabled=bool(item.get("enabled", True)),
        )
        current_route = existing_routes.get(item["name"])
        if current_route is None:
            await repo.add_route(name=item["name"], last_seen_msg_id=item.get("last_seen_msg_id"), **values)
            report.routes_added += 1
        else:
            await repo.update_route(current_route.id, **values)
            if item.get("last_seen_msg_id"):
                await repo.advance_watermark(current_route.id, int(item["last_seen_msg_id"]))
            report.routes_updated += 1

    if rules is not None:
        await repo.replace_filters((str(r["type"]), str(r["pattern"]), bool(r.get("enabled", True))) for r in rules)
        report.filters = len(rules)

    if replace:
        keep_routes = {item["name"] for item in routes}
        for name, route in existing_routes.items():
            if name not in keep_routes:
                await repo.delete_route(route.id)
                report.routes_removed += 1
        keep_targets = {item["name"] for item in targets}
        for target in await repo.list_targets():
            if target.name not in keep_targets:
                await repo.delete_target(target.id)
                report.targets_removed += 1
    return report
