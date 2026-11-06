"""SQLAlchemy 2.0 ORM models (portable across SQLite, PostgreSQL and MySQL)."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

JOB_STATUSES = ("pending", "downloading", "uploading", "done", "failed", "skipped")
TERMINAL_STATUSES = ("done", "failed", "skipped")
TARGET_TYPES = ("telegram", "local", "webdav")


def utcnow() -> datetime:
    """Naive UTC timestamp; identical semantics on every database backend."""

    return datetime.now(timezone.utc).replace(tzinfo=None)


class Base(DeclarativeBase):
    type_annotation_map = {dict[str, Any]: JSON, list[Any]: JSON}


class Setting(Base):
    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[Any] = mapped_column(JSON, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class Target(Base):
    __tablename__ = "targets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(64), unique=True)
    type: Mapped[str] = mapped_column(String(16))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    # telegram: {chat_id, topic_id, title}; local: {path}; webdav: {url, username, password, verify_tls}
    config: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    # For telegram targets: where oversized media goes instead (local/webdav target id).
    fallback_target_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("targets.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class Route(Base):
    __tablename__ = "routes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(64), unique=True)
    source_chat_id: Mapped[str] = mapped_column(String(32))
    source_title: Mapped[str] = mapped_column(String(255), default="")
    source_topic_id: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    target_ids: Mapped[list[Any]] = mapped_column(JSON, default=list)
    watch_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    last_seen_msg_id: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    # Dotted RuntimeSettings paths overriding the global value for this route,
    # e.g. {"transfer.include.document": true}
    overrides: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class FilterRule(Base):
    __tablename__ = "filter_rules"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    rule_type: Mapped[str] = mapped_column(String(16))  # keyword | regex
    pattern: Mapped[str] = mapped_column(Text)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Task(Base):
    __tablename__ = "tasks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    kind: Mapped[str] = mapped_column(String(16))  # adhoc | route_scan | catchup
    route_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("routes.id", ondelete="SET NULL"), nullable=True
    )
    source_chat_id: Mapped[str] = mapped_column(String(32))
    source_title: Mapped[str] = mapped_column(String(255), default="")
    source_topic_id: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    start_id: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    end_id: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    target_ids: Mapped[list[Any]] = mapped_column(JSON, default=list)
    status: Mapped[str] = mapped_column(String(16), default="scanning")  # scanning|queued|done|cancelled|error
    created_by: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    notify_chat_id: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    scanned_groups: Mapped[int] = mapped_column(Integer, default=0)
    enqueued: Mapped[int] = mapped_column(Integer, default=0)
    duplicates: Mapped[int] = mapped_column(Integer, default=0)
    skipped_at_scan: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)


class Job(Base):
    __tablename__ = "jobs"
    __table_args__ = (
        UniqueConstraint("source_chat_id", "target_id", "dedupe_key", name="uq_jobs_dedupe"),
        Index("ix_jobs_due", "status", "next_retry_at"),
        Index("ix_jobs_task", "task_id", "status"),
        Index("ix_jobs_reason", "status", "reason_code"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    task_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("tasks.id", ondelete="SET NULL"), nullable=True
    )
    route_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("routes.id", ondelete="SET NULL"), nullable=True
    )
    target_id: Mapped[int] = mapped_column(Integer, ForeignKey("targets.id", ondelete="CASCADE"))
    source_chat_id: Mapped[str] = mapped_column(String(32))
    source_title: Mapped[str] = mapped_column(String(255), default="")
    source_topic_id: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    source_message_id: Mapped[int] = mapped_column(BigInteger)
    source_message_ids: Mapped[list[Any]] = mapped_column(JSON, default=list)
    grouped_id: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    dedupe_key: Mapped[str] = mapped_column(String(64))
    media_type: Mapped[str] = mapped_column(String(32), default="unsupported")
    file_size: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    caption: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    message_date: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    media_manifest: Mapped[list[Any]] = mapped_column(JSON, default=list)
    status: Mapped[str] = mapped_column(String(16), default="pending")
    reason_code: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    transfer_route: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    dest_message_ids: Mapped[Optional[list[Any]]] = mapped_column(JSON, nullable=True)
    remote_uri: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    writer_identity: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    upload_limit_bytes: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    next_retry_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    verified_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class RemoteObject(Base):
    """Oversized media already archived to a fallback target (reused across targets)."""

    __tablename__ = "remote_objects"

    source_chat_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    target_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    dedupe_key: Mapped[str] = mapped_column(String(64), primary_key=True)
    remote_uri: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
