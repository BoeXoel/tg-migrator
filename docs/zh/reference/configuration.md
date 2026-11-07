# 命令与参数速查字典

本页汇总了 TG Migrator 的全部 Bot 斜杠命令、`.env` 启动环境变量以及可通过 `/set` 在线热更新的运行时配置项，方便日常速查。

---

## 1. Bot 斜杠命令速查表

| 命令 | 参数格式 | 功能说明 |
| :--- | :--- | :--- |
| `/start` 或 `/help` | — | 打开交互式主控制面板 |
| `/lang` | `[en\|zh]` | 一键切换或指定 Bot 面板与通知语言（`en` 英文 / `zh` 简体中文） |
| `/status` | — | 查看读取/写入账号状态、队列统计、Worker 状态与磁盘剩余空间 |
| `/tasks` | — | 查看近期搬运任务列表与执行进度 |
| `/targets` | — | 查看并管理所有目的地（Telegram、本地目录、WebDAV） |
| `/bind` | `[绑定码]` | 在群组/子话题内发送：绑定为目的地；在私聊发送：生成 10 分钟一次性绑定码 |
| `/add_tg` | `<群ID或@名> [话题ID]` | 在私聊中手动添加 Telegram 群组/频道或指定子 Topic 目的地 |
| `/add_local` | — | 启动向导新建本地 NAS 归档目录（位于 `ARCHIVE_ROOT` 下） |
| `/add_webdav` | — | 启动向导新建 WebDAV 归档目的地 |
| `/routes` | — | 查看并管理长期监听路由 |
| `/add_route` | — | 启动向导新建长期监听路由 |
| `/route_set` | `<路由ID> <键> <值\|->` | 为指定路由设置独立参数覆盖（传 `-` 移除该覆盖项） |
| `/config` | — | 打开交互式参数设置面板 |
| `/get` | `[分区名\|键名]` | 查看全部设置、某一分区（如 `/get transfer`）或单个配置项当前值 |
| `/set` | `<键名> <新值>` | 在线修改任意运行时参数（立即落库生效，无需重启容器） |
| `/filters` | — | 查看内容过滤开关及已配置的关键词/正则规则 |
| `/filter_add` | `keyword\|regex <内容>` | 新增关键词或正则表达式过滤规则 |
| `/filter_del` | `<规则ID>` | 删除指定编号的过滤规则 |
| `/export` | `[secrets]` | 将全部设置、目的地、路由与过滤规则导出为 YAML（加 `secrets` 包含明文密码） |
| `/import` | `[replace]` | 回复一条 YAML 文件消息进行增量合并（默认）或全量覆盖（加 `replace`） |
| `/pause` / `/resume` | — | 安全暂停（等待当前文件传完）或恢复后台传输队列 |
| `/retry` | `[任务ID]` | 重新排队全部失败项，或仅重试指定任务的失败项 |
| `/recover` | — | 在队列暂停状态下，将中断的 `in_progress` 任务重置回 `pending` |
| `/cancel_task` | `<任务ID>` | 取消正在扫描或排队中的搬运任务 |
| `/verify` | — | 抽检已发送到 Telegram 目的地的消息是否仍然完好存在 |
| `/report` | — | 导出因超过上传体积上限而被跳过的超限文件 CSV 报表 |
| `/login` / `/logout` | — | 在线登录或退出个人 Telegram 读取账号 |
| `/cancel` | — | 随时中止当前正在进行的多步交互向导 |

---

## 2. `.env` 环境变量字典

以下变量在容器启动时读取：

| 变量名 | 是否必填 | 默认值 | 说明 |
| :--- | :--- | :--- | :--- |
| `API_ID` | **必填** | — | 在 [my.telegram.org](https://my.telegram.org) 申请的纯数字 `api_id` |
| `API_HASH` | **必填** | — | 32 位字符串 `api_hash` |
| `BOT_TOKEN` | **必填** | — | 从 [@BotFather](https://t.me/BotFather) 获取的机器人 Token |
| `ADMIN_IDS` | **必填** | — | 允许控制 Bot 的管理员数字 User ID（多个用英文逗号分隔） |
| `BOT_LANG` | 可选 | `en` | 初始面板语言（`en` 英文 或 `zh` 简体中文） |
| `NOTIFY_CHAT_IDS` | 可选 | 同 `ADMIN_IDS` | 接收系统通知的会话 ID 列表 |
| `USE_BOT_AS_WRITER` | 可选 | `true` | 默认使用 Bot（`true`）还是个人读取号（`false`）作为 Telegram 写入端 |
| `DATABASE_URL` | 可选 | *（内置 SQLite）* | 留空即使用 `./data/migrator.db`；使用外部库时填 `postgresql+asyncpg://...` 或 `mysql+aiomysql://...` |
| `SECRET_KEY` | 可选 | *（自动生成）* | 用于在数据库加密保存 WebDAV 密码的主密钥。留空时首次启动自动生成并持久化在 `data/secret.key` |
| `PROXY_URL` | 可选 | — | 代理地址，支持 `socks5://user:pass@host:port` 或 `http://host:port` |
| `PROXY_APPLY_TO` | 可选 | `telegram,webdav` | 走代理的流量范围（`telegram`、`webdav` 或 `telegram,webdav`） |
| `NO_PROXY` | 可选 | — | 额外绕过代理的域名/网段（局域网 IP 与单标签容器名已默认自动直连） |
| `LOG_LEVEL` | 可选 | `INFO` | 日志级别：`DEBUG`、`INFO`、`WARNING`、`ERROR` |

---

## 3. 运行时参数字典（`/set <键名> <值>`）

所有运行时参数均保存在数据库中，通过 `/set` 或 `/config` 面板修改后**立即生效**：

### `ui.*` — 界面语言
| 参数键名 | 默认值 | 说明 |
| :--- | :--- | :--- |
| `ui.language` | `en` | 面板与通知语言：`en`（英文）或 `zh`（简体中文） |

### `transfer.*` — 搬运行为与媒体类型开关
| 参数键名 | 默认值 | 说明 |
| :--- | :--- | :--- |
| `transfer.hide_sender` | `true` | 原生复制时隐藏原转发来源抬头（无引用转发） |
| `transfer.drop_caption` | `false` | 搬运媒体时是否剥离附带的文字说明（Caption） |
| `transfer.strip_spoilers` | `true` | 自动剥离图片/视频/文字上的防剧透遮罩（含遮罩时会自动走重传剥离） |
| `transfer.native_copy` | `true` | 当读取号同时作为写入号且无需剥离剧透时，优先尝试零流量服务端原生复制 |
| `transfer.native_only` | `false` | 仅允许原生复制（遇到禁止转发频道直接跳过而不下载重传） |
| `transfer.allow_premium_user_fallback` | `false` | 当 Bot 遇到 2GB~4GB 超大文件时，允许自动切换为已登录的 Premium 个人号上传 |
| `transfer.allow_download_unknown_size` | `false` | 是否允许下载未预先标明文件大小的媒体 |
| `transfer.caption_overflow` | `split` | 超长说明文字处理方式：`split`（超出部分拆分为紧随其后的纯文本消息）或 `truncate`（截断） |
| `transfer.include.text` | `true` | 是否搬运纯文本消息 |
| `transfer.include.photo` | `true` | 是否搬运图片 |
| `transfer.include.video` | `true` | 是否搬运视频 |
| `transfer.include.animation` | `true` | 是否搬运 GIF 动图 |
| `transfer.include.audio` | `true` | 是否搬运音乐/音频 |
| `transfer.include.voice` | `true` | 是否搬运语音消息 |
| `transfer.include.video_note` | `true` | 是否搬运圆形视频消息 |
| `transfer.include.document` | `false` | **注意默认关闭**：是否搬运通用文件/压缩包（如 `.zip`、`.pdf`、`.apk` 等，需要搬运文件时请设为 `true`！） |
| `transfer.include.sticker` | `false` | 是否搬运贴纸 |
| `transfer.include.other` | `false` | 是否搬运仅支持原生复制的特殊类型（投票、名片、位置等） |

### `archive.*` — 本地目录与 WebDAV 归档格式
| 参数键名 | 默认值 | 说明 |
| :--- | :--- | :--- |
| `archive.sidecar_format` | `json` | 元数据伴生文件格式：`json`、`md`、`both` 或 `none` |
| `archive.sidecar_always` | `false` | 对没有文字说明的纯媒体文件是否也生成伴生元数据文件 |
| `archive.group_by_month` | `false` | 是否按消息发送月份自动创建 `YYYY-MM` 子目录分拣 |
| `archive.folder_naming` | `title_id` | 源频道文件夹命名风格：`title_id`（名称_ID）、`id` 或 `title` |
| `archive.webdav_mode` | `auto` | WebDAV 上传模式：`auto`（自动）、`spool`（先落盘再上传）或 `stream`（零磁盘流式直传） |

### `limits.*` & `worker.*` — 速率限制与防封号保护
| 参数键名 | 默认值 | 说明 |
| :--- | :--- | :--- |
| `limits.download_delay_seconds` | `5.0` | 两次媒体下载之间的最小间隔秒数 |
| `limits.upload_delay_seconds` | `30.0` | 两次媒体上传之间的最小间隔秒数 |
| `limits.copy_delay_seconds` | `10.0` | 两次原生转发之间的最小间隔秒数 |
| `worker.batch_size` | `25` | 每连续处理多少个任务后进行一次批次冷却休息 |
| `worker.pause_between_batches_seconds` | `600` | 批次之间的冷却休息秒数（默认休息 10 分钟，保护账号免遭 FloodWait 限制） |
| `worker.concurrency` | `1` | 并发 Worker 数量（`1` ~ `8`） |
| `watch.poll_interval_seconds` | `300` | 监听路由的水位线兜底轮询间隔（秒） |
| `downloads.min_free_bytes` | `5368709120` | 启动新下载前要求下载目录至少保留的可用磁盘空间（默认 `5 GiB`） |
