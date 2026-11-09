<div align="center">
  <img src="./docs/public/logo.png" alt="TG Migrator logo" width="80" height="80" />
  <h1>TG Migrator</h1>
  <p><strong>Your Telegram chats, archived and routed anywhere.</strong></p>
  <p>Send a message link to your Bot to migrate or mirror chats into Telegram Topics, local NAS, or WebDAV.<br />Self-hosted, open source, controlled entirely from Telegram.</p>
  <p>
    <a href="https://hub.docker.com/r/boexoel/tg-migrator" target="_blank" rel="noopener noreferrer"><img src="https://img.shields.io/docker/v/boexoel/tg-migrator?sort=semver&color=0284c7&label=docker" alt="Docker version" /></a>
    <a href="./LICENSE"><img src="https://img.shields.io/github/license/BoeXoel/tg-migrator?color=52525b" alt="AGPL-3.0 license" /></a>
    <a href="https://github.com/BoeXoel/tg-migrator/stargazers"><img src="https://img.shields.io/github/stars/BoeXoel/tg-migrator?style=flat&color=52525b" alt="GitHub stars" /></a>
  </p>
  <p><a href="https://tg-migrator.isgay.top/" target="_blank" rel="noopener noreferrer">Documentation</a> · <a href="https://hub.docker.com/r/boexoel/tg-migrator" target="_blank" rel="noopener noreferrer">Docker Hub</a> · <a href="./docs/README.zh-CN.md">简体中文</a></p>
</div>

## Quick start

Download [`docker-compose.yml`](./docker-compose.yml) and [`.env.example`](./.env.example), fill in `API_ID`, `API_HASH`, `BOT_TOKEN`, and `ADMIN_IDS`, then start the container:

```sh
cp .env.example .env
mkdir -p data sessions downloads archive && sudo chown -R 1000:1000 data sessions downloads archive
docker compose pull && docker compose up -d
```

Open a private chat with your Bot in Telegram:

```text
  /start                                  → check system readiness
  /login                                  → sign in reader account via inline keypad
  /bind                                   → bind a destination chat or Forum Topic
  https://t.me/c/1234567890/100-500       → reply with target ID to start migrating
```

**Private by default:** only user IDs listed in `ADMIN_IDS` receive the Bot's `/` command menu and responses. Everyone else is silently ignored.

TG Migrator is free and open source. If it helps you, [a star on GitHub](https://github.com/BoeXoel/tg-migrator) helps other self-hosters find it, and it's the best encouragement for the project.

## Why TG Migrator

- **Controlled entirely from Telegram.** No extra web panel to expose. Log in via an anti-interception inline keypad, bind destinations, manage watch routes, and tune runtime settings directly in your Bot chat.
- **Scales without UI clutter.** Paginated text lists (`/targets`, `/routes`, `/tasks`) and direct ID commands (`/targets 1`, `/routes 2`, `/tasks 15`) keep the Bot console crisp even with hundreds of routes and tasks.
- **Multi-sink by design.** Deliver messages to other Telegram chats (including specific Forum Sub-Topics), mounted NAS directories (`ARCHIVE_ROOT`), or WebDAV storage (OpenList, Nextcloud, etc.) with both disk-spooling and zero-disk streaming modes.
- **Zero-download when possible, lossless when not.** Uses native copy whenever allowed; automatically falls back to local stream re-upload when cross-chat copy is unavailable or when stripping spoiler masks, preserving video dimensions, audio tags, voice waveforms, original filenames, and full Album grouping.
- **Continuous watch with watermark catch-up.** Real-time event listeners aggregate Albums on the fly, while periodic `last_seen_msg_id` watermark polling backfills anything missed during container restarts.
- **Built to protect your account.** Fair round-robin multi-task scheduling, independent per-operation rate limiters, batch cooldown pauses, dynamic FloodWait backoff, and automatic circuit-breaker pause on account restrictions.

## Use it to

- consolidate scattered groups and channels you belong to into a single, organized Forum Topic group,
- archive media, documents, and structured `.json` / `.md` metadata sidecars to a home NAS or OpenList / Nextcloud WebDAV server,
- mirror ongoing messages from a source chat (or a specific Forum Topic) to multiple destinations 24/7,
- automatically offload oversized `2–4 GiB` files to a linked Local / WebDAV fallback target (or upload via a logged-in Premium account),
- maintain portable self-hosted setups across SQLite, PostgreSQL, or MySQL with one-command YAML backup and restore.

## Usage

Send message links or commands directly to the Bot in a private chat:

```text
<message_link>                      https://t.me/c/1234567890/100
<range_link>                        https://t.me/c/1234567890/100-500
<topic_message_link>                https://t.me/c/1234567890/456/100

  /start, /help                     readiness overview & full command reference
  /login, /logout                   log in or log out reader account
  /bind                             bind a Telegram group, channel, or Forum Topic
  /add_local, /add_webdav           add a local NAS folder or WebDAV endpoint
  /targets [id]                     paginated target list, or manage target #<id>
  /add_route, /routes [id]          create watch route, or manage route #<id>
  /tasks [id], /status              paginated task queue, or inspect/cancel task #<id>
  /pause, /resume, /retry [id]      pause/resume worker queue or retry failed items
  /config, /get, /set <k> <v>       interactive settings panel or live parameter update
  /filters                          keyword/regex allowlists, blocklists & media filters
  /export [secrets], /import        YAML configuration backup and atomic restore
  /lang                             switch Bot UI language (English / 简体中文)
```

When you paste a link, the Bot displays your enabled targets. Reply with one or more target IDs (e.g. `1` or `1, 2`) to launch the task immediately.

### Oversized files & fallbacks

- Standard Telegram accounts and Bots can upload up to `2000 MiB` per file (`4000 MiB` for Telegram Premium).
- Open any Telegram target via `/targets <id>` to link a **Local or WebDAV fallback target** for files exceeding the limit, or enable `/set transfer.allow_premium_user_fallback true` to let a logged-in Premium reader account upload `2–4 GiB` files.
- Downloads always respect `downloads.min_free_bytes` (`5 GiB` by default) so large queues never exhaust host disk space.

## How it works

```text
                                                               ┌──▶ Telegram Chat / Forum Topic
Source Chat / Topic ──▶ Reader / Bot ──▶ Fair Queue & Limiter ─┼──▶ Local NAS (ARCHIVE_ROOT)
                                                               └──▶ WebDAV (OpenList / Nextcloud)
```

TG Migrator runs two coordinated Telethon sessions inside a single non-root container: a **Bot client** for interactive control and standard deliveries, and an optional **Reader account** for reading joined private chats and handling `2–4 GiB` Premium uploads. Tasks and live-watch routes are interleaved in fair round-robin waves with independent rate-limit locks for `read`, `download`, `upload`, and `copy`.

Full guides for Docker deployment, Forum Topic binding, WebDAV modes, database profiles, and proxy rules are in the <a href="https://tg-migrator.isgay.top/" target="_blank" rel="noopener noreferrer">Documentation</a>.

## Troubleshooting

Common issues during deployment and daily operation:

| Symptom | Meaning | What to do |
| --- | --- | --- |
| `PermissionError: '/app/data/secret.key'` | Host volume is owned by `root` while container runs as `1000:1000` | Run `sudo chown -R 1000:1000 data sessions downloads archive` |
| `could not translate host name "postgres"` | `DATABASE_URL` points to `postgres` without starting the DB profile | Comment out `DATABASE_URL` for SQLite, or run `docker compose --profile postgres up -d` |
| Bot doesn't respond to `/start` | Your Telegram User ID is not in `ADMIN_IDS` | Add your numeric ID to `ADMIN_IDS` in `.env` and restart the container |
| `ChatAdminRequiredError` / `Topic closed` | Bot lacks post/topic permissions in the destination group | Grant the Bot `Post Messages` and `Manage Topics` permissions in the target chat |
| `FILE_TOO_BIG` (`> 2000 MiB`) | File exceeds standard Bot upload limit | Link a Local/WebDAV fallback in `/targets <id>` or enable Premium fallback |

## Contributing

Issues and pull requests are welcome. See [CONTRIBUTING.md](./CONTRIBUTING.md).

## Disclaimer

This project is a self-hosted personal backup and archiving tool intended strictly for organizing messages and media that **you already have legitimate permission to access**. Users must comply with local laws, copyright regulations, and the <a href="https://telegram.org/tos" target="_blank" rel="noopener noreferrer">Telegram Terms of Service</a>.

## License

[AGPL-3.0](./LICENSE)
