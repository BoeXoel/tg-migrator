"""Application wiring and service lifecycle."""

from __future__ import annotations

import asyncio
import logging
import signal

from app import __version__
from app.bot.context import AppContext
from app.bot.router import register_all
from app.config import BootstrapConfig
from app.db.engine import create_engine, describe_database, init_models, make_sessionmaker, normalise_database_url
from app.db.repository import Repository
from app.engine.notifier import Notifier
from app.engine.scanner import Scanner
from app.engine.verifier import Verifier
from app.engine.watcher import Watcher
from app.engine.worker import Worker
from app.logging import setup_logging
from app.security import SecretBox, load_or_create_key
from app.settings_store import SettingsStore
from app.targets.dispatcher import TargetDispatcher
from app.telegram.client_manager import ClientManager
from app.telegram.limiter import TelegramLimiter

log = logging.getLogger("migrator")


async def build_context(boot: BootstrapConfig) -> AppContext:
    boot.ensure_directories()
    boot.archive_root.mkdir(parents=True, exist_ok=True)
    secrets = SecretBox(load_or_create_key(boot.secret_key, boot.data_dir))
    database_url = normalise_database_url(boot.database_url)
    engine = create_engine(database_url)
    await init_models(engine)
    repo = Repository(engine, make_sessionmaker(engine), secrets)
    store = SettingsStore(repo)
    await store.load()

    limiter = TelegramLimiter(store)
    clients = ClientManager(boot, limiter)
    notifier = Notifier(boot.notify_chat_ids)
    dispatcher = TargetDispatcher(boot, clients, limiter, repo)
    worker = Worker(boot, clients, limiter, repo, store, dispatcher, notifier)
    scanner = Scanner(clients, limiter, repo, store, wake_worker=worker.wake)
    watcher = Watcher(clients, repo, store, scanner, notifier)
    verifier = Verifier(clients, limiter, repo)
    return AppContext(
        boot=boot,
        repo=repo,
        store=store,
        limiter=limiter,
        clients=clients,
        notifier=notifier,
        scanner=scanner,
        watcher=watcher,
        worker=worker,
        verifier=verifier,
        database_label=describe_database(database_url),
    )


async def run_service(boot: BootstrapConfig) -> None:
    setup_logging(boot.log_level, boot.log_file)
    log.info("TG Migrator %s starting", __version__)
    log.info("Proxy: %s", boot.proxy.describe())
    ctx = await build_context(boot)
    log.info("Database: %s", ctx.database_label)

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop.set)
        except (NotImplementedError, RuntimeError):
            pass

    async def on_reader_ready() -> None:
        await ctx.watcher.start()
        ctx.worker.wake()

    try:
        bot = await ctx.clients.start_bot()
        ctx.notifier.bot = bot
        register_all(bot, ctx)
        ctx.clients.on_reader_ready(on_reader_ready)
        await ctx.worker.load_state()
        reader_ok = await ctx.clients.start_reader()
        ctx.worker.start()

        status = "读取账号已登录" if reader_ok else "读取账号未登录，请发送 /login"
        paused = "，队列处于暂停状态（/resume 恢复）" if ctx.worker.paused else ""
        await ctx.notifier.notify(f"🟢 TG Migrator {__version__} 已启动：{status}{paused}")

        disconnected = asyncio.ensure_future(bot.disconnected)
        stopper = asyncio.ensure_future(stop.wait())
        await asyncio.wait({disconnected, stopper}, return_when=asyncio.FIRST_COMPLETED)
        if disconnected.done() and not stop.is_set():
            log.error("Bot connection closed permanently; exiting so the container restarts")
    finally:
        log.info("Shutting down")
        for task in list(ctx.state.scan_tasks.values()):
            task.cancel()
        await ctx.worker.stop()
        await ctx.watcher.stop()
        await ctx.clients.stop()
        await ctx.repo.engine.dispose()
