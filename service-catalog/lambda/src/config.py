"""Environment-based configuration for S3 Hydration Lambda."""

from __future__ import annotations

import os
from dataclasses import dataclass

from .exceptions import ConfigurationError


@dataclass(frozen=True)
class Config:
    """Immutable configuration loaded from environment variables."""

    producer_bucket: str
    consumer_bucket: str
    cross_account_role_arn: str
    external_id: str
    consumer_kms_key_id: str
    producer_kms_key_arn: str
    transfer_prefix: str
    marker_key: str

    @classmethod
    def from_env(cls) -> Config:
        """Load configuration from environment variables.

        Raises:
            ConfigurationError: If any required variable is missing.
        """

        def _require(name: str) -> str:
            value = os.environ.get(name)
            if not value:
                raise ConfigurationError(f"Missing required environment variable: {name}")
            return value

        return cls(
            producer_bucket=_require("PRODUCER_BUCKET"),
            consumer_bucket=_require("CONSUMER_BUCKET"),
            cross_account_role_arn=_require("CROSS_ACCOUNT_ROLE_ARN"),
            external_id=_require("EXTERNAL_ID"),
            consumer_kms_key_id=_require("CONSUMER_KMS_KEY_ID"),
            producer_kms_key_arn=_require("PRODUCER_KMS_KEY_ARN"),
            transfer_prefix=os.environ.get("TRANSFER_PREFIX", ""),
            marker_key=os.environ.get("MARKER_KEY", "_s3_hydration_last_sync"),
        )
