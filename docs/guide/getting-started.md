# Getting Started & Deployment

This guide covers TG Migrator's core architectural concepts, credential prerequisites, standard Docker Compose deployment, and initial migration workflow.

---

## 1. Core Architectural Concepts

TG Migrator is built around 4 core components:

| Concept | Role in TG Migrator | Required? |
| :--- | :--- | :--- |
| **🤖 Bot Account** (`BOT_TOKEN`) | Hosts the interactive control panel (`/start`) **and** serves as the default **Writer** when delivering messages to destination Telegram chats. | **Yes** |
| **👤 Reader Account** (`/login`) | A personal Telegram user account authenticated via `/login`. Used to **read** joined groups, channels, or Forum Topics where a Bot cannot join via invite link or pull history directly. | **Recommended** (Optional only for public channels) |
| **🎯 Target (Destination)** | Where migrated content is stored: a **Telegram chat/Topic**, a **Local NAS directory**, or a **WebDAV server**. Multiple targets can be configured simultaneously. | **Yes** (At least 1) |
| **📋 Task vs 📡 Watch Route** | A **Task** executes a one-off migration (a single message link, an ID range such as `100-500`, or a full history scan). A **Watch Route** continuously monitors a source chat and forwards new messages automatically. | Either or both |

---

## 2. Prerequisites (4 Credentials)

Prepare the following 4 values before configuring `.env`:

1. **`API_ID` & `API_HASH`**
   Sign in to [my.telegram.org](https://my.telegram.org) → open **API development tools** → create an application to obtain the numeric `api_id` and 32-character `api_hash`.
2. **`BOT_TOKEN`**
   Message [@BotFather](https://t.me/BotFather) on Telegram → send `/newbot` → copy the HTTP API token (format: `123456789:AAH...`).
3. **`ADMIN_IDS`**
   Message [@userinfobot](https://t.me/userinfobot) on Telegram to obtain the administrator's numeric Telegram User ID (e.g. `123456789`).
   > [!IMPORTANT] Admin Allowlist
   > Only Telegram User IDs listed in `ADMIN_IDS` are authorized to interact with the Bot in private chat. Requests from unlisted accounts are silently ignored. Multiple IDs can be separated by commas.

---

## 3. Docker Compose Deployment

### Step 1: Download Deployment Files & Initialize Directory Permissions

Create a deployment directory on the host or NAS, download `docker-compose.yml` and `.env.example`, and pre-create the bind-mounted directories with UID/GID `1000:1000` (matching the non-privileged container user):

```bash
mkdir -p tg-migrator && cd tg-migrator

curl -fsSLO https://raw.githubusercontent.com/BoeXoel/tg-migrator/main/docker-compose.yml
curl -fsSL https://raw.githubusercontent.com/BoeXoel/tg-migrator/main/.env.example -o .env

# Create persistent directories and grant ownership to container user (UID 1000)
mkdir -p data sessions downloads archive
sudo chown -R 1000:1000 data sessions downloads archive
chmod 700 data sessions
```

> [!WARNING] Host Directory Ownership (`1000:1000`)
> The container runs as non-privileged user `migrator (1000:1000)`. If the Docker daemon creates missing host directories automatically, they are owned by `root:root`, causing startup to fail with `PermissionError: [Errno 13] Permission denied: '/app/downloads/active'`. Pre-creating the directories and running `chown -R 1000:1000` prevents this issue.

### Step 2: Configure `.env`

Open `.env` and populate the 4 required credentials:

```ini
API_ID=12345678
API_HASH=0123456789abcdef0123456789abcdef
BOT_TOKEN=123456789:AAHxxxxxxxxxxxxxxxxxxxxxxxxxxxx
ADMIN_IDS=123456789

# Default Bot UI language: en (English) or zh (Simplified Chinese)
# Can also be switched at runtime via /lang
BOT_LANG=en
```

::: tip Database Backend Selection (Built-in SQLite by Default)
- **SQLite (Default, Recommended for single-node / NAS deployments)**: Leave `DATABASE_URL=` **empty** in `.env`. Data is persisted automatically in `./data/migrator.db`.
- **PostgreSQL / MySQL**: Only configure `DATABASE_URL` when starting the corresponding database container profile (`docker compose --profile postgres up -d`) or connecting to an external database server. Setting `DATABASE_URL` to `@postgres:5432` without enabling the `postgres` profile will result in `socket.gaierror: Temporary failure in name resolution`.
:::

### Step 3: Start the Service

```bash
docker compose up -d
docker compose logs -f tg-migrator
```

Once `TG Migrator 0.0.3 starting` appears in the logs, the Bot is online and sends a startup notification to the configured administrator chat.

---

## 4. Initial 5-Minute Workflow

Open a private chat with the Bot in Telegram and complete the following 4 steps:

1. **Open the Control Panel (`/start`)**
   Send `/start` to display the main menu. To switch between English and Simplified Chinese at any time, tap **`🌐 Language`** or send `/lang zh` / `/lang en`.
2. **Authenticate the Reader Account (`/login`)**
   Tap **`🔑 Account`** (or send `/login`) → enter the phone number in international format (e.g. `+12025550123`) → input the 5-digit verification code using the **on-screen inline keypad** (never send the login code as a plain text chat message, or Telegram will immediately invalidate it).
3. **Bind a Destination (`/bind`)**
   Add the Bot to the destination Telegram group or channel (with permission to post messages), then send `/bind` inside that group (or inside a specific **Forum Sub-Topic**).
4. **Launch a Migration Task**
   Forward a message from a source chat to the Bot, or send a Telegram message link such as `https://t.me/c/123456789/42`, then reply with the target ID (e.g. `1`) to start transferring.

---

## 5. Smooth Upgrades

Upgrading to a newer image release preserves all existing state:

```bash
docker compose pull
docker compose up -d
```

- **Full State Preservation**: The database (`data/migrator.db`), master encryption key (`data/secret.key`), authenticated Telegram sessions (`sessions/`), targets, and watch routes remain intact across upgrades.
- **Automatic Interrupted-Job Recovery**: Any transfer job that was `in_progress` when the container restarted is automatically reset to `pending` and resumed upon startup.
