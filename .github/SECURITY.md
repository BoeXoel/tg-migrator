# Security Policy

## Supported Versions

Only the latest release on the `main` branch and the corresponding `:latest` / latest versioned Docker Hub image are actively supported with security updates.

| Version | Supported |
| ------- | --------- |
| `0.0.x` | :white_check_mark: |

## Reporting a Vulnerability

**Please do NOT report security vulnerabilities through public GitHub issues, discussions, or pull requests.**

Instead, please report vulnerabilities privately using **[GitHub Private Vulnerability Reporting (Security Advisories)](https://github.com/BoeXoel/tg-migrator/security/advisories/new)**.

When submitting a report, please include:
- A clear description of the vulnerability and its potential impact;
- Step-by-step instructions or a minimal proof-of-concept to reproduce the issue;
- The version or commit SHA tested.

## Operational Security Reminders for Users

- **Never share `.session` files or `.env` contents** when opening issues or asking for help. Telegram `.session` files grant full access to the logged-in account.
- **Keep `ADMIN_IDS` strictly limited** to your own numeric Telegram User IDs.
- **Use `/export` (redacted)** instead of `/export secrets` if you ever need to share configuration snippets for debugging.
