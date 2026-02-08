"""Tests for Lambda handler."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from moto import mock_aws

from src.exceptions import ConfigurationError, S3HydrationError
from src.handler import lambda_handler
from src.transfer import TransferResult


class TestLambdaHandler:
    @mock_aws
    def test_success(self, env_vars, lambda_context, s3_client, producer_bucket, consumer_bucket, sample_objects):
        with patch("src.handler.MetricsPublisher") as mock_metrics_cls:
            mock_metrics = MagicMock()
            mock_metrics_cls.return_value = mock_metrics

            with patch("src.transfer.boto3.client") as mock_boto_client:
                # Make STS return credentials that point back to moto
                mock_sts = MagicMock()
                mock_sts.assume_role.return_value = {
                    "Credentials": {
                        "AccessKeyId": "testing",
                        "SecretAccessKey": "testing",
                        "SessionToken": "testing",
                    }
                }

                def client_factory(service, **kwargs):
                    if service == "sts":
                        return mock_sts
                    if service == "s3":
                        return s3_client
                    return MagicMock()

                mock_boto_client.side_effect = client_factory

                result = lambda_handler({}, lambda_context)

        assert result["request_id"] == "test-request-id-123"
        assert result["transferred"] == len(sample_objects)
        assert result["failed"] == 0
        assert result["bytes_transferred"] > 0
        assert "duration_seconds" in result

        mock_metrics.objects_transferred.assert_called_once_with(len(sample_objects))
        mock_metrics.objects_failed.assert_called_once_with(0)

    def test_missing_config_raises(self, lambda_context, monkeypatch):
        # Ensure no env vars are set
        for var in ["PRODUCER_BUCKET", "CONSUMER_BUCKET", "CROSS_ACCOUNT_ROLE_ARN",
                     "EXTERNAL_ID", "CONSUMER_KMS_KEY_ID", "PRODUCER_KMS_KEY_ARN"]:
            monkeypatch.delenv(var, raising=False)

        with patch("src.handler.MetricsPublisher"):
            with pytest.raises(S3HydrationError):
                lambda_handler({}, lambda_context)

    @mock_aws
    def test_partial_failure_raises(self, env_vars, lambda_context):
        failed_result = TransferResult(
            transferred=["good.txt"],
            failed=[MagicMock(key="bad.txt")],
            bytes_transferred=100,
        )

        with patch("src.handler.MetricsPublisher"):
            with patch("src.handler.S3TransferService") as mock_service_cls:
                mock_service_cls.return_value.execute.return_value = failed_result

                with pytest.raises(S3HydrationError, match="1 object\\(s\\) failed"):
                    lambda_handler({}, lambda_context)

    @mock_aws
    def test_unexpected_error_wrapped(self, env_vars, lambda_context):
        with patch("src.handler.MetricsPublisher"):
            with patch("src.handler.S3TransferService") as mock_service_cls:
                mock_service_cls.return_value.execute.side_effect = RuntimeError("boom")

                with pytest.raises(S3HydrationError, match="Transfer failed.*boom"):
                    lambda_handler({}, lambda_context)

    @mock_aws
    def test_structured_logging(self, env_vars, lambda_context, caplog):
        empty_result = TransferResult()

        with patch("src.handler.MetricsPublisher"):
            with patch("src.handler.S3TransferService") as mock_service_cls:
                mock_service_cls.return_value.execute.return_value = empty_result

                import logging
                with caplog.at_level(logging.INFO):
                    result = lambda_handler({}, lambda_context)

        assert result["transferred"] == 0
