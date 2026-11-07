# Migration Tasks & Watch Routes

TG Migrator offers two complementary ways to migrate content:
1. **Ad-hoc Migration Tasks**: Send a link (or range of links) directly to the Bot for one-off or batch historical transfers.
2. **Watch Routes (`/routes`)**: Bind a source chat/topic to one or more destinations for continuous real-time synchronization and scheduled watermark polling.

---

## 1. One-Off & Batch Migration Tasks

Whenever you want to grab a specific message, an album, a range of messages, or an entire topic's history, simply send the message link(s) to the Bot in private chat.

### Supported Link Formats

| What You Want to Migrate | Example Input Sent to Bot |
| :--- | :--- |
| **Single Message / Album** | `https://t.me/c/123456789/42`<br>`https://t.me/public_channel/42` |
| **Single Message inside a Forum Topic** | `https://t.me/c/123456789/15/42` *(Topic `15`, Message `42`)* |
| **Message Range (Hyphen Syntax)** | `https://t.me/c/123456789/100-500`<br>`https://t.me/c/123456789/15/100-500` |
| **Message Range (Two Links)** | `https://t.me/c/123456789/100 https://t.me/c/123456789/500` |
| **Private Group Invite Link** | `https://t.me/+AbCdEfGhIjKlMnOp` *(Reader account must be a member)* |

### Target Selection Interactive Prompt

After you send a link or range:
1. The Bot replies with a **target selection card** listing all your enabled Targets (`☑️ #1 MyGroup_t128`, `⬜ #2 nas_movies`, etc.).
2. Tap one or more targets to toggle `☑️` / `⬜` (multi-destination fan-out is supported!).
3. Tap **`🚀 Start`** to launch the scanner and enqueue the jobs.

> [!TIP] Album Grouping & Deduplication
> - **Albums**: If a single link points to one photo/video inside a 10-item Telegram Album, TG Migrator automatically detects `grouped_id`, fetches all 10 items of that album, and delivers them together as a single intact Album!
> - **Deduplication**: Every job is hashed by its global Telegram media ID (for files) or `(source_chat_id, message_ids)` (for text). Re-running an overlapping range will automatically skip already-queued/delivered items.

---

## 2. Continuous Watch Routes (`/routes`)

If you want to mirror a source channel/group (or a specific Forum Topic) continuously—and optionally backfill its entire history—create a **Watch Route**.

### Creating a Watch Route

1. Open **`🔁 Routes`** → tap **`➕ New Route`** (or send `/add_route`).
2. Follow the 4-step wizard:
   - **Route name**: e.g. `news_feed`
   - **Source**: Send any message link from the source chat (if the link is from a Forum Topic like `https://t.me/c/123456/15/99`, the route automatically locks onto Topic `15`!), or send `@username` / `-100ID [topic_id]`.
   - **Target IDs**: Enter one or more target numbers separated by commas (e.g. `1, 2`).
   - **Enable live watching (`y/n`)**: Choose `y` to start monitoring immediately.

### How Hybrid Live Watch Works

TG Migrator uses a **dual-engine watch architecture** so no message is ever lost:
1. **Real-Time Event Listener**: Receives new messages (and albums after a 2.5-second grouping window) the instant they are posted.
2. **Periodic Watermark Polling (`last_seen_msg_id`)**: Every `watch.poll_interval_seconds` (default `300s`), the watcher polls any messages newer than the persisted `last_seen_msg_id` watermark. Even if your server reboots or loses network for an hour, it automatically catches up on every missed message as soon as it reconnects!

### Backfilling Historical Messages on a Route

In **`🔁 Routes`** → select your route → tap **`📜 Scan History`**:
- **`All History (from 1)`**: Scans the entire source chat (or source Topic) from the very first message to the latest.
- **`Custom Range`**: Prompts you for `start_id - end_id` (e.g. `1 - 1000` or two message links).
- **`⏩ Catch Up Now`**: Immediately triggers a watermark catch-up poll without waiting for the timer.

---

## 3. Content Filtering (`/filters`)

You can block unwanted messages (such as sponsored posts, external ad links, or specific keywords) across both Tasks and Watch Routes.

- **Open Filter Panel**: Send `/filters` (or go to **`⚙️ Settings` → `🧹 Filters`**).
- **Add a Keyword Rule**:
  ```text
  /filter_add keyword #ad
  ```
- **Add a Regular Expression Rule**:
  ```text
  /filter_add regex https?://t\.me/joinchat/\S+
  ```
- **Delete a Rule**:
  ```text
  /filter_del 1
  ```
- **Toggle Filter Engine or Case Sensitivity**:
  Use the inline buttons in `/filters` or run `/set filters.enabled true` and `/set filters.case_sensitive false`.

---

## 4. Per-Route Setting Overrides (`/route_set`)

Need global settings to keep captions, but want **one specific route** to strip captions or only transfer videos? Every route supports dotted setting overrides that take precedence over global settings:

```text
# Strip captions only on Route #1
/route_set 1 transfer.drop_caption true

# Disable text messages on Route #1 (media only)
/route_set 1 transfer.include.text false

# Remove a route override and revert to global setting
/route_set 1 transfer.drop_caption -
```

---

## 5. Queue Management & Recovery

- **View Live Queue & Disk Space**: Send `/status` (or tap **`📊 Status`**).
- **Pause / Resume Worker**: Send `/pause` (finishes the current in-flight file cleanly and pauses) and `/resume`.
- **Retry Failed Jobs**: Send `/retry` (retries all failed jobs) or `/retry 5` (retries failed jobs in Task `#5`).
- **Cancel a Running Task**: Send `/cancel_task 5` (or tap **`✖️ Cancel`** inside **`📋 Tasks`**).
- **Verify Delivered Messages**: Send `/verify` to check that previously delivered Telegram messages still exist in the destination chat.
