"""Job failure classification.

* ``PermanentJobError``  -> job is marked ``skipped`` (needs config/user action)
* ``RetryableJobError``  -> consumes an attempt, exponential backoff, then ``failed``
* ``DeferredJobError``   -> retried later *without* consuming an attempt
* ``PauseWorkerError``   -> deferred and the whole worker pauses (account risk,
  unresolved peers) until an admin resumes it
"""

from __future__ import annotations


class JobError(Exception):
    def __init__(self, message: str = "", *, reason_code: str | None = None) -> None:
        super().__init__(message)
        self.reason_code = reason_code


class PermanentJobError(JobError):
    """The job cannot succeed without a configuration or permission change."""


class RetryableJobError(JobError):
    """The job may succeed on a later attempt."""


class DeferredJobError(RetryableJobError):
    """Retry later without consuming a transfer attempt."""

    def __init__(self, message: str = "", *, reason_code: str | None = None, retry_after_seconds: int = 300) -> None:
        super().__init__(message, reason_code=reason_code)
        self.retry_after_seconds = max(1, int(retry_after_seconds))


class PauseWorkerError(DeferredJobError):
    """Defer the job and pause the worker until an admin resumes it."""


class FloodWaitDeferred(DeferredJobError):
    def __init__(self, retry_after_seconds: int) -> None:
        super().__init__(
            f"Telegram requested a {retry_after_seconds}-second FloodWait",
            reason_code="flood_wait",
            retry_after_seconds=retry_after_seconds,
        )


class AccountRestrictedError(PauseWorkerError):
    def __init__(self, message: str, *, retry_after_seconds: int) -> None:
        super().__init__(message, reason_code="account_restricted", retry_after_seconds=retry_after_seconds)


class PeerUnresolvedError(PauseWorkerError):
    def __init__(self, peer: str) -> None:
        super().__init__(
            f"The writer session does not know {peer}; send /bind in that chat so the Bot can see it",
            reason_code="peer_unresolved",
            retry_after_seconds=60,
        )


class ReaderUnavailableError(PauseWorkerError):
    def __init__(self) -> None:
        super().__init__(
            "The reading user account is not logged in; use /login in the Bot",
            reason_code="reader_unavailable",
            retry_after_seconds=60,
        )


class DiskLowError(DeferredJobError):
    def __init__(self, message: str) -> None:
        super().__init__(message, reason_code="disk_low", retry_after_seconds=600)


class DiskFullError(DeferredJobError):
    def __init__(self, message: str) -> None:
        super().__init__(message, reason_code="disk_full", retry_after_seconds=600)


class JobSizeLimitError(PermanentJobError):
    def __init__(self, message: str) -> None:
        super().__init__(message, reason_code="disk_job_limit")


class TargetConfigError(PermanentJobError):
    def __init__(self, message: str) -> None:
        super().__init__(message, reason_code="target_config")


class RemotePermissionError(PermanentJobError):
    def __init__(self, message: str) -> None:
        super().__init__(message, reason_code="remote_permission_denied")


class RemoteTransferError(RetryableJobError):
    def __init__(self, message: str) -> None:
        super().__init__(message, reason_code="remote_error")


class TopicDeliveryError(PermanentJobError):
    def __init__(self, message: str) -> None:
        super().__init__(message, reason_code="topic_error")


def compact_error(exc: BaseException, limit: int = 2000) -> str:
    message = f"{exc.__class__.__name__}: {exc}"
    return " ".join(message.split())[:limit]
