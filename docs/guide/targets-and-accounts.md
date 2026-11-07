# Accounts & Destinations (Topics, NAS, WebDAV)

This guide covers authenticating a personal Reader account (`/login`), binding Telegram groups, channels, and **Forum Sub-Topics**, configuring Local NAS and WebDAV archive destinations, and handling oversized files (> 2 GiB).

---

## 1. Reader Account Authentication (`/login`)

Due to Telegram platform limitations, Bot accounts cannot join groups via invite links or pull historical messages from non-admin channels. Authenticating a personal **Reader Account** enables TG Migrator to read any chat, channel, or topic accessible to that user account.

### Standard Authentication Flow

1. In private chat with the Bot, tap **`🔑 Account`** (or send `/login`).
2. Tap **`📱 Start Login`** and send the phone number in international format (e.g. `+12025550123`).
3. Telegram sends a 5-digit verification code to the official Telegram client.
   > [!CAUTION] Always Use the On-Screen Inline Keypad
   > Never send the login code as a plain text message inside a Telegram chat. When a login code is transmitted as a regular chat message, Telegram's security system immediately revokes it. **Always enter the digits using the Bot's inline keypad** and tap `✔️` to submit.
4. **Two-Step Verification (2FA) Password (if enabled)**:
   Send the 2FA password in the chat. The Bot automatically deletes the password message immediately upon receipt.
   > [!TIP] Passwords Containing `_`, `*`, `~`, or `||`
   > Telegram clients automatically parse matching pairs of `_` (italic), `*` (bold), `~` (strikethrough), and `||` (spoiler), stripping those symbols from the message text.
   > Wrap passwords containing special symbols in single backticks (inline code):
   > ```text
   > `my_s3cret_p*ssw0rd`
   > ```
   > If a password is sent unquoted and altered by client Markdown formatting, TG Migrator automatically detects the formatting entities, rejects the corrupted input, and prompts for a backtick-wrapped retry.

---

## 2. Binding Telegram Destinations (`/bind` & Forum Topics)

TG Migrator supports delivering messages to:
- Regular groups and supergroups
- Broadcast channels
- **Specific Forum Sub-Topics** inside topic-enabled supergroups

### Scenario A: Binding a Regular Group or Main Topic (`General`)

1. Add the Bot to the destination group and grant permission to **Send Messages** (and media).
2. Send `/bind` directly in the group (or inside the default `General` topic).
3. The Bot deletes the `/bind` command message and sends a confirmation in private chat:
   `🎯 Bound as target #1 ArchiveGroup`

### Scenario B: Binding a Specific Forum Sub-Topic

The `/bind` command binds **the exact topic context in which the command message is posted**. Sending `/bind` inside `# General` binds the main topic. To bind a specific sub-topic:

1. In the target Forum group, **navigate into the specific Sub-Topic view** intended as the destination (do not stay in `# General`).
2. Inside that Sub-Topic's message input box, send:
   ```text
   /bind
   ```
3. TG Migrator extracts the sub-topic's `topic_id` from the message header and registers a dedicated target for that sub-topic:
   `🎯 Bound as target #2 ArchiveGroup_t128 (ArchiveGroup · Topic 128)`

::: tip Binding Multiple Sub-Topics in the Same Group
Sending `/bind` inside multiple different Sub-Topics of the same group registers each sub-topic as an independent Target (e.g. `ArchiveGroup_t10`, `ArchiveGroup_t25`). Different source channels or watch routes can then be routed into separate sub-topics within a single archive group.
:::

### Scenario C: Binding a Sub-Topic via `/add_tg` in Private Chat

A sub-topic can also be bound directly from the Bot's private chat without posting `/bind` in the group:

1. Right-click (or long-press) any message inside the target Sub-Topic and select **Copy Message Link**.
2. A Forum Sub-Topic message link follows the structure:
   `https://t.me/c/1234567890/128/456`
   - `1234567890` → Group ID (`-1001234567890`)
   - `128` → **Topic ID**
   - `456` → Message ID
3. Send the following command in private chat with the Bot:
   ```text
   /add_tg -1001234567890 128
   ```

### Scenario D: Binding a Broadcast Channel or as an Anonymous Admin

In broadcast channels (or when posting anonymously as the group title), Telegram omits the sender's personal `sender_id`. Use a one-time bind code to authorize the binding:

1. Send `/bind` to the Bot **in private chat** first.
2. The Bot generates a one-time code valid for 10 minutes:
   `Post /bind 839201 there (valid for 10 minutes)`
3. Post `/bind 839201` inside the target channel or group to complete the binding.

---

## 3. Local NAS Directory Destinations (`/add_local`)

Local destinations store media files and optional metadata sidecar files under `/app/archive` inside the container (mounted to `./archive` or a NAS path on the host).

1. Open **`🎯 Targets`** → tap **`➕ Local`** (or send `/add_local`).
2. Enter a target name (e.g. `nas_movies`).
3. Enter a relative subdirectory under `ARCHIVE_ROOT` (e.g. `telegram/movies`), or send `-` to store directly under `/app/archive`.
4. Archived files are organized automatically as:
   `<ARCHIVE_ROOT>/<subpath>/<source_chat_name>/<YYYY-MM>/<msg_id>_<original_filename>`

### Metadata Sidecar Files (`archive.sidecar_format`)
Each archived message can be accompanied by a metadata sidecar file recording the full message text/caption, original timestamp, sender ID, message ID, and source link:
- `json` (default): Structured JSON metadata
- `md`: Markdown note format (compatible with Obsidian / Logseq)
- `both`: Generates both `.json` and `.md`
- `none`: Saves media files only

Change the format at runtime via `/set archive.sidecar_format md`.

---

## 4. WebDAV Destinations (`/add_webdav`)

WebDAV destinations are compatible with **OpenList**, **Nextcloud**, **ownCloud**, **Synology WebDAV Server**, and **rclone serve webdav**.

1. Open **`🎯 Targets`** → tap **`➕ WebDAV`** (or send `/add_webdav`).
2. Complete the interactive wizard prompts:
   - **Target name**: e.g. `openlist_onedrive`
   - **WebDAV URL**: Supports external `https://...` as well as internal Docker/LAN `http://...` endpoints (e.g. `http://openlist:5244/dav`).
     > [!NOTE] Automatic LAN Proxy Bypass
     > Even when a global `PROXY_URL` is configured, private network addresses (`localhost`, `192.168.x.x`, `10.x.x.x`, `100.64.x.x` Tailscale/CGNAT, and single-label Docker service hostnames such as `http://openlist:5244`) **automatically bypass the proxy** and connect directly.
   - **Username**: WebDAV username (or `-` for anonymous access).
   - **Password**: WebDAV password (encrypted at rest using Fernet AES-128-CBC + HMAC-SHA256). Wrap in single backticks `` `password` `` if it contains `_`, `*`, or `~`.
   - **Subdirectory**: e.g. `OneDrive/TelegramArchive` (created automatically via `MKCOL` if absent).

### `auto`, `spool`, and `stream` Upload Modes (`archive.webdav_mode`)
- **`auto` (Default)**: Uses disk-spooling when free disk space is sufficient and automatically switches to streaming when free space is low.
- **`spool`**: Downloads media to temporary storage (`downloads/active`) before uploading to WebDAV with automatic retry. Recommended for unstable connections.
- **`stream` (Zero-Disk Streaming)**: Streams chunks directly from Telegram MTProto into an HTTP PUT request to WebDAV without writing temporary files to disk. Recommended for storage-constrained VPS instances (`/set archive.webdav_mode stream`).

---

## 5. Handling Oversized Files (> 2 GiB / 4 GiB)

Standard Telegram accounts and Bots support uploading files up to **2,000 MiB (~2 GiB)**, whereas Telegram Premium user accounts support up to **4,000 MiB (~4 GiB)**. Two built-in fallback mechanisms handle oversized files:

1. **Configure an Oversized Archive Fallback Target (Recommended)**:
   Open **`🎯 Targets`** → select the Telegram target → tap **`🗄 Set Oversized Fallback`** → select a Local NAS or WebDAV target. Files within Telegram's limit are delivered to Telegram as usual, while oversized files are automatically routed to the NAS or WebDAV fallback target.
2. **Enable Premium Reader Upload Fallback**:
   If the authenticated Reader account has Telegram Premium and permission to post in the destination chat, run:
   ```text
   /set transfer.allow_premium_user_fallback true
   ```
   Files between 2 GiB and 4 GiB will automatically be uploaded via the Premium Reader account.
