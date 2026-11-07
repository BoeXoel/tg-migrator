# Troubleshooting & FAQ

This page collects real-world deployment issues, common configuration pitfalls, and their one-line fixes.

---

## 1. Container Startup Issues

### `PermissionError: [Errno 13] Permission denied: '/app/downloads/active'`
- **Cause**: Docker automatically created the host bind-mount directories (`data`, `sessions`, `downloads`, `archive`) as `root:root`, whereas the container runs as non-root user `migrator (1000:1000)`.
- **Fix**: Run the following command in your deployment directory on the host and restart:
  ```bash
  sudo chown -R 1000:1000 data sessions downloads archive
  docker compose restart tg-migrator
  ```

---

### `socket.gaierror: [Errno -3] Temporary failure in name resolution` (for `postgres` or `mysql`)
- **Cause**: You uncommented `DATABASE_URL=postgresql+asyncpg://...@postgres:5432/...` in `.env`, but started the stack with `docker compose up -d` (without `--profile postgres`), so no `postgres` container exists on the Docker network.
- **Fix**:
  - **If you want the default built-in SQLite database**: Edit `.env` and leave `DATABASE_URL=` **completely empty**, then run `docker compose up -d`.
  - **If you want PostgreSQL**: Start with `docker compose --profile postgres up -d`.

---

### `required variable MYSQL_PASSWORD / POSTGRES_PASSWORD is missing a value`
- **Cause**: An older `docker-compose.yml` from `v0.0.1` used strict `:?` variable interpolation even when database profiles were disabled.
- **Fix**: Download the updated `docker-compose.yml` from `v0.0.2+` (which uses `:-`), or set dummy values in `.env`.

---

## 2. Bot & Transfer Questions

### Why did `/bind` bind the group's main `General` topic instead of my Sub-Topic?
- **Cause**: `/bind` binds whichever topic the command message is sent inside. If you type `/bind` inside `# General`, `topic_id` is `None`.
- **Fix**:
  1. Open the target Forum group and **click into the specific Sub-Topic** first;
  2. Send `/bind` inside that sub-topic's message input box;
  3. Or, in private chat with the Bot, run `/add_tg -100<GroupID> <TopicID>`.

---

### Why are `.zip`, `.pdf`, or general file attachments being skipped?
- **Cause**: By default, `transfer.include.document` is set to `false` so media-focused channels don't accidentally pull unwanted attachments.
- **Fix**: Enable document transfer in the Bot:
  ```text
  /set transfer.include.document true
  ```
  *(Or tap **`⚙️ Settings` → `📦 Media Types` → `📄 Document`** to toggle it on.)*

---

### Why did my WebDAV or 2FA password fail when it contains `_`, `*`, `~`, or `||`?
- **Cause**: Telegram's chat client automatically interprets pairs of `_` as *italic*, `*` as **bold**, `~` as ~~strikethrough~~, and `||` as spoiler, stripping those symbols before the Bot receives the message.
- **Fix**: Wrap your password in single backticks (inline code) when sending it to the Bot:
  ```text
  `your_password*with_symbols`
  ```
  The Bot automatically strips the surrounding backticks and preserves every symbol inside verbatim.

---

### Why does the Bot ignore my messages in private chat?
- **Cause**: For security, TG Migrator enforces a strict `ADMIN_IDS` allowlist. Any user ID not listed in `ADMIN_IDS` is silently ignored.
- **Fix**: Message [@userinfobot](https://t.me/userinfobot) to verify your numeric Telegram User ID, add it to `ADMIN_IDS=` in `.env`, and run `docker compose up -d`.
