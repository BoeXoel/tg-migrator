# 常见报错与排坑 FAQ

本页汇总了部署与运行过程中常见的故障现象、触发原因及标准排查方案。

---

## 1. 容器启动与环境报错

### 报错一：`PermissionError: [Errno 13] Permission denied: '/app/downloads/active'`
- **触发原因**：首次直接执行 `docker compose up -d` 时，若宿主机尚不存在挂载目录，Docker 守护进程会自动以 `root:root` 属主创建 `data`、`sessions`、`downloads`、`archive` 四个目录；而容器内部默认以非特权用户 `migrator (1000:1000)` 运行，从而因缺少写权限而启动失败。
- **解决方案**：在宿主机部署目录下修正挂载目录属主并重启容器：
  ```bash
  sudo chown -R 1000:1000 data sessions downloads archive
  docker compose restart tg-migrator
  ```

---

### 报错二：`socket.gaierror: [Errno -3] Temporary failure in name resolution`（无法解析 `postgres` 或 `mysql`）
- **触发原因**：`.env` 配置文件中启用了指向内部容器名（如 `@postgres:5432` 或 `@mysql:3306`）的 `DATABASE_URL`，但启动服务时仅执行了默认的 `docker compose up -d`（未指定 `--profile postgres` 或 `--profile mysql`），导致 Docker 网络内未启动对应的数据库容器。
- **解决方案**：
  - **使用内置 SQLite 数据库（默认推荐）**：编辑 `.env`，将 `DATABASE_URL=` 置空（等号右侧不填任何内容），随后执行 `docker compose up -d`。
  - **使用外部容器数据库（PostgreSQL / MySQL）**：启动时显式指定对应的 Compose Profile，例如 `docker compose --profile postgres up -d`。

---

### 报错三：`required variable MYSQL_PASSWORD / POSTGRES_PASSWORD is missing a value`
- **触发原因**：`v0.0.1` 版本的 `docker-compose.yml` 对数据库密码变量采用了 `:?` 强制非空校验，导致即使未启用数据库 Profile 也会触发环境变量检查。
- **解决方案**：更新至 `v0.0.2` 及以上版本的 `docker-compose.yml`（已改为 `:-` 缺省兼容语法）。

---

## 2. Bot 操作与搬运常见问题

### 问题一：在开启了话题（Topics）的群组中发送 `/bind`，为何仅绑定了主话题（General）？
- **触发原因**：`/bind` 指令根据消息所在的当前话题上下文确定绑定目标。若在 `# General` 主话题中发送 `/bind`，消息不包含子话题 `topic_id`，系统默认将其绑定为群组主话题。
- **解决方案**：
  1. 在目标群组中，**先进入需要作为目的地的具体子 Topic（子话题）聊天视图**；
  2. 在该子 Topic 的消息输入框中发送 `/bind`，系统即会创建带 `_t<话题ID>` 标识的子话题目的地；
  3. 也可在与 Bot 的私聊窗口中直接执行 `/add_tg -100<群组ID> <话题ID>` 手动绑定。

---

### 问题二：图片与视频正常搬运，为何 `.zip`、`.pdf`、`.apk` 等文件附件被跳过？
- **触发原因**：为防止在纯影音/图集场景下误搬运无关附件，系统默认将通用文档类型开关 `transfer.include.document` 设为 `false`。
- **解决方案**：在与 Bot 的私聊窗口中开启通用文档传输：
  ```text
  /set transfer.include.document true
  ```
  *（或通过交互面板依次点击 **`⚙️ 设置` → `📦 媒体类型` → `📄 文档/文件`** 将其切换为 `✅`。）*

---

### 问题三：输入含特殊字符的 WebDAV 密码或两步验证（2FA）密码时，为何提示密码错误？
- **触发原因**：当密码字符串中包含成对的 `_`、`*`、`~` 或 `||` 时，Telegram 客户端在发送消息时会自动将其解析为*斜体*、**粗体**、~~删除线~~或剧透富文本格式，并剥离原始符号，导致服务端收到的字符不完整。
- **解决方案**：发送包含特殊字符的密码时，使用英文单反引号（行内代码格式）将其包裹：
  ```text
  `your_password*with_symbols`
  ```
  系统会自动剥离首尾的单反引号，并原样提取内部的全部原始字符。

---

### 问题四：在私聊窗口向 Bot 发送 `/start` 为何无任何响应？
- **触发原因**：系统启用了严格的 `ADMIN_IDS` 管理员白名单鉴权机制，非白名单 User ID 发送的私聊指令会被静默丢弃。
- **解决方案**：通过 [@userinfobot](https://t.me/userinfobot) 查询当前账号的纯数字 User ID，将其填入 `.env` 的 `ADMIN_IDS=` 配置项后执行 `docker compose up -d` 重载容器。
