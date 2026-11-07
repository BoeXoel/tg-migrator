# 快速部署与升级指南

本篇指南带你用 5 分钟理清 TG Migrator 的核心工作逻辑、准备必需凭据、通过 Docker Compose 部署服务，并完成第一次搬运任务。

---

## 1. 一分钟看懂核心概念

在动手配置前，先了解系统里的 4 个核心角色，后续操作就会非常顺畅：

| 核心概念 | 在系统中的作用 | 是否必需 |
| :--- | :--- | :--- |
| **🤖 控制机器人（Bot）** | 提供交互式管理面板（`/start`），同时默认作为向目标 Telegram 群组/频道发送消息的 **写入账号（Writer）**。 | **必需** |
| **👤 读取账号（Reader）** | 你通过 `/login` 登录的个人 Telegram 账号。用于**读取**私密群组、禁止转发（`noforwards`）频道，或解决 Bot 无法通过邀请链接进群的限制。 | **强烈推荐**（仅搬运公开无限制频道时可不登） |
| **🎯 目的地（Target）** | 搬运内容的存放位置：可以是 **Telegram 群组/频道/子 Topic**、**本地 NAS 目录** 或 **WebDAV 服务器**。支持同时配置多个目的地。 | **必需**（至少 1 个） |
| **📋 搬运任务 vs 📡 监听路由** | **任务（Task）** 是一次性搬运（单条链接、`100-500` 消息区间或全群从头扫描）；**监听路由（Route）** 则是 7×24 小时自动监听源会话并实时同步新消息。 | 按需使用 |

---

## 2. 提前准备 4 项凭据

在配置 `.env` 前，请准备好以下 4 项信息：

1. **`API_ID` 与 `API_HASH`**
   使用手机号登录 [my.telegram.org](https://my.telegram.org) → 进入 **API development tools** → 创建一个应用即可获取纯数字 `api_id` 和 32 位字符串 `api_hash`。
2. **`BOT_TOKEN`**
   在 Telegram 私聊 [@BotFather](https://t.me/BotFather) → 发送 `/newbot` 创建机器人 → 复制形如 `123456789:AAH...` 的 Token。
3. **`ADMIN_IDS`**
   在 Telegram 私聊 [@userinfobot](https://t.me/userinfobot) 即可查看你自己的纯数字 User ID（例如 `123456789`）。
   > [!IMPORTANT] 安全白名单机制
   > 只有列入 `ADMIN_IDS` 的 Telegram 用户才能与 Bot 私聊交互，其他陌生人发消息会被完全静默忽略。多个管理员用英文逗号分隔。

---

## 3. Docker Compose 部署步骤

### 第一步：下载配置文件并初始化目录权限

在服务器或 NAS 上创建工作目录，下载 `docker-compose.yml` 与 `.env.example`，并将挂载目录权限赋予容器内的非 root 用户（UID/GID `1000:1000`）：

```bash
mkdir -p tg-migrator && cd tg-migrator

curl -fsSLO https://raw.githubusercontent.com/BoeXoel/tg-migrator/main/docker-compose.yml
curl -fsSL https://raw.githubusercontent.com/BoeXoel/tg-migrator/main/.env.example -o .env

# 预先创建持久化目录并赋权给容器内 1000:1000 用户
mkdir -p data sessions downloads archive
sudo chown -R 1000:1000 data sessions downloads archive
chmod 700 data sessions
```

> [!WARNING] 为什么要执行 `chown -R 1000:1000`？
> 为了安全隔离，容器内部以非特权用户 `migrator (1000:1000)` 运行。如果让 Docker 自动在宿主机创建挂载目录，目录属主会变成 `root:root`，导致容器启动时报 `PermissionError: [Errno 13] Permission denied: '/app/downloads/active'`。提前执行上述 `chown` 即可彻底避免权限报错。

### 第二步：填写 `.env` 配置文件

编辑 `.env`，填入刚才准备好的 4 项必填凭据：

```ini
API_ID=12345678
API_HASH=0123456789abcdef0123456789abcdef
BOT_TOKEN=123456789:AAHxxxxxxxxxxxxxxxxxxxxxxxxxxxx
ADMIN_IDS=123456789

# 默认面板语言：en（英文）或 zh（简体中文），启动后也可随时在 Bot 内通过 /lang 切换
BOT_LANG=zh
```

::: tip 数据库选择（默认 SQLite 零配置开箱即用）
- **SQLite（默认，强烈推荐单机/NAS 使用）**：保持 `.env` 中的 `DATABASE_URL=` **完全留空**即可，数据会自动保存在 `./data/migrator.db`。
- **PostgreSQL / MySQL**：只有当你主动使用 `--profile postgres` 或 `--profile mysql` 启动外部数据库容器时，才需要填写 `DATABASE_URL`。若未开启对应数据库容器却取消注释了 `DATABASE_URL=...postgres...`，启动时会报 `socket.gaierror: Temporary failure in name resolution`。
:::

### 第三步：启动服务

```bash
docker compose up -d
docker compose logs -f tg-migrator
```

看到日志输出 `TG Migrator 0.0.2 starting` 后，你的 Bot 就会向你的 Telegram 账号发送一条上线通知！

---

## 4. 首次上手 5 分钟通关流程

打开 Telegram 与你的 Bot 私聊，按顺序执行以下 4 步即可完成首次搬运：

1. **呼出主面板（`/start`）**
   发送 `/start` 打开控制面板。如需切换中英文，直接点击主菜单的 **`🌐 Language / 语言`** 按钮，或发送 `/lang zh`。
2. **登录读取账号（`/login`）**
   点击 **`🔑 账号`**（或发送 `/login`）→ 发送带国际区号的手机号（如 `+8613800138000`）→ 在弹出的**内联数字键盘**上依次点击收到的验证码并点 `✔️`（切勿直接在聊天框打字发数字，否则会被 Telegram 官方拦截失效！）。
3. **绑定搬运目的地（`/bind`）**
   将你的 Bot 拉入目标群组或频道（授予发送消息权限），在群内（或目标**子 Topic 话题内部**）直接发送 `/bind`。
4. **发起第一次搬运**
   回到与 Bot 的私聊窗口，直接发送一条源频道的消息链接（例如 `https://t.me/c/123456789/42`），点击选择刚才绑定的目的地，即可实时查看搬运进度！

---

## 5. 后续版本平滑升级

当项目发布新版本时，已部署的服务可以无损热升级：

```bash
docker compose pull
docker compose up -d
```

- **数据与登录态 100% 保留**：数据库（`data/migrator.db`）、主加密密钥（`data/secret.key`）、已登录的 Telegram Session（`sessions/`）、已绑定的目的地与监听路由均原地沿用，无需重配。
- **未完成任务自动断点恢复**：重启瞬间处于 `in_progress` 状态的传输任务会在新容器启动时自动重置回 `pending` 继续执行，不会丢消息或卡死。
