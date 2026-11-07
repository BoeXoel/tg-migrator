# 常见报错与排坑 FAQ

本页汇总了实际部署与日常使用中最容易遇到的报错及一键解决方法。

---

## 1. 容器启动与环境报错

### 报错一：`PermissionError: [Errno 13] Permission denied: '/app/downloads/active'`
- **原因**：直接运行 `docker compose up -d` 时，Docker 守护进程会自动以 `root:root` 身份在宿主机创建 `data`、`sessions`、`downloads`、`archive` 四个挂载目录；而容器内部为了安全以非特权用户 `migrator (1000:1000)` 运行，导致无权写入。
- **解决方法**：在宿主机部署目录下执行以下命令修改属主并重启即可：
  ```bash
  sudo chown -R 1000:1000 data sessions downloads archive
  docker compose restart tg-migrator
  ```

---

### 报错二：`socket.gaierror: [Errno -3] Temporary failure in name resolution`（连不上 `postgres` 或 `mysql`）
- **原因**：你在 `.env` 中取消注释并填写了 `DATABASE_URL=postgresql+asyncpg://...@postgres:5432/...`，但启动时执行的是普通的 `docker compose up -d`（未开启 `--profile postgres`），导致容器网络内根本不存在名为 `postgres` 的数据库容器。
- **解决方法**：
  - **如果你想用默认的 SQLite（最简单，推荐）**：编辑 `.env`，将 `DATABASE_URL=` **等号后面完全留空**，然后重新执行 `docker compose up -d`。
  - **如果你确实想用 PostgreSQL**：启动时请加上 Profile 参数：`docker compose --profile postgres up -d`。

---

### 报错三：`required variable MYSQL_PASSWORD / POSTGRES_PASSWORD is missing a value`
- **原因**：早期 `v0.0.1` 版本的 `docker-compose.yml` 使用了 `:?` 强校验语法，即使未启用外部数据库也会检查密码变量。
- **解决方法**：更新到 `v0.0.2+` 版本的 `docker-compose.yml`（已改为 `:-` 兼容语法）。

---

## 2. Bot 操作与搬运常见问题

### 问题一：为什么在开启了话题的群里发 `/bind`，却只绑定了主话题（General）？
- **原因**：`/bind` 的绑定逻辑是 **“在哪个话题里发送指令，就绑定哪个话题”**。如果你停留在 `# General` 主话题里发送 `/bind`，消息不含子话题标识，就会绑定为主话题。
- **解决方法**：
  1. 在目标群组中，**先点击进入你想作为目的地的那个子 Topic（子话题）聊天界面**；
  2. 在该子 Topic 内部发送 `/bind`，即可生成带 `_t话题ID` 的独立子话题目的地；
  3. 或者在私聊 Bot 时直接发送 `/add_tg -100群组ID 话题ID`。

---

### 问题二：为什么图片和视频都搬运成功了，但 `.zip`、`.pdf`、`.apk` 等文件被跳过了？
- **原因**：为了避免在纯影视/图集频道里误搬运无关附件，默认配置中 `transfer.include.document`（通用文档/压缩包）是**关闭（`false`）**的。
- **解决方法**：在 Bot 私聊中开启文档搬运开关即可：
  ```text
  /set transfer.include.document true
  ```
  *（或者点击 **`⚙️ 设置` → `📦 媒体类型` → `📄 文档/文件`** 将其点亮为 `✅`。）*

---

### 问题三：输入 WebDAV 密码或两步验证（2FA）密码时提示密码错误？
- **原因**：如果你的密码中包含成对的 `_`、`*`、`~` 或 `||`，Telegram 客户端在发送消息时会自动把它们解析成*斜体*、**粗体**、~~删除线~~或剧透格式，从而把这些特殊符号本身吞掉。
- **解决方法**：发送密码时，在密码首尾加上英文单反引号（变成行内代码格式）发送：
  ```text
  `your_password*with_symbols`
  ```
  Bot 会自动剥离首尾的反引号，并原封不动地读取里面的所有特殊字符。

---

### 问题四：为什么我在私聊里给 Bot 发 `/start` 没有任何反应？
- **原因**：TG Migrator 启用了严格的 `ADMIN_IDS` 管理员白名单机制，非白名单用户的私聊消息会被完全静默丢弃。
- **解决方法**：在 Telegram 私聊 [@userinfobot](https://t.me/userinfobot) 确认你的纯数字 User ID，填入 `.env` 的 `ADMIN_IDS=` 中，然后执行 `docker compose up -d` 重载配置。
