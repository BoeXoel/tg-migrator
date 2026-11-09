# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.0.0] - 2026-11-09

### Added
- **Admin-Scoped Bot Command Autocomplete Menu**: Automatically registers localized `/` command menus (`en` / `zh`) exclusively for user IDs in `ADMIN_IDS` via `BotCommandScopePeer` while clearing the global `BotCommandScopeDefault` menu so unauthorized users see no commands.
- **Dedicated `/help` Command Reference & Direct Item Lookup**: Split `/start` (concise readiness overview and 3-step onboarding guide) from `/help` (full categorized command dictionary), and added `/targets <id>` (`/target <id>`), `/routes <id>` (`/route <id>`), and `/tasks <id>` (`/task <id>`) to open any item's management card directly.

### Changed
- **De-Bloated Paginated Bot Console (`PAGE_SIZE = 10`)**: Replaced per-item inline button grids in `/targets`, `/routes`, `/tasks`, and `/filters` with clean paginated text lists (`⬅️ Prev` / `📄 X/Y` / `Next ➡️`) so the Bot UI remains compact regardless of how many destinations, routes, or tasks exist.
- **Reply-ID Target Selection for Message Links**: Sending a Telegram message link now displays a paginated text list of enabled targets and launches the migration task immediately when the admin replies with target ID(s) (e.g. `1` or `1, 2`).
- **Documentation Site & README Overhaul**: Added custom site logo and SVG favicon, dynamic copyright year in the VitePress footer, objective third-person FAQ tone, standardized WebDAV examples on OpenList, and redesigned the bilingual `README` with a centered hero header, ASCII architecture diagram, and quick-reference layout.

### Fixed
- **Documentation Homepage `MutationObserver` Freeze**: Guarded the dynamic footer copyright year script in `docs/.vitepress/config.mts` so it only writes to the DOM when the year string actually changes, eliminating an infinite microtask loop on `layout: home` pages.

## [0.0.3] - 2026-11-07

### Added
- **Bilingual VitePress Documentation Site (`docs/`)**: Comprehensive English (`/`) and Simplified Chinese (`/zh/`) documentation covering Docker deployment, directory permissions, `/login` inline keypad & 2FA, Forum Sub-Topic `/bind` workflows, Local NAS/WebDAV archiving, Watch Routes, command/setting dictionaries, and troubleshooting FAQ.
- **Cloudflare Workers Static Assets Deployment (`.github/workflows/docs.yml`)**: Automated VitePress build and zero-script static asset deployment to Cloudflare Workers (`tg-migrator-docs`) with `workers_dev: false`.
- **Automatic GitHub Release Publishing**: Updated `.github/workflows/docker-publish.yml` to automatically extract version notes from `CHANGELOG.md` and publish a GitHub Release whenever a new version is merged into `main`.

### Changed
- **Round-Robin Multi-Task Scheduling & Concurrency**: `Repository.fetch_due_jobs` and `Worker.run` now interleave active tasks and live-watch routes in fair Round-Robin waves (default `worker.concurrency = 3`) while preserving chronological message order within each task/stream.
- **Independent Per-Operation Rate Locks**: Refactored `TelegramLimiter` so `read`, `download`, `upload`, and `copy` operations maintain independent locks and timers without blocking one another.
- **Single-Upload Album Pacing & Tuned Cooldowns**: Fixed `TelegramTarget._send_album` so multi-item albums count as a single upload interval instead of pacing every intermediate chunk; tuned default `limits.upload_delay_seconds` to `10.0s`, `worker.batch_size` to `50` (counting only actual Telegram deliveries), and `worker.pause_between_batches_seconds` to `120s`.

## [0.0.2] - 2026-11-07

### Added
- **Bilingual Bot UI (`en` / `zh`)**: Default Bot interface language is now English (`en`), configurable via `BOT_LANG` in `.env` and switchable at runtime via `/lang`, `/set ui.language zh|en`, or the `🌐 Language` button in the Bot panel.
- **Complete Back-Navigation in Bot Panel**: Added `⬅️ Main Menu` buttons across all sub-panels (`status`, `tasks`, `targets`, `routes`, `config`) and inline `✖️ Cancel` buttons on interactive wizards.
- **Markdown-Safe Credential Input**: Secret/password prompts (WebDAV password and 2FA password) now support backtick-wrapped inline code (`` `password` ``) and automatically detect & reject inputs corrupted by Telegram Markdown formatting (`_`, `*`, `~`, `||`).
- **Documentation & Community Standards**: Added credentials preparation guide in bilingual `README`, `CHANGELOG.md`, and `.github/` community health files (`SECURITY.md`, `CONTRIBUTING.md`, Issue & PR templates).

### Changed
- **CI/CD Docker Publishing**: Streamlined `.github/workflows/docker-publish.yml` to publish exclusively to Docker Hub (`boexoel/tg-migrator`) with automatic semantic version tags (`0.0.2`, `v0.0.2`, `latest`, `sha-*`).

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
