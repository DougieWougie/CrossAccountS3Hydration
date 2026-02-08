"""Core transfer logic for S3 Hydration Consumer Pull model."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import TYPE_CHECKING

import boto3

from .config import Config
from .exceptions import ObjectTransferError, TransferError

if TYPE_CHECKING:
    from mypy_boto3_s3 import S3Client

logger = logging.getLogger(__name__)


@dataclass
class TransferResult:
    """Summary of a transfer run."""

    transferred: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    failed: list[ObjectTransferError] = field(default_factory=list)
    bytes_transferred: int = 0


class S3TransferService:
    """Implements the Consumer Pull pattern for cross-account S3 transfer.

    Assumes a read-only role in the producer account, lists objects,
    streams each to the consumer bucket with re-encryption under the
    consumer's KMS key, and tracks sync state via a marker object.
    """

    def __init__(self, config: Config, consumer_s3: S3Client | None = None) -> None:
        self._config = config
        self._consumer_s3 = consumer_s3 or boto3.client("s3")
        self._producer_s3: S3Client | None = None

    def execute(self) -> TransferResult:
        """Run a full transfer cycle.

        Returns:
            TransferResult with counts of transferred, skipped, and failed objects.

        Raises:
            TransferError: If role assumption or object listing fails entirely.
        """
        self._producer_s3 = self._assume_producer_role()
        last_sync = self._get_last_sync_time()
        keys = self._list_objects(since=last_sync)

        result = TransferResult()
        for key in keys:
            try:
                if self._should_skip(key):
                    result.skipped.append(key)
                    continue
                size = self._transfer_object(key)
                result.transferred.append(key)
                result.bytes_transferred += size
            except Exception as exc:
                error = ObjectTransferError(key, str(exc))
                result.failed.append(error)
                logger.error("Object transfer failed: %s", error)

        if result.transferred:
            self._write_sync_marker()

        logger.info(
            "Transfer complete: %d transferred, %d skipped, %d failed, %d bytes",
            len(result.transferred),
            len(result.skipped),
            len(result.failed),
            result.bytes_transferred,
        )
        return result

    def _assume_producer_role(self) -> S3Client:
        """Assume the cross-account read-only role in the producer account.

        Raises:
            TransferError: If STS AssumeRole fails.
        """
        try:
            sts = boto3.client("sts")
            response = sts.assume_role(
                RoleArn=self._config.cross_account_role_arn,
                RoleSessionName="s3-hydration-consumer",
                ExternalId=self._config.external_id,
                DurationSeconds=3600,
            )
            credentials = response["Credentials"]
            return boto3.client(
                "s3",
                aws_access_key_id=credentials["AccessKeyId"],
                aws_secret_access_key=credentials["SecretAccessKey"],
                aws_session_token=credentials["SessionToken"],
            )
        except Exception as exc:
            raise TransferError(f"Failed to assume producer role: {exc}") from exc

    def _get_last_sync_time(self) -> datetime | None:
        """Read the last sync marker from the consumer bucket.

        Returns:
            The LastModified time of the marker object, or None if no marker exists.
        """
        try:
            response = self._consumer_s3.head_object(
                Bucket=self._config.consumer_bucket,
                Key=self._config.marker_key,
            )
            return response["LastModified"]
        except self._consumer_s3.exceptions.ClientError as exc:
            if exc.response["Error"]["Code"] == "404":
                return None
            raise

    def _list_objects(self, since: datetime | None = None) -> list[str]:
        """List objects in the producer bucket, optionally filtering by date.

        Args:
            since: Only return objects modified after this time.

        Returns:
            List of S3 object keys.

        Raises:
            TransferError: If listing fails.
        """
        try:
            keys: list[str] = []
            paginator = self._producer_s3.get_paginator("list_objects_v2")
            params: dict = {"Bucket": self._config.producer_bucket}
            if self._config.transfer_prefix:
                params["Prefix"] = self._config.transfer_prefix

            for page in paginator.paginate(**params):
                for obj in page.get("Contents", []):
                    key = obj["Key"]
                    if key.endswith("/"):
                        continue
                    if since and obj["LastModified"] <= since:
                        continue
                    keys.append(key)
            return keys
        except Exception as exc:
            raise TransferError(f"Failed to list producer objects: {exc}") from exc

    def _should_skip(self, key: str) -> bool:
        """Check if an object already exists in the consumer bucket with matching ETag.

        Returns:
            True if the object exists and has a matching ContentLength (idempotency check).
        """
        try:
            producer_head = self._producer_s3.head_object(
                Bucket=self._config.producer_bucket,
                Key=key,
            )
            consumer_head = self._consumer_s3.head_object(
                Bucket=self._config.consumer_bucket,
                Key=key,
            )
            return consumer_head["ContentLength"] == producer_head["ContentLength"]
        except self._consumer_s3.exceptions.ClientError as exc:
            if exc.response["Error"]["Code"] == "404":
                return False
            raise

    def _transfer_object(self, key: str) -> int:
        """Stream a single object from producer to consumer with re-encryption.

        Args:
            key: The S3 object key to transfer.

        Returns:
            The size in bytes of the transferred object.
        """
        response = self._producer_s3.get_object(
            Bucket=self._config.producer_bucket,
            Key=key,
        )
        body = response["Body"]
        content_length = response["ContentLength"]
        content_type = response.get("ContentType", "application/octet-stream")

        try:
            self._consumer_s3.put_object(
                Bucket=self._config.consumer_bucket,
                Key=key,
                Body=body,
                ContentType=content_type,
                ServerSideEncryption="aws:kms",
                SSEKMSKeyId=self._config.consumer_kms_key_id,
            )
        finally:
            body.close()

        logger.info("Transferred: %s (%d bytes)", key, content_length)
        return content_length

    def _write_sync_marker(self) -> None:
        """Write a marker object to record the sync timestamp."""
        self._consumer_s3.put_object(
            Bucket=self._config.consumer_bucket,
            Key=self._config.marker_key,
            Body=datetime.now(timezone.utc).isoformat().encode(),
            ServerSideEncryption="aws:kms",
            SSEKMSKeyId=self._config.consumer_kms_key_id,
        )
