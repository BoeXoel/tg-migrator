import { defineConfig } from 'vitepress'

const currentYear = new Date().getFullYear()

export default defineConfig({
  title: 'TG Migrator',
  base: '/',
  cleanUrls: true,
  lastUpdated: true,
  srcExclude: ['README.zh-CN.md'],

  head: [
    ['link', { rel: 'icon', type: 'image/svg+xml', href: '/favicon.svg' }],
    ['link', { rel: 'icon', type: 'image/png', href: '/logo.png' }],
    ['link', { rel: 'apple-touch-icon', href: '/logo.png' }],
    ['meta', { name: 'theme-color', content: '#2481cc' }],
    [
      'script',
      {},
      `(() => {
        const updateYear = () => {
          const year = String(new Date().getFullYear());
          document.querySelectorAll('.VPFooter .copyright').forEach((el) => {
            if (el.textContent) {
              el.textContent = el.textContent.replace(/©\\s*\\d{4}/, '© ' + year);
            }
          });
        };
        if (typeof window !== 'undefined') {
          window.addEventListener('DOMContentLoaded', () => {
            updateYear();
            new MutationObserver(updateYear).observe(document.body, { childList: true, subtree: true });
          });
        }
      })();`,
    ],
  ],

  themeConfig: {
    logo: '/logo.png',
    search: {
      provider: 'local',
      options: {
        locales: {
          zh: {
            translations: {
              button: {
                buttonText: '搜索文档',
                buttonAriaLabel: '搜索文档',
              },
              modal: {
                noResultsText: '无法找到相关结果',
                resetButtonTitle: '清除查询条件',
                footer: {
                  selectText: '选择',
                  navigateText: '切换',
                  closeText: '关闭',
                },
              },
            },
          },
        },
      },
    },
    socialLinks: [
      { icon: 'github', link: 'https://github.com/BoeXoel/tg-migrator' },
      { icon: 'docker', link: 'https://hub.docker.com/r/boexoel/tg-migrator' },
    ],
  },

  locales: {
    root: {
      label: 'English',
      lang: 'en-US',
      description: 'Dockerized Telegram Bot-controlled message migration & archiving service for Telegram, Local NAS, and WebDAV.',
      themeConfig: {
        nav: [
          { text: 'Getting Started', link: '/guide/getting-started' },
          { text: 'User Guide', link: '/guide/targets-and-accounts' },
          { text: 'Reference', link: '/reference/configuration' },
          { text: 'Troubleshooting', link: '/reference/troubleshooting' },
        ],
        sidebar: [
          {
            text: 'Guide',
            items: [
              { text: 'Getting Started & Deployment', link: '/guide/getting-started' },
              { text: 'Accounts & Destinations (Topics, NAS, WebDAV)', link: '/guide/targets-and-accounts' },
              { text: 'Migration Tasks & Watch Routes', link: '/guide/tasks-and-routes' },
            ],
          },
          {
            text: 'Reference & FAQ',
            items: [
              { text: 'Commands & Settings Reference', link: '/reference/configuration' },
              { text: 'Troubleshooting & FAQ', link: '/reference/troubleshooting' },
            ],
          },
        ],
        outline: {
          label: 'On this page',
          level: [2, 3],
        },
        footer: {
          message: 'Released under the AGPL-3.0 License.',
          copyright: `Copyright © ${currentYear} BoeXoel`,
        },
      },
    },

    zh: {
      label: '简体中文',
      lang: 'zh-CN',
      link: '/zh/',
      description: '由 Telegram Bot 交互控制的 Docker 化消息搬运与归档服务（支持 Telegram 群组/子话题、本地 NAS 与 WebDAV）。',
      themeConfig: {
        nav: [
          { text: '快速上手', link: '/zh/guide/getting-started' },
          { text: '操作手册', link: '/zh/guide/targets-and-accounts' },
          { text: '配置速查', link: '/zh/reference/configuration' },
          { text: '常见排错', link: '/zh/reference/troubleshooting' },
        ],
        sidebar: [
          {
            text: '核心指南',
            items: [
              { text: '快速部署与升级指南', link: '/zh/guide/getting-started' },
              { text: '账号登录与目的地绑定（含子 Topic 详解）', link: '/zh/guide/targets-and-accounts' },
              { text: '搬运任务与持续监听实战', link: '/zh/guide/tasks-and-routes' },
            ],
          },
          {
            text: '速查与排坑',
            items: [
              { text: '命令与参数速查字典', link: '/zh/reference/configuration' },
              { text: '常见报错与排坑 FAQ', link: '/zh/reference/troubleshooting' },
            ],
          },
        ],
        outline: {
          label: '本页目录',
          level: [2, 3],
        },
        docFooter: {
          prev: '上一页',
          next: '下一页',
        },
        lastUpdated: {
          text: '最后更新于',
        },
        footer: {
          message: '基于 AGPL-3.0 协议开源发布',
          copyright: `Copyright © ${currentYear} BoeXoel`,
        },
      },
    },
  },
})
