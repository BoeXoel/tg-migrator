# Accounts & Destinations (Topics, NAS, WebDAV)

This guide covers logging in your personal Reader account, binding Telegram groups, channels, and **Forum Sub-Topics**, configuring Local NAS / WebDAV archive targets, and setting up oversized file fallbacks.

---

## 1. Reader Account Login (`/login`)

A Telegram Bot cannot join private groups via invite links or read messages from channels that restrict forwarding (`noforwards`) unless it is an admin there. By logging in a personal **Reader Account**, TG Migrator can read any chat your personal account has joined.

### Step-by-Step Login Flow

1. In private chat with your Bot, tap **`🔑 Account`** (or send `/login`).
2. Tap **`📱 Start Login`** and send your phone number in international format (e.g. `+12025550123`).
3. Telegram will send a 5-digit login code to your official Telegram app.
   > [!CAUTION] Use the Inline Keypad — Never Type the Code!
   > If you send the login code as a plain text message inside Telegram, Telegram's security system immediately invalidates the code. **Always tap the digits on the Bot's inline keypad** and press `✔️` to submit.
4. **If Two-Step Verification (2FA) is enabled**:
   Send your 2FA password in the chat. The Bot immediately deletes your password message upon receipt.
   > [!TIP] Password Contains `_`, `*`, `~`, or `||`? Wrap It in Backticks!
   > Telegram's client automatically parses pairs of `_` (italic), `*` (bold), `~` (strikethrough), and `||` (spoiler) and strips those symbols from the message text.
   > To ensure the Bot receives every character verbatim, wrap your password in single backticks:
   > ```text
   > `my_s3cret_p*ssw0rd`
   > ```
   > If you forget and Telegram formats your message, TG Migrator will automatically detect the formatting, reject the corrupted input, and prompt you to resend it wrapped in backticks.

---

## 2. Binding Telegram Destinations (`/bind` & Forum Topics)

TG Migrator supports delivering messages to:
- Regular groups & supergroups
- Broadcast channels
- **Specific Forum Sub-Topics** inside a topic-enabled supergroup

### A. Binding a Regular Group or Main Topic (`General`)

1. Add your Bot to the target group and grant it permission to **Send Messages** (and media).
2. Send `/bind` directly in the group (or inside the default `General` topic).
3. The Bot deletes the `/bind` command and notifies you in private chat:
   `🎯 Bound as target #1 MyGroup`

### B. Binding a Specific Forum Sub-Topic (Most Common Question!)

Because `/bind` binds **whichever topic the command is sent inside**, binding a specific sub-topic works like this:

1. In your target Forum group, **open the specific Sub-Topic** you want to use as a destination (do **not** stay in `# General`).
2. Inside that Sub-Topic's message input box, send:
   ```text
   /bind
   ```
3. TG Migrator reads the message's `reply_to` topic header and creates a dedicated target for that sub-topic:
   `🎯 Bound as target #2 MyGroup_t128 (MyGroup · Topic 128)`

::: tip Multiple Sub-Topics in the Same Group
You can enter 5 different Sub-Topics in the same group and send `/bind` in each of them. TG Migrator will register 5 independent Targets (e.g. `MyGroup_t10`, `MyGroup_t25`, etc.), allowing you to route different source channels into different sub-topics of the same archive group!
:::

### C. Alternative: Binding a Topic via `/add_tg` in Private Chat

If you prefer not to send `/bind` inside the sub-topic, you can also add it directly in private chat with the Bot:

1. Right-click (or tap) any message inside your target Sub-Topic and select **Copy Message Link**.
2. A Forum Topic message link looks like:
   `https://t.me/c/1234567890/128/456`
   - `1234567890` → Chat ID (`-1001234567890`)
   - `128` → **Topic ID**
   - `456` → Message ID
3. Send the following command to the Bot in private chat:
   ```text
   /add_tg -1001234567890 128
   ```

### D. Binding a Broadcast Channel or as an Anonymous Admin

In broadcast channels (or when you post anonymously as the group title), Telegram hides your personal `sender_id`. To authorize `/bind`:

1. Send `/bind` to the Bot **in private chat** first.
2. The Bot generates a one-time code valid for 10 minutes, e.g.:
   `Post /bind 839201 there (valid for 10 minutes)`
3. Copy and post `/bind 839201` inside your target channel or group.

---

## 3. Local NAS Directory Targets (`/add_local`)

Local targets save media files and optional metadata sidecar files into subdirectories under `/app/archive` (mounted to `./archive` on your host or NAS).

1. Go to **`🎯 Targets`** → tap **`➕ Local`** (or send `/add_local`).
2. Enter a target name (e.g. `nas_movies`).
3. Enter a relative subdirectory under `ARCHIVE_ROOT` (e.g. `telegram/movies`), or send `-` to use the root of `/app/archive`.
4. Files are organized automatically as:
   `<ARCHIVE_ROOT>/<subpath>/<source_chat_name>/<YYYY-MM>/<msg_id>_<original_filename>`

### Sidecar Metadata Files (`archive.sidecar_format`)
Every archived message can be accompanied by a sidecar file containing the full message text/caption, original timestamp, sender ID, message ID, and source link:
- `json` (default): Structured JSON metadata
- `md`: Human-readable `.md` note (ideal for Obsidian / Logseq)
- `both`: Writes both `.json` and `.md`
- `none`: Saves media files only

Switch anytime via `/set archive.sidecar_format md`.

---

## 4. WebDAV Targets (`/add_webdav`)

WebDAV targets work seamlessly with **AList**, **OpenList**, **Nextcloud**, **ownCloud**, **Synology WebDAV Server**, and **rclone serve webdav**.

1. Go to **`🎯 Targets`** → tap **`➕ WebDAV`** (or send `/add_webdav`).
2. Follow the wizard prompts:
   - **Target name**: e.g. `alist_onedrive`
   - **WebDAV URL**: Supports external `https://...` as well as internal Docker/LAN `http://...` (e.g. `http://alist:5244/dav`).
     > [!NOTE] Automatic LAN Proxy Bypass
     > Even if you configured a global `PROXY_URL` for Telegram/WebDAV, internal addresses (`localhost`, `192.168.x.x`, `10.x.x.x`, `100.64.x.x` Tailscale/CGNAT, and single-label Docker service hostnames like `http://alist:5244`) **automatically bypass the proxy** and connect directly.
   - **Username**: Your WebDAV username (or `-` for anonymous).
   - **Password**: Your WebDAV password (stored encrypted with Fernet AES-128-CBC + HMAC-SHA256 in the database). Remember to wrap in backticks `` `password` `` if it contains `_`, `*`, or `~`!
   - **Subdirectory**: e.g. `OneDrive/TelegramArchive` (automatically created via `MKCOL` if missing).

### `auto`, `spool`, and `stream` Upload Modes (`archive.webdav_mode`)
- **`auto` (Default)**: Uses disk-spooling when free disk space is sufficient, and automatically switches to streaming when disk is tight.
- **`spool`**: Downloads the file to temporary disk (`downloads/active`) first, then uploads to WebDAV with automatic retry. Safest for unstable networks.
- **`stream` (Zero-Disk Streaming)**: Streams chunks directly from Telegram MTProto into an HTTP chunked `PUT` request to WebDAV without writing the file to disk. Ideal for low-disk VPS instances (`/set archive.webdav_mode stream`).

---

## 5. Handling Oversized Files (> 2 GiB / 4 GiB)

Standard Telegram accounts and Bots can upload files up to **2,000 MiB (~2 GiB)**, while Telegram Premium user accounts can upload up to **4,000 MiB (~4 GiB)**.

If a source channel contains files larger than your writer's limit, you have two built-in options:

1. **Set an Archive Fallback Target (Recommended)**:
   Open **`🎯 Targets`** → select your Telegram target → tap **`🗄 Set Oversized Fallback`** → choose a Local NAS or WebDAV target. Any file exceeding Telegram's upload limit will automatically be saved to your NAS/WebDAV instead of failing!
2. **Enable Premium Reader Upload Fallback**:
   If your logged-in Reader account has Telegram Premium and is a member with post permissions in the destination chat, run:
   ```text
   /set transfer.allow_premium_user_fallback true
   ```
   Files between 2 GiB and 4 GiB will automatically be uploaded using your Premium Reader account.
