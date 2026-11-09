<div align="center">
  <img src="./public/logo.png" alt="TG Migrator logo" width="80" height="80" />
  <h1>TG Migrator</h1>
  <p><strong>你的 Telegram 消息，随处归档与多端路由。</strong></p>
  <p>给你的 Bot 发一条消息链接，即可将群组、频道或子话题同步归档至 Telegram、本地 NAS 或 WebDAV。<br />自托管、开源、全程在 Telegram 内交互管控。</p>
  <p>
    <a href="https://hub.docker.com/r/boexoel/tg-migrator" target="_blank" rel="noopener noreferrer"><img src="https://img.shields.io/docker/v/boexoel/tg-migrator?sort=semver&color=0284c7&label=docker" alt="Docker version" /></a>
    <a href="../LICENSE"><img src="https://img.shields.io/github/license/BoeXoel/tg-migrator?color=52525b" alt="AGPL-3.0 license" /></a>
    <a href="https://github.com/BoeXoel/tg-migrator/stargazers"><img src="https://img.shields.io/github/stars/BoeXoel/tg-migrator?style=flat&color=52525b" alt="GitHub stars" /></a>
  </p>
  <p><a href="https://tg-migrator.isgay.top/zh/" target="_blank" rel="noopener noreferrer">文档</a> · <a href="https://hub.docker.com/r/boexoel/tg-migrator" target="_blank" rel="noopener noreferrer">Docker Hub</a> · <a href="../README.md">English</a></p>
</div>

## 快速开始

下载 [`docker-compose.yml`](../docker-compose.yml) 与 [`.env.example`](../.env.example)，在 `.env` 中填入 `API_ID`、`API_HASH`、`BOT_TOKEN` 和 `ADMIN_IDS`，然后启动容器：

```sh
cp .env.example .env
mkdir -p data sessions downloads archive && sudo chown -R 1000:1000 data sessions downloads archive
docker compose pull && docker compose up -d
```

在 Telegram 中私聊你的 Bot：

```text
  /start                                  → 查看系统就绪状态与上手引导
  /login                                  → 通过内联数字键盘安全登录读取账号
  /bind                                   → 绑定目标群组、频道或 Forum 子话题
  https://t.me/c/1234567890/100-500       → 直接回复目的地 ID 立即启动迁移
```

**默认私密安全：** 只有列入 `ADMIN_IDS` 的管理员能获得 Bot 的 `/` 快捷指令菜单与响应，其他任何用户的消息都会被静默忽略。

TG Migrator 免费开源。如果它帮到了你，欢迎[在 GitHub 上点个 Star](https://github.com/BoeXoel/tg-migrator)：能让更多自托管玩家发现它，也是对这个项目最大的鼓励。

## 为什么用 TG Migrator

- **全程在 Telegram 内管控。** 无需额外暴露 Web 管理后台。防截获内联数字键盘登录、目的地绑定、监听路由管理、运行参数热更新全部在私聊窗口内完成。
- **规模增长界面依然清爽。** 目的地（`/targets`）、路由（`/routes`）与任务（`/tasks`）均采用纯文本分页列表，配合 `/targets 1`、`/routes 2`、`/tasks 15` 直达单条目操作卡片，几百条任务也不会让按钮撑爆屏幕。
- **原生多端存储（Multi-Sink）。** 支持路由至其他 Telegram 会话（可精确投递到指定 Forum 子话题）、本地 NAS 挂载目录（`ARCHIVE_ROOT`）或 WebDAV 存储（OpenList、Nextcloud 等），兼顾落盘缓存与零落盘流式直传。
- **能免下载就原生复制，需重传也无损还原。** 条件允许时优先使用零流量原生复制；跨会话受限或需去除剧透遮罩时自动回退为流式下载重传，完整保留视频宽高时长、音频标签、语音波形、原始文件名及相册（Album）整组聚合。
- **实时监听 + 水位线自动补齐。** 事件监听实时捕获新消息并自动凑齐相册，同时基于数据库 `last_seen_msg_id` 水位线定时巡检，容器重启或网络闪断后自动补齐离线期间遗漏的消息。
- **专为账号防风控设计。** 多任务公平轮询（Round-Robin）并发调度、读写传独立限速锁、批次冷却休眠、FloodWait 动态退避抖动，触发账号限制时自动熔断暂停并推送告警。

## 适合用来

- 把分散在多个群组、频道里的资料按分类汇总到一个开启了子话题（Topics）的私人群组；
- 将媒体、文档以及结构化 `.json` / `.md` 元数据自动归档到家里的 NAS 或 OpenList / Nextcloud 等 WebDAV 网盘；
- 7×24 小时将某个来源群组（或指定子话题）的新消息实时镜像分发到多个目的地；
- 自动将超过 `2000 MiB` 的超大文件降级分流至绑定的本地 / WebDAV 备用目的地（或交由已登录的 Premium 账号上传 `2–4 GiB` 文件）；
- 在 SQLite、PostgreSQL 或 MySQL 之间自由切换，通过 `/export` 与 `/import` 一键备份和恢复 YAML 配置。

## 用法

在私聊窗口直接向 Bot 发送消息链接或命令：

```text
<单条消息链接>                      https://t.me/c/1234567890/100
<消息范围链接>                      https://t.me/c/1234567890/100-500
<指定子话题消息链接>                https://t.me/c/1234567890/456/100

  /start, /help                     系统就绪概览与完整命令字典
  /login, /logout                   登录或登出 Telegram 读取账号
  /bind                             绑定 Telegram 群组、频道或 Forum 子话题
  /add_local, /add_webdav           新增本地 NAS 目录或 WebDAV 存储目的地
  /targets [ID]                     分页查看目的地列表，或管理指定 #<ID> 目的地
  /add_route, /routes [ID]          创建实时监听路由，或管理指定 #<ID> 路由
  /tasks [ID], /status              分页查看任务队列，或查看/取消 #<ID> 任务
  /pause, /resume, /retry [ID]      暂停/恢复全局队列，或重试失败的消息项
  /config, /get, /set <键> <值>     交互式参数面板，或在线热更新配置项
  /filters                          管理关键词/正则黑白名单与媒体类型过滤
  /export [secrets], /import        导出或原子化导入 YAML 配置备份
  /lang                             一键切换中英文界面（简体中文 / English）
```

粘贴消息链接后，Bot 会列出当前已启用的目的地编号。直接回复目的地 ID（如 `1` 或 `1, 2`）即可立即启动任务。

### 超大文件与降级分流

- 普通 Telegram 账号与 Bot 单文件上传上限为 `2000 MiB`（Telegram Premium 为 `4000 MiB`）。
- 发送 `/targets <ID>` 打开任意 Telegram 目的地详情，可为其绑定一个**本地或 WebDAV 超大文件降级目的地**；也可开启 `/set transfer.allow_premium_user_fallback true`，让已登录的 Premium 读取账号代为上传 `2–4 GiB` 文件。
- 下载暂存区始终受 `downloads.min_free_bytes`（默认保留 `5 GiB` 可用空间）水位保护，防止大批量任务撑满宿主机磁盘。

## 工作原理

```text
                                                               ┌──▶ Telegram 群组 / 频道 / 子话题
来源会话 / 子话题 ──▶ 读取账号 / Bot ──▶ 公平轮询队列与限速器 ─┼──▶ 本地 NAS 目录 (ARCHIVE_ROOT)
                                                               └──▶ WebDAV 存储 (OpenList / Nextcloud)
```

TG Migrator 在单个非 root 容器内协同运行两个 Telethon 会话：**Bot 客户端**负责交互控制与常规投递，可选的**个人读取账号（Reader）**负责读取已加入的私有群组及处理 `2–4 GiB` Premium 上传。历史迁移任务与实时监听路由通过公平轮询（Round-Robin）交错调度，`read`、`download`、`upload`、`copy` 四类操作拥有各自独立的限速锁，互不阻塞。

关于 Docker 部署、子话题绑定、WebDAV 流式模式、数据库切换与内网代理分流的完整指南，请参阅<a href="https://tg-migrator.isgay.top/zh/" target="_blank" rel="noopener noreferrer">在线文档</a>。

## 常见问题

部署与日常使用中最常见的现象及处理方式：

| 现象或报错 | 原因 | 解决办法 |
| --- | --- | --- |
| `PermissionError: '/app/data/secret.key'` | 宿主机挂载目录属主为 `root`，而容器以 `1000:1000` 运行 | 执行 `sudo chown -R 1000:1000 data sessions downloads archive` |
| `could not translate host name "postgres"` | `.env` 填写了 `postgres` 地址但未启用数据库 Profile | 用 SQLite 请注释掉 `DATABASE_URL`；用 PG 请执行 `docker compose --profile postgres up -d` |
| 给 Bot 发 `/start` 无任何响应 | 当前 Telegram 账号未列入 `ADMIN_IDS` 白名单 | 将你的纯数字用户 ID 填入 `.env` 的 `ADMIN_IDS` 并重启容器 |
| `ChatAdminRequiredError` / `Topic closed` | Bot 在目标群缺少发消息或管理话题权限 | 在目标群赋予 Bot「发布消息」与「管理话题」管理员权限 |
| `FILE_TOO_BIG`（`> 2000 MiB`） | 单文件超过普通 Bot 上传体积上限 | 在 `/targets <ID>` 中绑定本地/WebDAV 降级目的地，或开启 Premium 代传 |

## 参与开发

欢迎提交 Issue 和 Pull Request，详见 [CONTRIBUTING.md](../CONTRIBUTING.md)。

## 合规声明

本项目仅作为个人自托管的数据备份、群组迁移与知识归档工具提供，旨在帮助用户整理和保存**自身拥有合法访问权限**的社群消息与媒体资料。请严格遵守所在地区法律法规、知识产权规定及 <a href="https://telegram.org/tos" target="_blank" rel="noopener noreferrer">Telegram 服务条款</a>。

## License

[AGPL-3.0](../LICENSE)
