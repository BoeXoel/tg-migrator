"""Archive messages into a mounted local directory."""

from __future__ import annotations

import errno
import logging
import os
from pathlib import Path
from typing import Any

from app.config import BootstrapConfig
from app.engine.storage_guard import StorageGuard
from app.errors import DiskFullError, RetryableJobError, TargetConfigError
from app.targets.base import ArchivePathBuilder, DeliveryContext, DeliveryResult, build_sidecars
from app.telegram import media_helper as mh
from app.telegram.client_manager import ClientManager
from app.telegram.limiter import TelegramLimiter

log = logging.getLogger("migrator.target.local")


def resolve_local_root(archive_root: Path, subpath: str | None) -> Path:
    """Resolve a target directory and refuse anything outside ARCHIVE_ROOT."""

    root = archive_root.resolve()
    candidate = (root / (subpath or "").strip().lstrip("/")).resolve() if subpath else root
    if candidate != root and root not in candidate.parents:
        raise TargetConfigError(f"Local target path must stay inside ARCHIVE_ROOT ({root})")
    return candidate


def _write_atomic(path: Path, content: bytes) -> None:
    tmp = path.with_name(path.name + ".part")
    tmp.write_bytes(content)
    os.replace(tmp, path)


class LocalTarget:
    def __init__(self, boot: BootstrapConfig, clients: ClientManager, limiter: TelegramLimiter) -> None:
        self.boot = boot
        self.clients = clients
        self.limiter = limiter

    async def deliver(self, ctx: DeliveryContext) -> DeliveryResult:
        reader = self.clients.require_reader()
        root = resolve_local_root(self.boot.archive_root, ctx.target_config.get("path"))
        layout = ArchivePathBuilder(ctx.settings.archive)
        directory = root.joinpath(*layout.directory_parts(ctx.job))
        guard = StorageGuard(self.boot.active_dir, self.boot.failed_dir, ctx.settings.downloads)

        file_messages = [m for m in ctx.messages if mh.is_file_kind(mh.media_kind(m))]
        unknown = [m for m in file_messages if mh.file_size(m) is None]
        if unknown and not ctx.settings.transfer.allow_download_unknown_size:
            return DeliveryResult(
                status="skipped",
                reason=f"Size of message {unknown[0].id} is unknown; allow_download_unknown_size is off",
                reason_code="unknown_size",
            )

        directory.mkdir(parents=True, exist_ok=True)
        files: dict[int, str] = {}
        await ctx.on_phase("downloading")
        try:
            for message in file_messages:
                if ctx.stop_event.is_set():
                    raise RetryableJobError("Stop requested; job left for retry")
                name = layout.file_name(message)
                destination = directory / name
                size = mh.file_size(message)
                if destination.exists() and size and destination.stat().st_size == size:
                    files[int(message.id)] = name
                    continue
                guard.ensure_reservation(size or ctx.settings.downloads.max_job_bytes or 0, path=directory)
                await self._download(reader, message, destination, guard, size or 0)
                files[int(message.id)] = name

            for name, content in build_sidecars(
                ctx.job, ctx.messages, files, ctx.settings, source_username=ctx.source_username
            ).items():
                _write_atomic(directory / name, content)
        except OSError as exc:
            if exc.errno in {errno.ENOSPC, getattr(errno, "EDQUOT", errno.ENOSPC)}:
                raise DiskFullError("The archive filesystem is out of space") from exc
            raise

        relative = directory.relative_to(self.boot.archive_root.resolve())
        return DeliveryResult(status="done", transfer_route="local", remote_uri=f"local:/{relative.as_posix()}")

    async def _download(self, reader: Any, message: Any, destination: Path, guard: StorageGuard, size: int) -> None:
        part = destination.with_name(destination.name + ".part")
        part.unlink(missing_ok=True)

        def progress(current: int, _total: int) -> None:
            guard.check_progress(size - current, path=destination.parent)

        try:
            result = await self.limiter.call(
                "download", reader.download_media, message, file=str(part), progress_callback=progress
            )
            if not result or not Path(result).exists():
                raise RetryableJobError("Telegram download produced no file", reason_code="network_error")
            os.replace(result, destination)
        finally:
            part.unlink(missing_ok=True)
