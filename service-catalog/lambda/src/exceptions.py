"""Exception hierarchy for S3 Hydration."""


class S3HydrationError(Exception):
    """Base exception for all S3 Hydration errors."""


class ConfigurationError(S3HydrationError):
    """Raised when required configuration is missing or invalid."""


class TransferError(S3HydrationError):
    """Raised when the overall transfer operation fails."""


class ObjectTransferError(S3HydrationError):
    """Raised when an individual object transfer fails.

    Attributes:
        key: The S3 object key that failed to transfer.
    """

    def __init__(self, key: str, message: str) -> None:
        self.key = key
        super().__init__(f"Failed to transfer '{key}': {message}")
