# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- **Bilingual Bot UI (`en` / `zh`)**: Default Bot interface language is now English (`en`), configurable via `BOT_LANG` in `.env` and switchable at runtime via `/lang`, `/set ui.language zh|en`, or the `🌐 Language` button in the Bot panel.
- **Complete Back-Navigation in Bot Panel**: Added `⬅️ Main Menu` buttons across all sub-panels (`status`, `tasks`, `targets`, `routes`, `config`) and inline `✖️ Cancel` buttons on interactive wizards.
- **Markdown-Safe Credential Input**: Secret/password prompts (WebDAV password and 2FA password) now support backtick-wrapped inline code (`` `password` ``) and automatically detect & reject inputs corrupted by Telegram Markdown formatting (`_`, `*`, `~`, `||`).

### Fixed
- **Docker Compose Default Startup**: Changed optional `POSTGRES_PASSWORD` and `MYSQL_PASSWORD` interpolation in `docker-compose.yml` from `:?` to `:-` so `docker compose up -d` starts cleanly with SQLite when external database profiles are not enabled.

## [0.0.1] - 2026-11-06

### Added
- **Interactive Telegram Bot Control**: Full task creation, target binding (`/bind`, `/add_tg`, `/add_local`, `/add_webdav`), watch route management, runtime configuration, and keypad-based reader account login (`/login`) with strict `ADMIN_IDS` allowlist enforcement.
- **Multi-Sink Architecture**:
  - `telegram` destination supporting zero-download native copy, local stream re-upload fallback with spoiler stripping, media metadata & safe thumbnail preservation, Forum Topic targeting, and Album grouping.
  - `local` destination with strict `ARCHIVE_ROOT` path confinement and configurable JSON/Markdown sidecar metadata.
  - `webdav` destination supporting HTTPS and internal HTTP endpoints in both disk-spool (`spool`) and zero-disk streaming (`stream`) modes.
- **Continuous Watch & Routing Engine**: Real-time event listener combined with periodic `last_seen_msg_id` watermark polling and per-route setting overrides.
- **Multi-Database Persistence**: SQLAlchemy 2.0 Async ORM supporting SQLite (default), PostgreSQL (`asyncpg`), and MySQL/MariaDB (`aiomysql`), plus atomic YAML configuration backup and restore (`/export` and `/import`).
- **Proxy & LAN Bypass**: SOCKS5 and HTTP CONNECT proxy support with automatic bypass for loopback, RFC1918, CGNAT, single-label Docker service hostnames, and DNS-resolved private IPs.
- **Containerized Deployment**: Multi-stage hardened `Dockerfile` (`linux/amd64` and `linux/arm64`) with SHA256 hash-locked dependencies and non-root execution.
