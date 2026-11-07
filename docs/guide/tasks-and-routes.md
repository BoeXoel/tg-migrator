# Migration Tasks & Watch Routes

TG Migrator provides two complementary migration workflows:
1. **Ad-hoc Migration Tasks**: Send a message link or range directly to the Bot for one-off or batch historical transfers.
2. **Watch Routes (`/routes`)**: Bind a source chat or topic to one or more destinations for continuous 24/7 synchronization and historical backfilling.

---

## 1. One-Off & Batch Migration Tasks

To transfer a single message, an entire album, a message ID range, or a topic's history, send the message link(s) directly to the Bot in private chat.

### Supported Link Formats

| Migration Scenario | Example Input Sent to Bot |
| :--- | :--- |
| **Single Message / Album** | `https://t.me/c/123456789/42`<br>`https://t.me/public_channel/42` |
| **Single Message inside a Forum Topic** | `https://t.me/c/123456789/15/42` *(Topic `15`, Message `42`)* |
| **Message Range (Hyphen Syntax)** | `https://t.me/c/123456789/100-500`<br>`https://t.me/c/123456789/15/100-500` *(Scans messages `100`–`500` within Topic `15`)* |
| **Message Range (Two Links)** | `https://t.me/c/123456789/100 https://t.me/c/123456789/500` |
| **Private Group Invite Link** | `https://t.me/+AbCdEfGhIjKlMnOp` *(Reader account must be a member)* |

### Target Selection & Execution

When a link or range is submitted:
1. The Bot replies with a **target selection card** displaying a paginated text list of enabled destinations (10 per page, with `⬅️ Prev` / `Next ➡️` navigation when more than 10 targets exist).
2. Reply directly with one or more target IDs (comma-separated, e.g. `1` or `1, 2` for multi-destination fan-out) to launch the scanner and enqueue transfer jobs immediately, or tap **`✖️ Cancel`** to abort.

> [!TIP] Automatic Album Grouping & Global Deduplication
> - **Album Grouping**: When a single link points to one item inside a multi-item Telegram Album, TG Migrator automatically inspects surrounding messages for a matching `grouped_id`, groups all items of the album, and delivers them together as an intact Album.
> - **Deduplication**: Every media job is keyed by its global Telegram media identifier (`doc_id` / `photo_id`). Re-scanning an overlapping message range automatically skips previously delivered items.

---

## 2. Continuous Watch Routes (`/routes`)

To continuously mirror a source channel, group, or specific Forum Topic, create a **Watch Route**.

### Creating a Watch Route

1. Open **`🔁 Routes`** → tap **`➕ New Route`** (or send `/add_route`).
2. Complete the 4-step interactive wizard:
   - **Route name**: e.g. `news_feed`
   - **Source**:
     - Send any message link from the source chat;
     - **Locking onto a Source Forum Topic**: If a Forum Topic message link is provided (e.g. `https://t.me/c/123456/15/99`), the route automatically scopes itself to **Topic `15` only**, ignoring messages from other topics in that group;
     - Alternatively, enter `@username` or `-100<ChatID> [topic_id]`.
   - **Target IDs**: Enter one or more target IDs separated by commas (e.g. `1, 2`).
   - **Enable live watching (`y/n`)**: Enter `y` to start monitoring immediately.

### Hybrid Live Watch Architecture

To prevent missed messages during network interruptions or container restarts, TG Migrator combines two mechanisms:
1. **Real-Time Event Listener**: Captures new messages immediately upon publication (applying a 2.5-second grouping window for multi-item albums).
2. **Periodic Watermark Polling (`last_seen_msg_id`)**: Every `watch.poll_interval_seconds` (default `300s`), the watcher queries messages newer than the persisted `last_seen_msg_id` watermark. After any downtime or restart, missed messages are automatically backfilled upon reconnection.

### Backfilling Historical Messages on a Route

Send `/routes <ID>` (e.g. `/routes 1` or `/route 1`) to open the route's management card, then tap **`📜 Scan History`**:
- **Message Range**: Enter `start_id - end_id` (e.g. `1 - 5000` or two message links) to backfill historical messages.
- **`⏩ Catch Up Now`**: Triggers an immediate incremental watermark poll without waiting for the scheduled interval.

---

## 3. Content Filtering (`/filters`)

Content filters automatically block unwanted messages (such as sponsored posts, invite links, or specific keywords) across both Tasks and Watch Routes.

- **Open Filter Panel**: Send `/filters` (or navigate to **`⚙️ Settings` → `🧹 Filters`**).
- **Add a Keyword Rule**:
  ```text
  /filter_add keyword #ad
  ```
- **Add a Regular Expression Rule**:
  ```text
  /filter_add regex https?://t\.me/(\+|joinchat/)\S+
  ```
- **Delete a Rule**:
  ```text
  /filter_del 1
  ```
- **Toggle Filter Engine or Case Sensitivity**:
  Use the inline buttons in `/filters` or run `/set filters.enabled true` and `/set filters.case_sensitive false`.

---

## 4. Per-Route Setting Overrides (`/route_set`)

Each Watch Route supports dotted setting overrides that take precedence over global runtime settings. For example, to keep captions globally while stripping captions or excluding plain text on a single route:

```text
# Strip media captions on Route #1 only
/route_set 1 transfer.drop_caption true

# Ignore plain text messages on Route #1 only
/route_set 1 transfer.include.text false

# Remove a route override and revert to global setting
/route_set 1 transfer.drop_caption -
```

---

## 5. Queue Management & Recovery

- **Inspect Queue & Disk Space**: Send `/status` (or tap **`📊 Status`**).
- **Pause / Resume Worker**: Send `/pause` (completes the current in-flight transfer cleanly before pausing) and `/resume`.
- **Retry Failed Jobs**: Send `/retry` (retries all failed jobs) or `/retry 5` (retries failed jobs in Task `#5`).
- **Cancel a Running Task**: Send `/cancel_task 5` (or tap **`✖️ Cancel`** inside **`📋 Tasks`**).
- **Verify Delivered Messages**: Send `/verify` to sample and verify that previously delivered Telegram messages still exist in the destination chat.
