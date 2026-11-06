"""CSV report of oversized media."""

from __future__ import annotations

import csv
import io
from urllib.parse import urlsplit, urlunsplit

from app.db.repository import Repository
from app.telegram.media_helper import source_link

COLUMNS = (
    "job_id",
    "source_link",
    "source_chat_id",
    "source_title",
    "source_topic_id",
    "message_id",
    "size_bytes",
    "media_type",
    "file_name",
    "caption_summary",
    "target_id",
    "writer_identity",
    "upload_limit_bytes",
    "status",
    "reason_code",
    "transfer_route",
    "remote_uri",
)


def _redact(uri: str | None) -> str:
    if not uri:
        return ""
    parsed = urlsplit(uri)
    if not parsed.hostname:
        return uri
    host = parsed.hostname + (f":{parsed.port}" if parsed.port else "")
    return urlunsplit((parsed.scheme, host, parsed.path, "", ""))


def _summary(text: str | None, limit: int = 160) -> str:
    value = " ".join((text or "").split())
    return value if len(value) <= limit else value[: limit - 1] + "…"


async def oversized_csv(repo: Repository) -> tuple[int, bytes]:
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=COLUMNS)
    writer.writeheader()
    rows = 0
    for job in await repo.oversized_jobs():
        items = job.media_manifest or [{"message_id": job.source_message_id, "size": job.file_size, "type": job.media_type}]
        for item in items:
            writer.writerow(
                {
                    "job_id": job.id,
                    "source_link": source_link(job.source_chat_id, int(item.get("message_id") or job.source_message_id), job.source_topic_id),
                    "source_chat_id": job.source_chat_id,
                    "source_title": job.source_title,
                    "source_topic_id": job.source_topic_id or "",
                    "message_id": item.get("message_id"),
                    "size_bytes": item.get("size") or "",
                    "media_type": item.get("type") or job.media_type,
                    "file_name": item.get("name") or "",
                    "caption_summary": _summary(job.caption),
                    "target_id": job.target_id,
                    "writer_identity": job.writer_identity or "",
                    "upload_limit_bytes": job.upload_limit_bytes or "",
                    "status": job.status,
                    "reason_code": job.reason_code or "",
                    "transfer_route": job.transfer_route or "",
                    "remote_uri": _redact(job.remote_uri),
                }
            )
            rows += 1
    return rows, buffer.getvalue().encode("utf-8-sig")
