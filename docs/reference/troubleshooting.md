# Troubleshooting & FAQ

This page summarizes common deployment and operational issues, their root causes, and standard resolutions.

---

## 1. Container Startup Issues

### `PermissionError: [Errno 13] Permission denied: '/app/downloads/active'`
- **Cause**: When `docker compose up -d` is executed without pre-creating host mount directories, the Docker daemon creates `data`, `sessions`, `downloads`, and `archive` with `root:root` ownership. Because the container runs as the non-privileged user `migrator (1000:1000)`, write operations to those directories fail.
- **Resolution**: Update directory ownership to UID/GID `1000:1000` on the host and restart the container:
  ```bash
  sudo chown -R 1000:1000 data sessions downloads archive
  docker compose restart tg-migrator
  ```

---

### `socket.gaierror: [Errno -3] Temporary failure in name resolution` (for `postgres` or `mysql`)
- **Cause**: `DATABASE_URL` in `.env` is configured to connect to an internal container hostname (such as `@postgres:5432` or `@mysql:3306`), but the stack was started with standard `docker compose up -d` (without `--profile postgres` or `--profile mysql`), leaving no database container on the Docker network.
- **Resolution**:
  - **To use the built-in SQLite database (default, recommended)**: Leave `DATABASE_URL=` empty in `.env` and run `docker compose up -d`.
  - **To use PostgreSQL or MySQL**: Start the stack with the corresponding Compose profile enabled, e.g. `docker compose --profile postgres up -d`.

---

### `required variable MYSQL_PASSWORD / POSTGRES_PASSWORD is missing a value`
- **Cause**: The `v0.0.1` `docker-compose.yml` used strict `:?` variable interpolation, which required database password variables to be set even when the database profiles were inactive.
- **Resolution**: Update to the `docker-compose.yml` from `v0.0.2` or later (which uses `:-` fallback syntax).

---

## 2. Bot Operations & Transfer FAQ

### Why does `/bind` bind the main `General` topic instead of a specific Forum Sub-Topic?
- **Cause**: The `/bind` command binds the specific topic context in which the message is posted. When `/bind` is sent inside `# General`, no sub-topic `topic_id` is attached to the message, so the main topic is bound.
- **Resolution**:
  1. In the target Forum group, **navigate into the specific Sub-Topic view** first;
  2. Send `/bind` inside that sub-topic's message input box to register a dedicated target (`<Group>_t<TopicID>`);
  3. Alternatively, run `/add_tg -100<GroupID> <TopicID>` in private chat with the Bot.

---

### Why are `.zip`, `.pdf`, or general file attachments skipped during transfer?
- **Cause**: To prevent unintended file transfers when archiving photo/video channels, generic document transfer (`transfer.include.document`) defaults to `false`.
- **Resolution**: Enable document transfers via Bot command:
  ```text
  /set transfer.include.document true
  ```
  *(Or toggle **`⚙️ Settings` → `📦 Media Types` → `📄 Document`** in the interactive control panel.)*

---

### Why are WebDAV or 2FA passwords rejected when they contain `_`, `*`, `~`, or `||`?
- **Cause**: Telegram clients automatically parse matching pairs of `_` as *italic*, `*` as **bold**, `~` as ~~strikethrough~~, and `||` as spoiler formatting, stripping the delimiter characters before the message reaches the Bot.
- **Resolution**: Wrap passwords containing special characters in single backticks (inline code) when sending them to the Bot:
  ```text
  `your_password*with_symbols`
  ```
  The Bot automatically strips the surrounding backticks and preserves the exact literal string inside.

---

### Why does the Bot not respond to `/start` in private chat?
- **Cause**: TG Migrator enforces an `ADMIN_IDS` allowlist. Private messages from Telegram accounts not listed in `ADMIN_IDS` are silently ignored.
- **Resolution**: Query [@userinfobot](https://t.me/userinfobot) to obtain the numeric Telegram User ID, add it to `ADMIN_IDS=` in `.env`, and run `docker compose up -d` to reload the container.
