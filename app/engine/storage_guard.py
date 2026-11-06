"""Disk-space protection for temporary downloads and local archives."""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path

from app.config import DownloadSettings
from app.errors import DiskFullError, DiskLowError, JobSizeLimitError


def format_bytes(value: int | None) -> str:
    if value is None:
        return "unknown"
    amount = float(max(value, 0))
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if amount < 1024 or unit == "TiB":
            return f"{amount:.1f} {unit}"
        amount /= 1024
    return f"{amount:.1f} TiB"


def free_bytes(path: Path) -> int:
    probe = path
    while not probe.exists() and probe != probe.parent:
        probe = probe.parent
    return int(shutil.disk_usage(probe).free)


@dataclass
class StorageGuard:
    active_dir: Path
    failed_dir: Path
    settings: DownloadSettings

    def ensure_reservation(self, required_bytes: int, *, path: Path | None = None) -> None:
        """Refuse to start a download that would eat into the reserve."""

        if required_bytes < 0:
            raise ValueError("required_bytes cannot be negative")
        if self.settings.max_job_bytes and required_bytes > self.settings.max_job_bytes:
            raise JobSizeLimitError(
                f"Job needs {format_bytes(required_bytes)}, above downloads.max_job_bytes "
                f"({format_bytes(self.settings.max_job_bytes)})"
            )
        free = free_bytes(path or self.active_dir)
        if free - required_bytes < self.settings.min_free_bytes:
            raise DiskLowError(
                f"Only {format_bytes(free)} free; {format_bytes(required_bytes)} more would leave less than "
                f"the {format_bytes(self.settings.min_free_bytes)} reserve"
            )

    def can_reserve(self, required_bytes: int, *, path: Path | None = None) -> bool:
        try:
            self.ensure_reservation(required_bytes, path=path)
            return True
        except (DiskLowError, JobSizeLimitError):
            return False

    def check_progress(self, remaining_bytes: int, *, path: Path | None = None) -> None:
        free = free_bytes(path or self.active_dir)
        if free - max(remaining_bytes, 0) < self.settings.min_free_bytes:
            raise DiskFullError(
                f"Free space fell below the reserve while downloading (free={format_bytes(free)}, "
                f"remaining={format_bytes(max(remaining_bytes, 0))})"
            )

    def job_dir(self, job_id: int, suffix: str = "") -> Path:
        path = self.active_dir / f"job-{job_id}{suffix}"
        path.mkdir(parents=True, exist_ok=True)
        return path

    def cleanup_job_dir(self, path: Path, *, success: bool, force_delete: bool = False) -> None:
        if not path.exists():
            return
        if not success and not force_delete and self.settings.keep_failed and self.settings.max_failed_bytes > 0:
            destination = self.failed_dir / path.name
            if destination.exists():
                shutil.rmtree(destination, ignore_errors=True)
            self.failed_dir.mkdir(parents=True, exist_ok=True)
            shutil.move(str(path), str(destination))
            self.prune_failed()
            return
        shutil.rmtree(path, ignore_errors=True)

    def cleanup_active(self) -> int:
        return self._remove_jobs(self.active_dir)

    def prune_failed(self) -> int:
        if not self.settings.keep_failed or self.settings.max_failed_bytes == 0:
            return self._remove_jobs(self.failed_dir)
        jobs = sorted(self._job_dirs(self.failed_dir), key=lambda item: item.stat().st_mtime)
        total = sum(_directory_size(item) for item in jobs)
        removed = 0
        for item in jobs:
            if total <= self.settings.max_failed_bytes:
                break
            total -= _directory_size(item)
            shutil.rmtree(item, ignore_errors=True)
            removed += 1
        return removed

    @staticmethod
    def _job_dirs(root: Path) -> list[Path]:
        if not root.exists():
            return []
        return [item for item in root.iterdir() if item.is_dir() and item.name.startswith("job-")]

    def _remove_jobs(self, root: Path) -> int:
        removed = 0
        for item in self._job_dirs(root):
            shutil.rmtree(item, ignore_errors=True)
            removed += 1
        return removed


def _directory_size(path: Path) -> int:
    total = 0
    for child in path.rglob("*"):
        try:
            if child.is_file():
                total += child.stat().st_size
        except OSError:
            continue
    return total
