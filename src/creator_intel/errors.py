"""Actionable application errors."""


class CreatorIntelError(RuntimeError):
    """Base error exposed by the CLI."""


class InvalidTikTokUrl(CreatorIntelError):
    """The supplied URL is not a supported public TikTok URL."""


class ProviderError(CreatorIntelError):
    """A profile/video metadata provider failed."""


class MediaToolError(CreatorIntelError):
    """A required media operation failed."""


class MissingCredentialError(CreatorIntelError):
    """A requested stage requires a credential that is not configured."""

