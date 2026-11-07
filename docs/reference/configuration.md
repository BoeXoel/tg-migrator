# Commands & Settings Reference

This page is your complete lookup dictionary for all Telegram Bot commands, `.env` bootstrap environment variables, and runtime settings editable via `/set`.

---

## 1. Bot Commands Cheat Sheet

| Command | Arguments | Description |
| :--- | :--- | :--- |
| `/start` or `/help` | — | Open the main interactive control panel |
| `/lang` | `[en\|zh]` | Toggle or set Bot UI and notification language (`en` or `zh`) |
| `/status` | — | Show account capabilities, queue counters, worker state, and free disk space |
| `/tasks` | — | List recent migration tasks and inspect progress |
| `/targets` | — | List and manage all destinations (`telegram`, `local`, `webdav`) |
| `/bind` | `[code]` | In a group/topic: bind it as a target. In private chat: generate a 10-min one-time bind code |
| `/add_tg` | `<chat> [topic_id]` | Manually add a Telegram target by `@username` or `-100ID` and optional Forum Topic ID |
| `/add_local` | — | Launch wizard to create a Local NAS directory target under `ARCHIVE_ROOT` |
| `/add_webdav` | — | Launch wizard to create a WebDAV server target |
| `/routes` | — | List and manage continuous Watch Routes |
| `/add_route` | — | Launch wizard to create a new Watch Route |
| `/route_set` | `<id> <key> <val\|->` | Override a runtime setting for a specific route (use `-` to remove override) |
| `/config` | — | Open the interactive runtime settings panel |
| `/get` | `[section\|key]` | View all runtime settings, a specific section (e.g. `/get transfer`), or a single key |
| `/set` | `<key> <value>` | Modify a runtime setting immediately (persisted in DB) |
| `/filters` | — | View content filter switches and active keyword/regex rules |
| `/filter_add` | `keyword\|regex <pat>` | Add a content filter rule |
| `/filter_del` | `<rule_id>` | Delete a content filter rule by ID |
| `/export` | `[secrets]` | Export all settings, targets, routes, and filters to YAML (`/export secrets` includes passwords) |
| `/import` | `[replace]` | Reply to a YAML file with `/import` (merge) or `/import replace` (full overwrite) |
| `/pause` / `/resume` | — | Pause or resume the background transfer worker |
| `/retry` | `[task_id]` | Re-queue failed jobs globally or for a specific task |
| `/recover` | — | Reset interrupted `in_progress` jobs back to `pending` (when worker is paused) |
| `/cancel_task` | `<task_id>` | Cancel an active scan or queued task |
| `/verify` | — | Verify that delivered Telegram messages still exist in destination chats |
| `/report` | — | Export a CSV report of skipped oversized files |
| `/login` / `/logout` | — | Log in or log out the personal Telegram Reader account |
| `/cancel` | — | Abort the current multi-step interactive wizard |

---

## 2. Environment Variables (`.env`)

Bootstrap variables are loaded on container startup:

| Variable | Required | Default | Description |
| :--- | :--- | :--- | :--- |
| `API_ID` | **Yes** | — | Numeric Telegram API ID from [my.telegram.org](https://my.telegram.org) |
| `API_HASH` | **Yes** | — | 32-character Telegram API Hash |
| `BOT_TOKEN` | **Yes** | — | Bot token from [@BotFather](https://t.me/BotFather) |
| `ADMIN_IDS` | **Yes** | — | Comma-separated numeric Telegram User IDs allowed to control the Bot |
| `BOT_LANG` | No | `en` | Initial Bot interface language (`en` or `zh`) |
| `NOTIFY_CHAT_IDS` | No | `ADMIN_IDS` | Comma-separated chat IDs to receive system notifications |
| `USE_BOT_AS_WRITER` | No | `true` | Use the Bot (`true`) or the Reader user account (`false`) as the default Telegram writer |
| `DATABASE_URL` | No | *(SQLite)* | Leave empty for default SQLite (`data/migrator.db`), or set `postgresql+asyncpg://...` / `mysql+aiomysql://...` |
| `SECRET_KEY` | No | *(Auto)* | Master passphrase for encrypting WebDAV credentials at rest. Auto-generated at `data/secret.key` if empty |
| `PROXY_URL` | No | — | `socks5://user:pass@host:port` or `http://host:port` |
| `PROXY_APPLY_TO` | No | `telegram,webdav` | Traffic classes routed through proxy (`telegram`, `webdav`, or `telegram,webdav`) |
| `NO_PROXY` | No | — | Extra hostnames/CIDRs that bypass the proxy (private LAN/Docker names always bypass automatically) |
| `LOG_LEVEL` | No | `INFO` | `DEBUG`, `INFO`, `WARNING`, or `ERROR` |

---

## 3. Runtime Settings (`/set <key> <value>`)

All runtime settings are stored in the database and take effect immediately without restarting the container:

### `ui.*` — Interface
| Key | Default | Allowed Values / Description |
| :--- | :--- | :--- |
| `ui.language` | `en` | `en` (English) or `zh` (Simplified Chinese) |

### `transfer.*` — Transfer & Media Handling
| Key | Default | Description |
| :--- | :--- | :--- |
| `transfer.hide_sender` | `true` | Hide original sender header on native copy (`drop_author`) |
| `transfer.drop_caption` | `false` | Strip text captions from media messages |
| `transfer.strip_spoilers` | `true` | Remove Telegram spoiler masks from photos, videos, and text (forces re-upload when present) |
| `transfer.native_copy` | `true` | Attempt zero-download server-side copy first when Reader is also Writer and no spoiler removal is needed |
| `transfer.native_only` | `false` | Skip messages that cannot be copied natively instead of downloading & re-uploading |
| `transfer.allow_premium_user_fallback` | `false` | Use a logged-in Telegram Premium Reader account to upload 2–4 GiB files when Bot is the writer |
| `transfer.allow_download_unknown_size` | `false` | Allow downloading media whose file size is not reported upfront |
| `transfer.caption_overflow` | `split` | How to handle captions exceeding Telegram's limit: `split` (send overflow as follow-up text) or `truncate` |
| `transfer.include.text` | `true` | Include plain text messages |
| `transfer.include.photo` | `true` | Include photos |
| `transfer.include.video` | `true` | Include videos |
| `transfer.include.animation` | `true` | Include GIFs / animations |
| `transfer.include.audio` | `true` | Include music / audio files |
| `transfer.include.voice` | `true` | Include voice notes |
| `transfer.include.video_note` | `true` | Include round video notes |
| `transfer.include.document` | `false` | Include generic documents/archives (`.zip`, `.pdf`, `.apk`, etc.) |
| `transfer.include.sticker` | `false` | Include stickers |
| `transfer.include.other` | `false` | Include native-only kinds (polls, contacts, locations, dice) |

### `archive.*` — Local NAS & WebDAV Formatting
| Key | Default | Description |
| :--- | :--- | :--- |
| `archive.sidecar_format` | `json` | Metadata sidecar format: `json`, `md`, `both`, or `none` |
| `archive.sidecar_always` | `false` | Also write a sidecar file for media messages that have no caption |
| `archive.group_by_month` | `false` | Group archived files into `YYYY-MM` subdirectories |
| `archive.folder_naming` | `title_id` | Source folder naming style: `title_id`, `id`, or `title` |
| `archive.webdav_mode` | `auto` | WebDAV upload strategy: `auto`, `spool` (disk buffer), or `stream` (zero-disk streaming) |

### `limits.*` & `worker.*` — Rate Limiting & Pacing
| Key | Default | Description |
| :--- | :--- | :--- |
| `limits.download_delay_seconds` | `5.0` | Minimum delay between media downloads |
| `limits.upload_delay_seconds` | `30.0` | Minimum delay between media uploads |
| `limits.copy_delay_seconds` | `10.0` | Minimum delay between native server-side copies |
| `worker.batch_size` | `25` | Number of jobs processed before taking a batch cooldown pause |
| `worker.pause_between_batches_seconds` | `600` | Cooldown pause (in seconds) between batches to protect accounts from FloodWait |
| `worker.concurrency` | `1` | Number of concurrent worker tasks (`1`–`8`) |
| `watch.poll_interval_seconds` | `300` | Interval (in seconds) between watermark catch-up polls on Watch Routes |
| `downloads.min_free_bytes` | `5368709120` | Minimum free disk space (`5 GiB`) required before starting a new download |
