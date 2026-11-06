"""Archive messages to a WebDAV server (HTTPS or internal HTTP)."""

from __future__ import annotations

import logging
import uuid
from pathlib import Path
from typing import Any, AsyncIterator
from urllib.parse import quote, urlsplit, urlunsplit

import httpx

from app.config import BootstrapConfig
from app.engine.storage_guard import StorageGuard
from app.errors import RemotePermissionError, RemoteTransferError, RetryableJobError, TargetConfigError
from app.proxy import http_proxy_for
from app.targets.base import ArchivePathBuilder, DeliveryContext, DeliveryResult, build_sidecars
from app.telegram import media_helper as mh
from app.telegram.client_manager import ClientManager
from app.telegram.limiter import TelegramLimiter

log = logging.getLogger("migrator.target.webdav")

CHUNK_SIZE = 512 * 1024


def validate_webdav_url(url: str) -> str:
    parsed = urlsplit(url.strip())
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise TargetConfigError("WebDAV URL must start with http:// or https:// and include a host")
    if parsed.username or parsed.password:
        raise TargetConfigError("Put WebDAV credentials in username/password, not in the URL")
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path.rstrip("/"), "", ""))


def join_url(base: str, parts: list[str]) -> str:
    parsed = urlsplit(base)
    path = parsed.path.rstrip("/")
    for part in parts:
        path += "/" + quote(part, safe="")
    return urlunsplit((parsed.scheme, parsed.netloc, path or "/", "", ""))


class WebDAVClient:
    def __init__(
        self, config: dict[str, Any], boot: BootstrapConfig, *, transport: httpx.AsyncBaseTransport | None = None
    ) -> None:
        self.base_url = validate_webdav_url(str(config.get("url") or ""))
        self.username = str(config.get("username") or "")
        self.password = str(config.get("password") or "")
        self.verify_tls = bool(config.get("verify_tls", True))
        self.boot = boot
        self._transport = transport
        self._client: httpx.AsyncClient | None = None
        self._known_dirs: set[str] = set()

    async def __aenter__(self) -> "WebDAVClient":
        proxy = await http_proxy_for(self.base_url, self.boot.proxy)
        self._client = httpx.AsyncClient(
            auth=(self.username, self.password) if self.username else None,
            verify=self.verify_tls,
            proxy=proxy if self._transport is None else None,
            transport=self._transport,
            trust_env=False,  # never pick up proxies from the container environment
            timeout=httpx.Timeout(connect=30.0, read=600.0, write=600.0, pool=60.0),
            follow_redirects=False,
            headers={"User-Agent": "tg-migrator"},
        )
        log.debug("WebDAV %s via %s", urlsplit(self.base_url).hostname, "proxy" if proxy else "direct")
        return self

    async def __aexit__(self, *_exc: object) -> None:
        if self._client is not None:
            await self._client.aclose()

    @property
    def client(self) -> httpx.AsyncClient:
        assert self._client is not None, "use 'async with WebDAVClient(...)'"
        return self._client

    @staticmethod
    def _check(response: httpx.Response, action: str) -> None:
        status = response.status_code
        if 200 <= status < 300:
            return
        if status in {401, 403}:
            raise RemotePermissionError(f"WebDAV {action} rejected with HTTP {status} (check credentials/permissions)")
        if status in {507, 423, 429} or status >= 500:
            raise RemoteTransferError(f"WebDAV {action} failed with HTTP {status}")
        raise TargetConfigError(f"WebDAV {action} failed with HTTP {status}")

    async def _request(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        try:
            return await self.client.request(method, url, **kwargs)
        except httpx.TransportError as exc:
            raise RemoteTransferError(f"WebDAV {method} network error: {exc.__class__.__name__}") from exc

    async def ensure_dirs(self, parts: list[str]) -> None:
        for index in range(1, len(parts) + 1):
            url = join_url(self.base_url, parts[:index]) + "/"
            if url in self._known_dirs:
                continue
            response = await self._request("MKCOL", url)
            # 201 created; 405 already exists; some servers answer 301/302 for existing collections.
            if response.status_code not in {200, 201, 204, 301, 302, 405}:
                self._check(response, "MKCOL")
            self._known_dirs.add(url)

    async def put_bytes(self, parts: list[str], content: bytes) -> str:
        url = join_url(self.base_url, parts)
        response = await self._request("PUT", url, content=content, headers={"Content-Type": "application/octet-stream"})
        self._check(response, "PUT")
        return url

    async def put_file(self, parts: list[str], path: Path) -> str:
        size = path.stat().st_size

        async def chunks() -> AsyncIterator[bytes]:
            with path.open("rb") as handle:
                while chunk := handle.read(CHUNK_SIZE):
                    yield chunk

        return await self.put_stream(parts, chunks(), size=size)

    async def put_stream(self, parts: list[str], chunks: AsyncIterator[bytes], *, size: int | None) -> str:
        url = join_url(self.base_url, parts)
        headers = {"Content-Type": "application/octet-stream"}
        if size is not None:
            # An explicit length avoids chunked uploads, which some servers reject.
            headers["Content-Length"] = str(size)
        response = await self._request("PUT", url, content=chunks, headers=headers)
        self._check(response, "PUT")
        return url

    async def delete(self, parts: list[str]) -> None:
        response = await self._request("DELETE", join_url(self.base_url, parts))
        if response.status_code not in {200, 204, 404}:
            self._check(response, "DELETE")

    async def self_test(self) -> str:
        """Create, write and delete a probe file; returns a human summary."""

        probe = [f".tg-migrator-probe-{uuid.uuid4().hex[:8]}.txt"]
        await self.ensure_dirs([])
        await self.put_bytes(probe, b"tg-migrator connectivity probe\n")
        await self.delete(probe)
        proxy = await http_proxy_for(self.base_url, self.boot.proxy)
        return f"OK ({'via proxy' if proxy else 'direct connection'})"


class WebDAVTarget:
    def __init__(self, boot: BootstrapConfig, clients: ClientManager, limiter: TelegramLimiter) -> None:
        self.boot = boot
        self.clients = clients
        self.limiter = limiter
        self.transport: httpx.AsyncBaseTransport | None = None  # injectable for tests

    async def deliver(self, ctx: DeliveryContext) -> DeliveryResult:
        reader = self.clients.require_reader()
        layout = ArchivePathBuilder(ctx.settings.archive)
        prefix = [part for part in str(ctx.target_config.get("base_path") or "").split("/") if part]
        directory = prefix + layout.directory_parts(ctx.job)
        guard = StorageGuard(self.boot.active_dir, self.boot.failed_dir, ctx.settings.downloads)
        mode = ctx.settings.archive.webdav_mode

        file_messages = [m for m in ctx.messages if mh.is_file_kind(mh.media_kind(m))]
        unknown = [m for m in file_messages if mh.file_size(m) is None]
        if unknown and not ctx.settings.transfer.allow_download_unknown_size and mode != "stream":
            return DeliveryResult(
                status="skipped",
                reason=f"Size of message {unknown[0].id} is unknown; allow_download_unknown_size is off",
                reason_code="unknown_size",
            )

        files: dict[int, str] = {}
        async with WebDAVClient(ctx.target_config, self.boot, transport=self.transport) as dav:
            await dav.ensure_dirs(directory)
            for message in file_messages:
                if ctx.stop_event.is_set():
                    raise RetryableJobError("Stop requested; job left for retry")
                name = layout.file_name(message)
                size = mh.file_size(message)
                spool = mode == "spool" or (mode == "auto" and size is not None and guard.can_reserve(size))
                if spool:
                    await self._spool(ctx, reader, dav, guard, message, directory + [name], size or 0)
                else:
                    await ctx.on_phase("uploading")
                    await self._stream(reader, dav, message, directory + [name], size)
                files[int(message.id)] = name

            for name, content in build_sidecars(
                ctx.job, ctx.messages, files, ctx.settings, source_username=ctx.source_username
            ).items():
                await dav.put_bytes(directory + [name], content)

        return DeliveryResult(status="done", transfer_route="webdav", remote_uri=join_url(dav.base_url, directory))

    async def _spool(
        self,
        ctx: DeliveryContext,
        reader: Any,
        dav: WebDAVClient,
        guard: StorageGuard,
        message: Any,
        remote: list[str],
        size: int,
    ) -> None:
        guard.ensure_reservation(size)
        job_dir = guard.job_dir(ctx.job.id, "-webdav")
        success = False
        try:
            await ctx.on_phase("downloading")

            def progress(current: int, _total: int) -> None:
                guard.check_progress(size - current)

            result = await self.limiter.call(
                "download", reader.download_media, message, file=str(job_dir / remote[-1]), progress_callback=progress
            )
            if not result or not Path(result).exists():
                raise RetryableJobError("Telegram download produced no file", reason_code="network_error")
            await ctx.on_phase("uploading")
            await dav.put_file(remote, Path(result))
            success = True
        finally:
            # Remote failures never use failed/: big files must not pile up locally.
            guard.cleanup_job_dir(job_dir, success=success, force_delete=True)

    async def _stream(self, reader: Any, dav: WebDAVClient, message: Any, remote: list[str], size: int | None) -> None:
        """Telegram -> WebDAV without touching the local disk."""

        await self.limiter.wait("download")
        if mh.media_kind(message) == "photo":
            content = await reader.download_media(message, file=bytes)
            await dav.put_bytes(remote, content)
            return

        document = mh.media_object(message)

        async def chunks() -> AsyncIterator[bytes]:
            async for chunk in reader.iter_download(document, request_size=CHUNK_SIZE):
                yield bytes(chunk)

        await dav.put_stream(remote, chunks(), size=size)
