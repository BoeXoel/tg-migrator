# 快速部署与升级指南

本指南介绍 TG Migrator 的核心架构概念、前置凭据准备、Docker Compose 标准部署流程，以及首次搬运任务的标准操作步骤。

---

## 1. 核心架构概念

在配置服务前，建议先了解系统中的 4 个核心角色：

| 核心概念 | 在系统中的职责 | 是否必需 |
| :--- | :--- | :--- |
| **🤖 控制机器人（Bot）** | 提供交互式管理面板（`/start`），同时默认作为向目标 Telegram 群组/频道发送消息的 **写入账号（Writer）**。 | **必需** |
| **👤 读取账号（Reader）** | 通过 `/login` 登录的个人 Telegram 账号。用于**读取**已加入的群组、频道或子话题，解决 Bot 账号无法通过邀请链接进群或无法直接拉取历史消息的限制。 | **推荐配置**（仅同步公开频道时可省略） |
| **🎯 目的地（Target）** | 搬运内容的归档位置：支持 **Telegram 群组/频道/子 Topic**、**本地 NAS 目录** 或 **WebDAV 服务器**。支持同时配置多个目的地。 | **必需**（至少 1 个） |
| **📋 搬运任务 vs 📡 监听路由** | **任务（Task）** 用于一次性搬运（单条链接、`100-500` 消息区间或全群历史扫描）；**监听路由（Route）** 用于 7×24 小时持续监听源会话并自动同步新消息。 | 按需启用 |

---

## 2. 前置凭据准备

在配置 `.env` 前，需准备以下 4 项基础参数：

1. **`API_ID` 与 `API_HASH`**
   使用手机号登录 [my.telegram.org](https://my.telegram.org) → 进入 **API development tools** → 创建应用并获取纯数字 `api_id` 与 32 位字符串 `api_hash`。
2. **`BOT_TOKEN`**
   在 Telegram 中私聊 [@BotFather](https://t.me/BotFather) → 发送 `/newbot` 创建机器人 → 获取形如 `123456789:AAH...` 的 HTTP API Token。
3. **`ADMIN_IDS`**
   在 Telegram 中私聊 [@userinfobot](https://t.me/userinfobot) 查询管理员账号的纯数字 User ID（例如 `123456789`）。
   > [!IMPORTANT] 管理员白名单鉴权
   > 仅列入 `ADMIN_IDS` 的 Telegram User ID 可与 Bot 私聊交互，非白名单用户的私聊请求将被静默忽略。多个管理员 ID 之间使用英文逗号分隔。

---

## 3. Docker Compose 部署流程

### 第一步：下载配置文件并初始化目录权限

在宿主机或 NAS 上创建工作目录，下载 `docker-compose.yml` 与 `.env.example`，并将持久化挂载目录的属主设置为容器内部的非特权用户（UID/GID `1000:1000`）：

```bash
mkdir -p tg-migrator && cd tg-migrator

curl -fsSLO https://raw.githubusercontent.com/BoeXoel/tg-migrator/main/docker-compose.yml
curl -fsSL https://raw.githubusercontent.com/BoeXoel/tg-migrator/main/.env.example -o .env

# 预先创建持久化目录并赋权给容器内 1000:1000 用户
mkdir -p data sessions downloads archive
sudo chown -R 1000:1000 data sessions downloads archive
chmod 700 data sessions
```

> [!WARNING] 目录属主权限说明（`1000:1000`）
> 容器内部默认以非特权用户 `migrator (1000:1000)` 运行。若未预先创建目录而由 Docker 守护进程自动创建，宿主机挂载目录属主将默认为 `root:root`，导致容器启动时抛出 `PermissionError: [Errno 13] Permission denied: '/app/downloads/active'`。预先执行上述 `chown` 命令即可避免该问题。

### 第二步：编辑 `.env` 配置文件

编辑 `.env` 文件并填入上述 4 项必填凭据：

```ini
API_ID=12345678
API_HASH=0123456789abcdef0123456789abcdef
BOT_TOKEN=123456789:AAHxxxxxxxxxxxxxxxxxxxxxxxxxxxx
ADMIN_IDS=123456789

# 默认面板语言：en（英文）或 zh（简体中文），运行期间可随时在 Bot 内通过 /lang 切换
BOT_LANG=zh
```

::: tip 数据库后端选择（默认使用内置 SQLite）
- **SQLite（默认，推荐单机或 NAS 部署使用）**：保持 `.env` 中的 `DATABASE_URL=` **留空**即可，数据库文件将自动创建并持久化于 `./data/migrator.db`。
- **PostgreSQL / MySQL**：仅在通过 `--profile postgres` 或 `--profile mysql` 启动对应数据库容器（或连接外部独立数据库）时才需配置 `DATABASE_URL`。若填写了容器内网地址（如 `@postgres:5432`）但未启用对应的 Compose Profile，启动时将触发 `socket.gaierror: Temporary failure in name resolution`。
:::

### 第三步：启动服务

```bash
docker compose up -d
docker compose logs -f tg-migrator
```

当日志输出 `TG Migrator 0.0.3 starting` 时，服务即已完成初始化，Bot 会自动向管理员账号发送上线通知。

---

## 4. 首次运行快速引导

在 Telegram 中打开与 Bot 的私聊窗口，按以下 4 步即可完成首次搬运：

1. **打开控制面板（`/start`）**
   发送 `/start` 呼出主菜单。如需切换界面语言，可点击主面板的 **`🌐 Language / 语言`** 按钮，或直接发送 `/lang zh` / `/lang en`。
2. **登录读取账号（`/login`）**
   点击 **`🔑 账号`**（或发送 `/login`）→ 输入带国际区号的手机号（如 `+8613800138000`）→ 在弹出的**内联数字键盘**上依次点击收到的验证码并按 `✔️` 提交（切勿直接在聊天输入框中以纯文本发送验证码，否则会被 Telegram 安全机制判定泄露并立即作废）。
3. **绑定搬运目的地（`/bind`）**
   将 Bot 添加至目标群组或频道并授予发言权限，在群组内（或目标**子 Topic 话题内部**）直接发送 `/bind`。
4. **发起搬运任务**
   在 Bot 私聊窗口中转发一条源消息，或直接发送源频道消息链接（例如 `https://t.me/c/123456789/42`），勾选目标目的地并点击 **`🚀 开始`** 即可启动搬运。

---

## 5. 版本平滑升级

升级至新版本镜像时不会影响任何已有数据与配置：

```bash
docker compose pull
docker compose up -d
```

- **数据与会话持久化**：数据库（`data/migrator.db`）、主加密密钥（`data/secret.key`）、已登录的 Telegram Session（`sessions/`）、已绑定的目的地与监听路由均保存在挂载卷中，升级后自动沿用。
- **中断任务自动恢复**：容器重启瞬间处于 `in_progress` 状态的传输作业，会在新容器启动时自动重置为 `pending` 状态并继续执行。
