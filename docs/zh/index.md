---
layout: home

hero:
  name: "TG Migrator"
  text: "由 Bot 全程交互控制的 Telegram 搬运与归档服务"
  tagline: "将拥有访问权限的群组、频道与子话题内容，一键迁移或持续同步至 Telegram 群组/子 Topic、本地 NAS 目录与 WebDAV 存储服务。"
  image:
    src: /logo.png
    alt: TG Migrator
  actions:
    - theme: brand
      text: 5 分钟快速上手 →
      link: /zh/guide/getting-started
    - theme: alt
      text: 账号登录与子 Topic 绑定
      link: /zh/guide/targets-and-accounts
    - theme: alt
      text: GitHub 仓库
      link: https://github.com/BoeXoel/tg-migrator

features:
  - icon: 🤖
    title: 免 SSH 全流程 Bot 面板控制
    details: 内置防拦截内联数字键盘在线登录个人读取号（/login），群内发送 /bind 即可绑定目的地（支持精确绑定子 Topic），中英双语随时切换。
  - icon: 🗂️
    title: 三大归档目的地自由组合
    details: 支持投递至 Telegram 群组/频道（保留相册与子话题落点）、本地 NAS 挂载目录（支持生成 JSON/Markdown 元数据伴生文件）以及 WebDAV（OpenList / Nextcloud，支持流式零落盘直传）。
  - icon: 🛡️
    title: 双模传输引擎与媒体元数据保真
    details: 智能结合「零流量服务端原生复制」与「本地流式重传」，完整保留视频宽高时长、安全缩略图、音频波形与原始文件名，并支持自动剥离剧透遮罩。
  - icon: 📡
    title: 实时监听 + 断点轮询双保险
    details: 结合 Telethon 实时事件推送与 last_seen_msg_id 水位线周期轮询，配合关键词/正则过滤规则，容器重启也能自动补齐期间遗漏的新消息。
---
