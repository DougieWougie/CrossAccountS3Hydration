"""Lambda handler for S3 Hydration Consumer Pull."""

from __future__ import annotations

import logging
import time
from typing import Any

from .config import Config
from .exceptions import S3HydrationError
from .metrics import MetricsPublisher
from .transfer import S3TransferService

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


def lambda_handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """Entry point for the S3 Hydration Lambda function.

    Loads configuration from environment variables, executes the transfer,
    publishes metrics, and raises on any failures to trigger DLQ delivery.
    """
    request_id = getattr(context, "aws_request_id", "local")
    logging.basicConfig(
        format=f"%(asctime)s [{request_id}] %(levelname)s %(name)s - %(message)s",
        level=logging.INFO,
        force=True,
    )

    logger.info("S3 Hydration starting, request_id=%s", request_id)
    start = time.monotonic()
    metrics = MetricsPublisher()

    try:
        config = Config.from_env()
        service = S3TransferService(config)
        result = service.execute()

        elapsed = time.monotonic() - start
        metrics.objects_transferred(len(result.transferred))
        metrics.objects_skipped(len(result.skipped))
        metrics.objects_failed(len(result.failed))
        metrics.bytes_transferred(result.bytes_transferred)
        metrics.transfer_duration(elapsed)

        summary = {
            "request_id": request_id,
            "transferred": len(result.transferred),
            "skipped": len(result.skipped),
            "failed": len(result.failed),
            "bytes_transferred": result.bytes_transferred,
            "duration_seconds": round(elapsed, 2),
        }
        logger.info("Transfer summary: %s", summary)

        if result.failed:
            failed_keys = [e.key for e in result.failed]
            raise S3HydrationError(
                f"{len(result.failed)} object(s) failed to transfer: {failed_keys}"
            )

        return summary

    except S3HydrationError:
        raise
    except Exception as exc:
        logger.exception("Unexpected error during transfer")
        raise S3HydrationError(f"Transfer failed: {exc}") from exc
