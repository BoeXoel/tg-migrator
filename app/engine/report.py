"""CSV report of failed, blocked-skipped, and oversized media jobs."""

from __future__ import annotations

import csv
import io
from typing import Iterable
from urllib.parse import urlsplit, urlunsplit

from app.db.models import Job
from app.db.repository import Repository
from app.telegram.media_helper import source_link

COLUMNS = (
    "job_id",
    "task_id",
    "route_id",
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
    "attempts",
    "last_error",
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


def _encode_jobs_csv(jobs: Iterable[Job]) -> tuple[int, bytes]:
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=COLUMNS)
    writer.writeheader()
    rows = 0
    for job in jobs:
        items = job.media_manifest or [{"message_id": job.source_message_id, "size": job.file_size, "type": job.media_type}]
        for item in items:
            writer.writerow(
                {
                    "job_id": job.id,
                    "task_id": job.task_id or "",
                    "route_id": job.route_id or "",
                    "source_link": source_link(
                        job.source_chat_id, int(item.get("message_id") or job.source_message_id), job.source_topic_id
                    ),
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
                    "attempts": job.attempts or 0,
                    "last_error": _summary(job.last_error, limit=500),
                    "transfer_route": job.transfer_route or "",
                    "remote_uri": _redact(job.remote_uri),
                }
            )
            rows += 1
    return rows, buffer.getvalue().encode("utf-8-sig")


async def problem_report_csv(repo: Repository, *, task_id: int | None = None) -> tuple[int, bytes]:
    return _encode_jobs_csv(await repo.report_jobs(task_id=task_id))


async def oversized_csv(repo: Repository) -> tuple[int, bytes]:
    return _encode_jobs_csv(await repo.oversized_jobs())
