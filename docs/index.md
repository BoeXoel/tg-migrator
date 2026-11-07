---
layout: home

hero:
  name: "TG Migrator"
  text: "Bot-Controlled Telegram Migration & Archiving"
  tagline: "Copy, stream, and continuously watch restricted or public Telegram chats to Telegram Topics, Local NAS, and WebDAV — 100% controlled via an interactive Telegram Bot."
  actions:
    - theme: brand
      text: Get Started in 5 Minutes →
      link: /guide/getting-started
    - theme: alt
      text: Bind Topics & Targets
      link: /guide/targets-and-accounts
    - theme: alt
      text: GitHub
      link: https://github.com/BoeXoel/tg-migrator

features:
  - icon: 🤖
    title: Zero-SSH Bot Control Center
    details: Log in your reading account via an inline keypad (/login), bind groups or Forum sub-topics (/bind), launch batch migrations, and tune rate limits directly inside Telegram.
  - icon: 🗂️
    title: Multi-Sink Destinations
    details: Deliver to Telegram Groups/Channels (with Forum Topic targeting & Album grouping), Local NAS directories (with JSON/Markdown sidecars), or WebDAV servers (AList, OpenList, Nextcloud).
  - icon: 🛡️
    title: Restricted Content & Spoiler Handling
    details: Automatically falls back from native server-side copy to local stream re-upload when forwarding is restricted (noforwards), preserving video dimensions, durations, thumbnails, and stripping spoilers.
  - icon: 📡
    title: Continuous Hybrid Watch
    details: Real-time MTProto event listener combined with periodic watermark polling (last_seen_msg_id) and keyword/regex filtering ensures zero missed messages across restarts.
---
