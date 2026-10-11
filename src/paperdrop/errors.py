"""Error types, so the CLI can report a clear reason without a traceback."""


class PaperDropError(Exception):
    """Base class for expected, user-explicable failures."""


class ConfigError(PaperDropError):
    """Configuration is missing, malformed, or contradicts the user's intent."""


class SourceError(PaperDropError):
    """The configured source is not a usable wallpaper source."""


class FetchError(PaperDropError):
    """A network transfer failed."""

    def __init__(self, message, retryable=False, status=None, retry_after=None):
        super().__init__(message)
        self.retryable = retryable
        self.status = status
        self.retry_after = retry_after


class VerificationError(PaperDropError):
    """Downloaded bytes did not match the expected identity or were not decodable."""


class PlatformError(PaperDropError):
    """The desktop platform could not be detected or driven."""


class BusyError(PaperDropError):
    """Another PaperDrop instance holds the lock."""
