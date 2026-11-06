# TG Migrator

**English** | [简体中文](docs/README.zh-CN.md) | [Docker](https://hub.docker.com/r/boexoel/tg-migrator)

> **Self-Hosted Telegram Media Archiver & Multi-Sink Message Router Controlled via Telegram Bot**
> Synchronize and archive messages from Telegram groups, channels, or Forum Topics that you have legitimate access to into **other Telegram chats**, **local NAS directories**, or **WebDAV storage (Alist / Nextcloud / etc.)** — supporting both one-off historical migrations and 24/7 real-time watch routing.

---

## Key Features

- **Interactive Bot Control**: Create migration tasks simply by sending message links to your Bot in a private chat. Target binding, watch routes, content filters, and runtime settings are all managed via interactive inline panels or commands. Built-in strict `ADMIN_IDS` allowlist authentication silently ignores unauthorized users.
- **Multi-Sink Destinations**:
  - `telegram`: Supports target groups, channels, and Forum Topics. Uses zero-download **Native Copy** whenever possible; automatically falls back to **local stream re-upload** when cross-chat native copy is unavailable or when stripping spoiler masks, preserving video dimensions/duration, audio tags, voice waveforms, original filenames, safe thumbnails, and full Album grouping.
  - `local`: Structured archiving into mounted container directories (strictly confined within `ARCHIVE_ROOT` to prevent path traversal).
  - `webdav`: Supports both public HTTPS and internal HTTP endpoints (e.g., `http://alist:5244/dav`), offering both **local disk spooling (`spool`)** and **zero-disk streaming (`stream`)** modes.
- **Continuous Watch & Multi-Destination Routing**: Configure persistent routes mapping a single source (optionally scoped to a specific Forum Topic) to one or multiple destinations. Combines real-time event listening (with automatic Album aggregation) and periodic watermark (`last_seen_msg_id`) polling to automatically catch up on missed messages after restarts.
- **Comprehensive Media & Message Coverage**: Plain text, photos, videos, GIFs, audio tracks, voice notes, circular video notes, general documents, and stickers; polls and locations are supported via native copy.
- **Smart Oversized File Handling**: Automatically detects upload limits for standard accounts/Bots (`2000 MiB`) and Telegram Premium (`4000 MiB`). Supports optional fallback to a logged-in Premium reader account for `2–4 GiB` files, or automatic offloading of oversized files to a linked Local / WebDAV fallback destination.
- **Multi-Database Backend & Portable Configs**: Powered by SQLAlchemy 2.0 Async ORM, supporting **SQLite** (default, zero external dependencies), **PostgreSQL**, and **MySQL / MariaDB** out of the box. Full configuration backup and restore via YAML (`/export` and `/import`).
- **Production-Grade Resilience & Account Protection**: Per-operation token-bucket rate limiting, dynamic FloodWait backoff with jitter, automatic circuit-breaker pause and admin notifications on account restrictions, minimum free disk space guard, interrupted task recovery, and failed item retry.
- **Smart Proxy & LAN Bypass**: Supports SOCKS5 and HTTP CONNECT proxies with built-in private network detection and DNS resolution checks — automatically bypassing the proxy when connecting to LAN or Docker-internal WebDAV endpoints.

---

## Quick Start (Docker Deployment)

> [!IMPORTANT]
> **Prerequisites — Required Telegram Credentials (for `.env`)**
> Before starting the service, prepare the following three credentials:
> - **`API_ID` & `API_HASH`**: Log in to [my.telegram.org](https://my.telegram.org) and create an application under **API development tools** to obtain them.
> - **`BOT_TOKEN`**: Message [@BotFather](https://t.me/BotFather) on Telegram and send `/newbot` to create a control Bot and obtain its HTTP API token.
> - **`ADMIN_IDS`**: Your personal numeric Telegram User ID (comma-separated for multiple admins), obtainable by messaging bots such as [@userinfobot](https://t.me/userinfobot).

### Option 1: Deploy with Pre-Built Docker Hub Image (Recommended)

Pre-built multi-architecture images (`linux/amd64` and `linux/arm64`) are published on Docker Hub at **[`boexoel/tg-migrator`](https://hub.docker.com/r/boexoel/tg-migrator)**. You only need [`docker-compose.yml`](docker-compose.yml) and [`.env.example`](.env.example):

```bash
# 1. Copy and edit environment variables (fill in API_ID / API_HASH / BOT_TOKEN / ADMIN_IDS)
cp .env.example .env

# 2. Create persistent directories matching PUID/PGID in .env (default 1000:1000)
mkdir -p data sessions downloads archive
sudo chown -R 1000:1000 data sessions downloads archive

# 3. Pull image and start in the background
docker compose pull && docker compose up -d
docker compose logs -f
```

### Option 2: Build Locally from Source

If you modified the code and want to build the image locally, uncomment `build: .` in [`docker-compose.yml`](docker-compose.yml) and run:

```bash
docker compose up -d --build
```

---

## Initial Setup & Workflow

Once the container is running, open a private chat with your Bot in Telegram:

1. **Open Main Panel**: Send `/start` to view system status and the interactive menu.
2. **Log In Reader Account**:
   - Send `/login` and follow the prompt to enter your phone number. Enter the verification code via the **inline numeric keypad** (preventing plaintext login codes from being intercepted in chat history). If 2FA is enabled, the Bot immediately deletes your password message after reading it.
   - *Alternative*: You can also log in interactively from the server terminal via `docker compose run --rm migrator login`.
3. **Configure Destinations (Targets)**:
   - **Telegram Group / Channel / Topic**: Add the Bot to the destination chat with permission to post messages, then send `/bind` directly inside the target chat (or inside the target Forum Topic). For channels or anonymous admins, send `/bind` to the Bot in a private chat first to generate a one-time binding token, then post that token in the target channel.
   - **Local Directory**: Send `/add_local` in private chat to bind a subdirectory under `ARCHIVE_ROOT`.
   - **WebDAV Storage**: Send `/add_webdav` in private chat to configure the endpoint and credentials (automatically performs an `MKCOL` / `PUT` / `DELETE` connectivity check).
4. **Create One-Off Migration Tasks**:
   Send Telegram message links directly to the Bot:
   - Single message (Albums are automatically detected and migrated as a group): `https://t.me/c/1234567890/100`
   - Message range (single range link or two separate start/end links): `https://t.me/c/1234567890/100-500`
   - Specific Forum Topic message: `https://t.me/c/1234567890/456/100`

---

## Continuous Sync & Watch Routes

Send `/add_route` to launch the interactive wizard: **Route Name → Source Chat (optional Topic ID) → One or more Target IDs → Enable Real-Time Watch**.

- **Real-Time Events + Watermark Catch-Up**: When watching is enabled, new messages are captured in real time (with Album aggregation) and periodically checked every `watch.poll_interval_seconds` (as well as on startup) against the stored `last_seen_msg_id` watermark to backfill any messages missed during downtime.
- **Historical Backfill**: Click **"Scan History"** in the `/routes` detail panel to enqueue existing historical messages from the route's source.
- **Per-Route Overrides**: Override global settings for a specific route:
  - `/route_set <route_id> transfer.include.document true`
  - `/route_unset <route_id> transfer.include.document`

> **Note**: Do not configure two routes whose source and destination point to each other, as this will cause an infinite forwarding loop.

---

## Archive Directory Layout (Local / WebDAV)

Files and sidecar metadata follow a clean, flat directory hierarchy by default:

```text
<source_name_id>/[topic_<id>/][YYYY-MM/]<msg_id>_<original_filename>
<source_name_id>/[topic_<id>/][YYYY-MM/]<msg_id>.json
```

- **No Redundant Nesting**: Non-topic messages omit the `topic_<id>/` folder; monthly subdirectories (`YYYY-MM/`) are disabled by default (enable via `/set archive.group_by_month true`).
- **Structured Sidecar Metadata**: Text messages and media captions generate a companion `.json` file by default (containing message ID, original timestamp, sender, topic ID, entities, and saved file references). Switch to Markdown, both, or none via `/set archive.sidecar_format md|both|none`.
- **Source Folder Naming**: Configurable via `archive.folder_naming` (`title_id` by default, `id`, or `title`).

---

## Runtime Configuration

Send `/config` to open the interactive button panel (toggle media types, transfer policies, archive formats, and filters). All settings can also be inspected via `/get [prefix]` and updated live via `/set <key> <value>`, persisting immediately to the database without restarting the container.

| Setting Key | Default | Description |
| --- | --- | --- |
| `transfer.hide_sender` | `true` | Hide original sender header during native copy |
| `transfer.native_copy` / `native_only` | `true` / `false` | Prefer zero-download native copy / require native copy only |
| `transfer.strip_spoilers` | `true` | Strip media spoiler masks (triggers download & re-upload when present) |
| `transfer.allow_premium_user_fallback` | `false` | Allow logged-in Premium reader account to upload `2–4 GiB` files |
| `archive.webdav_mode` | `auto` | `auto` (spool when disk space suffices, stream otherwise), `stream` (zero-disk streaming), `spool` (disk spooling) |
| `archive.sidecar_format` | `json` | Metadata sidecar format: `json` / `md` / `both` / `none` |
| `archive.group_by_month` | `false` | Group archived files into `YYYY-MM/` monthly folders |
| `worker.batch_size` / `pause_between_batches_seconds` | `25` / `600` | Items processed per batch and cooldown pause between batches to reduce flood/restriction risk |
| `downloads.min_free_bytes` | `5368709120` (5 GiB) | Minimum free disk space maintained in the downloads directory |
| `limits.*` | — | Per-operation minimum intervals and FloodWait backoff thresholds |

---

## YAML Backup & Restore

- **Export**: Send `/export` to export all settings, targets, watch routes, and filters as a `.yaml` file (WebDAV passwords are redacted as `<REDACTED>` by default). Send `/export secrets` to include plaintext passwords (store securely).
- **Import**:
  - **Merge Mode (default)**: Send a `.yaml` file directly to the Bot to incrementally merge and update by name/key.
  - **Replace Mode**: Send `/import replace` followed by a `.yaml` file to perform a full synchronization and remove targets/routes absent from the backup.
  - Imports are validated atomically before committing; if validation fails, the database remains untouched. Redacted `<REDACTED>` passwords preserve existing encrypted values in the database.

---

## Multi-Database Support

Configure `DATABASE_URL` in `.env` (leave empty to use the default SQLite database at `./data/migrator.db`):

```env
# SQLite (Default, zero external dependencies)
DATABASE_URL=

# PostgreSQL
DATABASE_URL=postgresql+asyncpg://migrator:your_password@postgres:5432/migrator

# MySQL / MariaDB
DATABASE_URL=mysql+aiomysql://migrator:your_password@mysql:3306/migrator
```

[`docker-compose.yml`](docker-compose.yml) includes optional database service profiles (set `POSTGRES_PASSWORD` or `MYSQL_PASSWORD` in `.env` before starting):

```bash
docker compose --profile postgres up -d
# or
docker compose --profile mysql up -d
```

---

## Proxy & Internal Network Bypass

Configure proxy settings in `.env` (managed strictly via environment variables to prevent accidental Bot lockout):

```env
PROXY_URL=socks5://user:pass@proxy.example:1080
PROXY_APPLY_TO=telegram,webdav
NO_PROXY=nas.local,203.0.113.0/24
PROXY_BYPASS_RESOLVE=true
```

- **Fail-Closed Telegram Proxy**: When a proxy is enabled for Telegram traffic, connection failures never silently fall back to direct connections, protecting the host IP.
- **Automatic Internal WebDAV Bypass**: The following WebDAV targets automatically bypass the proxy and connect directly:
  - `localhost` and loopback addresses (`127.0.0.0/8`, `::1`)
  - Private & reserved ranges (`10.0.0.0/8`, `172.16.0.0/12`, `192.168.0.0/16`, `169.254.0.0/16`, `100.64.0.0/10` CGNAT/Tailscale, IPv6 ULA/Link-Local)
  - Single-label hostnames (e.g., Docker Compose service names like `alist`, `nextcloud`)
  - `.local`, `.lan`, `.internal`, `.home.arpa` domains, and entries in `NO_PROXY`
  - Custom domains resolving to any of the private IPs above when `PROXY_BYPASS_RESOLVE=true` (default).

---

## Security Architecture

- **Supply-Chain Integrity**: Uses official upstream libraries (Telethon, SQLAlchemy, httpx, cryptography). [`requirements.txt`](requirements.txt) locks SHA256 hashes for all wheels, and container builds enforce `--require-hashes --only-binary=:all:`.
- **Credential Encryption & Least Privilege**:
  - WebDAV credentials are encrypted at rest in the database using Fernet symmetric encryption (`SECRET_KEY` or auto-generated `data/secret.key`).
  - Telethon session files (`sessions/`) and key files enforce `0600` permissions.
  - Logs automatically redact Bot tokens and URL credentials; content filter matches log only rule IDs, never sensitive message text.
- **Container Hardening**: Runs as a non-root user (`UID/GID 1000`) with a read-only root filesystem (`read_only: true`), `no-new-privileges:true`, and all Linux capabilities dropped (`cap_drop: ALL`).

---

## Bot Command Reference

| Category | Commands | Description |
| --- | --- | --- |
| **Status & Tasks** | `/start` `/status` `/tasks` | Open main menu, view live runtime status and recent tasks |
| **Queue Control** | `/pause` `/resume` `/recover` | Pause worker queue, resume queue, recover interrupted tasks |
| **Retry & Cancel** | `/retry [task_id]` `/cancel_task <id>` | Re-queue failed items, cancel an active task |
| **Targets** | `/targets` `/bind` `/add_tg` `/add_local` `/add_webdav` | List, bind, add, or test storage destinations |
| **Watch Routes** | `/routes` `/add_route` `/route_set` `/route_unset` | Manage continuous routes, historical scans, and per-route overrides |
| **Configuration** | `/config` `/get [prefix]` `/set <key> <value>` | Interactive settings UI, query or live-update runtime parameters |
| **Content Filters** | `/filters` `/filter_add` `/filter_del` `/filter_toggle` | Manage keyword/regex allowlists/blocklists and media filters |
| **Backup & Restore** | `/export [secrets]` `/import [replace]` | Export or import YAML configuration backups |
| **Verify & Reports** | `/verify <task_id>` `/report [task_id]` | Sample-verify delivered messages, export CSV report of skipped oversized files |
| **Session** | `/login` `/logout` `/cancel` | Log in or log out reader account, abort active interactive wizard |

---

## Development & Testing

```bash
# Recompile SHA256 hash-locked dependency files (targeting Python 3.12)
uv pip compile --python-version 3.12 --generate-hashes requirements.in -o requirements.txt
uv pip compile --python-version 3.12 --generate-hashes requirements-dev.in -o requirements-dev.txt

# Run the full unit test suite inside the isolated container test stage
docker build --target test -t tg-migrator:test . && docker run --rm tg-migrator:test
```

---

## Disclaimer

1. This project is provided strictly as a self-hosted personal data backup, chat migration, and knowledge archiving tool, intended to help users organize and preserve messages and media that **they already have legitimate permission to access**.
2. Users must comply with all applicable local laws, copyright and intellectual property regulations, as well as the [Telegram Terms of Service](https://telegram.org/tos) and [Telegram API Terms of Service](https://core.telegram.org/api/terms).
3. Please respect the rights of original content creators. Do not use this tool for unauthorized commercial redistribution, copyright infringement, or violation of community guidelines. The user assumes full responsibility for any account restrictions or legal consequences arising from misuse.

---

## License

This project is licensed under the **[GNU Affero General Public License v3.0 (AGPL-3.0)](LICENSE)**.

- You are free to use, modify, and distribute the source code of this software under the terms of the AGPL-3.0.
- If you modify this software or provide a service based on this software over a network (including via a Telegram Bot interface), you **must** make the complete modified source code freely available under the same AGPL-3.0 license.
- Closed-source commercial distribution or paid hosted service offerings are prohibited without a separate commercial license from the author.
