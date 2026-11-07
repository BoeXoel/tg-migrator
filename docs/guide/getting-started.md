# Getting Started & Deployment

This guide walks you through understanding TG Migrator's core concepts, preparing your Telegram credentials, deploying the service with Docker Compose, and completing your first message migration in 5 minutes.

---

## 1. Core Concepts in 60 Seconds

Before deploying, understanding the 4 building blocks makes everything intuitive:

| Concept | Role in TG Migrator | Required? |
| :--- | :--- | :--- |
| **🤖 Bot Account** (`BOT_TOKEN`) | Runs the interactive control panel (`/start`) **and** acts as the default **Writer** when sending messages to your target Telegram chats. | **Yes** |
| **👤 Reader Account** (`/login`) | Your personal Telegram user account. Used to **read** restricted/private groups & channels that forbid forwarding or where the Bot is not a member. | **Recommended** (Optional only for public channels) |
| **🎯 Target (Destination)** | Where migrated content is stored: a **Telegram chat/Topic**, a **Local NAS directory**, or a **WebDAV server**. You can create multiple targets. | **Yes** (At least 1) |
| **📋 Task vs 📡 Watch Route** | A **Task** is a one-off migration (a single message link, an ID range like `100-500`, or a full history scan). A **Watch Route** continuously monitors a source chat and forwards new messages automatically. | Use either or both |

---

## 2. Prerequisites (4 Credentials)

Prepare the following 4 values before editing `.env`:

1. **`API_ID` & `API_HASH`**
   Log in to [my.telegram.org](https://my.telegram.org) with your phone number → open **API development tools** → create an app to obtain your numeric `api_id` and 32-character `api_hash`.
2. **`BOT_TOKEN`**
   Message [@BotFather](https://t.me/BotFather) on Telegram → send `/newbot` → copy the HTTP API token (format: `123456789:AAH...`).
3. **`ADMIN_IDS`**
   Message [@userinfobot](https://t.me/userinfobot) on Telegram to get your numeric Telegram User ID (e.g. `123456789`).
   > [!IMPORTANT]
   > Only user IDs listed in `ADMIN_IDS` can interact with your Bot in private chat. The Bot silently ignores everyone else.

---

## 3. Docker Compose Deployment

### Step 1: Download Deployment Files & Create Directories

Create a working directory on your server or NAS, download `docker-compose.yml` and `.env.example`, and pre-create the mounted directories with UID/GID `1000:1000` (the non-root user inside the container):

```bash
mkdir -p tg-migrator && cd tg-migrator

curl -fsSLO https://raw.githubusercontent.com/BoeXoel/tg-migrator/main/docker-compose.yml
curl -fsSL https://raw.githubusercontent.com/BoeXoel/tg-migrator/main/.env.example -o .env

# Create persistent directories and grant ownership to container user (UID 1000)
mkdir -p data sessions downloads archive
sudo chown -R 1000:1000 data sessions downloads archive
chmod 700 data sessions
```

> [!WARNING] Why `chown -R 1000:1000` matters
> For security, the container runs as non-root user `migrator (1000:1000)`. If Docker automatically creates the host directories as `root:root`, the container will fail on startup with `PermissionError: [Errno 13] Permission denied: '/app/downloads/active'`. Running the `chown` command above prevents this completely.

### Step 2: Configure `.env`

Open `.env` and fill in your 4 required credentials:

```ini
API_ID=12345678
API_HASH=0123456789abcdef0123456789abcdef
BOT_TOKEN=123456789:AAHxxxxxxxxxxxxxxxxxxxxxxxxxxxx
ADMIN_IDS=123456789

# Default Bot UI language: en (English) or zh (Simplified Chinese)
# Can also be switched anytime inside the Bot via /lang
BOT_LANG=en
```

::: tip Choosing a Database (SQLite by Default)
- **SQLite (Default, Recommended for single-node deployments)**: Leave `DATABASE_URL=` **completely empty** in `.env`. Data is stored automatically in `./data/migrator.db`.
- **PostgreSQL / MySQL**: Only uncomment `DATABASE_URL` if you start the corresponding database container profile (`docker compose --profile postgres up -d`). If you set `DATABASE_URL=postgresql+asyncpg://...@postgres:5432/...` without starting the `postgres` profile, startup will fail with `Temporary failure in name resolution`.
:::

### Step 3: Start the Service

```bash
docker compose up -d
docker compose logs -f tg-migrator
```

When you see `TG Migrator 0.0.2 starting`, your Bot is online and will send a startup notification to your Telegram account!

---

## 4. First-Run 5-Minute Walkthrough

Open a private chat with your Bot in Telegram and follow these 4 steps:

1. **Open the Control Panel (`/start`)**
   Send `/start` to bring up the main menu. To switch between English and Chinese at any time, tap **`🌐 Language`** or send `/lang zh` / `/lang en`.
2. **Log In Your Reader Account (`/login`)**
   Tap **`🔑 Account`** (or send `/login`) → enter your phone number in international format (e.g. `+12025550123`) → enter the login code using the **on-screen inline keypad** (never type the code as plain text, or Telegram will revoke it!).
3. **Bind a Destination (`/bind`)**
   Add your Bot to your target Telegram group/channel (grant it permission to post messages), then send `/bind` inside that group (or inside a specific **Forum Sub-Topic**).
4. **Run Your First Migration**
   Forward any message from a source chat to the Bot, or send a Telegram message link like `https://t.me/c/123456789/42`. Select your target and watch the progress live!

---

## 5. Smooth Upgrades

Upgrading to a newer release is completely non-destructive:

```bash
docker compose pull
docker compose up -d
```

- **100% Data & Session Preservation**: Your database (`data/migrator.db`), encryption key (`data/secret.key`), logged-in Telegram session (`sessions/`), targets, and watch routes are all preserved in place.
- **Automatic In-Progress Recovery**: Any transfer job that was interrupted mid-flight during container restart is automatically reset to `pending` and resumed cleanly on startup.
