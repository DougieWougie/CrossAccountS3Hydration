"""CloudWatch custom metrics publisher for S3 Hydration."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import boto3

if TYPE_CHECKING:
    from mypy_boto3_cloudwatch import CloudWatchClient

logger = logging.getLogger(__name__)

NAMESPACE = "S3Hydration"


class MetricsPublisher:
    """Publishes custom CloudWatch metrics for transfer operations."""

    def __init__(self, client: CloudWatchClient | None = None) -> None:
        self._client = client or boto3.client("cloudwatch")

    def put(self, metric_name: str, value: float, unit: str = "Count") -> None:
        try:
            self._client.put_metric_data(
                Namespace=NAMESPACE,
                MetricData=[
                    {
                        "MetricName": metric_name,
                        "Value": value,
                        "Unit": unit,
                    }
                ],
            )
        except Exception:
            logger.exception("Failed to publish metric %s", metric_name)

    def objects_transferred(self, count: int) -> None:
        self.put("ObjectsTransferred", count)

    def objects_skipped(self, count: int) -> None:
        self.put("ObjectsSkipped", count)

    def objects_failed(self, count: int) -> None:
        self.put("ObjectsFailed", count)

    def bytes_transferred(self, total_bytes: int) -> None:
        self.put("BytesTransferred", total_bytes, unit="Bytes")

    def transfer_duration(self, seconds: float) -> None:
        self.put("TransferDurationSeconds", seconds, unit="Seconds")
