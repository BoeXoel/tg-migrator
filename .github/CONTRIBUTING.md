# Contributing to TG Migrator

Thank you for your interest in contributing! To keep the codebase clean, secure, and maintainable, please follow these guidelines:

## 1. Branching Workflow

- Base your changes on the **`develop`** branch and open Pull Requests targeting **`develop`**.
- The `main` branch is reserved for tested releases that trigger automated Docker Hub image publishing.

## 2. Dependency & Supply-Chain Policy

All dependencies are strictly pinned with SHA256 hashes for Python 3.12 (`linux/amd64` and `linux/arm64` wheels). If you add or update a dependency in `requirements.in` or `requirements-dev.in`, recompile the lockfiles using `uv`:

```bash
uv pip compile --python-version 3.12 --generate-hashes requirements.in -o requirements.txt
uv pip compile --python-version 3.12 --generate-hashes requirements-dev.in -o requirements-dev.txt
```

## 3. Running Tests

Before opening a Pull Request, ensure all unit tests pass inside the isolated container test stage:

```bash
docker build --target test -t tg-migrator:test . && docker run --rm tg-migrator:test
```

## 4. Security & Privacy

- Never commit `.env`, `.session`, `.db`, or personal chat IDs/tokens.
- Ensure any new logging statements respect credential redaction and do not log raw message contents matched by content filters.
