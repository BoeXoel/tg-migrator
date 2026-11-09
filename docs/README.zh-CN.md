# TG Migrator

[English](../README.md) | **简体中文** | [文档](https://tg-migrator.isgay.top/zh/) | [Docker](https://hub.docker.com/r/boexoel/tg-migrator)

> **通过 Telegram Bot 交互管控的自托管消息归档与多端路由服务**
> 支持将你拥有合法访问权限的 Telegram 群组、频道或论坛 Topic 消息，同步与结构化归档至 **其他 Telegram 会话**、**本地 NAS 目录** 或 **WebDAV 存储（OpenList / Nextcloud 等）**，兼顾一次性历史迁移与 7×24 小时实时监听同步。

---

## 核心特性

- **全交互式 Bot 管控**：私聊发送消息链接即可下达迁移任务；目的地绑定、监听路由、过滤规则、运行参数均可在 Bot 内通过交互面板或命令完成。内置 `ADMIN_IDS` 严格白名单鉴权，对非管理员保持静默。
- **多端存储目的地（Multi-Sink）**：
  - `telegram`：支持目标群组、频道及 Forum Topic。优先采用免下载的**原生复制（Native Copy）**；当跨会话无法直接复制或需清洗剧透遮罩时，自动回退为**本地流式下载重传**，完整保留视频宽高与时长、音频标签、语音波形、原始文件名及安全缩略图，并支持相册（Album）整组聚合发送。
  - `local`：结构化归档至容器挂载的本地目录（严格做 `ARCHIVE_ROOT` 路径越界防护，适配 NAS 本地盘）。
  - `webdav`：支持公网 HTTPS 及内网 HTTP（如 `http://openlist:5244/dav`），提供**本地缓存落盘后上传（Spool）**与**零落盘流式直传（Zero-Disk Stream）**两种模式。
- **长期监听与多路路由（Watch & Route）**：支持配置“单来源（可精确到 Topic）→ 多目的地”的持久化路由；支持新消息实时监听（相册自动凑齐整组）与水位线（Watermark）定时巡检，服务重启后自动补齐期间遗漏的消息。
- **全媒体与消息形态覆盖**：纯文本、图片、视频、GIF 动图、音频、语音、圆形视频消息（Video Note）、通用文件、贴纸；投票与地理位置等特殊消息支持原生复制。
- **超大文件智能分流**：自动识别普通账号/Bot（2000 MiB）与 Telegram Premium（4000 MiB）上传上限；支持配置由 Premium 读取账号代发 2–4 GiB 大文件，或自动将超限文件降级转存至绑定的本地 / WebDAV 备用目的地。
- **多数据库引擎与配置便携**：基于 SQLAlchemy 2.0 异步 ORM，开箱即用支持 **SQLite**（默认），并可无缝切换至 **PostgreSQL** 或 **MySQL / MariaDB**；支持通过 `/export` 与 `/import` 进行 YAML 格式的全量配置备份与恢复。
- **生产级稳健性与风控保护**：细粒度操作限速令牌桶、FloodWait 动态退避与抖动、账号风控异常自动熔断暂停并推送告警、磁盘最低剩余空间水位保护、中断任务一键恢复与失败项重试。
- **智能代理分流**：支持 SOCKS5 / HTTP CONNECT 代理，内置内网地址识别与 DNS 解析校验，访问局域网或 Docker 内网 WebDAV 服务时自动绕过代理直连。

---

## 快速开始（Docker 部署）

> [!IMPORTANT]
> **前置准备：所需 Telegram 凭据（用于填写 `.env`）**
> 在启动服务前，请提前准备好以下三项凭据信息：
> - **`API_ID` 与 `API_HASH`**：使用手机号登录 [my.telegram.org](https://my.telegram.org)，进入 **API development tools** 创建一个应用即可获取。
> - **`BOT_TOKEN`**：在 Telegram 中私聊官方 [@BotFather](https://t.me/BotFather)，发送 `/newbot` 创建一个用于控制服务的 Bot 并获取其 Token。
> - **`ADMIN_IDS`**：你个人的 Telegram 纯数字用户 ID（支持多个，用英文逗号分隔），可通过私聊 [@userinfobot](https://t.me/userinfobot) 等机器人查询获取。

### 方式一：使用 Docker Hub 预构建镜像（推荐）

官方多架构预构建镜像（支持 `linux/amd64` 与 `linux/arm64`）已发布于 Docker Hub：**[`boexoel/tg-migrator`](https://hub.docker.com/r/boexoel/tg-migrator)**。在服务器上仅需准备 [`docker-compose.yml`](../docker-compose.yml) 与 [`.env.example`](../.env.example)：

```bash
# 1. 复制并编辑环境变量配置（填入 API_ID / API_HASH / BOT_TOKEN / ADMIN_IDS）
cp .env.example .env

# 2. 创建持久化目录并设置与 .env 中 PUID/PGID 一致的属主权限（默认 1000:1000）
mkdir -p data sessions downloads archive
sudo chown -R 1000:1000 data sessions downloads archive

# 3. 拉取镜像并后台启动服务
docker compose pull && docker compose up -d
docker compose logs -f
```

### 方式二：本地源码构建启动

如果你修改了源码希望在本地编译运行，只需取消 [`docker-compose.yml`](../docker-compose.yml) 中 `# build: .` 的注释并执行：

```bash
docker compose up -d --build
```

---

## 初始化与使用流程

服务启动后，在 Telegram 中私聊你的 Bot：

1. **打开主面板**：发送 `/start` 查看系统状态与交互菜单。
2. **登录读取账号（Reader）**：
   - 发送 `/login` 在 Bot 内按向导输入手机号，使用**内联数字键盘**输入验证码（避免明文验证码被聊天记录捕获），若开启了两步验证（2FA），Bot 读取密码后会立即自动删除该条密码消息。
   - *备选方案*：也可在服务器终端执行交互式登录：`docker compose run --rm migrator login`。
3. **配置目的地（Targets）**：
   - **Telegram 群组 / 频道 / Topic**：将 Bot 加入目标会话并赋予发消息权限，直接在目标群组（或指定 Topic 内）发送 `/bind` 即可自动完成绑定；若在频道或以匿名管理员身份操作，可先私聊 Bot 发送 `/bind` 获取一次性绑定口令，再转发或粘贴至目标频道。
   - **本地目录**：私聊发送 `/add_local` 按向导绑定子目录。
   - **WebDAV 存储**：私聊发送 `/add_webdav` 按向导配置地址与凭据（创建时会自动执行 `MKCOL` / `PUT` / `DELETE` 读写连通性自检）。
4. **下达一次性迁移任务**：
   直接向 Bot 发送 Telegram 消息链接即可创建任务：
   - 单条消息（相册会自动识别并整组迁移）：`https://t.me/c/1234567890/100`
   - 消息范围（单条范围链接或一次发送首尾两条链接）：`https://t.me/c/1234567890/100-500`
   - 指定 Forum Topic 消息：`https://t.me/c/1234567890/456/100`

---

## 长期同步与实时监听路由

发送 `/add_route` 按交互向导配置：**路由名称 → 来源会话（可选定特定 Topic） → 一个或多个目的地 ID → 是否开启实时监听**。

- **实时触发 + 巡检兜底**：开启监听后，既会通过事件流实时捕获新消息（相册自动聚合），也会按 `watch.poll_interval_seconds` 周期以及在容器重启时，基于数据库水位线（`last_seen_msg_id`）自动扫描并补齐离线期间遗漏的消息。
- **历史回溯**：发送 `/routes <ID>` 打开路由详情面板并点击「扫描历史」，即可将该路由来源的历史存量消息批量压入迁移队列。
- **路由级独立配置**：支持针对单条路由覆盖全局设置，例如：
  - `/route_set <路由ID> transfer.include.document true`
  - `/route_unset <路由ID> transfer.include.document`

> **注意**：请勿配置两条互为来源与目的地的路由，以免产生循环同步。

---

## 归档目录结构（Local / WebDAV）

文件与元数据默认采用扁平清晰的路径层级：

```text
<来源名称_ID>/[topic_<ID>/][YYYY-MM/]<消息ID>_<原始文件名>
<来源名称_ID>/[topic_<ID>/][YYYY-MM/]<消息ID>.json
```

- **智能省略冗余层级**：非 Forum Topic 消息不会生成 `topic_<ID>/` 目录；按月份分目录默认关闭（可通过 `/set archive.group_by_month true` 开启）。
- **结构化元数据（Sidecar）**：纯文本消息与媒体的配文（Caption）默认生成同名 `.json` 元数据文件（包含消息 ID、原始时间戳、发送者、Topic、文本实体及关联文件列表）；可通过 `/set archive.sidecar_format md|both|none` 切换为 Markdown、双格式同时生成或不生成。
- **目录命名策略**：支持通过 `archive.folder_naming` 选择 `title_id`（默认，如 `技术交流群_-100123456`）、`id` 或 `title`。

---

## 运行参数与在线配置

发送 `/config` 可调出可视化按钮面板（快速切换消息类型开关、传输策略、归档格式与过滤开关）；全部运行时参数支持通过 `/get [前缀]` 查询、`/set <配置项> <值>` 动态修改并持久化至数据库，无需重启容器。

| 配置项 | 默认值 | 说明 |
| --- | --- | --- |
| `ui.language` | `en` | Bot 界面语言：`en`（英文）或 `zh`（简体中文），初始默认值取自 `.env` 中的 `BOT_LANG` |
| `transfer.hide_sender` | `true` | 原生复制消息时隐藏原发送者引用头 |
| `transfer.native_copy` / `native_only` | `true` / `false` | 优先使用免下载原生复制 / 仅允许原生复制（需读取账号同样具备目标群写入权限） |
| `transfer.strip_spoilers` | `true` | 自动去除媒体剧透遮罩（开启后含遮罩媒体将通过下载重传去除标记） |
| `transfer.allow_premium_user_fallback` | `false` | 当文件处于 2–4 GiB 区间时，允许使用已登录的 Premium 读取账号代为上传 |
| `archive.webdav_mode` | `auto` | `auto`（本地空间充足时先落盘，不足时自动转流式）、`stream`（强制零落盘流式直传）、`spool`（强制先落盘） |
| `archive.sidecar_format` | `json` | 文本与配文元数据格式：`json` / `md` / `both` / `none` |
| `archive.group_by_month` | `false` | 是否在归档路径中增加 `YYYY-MM/` 月份目录层 |
| `worker.batch_size` / `pause_between_batches_seconds` | `50` / `120` | 每批次连续处理的任务项数及批次间冷却秒数，平滑请求曲线以降低账号风控概率 |
| `downloads.min_free_bytes` | `5368709120` (5 GiB) | 下载暂存目录最低保留可用空间水位线 |
| `limits.*` | — | 各类 MTProto 操作的最小间隔与 FloodWait 退避上限 |

---

## YAML 备份与恢复

- **导出备份**：发送 `/export` 导出当前全部设置、目的地、监听路由与过滤规则的 `.yaml` 文件（WebDAV 密码默认脱敏为 `<REDACTED>`）；发送 `/export secrets` 可导出含明文密码的完整备份（请妥善保管）。
- **导入恢复**：
  - **合并模式（默认）**：直接向 Bot 发送 `.yaml` 文件，按名称/配置增量合并更新。
  - **全量替换模式**：先发送 `/import replace`，再发送 `.yaml` 文件，将全量同步并清理备份中不存在的旧目的地与路由。
  - 导入过程具备事务级预校验，格式或字段校验不通过时不会改动现有数据库；若导入文件中密码为 `<REDACTED>`，会自动保留数据库中已有的加密密码。

---

## 多数据库配置

在 `.env` 中通过 `DATABASE_URL` 指定数据库后端（留空则默认使用 `./data/migrator.db` SQLite 数据库）：

```env
# SQLite（默认，零外部依赖）
DATABASE_URL=

# PostgreSQL
DATABASE_URL=postgresql+asyncpg://migrator:your_password@postgres:5432/migrator

# MySQL / MariaDB
DATABASE_URL=mysql+aiomysql://migrator:your_password@mysql:3306/migrator
```

[`docker-compose.yml`](../docker-compose.yml) 内置了可选的数据库服务 Profile，可按需一键启用（启用前请先在 `.env` 中设置 `POSTGRES_PASSWORD` 或 `MYSQL_PASSWORD`）：

```bash
docker compose --profile postgres up -d
# 或
docker compose --profile mysql up -d
```

---

## 代理与内网直连分流

在 `.env` 中配置代理（为防止在 Bot 内误操作导致服务与 Telegram 失联，代理参数仅通过环境变量管理）：

```env
PROXY_URL=socks5://user:pass@proxy.example:1080
PROXY_APPLY_TO=telegram,webdav
NO_PROXY=nas.local,203.0.113.0/24
PROXY_BYPASS_RESOLVE=true
```

- **严格代理策略**：启用 Telegram 代理后，若代理不可用将直接报错并等待恢复，不会静默回退为直连，防止暴露宿主机真实 IP。
- **内网 WebDAV 自动直连**：访问以下目标时会自动绕过代理直连：
  - `localhost` 及环回地址（`127.0.0.0/8`、`::1`）
  - 私有与保留网段（`10.0.0.0/8`、`172.16.0.0/12`、`192.168.0.0/16`、`169.254.0.0/16`、`100.64.0.0/10` CGNAT/Tailscale、IPv6 ULA/Link-Local）
  - 单标签主机名（如 Docker Compose 内部服务名 `openlist`、`nextcloud`）
  - `.local` / `.lan` / `.internal` / `.home.arpa` 域名及 `NO_PROXY` 列表
  - 当 `PROXY_BYPASS_RESOLVE=true`（默认）时，DNS 解析结果为上述内网 IP 的自定义域名同样自动直连。

---

## 安全架构说明

- **供应链与依赖安全**：仅采用主流官方活跃维护的上游库（Telethon、SQLAlchemy、httpx、cryptography 等）；[`requirements.txt`](../requirements.txt) 全量锁定 SHA256 哈希，容器构建启用 `--require-hashes --only-binary=:all:`，拒绝执行不受信任的第三方构建脚本。
- **凭据加密与权限收敛**：
  - WebDAV 凭据在数据库中使用基于 `SECRET_KEY`（或自动持久化于 `data/secret.key`）的 Fernet 对称加密存储。
  - Telethon 会话文件（`sessions/`）与密钥文件在创建时强制收敛为 `0600` 权限。
  - 日志输出自动脱敏 Bot Token 及 URL 中的账号密码；内容过滤器命中时仅记录规则编号，不将敏感原文写入日志。
- **容器运行时加固**：容器默认以非特权用户（UID/GID 1000）运行，启用只读根文件系统（`read_only: true`）、禁止提权（`no-new-privileges:true`）并丢弃全部 Linux Capabilities（`cap_drop: ALL`）。

---

## Bot 命令速查表

| 类别 | 命令 | 说明 |
| --- | --- | --- |
| **状态与任务** | `/start` `/help` `/status` `/tasks [ID]` | 打开主菜单、完整命令速查、查看系统实时运行状态与任务 |
| **任务控制** | `/pause` `/resume` `/recover` | 暂停队列、恢复队列、恢复异常中断的任务 |
| **重试与取消** | `/retry [任务ID]` `/cancel_task <ID>` | 重新排队失败的消息项、取消指定运行中任务 |
| **目的地管理** | `/targets [ID]` `/bind` `/add_tg` `/add_local` `/add_webdav` | 查看、管理单条目详情、绑定、新增或测试各类存储目的地 |
| **路由与监听** | `/routes [ID]` `/add_route` `/route_set` `/route_unset` | 管理长期同步路由、历史扫描与路由级参数覆盖 |
| **系统配置** | `/config` `/lang` `/get [前缀]` `/set <键> <值>` | 可视化配置面板、一键切换中英语言、查询或在线修改运行时参数 |
| **内容过滤** | `/filters` `/filter_add` `/filter_del` `/filter_toggle` | 管理关键词/正则表达式黑白名单与媒体类型过滤 |
| **备份与维护** | `/export [secrets]` `/import [replace]` | 导出或导入 YAML 配置文件 |
| **校验与报表** | `/verify <任务ID>` `/report [任务ID]` | 抽样校验目标端已投递消息完整性、导出超大未传文件 CSV 报表 |
| **会话管理** | `/login` `/logout` `/cancel` | 登录或登出 Telegram 读取账号、中止当前交互向导 |

---

## 开发与测试

```bash
# 重新生成带 SHA256 哈希的依赖锁定文件（目标环境 Python 3.12）
uv pip compile --python-version 3.12 --generate-hashes requirements.in -o requirements.txt
uv pip compile --python-version 3.12 --generate-hashes requirements-dev.in -o requirements-dev.txt

# 在与生产一致的容器环境内运行完整单元测试套件
docker build --target test -t tg-migrator:test . && docker run --rm tg-migrator:test
```

---

## 合规与免责声明（Disclaimer）

1. 本项目仅作为个人自托管的数据备份、群组迁移与知识归档工具提供，旨在帮助用户整理和持久化保存**自身拥有合法访问与使用权限**的社群消息与媒体资料。
2. 请严格遵守所在国家/地区的法律法规、知识产权与版权保护规定，以及 [Telegram 服务条款（Terms of Service）](https://telegram.org/tos) 与 [Telegram API 使用规范](https://core.telegram.org/api/terms)。
3. 请尊重原创内容创作者的合法权益，切勿将本工具用于未经授权的商业分发、侵犯他人版权或违反社群规则的行为。使用者因不当使用本工具所产生的一切账号风控或法律责任均由使用者自行承担，项目作者不承担任何连带责任。

---

## 开源协议（License）

本项目基于 **[GNU Affero General Public License v3.0 (AGPL-3.0)](../LICENSE)** 协议开源。

- 你可以自由地获取、使用、修改和分发本项目的源代码；
- 若你对本项目进行了修改，或基于本项目通过网络（包括通过 Telegram Bot 接口）向第三方提供服务，你**必须**遵循 AGPL-3.0 协议要求，将完整修改后的源代码以相同协议向用户免费公开；
- 未经作者另行书面商业授权，禁止将本项目或其衍生版本闭源用于商业售卖或付费托管服务。
