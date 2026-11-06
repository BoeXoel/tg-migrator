"""Telethon error groups, resolved by name so version differences never crash."""

from __future__ import annotations

from telethon import errors


def _get(*names: str) -> tuple[type[BaseException], ...]:
    return tuple(getattr(errors, name) for name in names if isinstance(getattr(errors, name, None), type))


PERMISSION_ERRORS = _get(
    "ChannelPrivateError",
    "ChatWriteForbiddenError",
    "ChatAdminRequiredError",
    "UserBannedInChannelError",
    "ChatRestrictedError",
    "ChatSendMediaForbiddenError",
    "ChatSendPhotosForbiddenError",
    "ChatSendVideosForbiddenError",
    "ChatSendDocsForbiddenError",
    "ChatSendAudiosForbiddenError",
    "ChatSendVoicesForbiddenError",
    "ChatSendRoundvideosForbiddenError",
    "ChatSendStickersForbiddenError",
    "ChatSendGifsForbiddenError",
    "ChatSendPlainForbiddenError",
    "ChatGuestSendForbiddenError",
    "ChannelInvalidError",
)
SOURCE_MISSING_ERRORS = _get("MessageIdInvalidError", "MediaEmptyError", "MessageIdsEmptyError")
FORWARD_RESTRICTED_ERRORS = _get("ChatForwardsRestrictedError")
ACCOUNT_RESTRICTED_ERRORS = _get("PeerFloodError", "UserRestrictedError")
AUTH_LOST_ERRORS = _get(
    "AuthKeyUnregisteredError",
    "SessionRevokedError",
    "SessionExpiredError",
    "AuthKeyDuplicatedError",
    "UserDeactivatedError",
    "UserDeactivatedBanError",
)
PEER_INVALID_ERRORS = _get("PeerIdInvalidError")
CAPTION_TOO_LONG_ERRORS = _get("MediaCaptionTooLongError")
RETRYABLE_TELEGRAM_ERRORS = _get("FileReferenceExpiredError", "ServerError", "TimedOutError", "RpcCallFailError")
BAD_REQUEST_ERRORS = _get("BadRequestError")
