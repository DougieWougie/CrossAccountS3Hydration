"""Tests for core transfer logic."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import boto3
import pytest
from moto import mock_aws

from src.config import Config
from src.exceptions import TransferError
from src.transfer import S3TransferService, TransferResult

from .conftest import CONSUMER_BUCKET, PRODUCER_BUCKET, REGION


class TestS3TransferService:
    @pytest.fixture
    def config(self, env_vars):
        return Config.from_env()

    @pytest.fixture
    def service(self, config, s3_client, producer_bucket, consumer_bucket):
        """Create a transfer service with mocked S3 (bypassing STS)."""
        svc = S3TransferService(config, consumer_s3=s3_client)
        # Bypass STS by injecting the same mocked S3 client as producer
        svc._producer_s3 = s3_client
        return svc

    def test_list_objects_empty_bucket(self, service):
        keys = service._list_objects()
        assert keys == []

    def test_list_objects_returns_keys(self, service, sample_objects):
        keys = service._list_objects()
        assert sorted(keys) == sorted(sample_objects.keys())

    def test_list_objects_skips_directories(self, service, s3_client, producer_bucket):
        s3_client.put_object(Bucket=PRODUCER_BUCKET, Key="folder/", Body=b"")
        s3_client.put_object(Bucket=PRODUCER_BUCKET, Key="folder/file.txt", Body=b"data")

        keys = service._list_objects()
        assert "folder/" not in keys
        assert "folder/file.txt" in keys

    def test_list_objects_with_prefix(self, service, s3_client, producer_bucket, config, monkeypatch):
        monkeypatch.setenv("TRANSFER_PREFIX", "data/")
        config_with_prefix = Config.from_env()
        service._config = config_with_prefix

        s3_client.put_object(Bucket=PRODUCER_BUCKET, Key="data/file.csv", Body=b"data")
        s3_client.put_object(Bucket=PRODUCER_BUCKET, Key="other/file.csv", Body=b"other")

        keys = service._list_objects()
        assert "data/file.csv" in keys
        assert "other/file.csv" not in keys

    def test_list_objects_incremental_filter(self, service, s3_client, producer_bucket):
        s3_client.put_object(Bucket=PRODUCER_BUCKET, Key="old.txt", Body=b"old")
        s3_client.put_object(Bucket=PRODUCER_BUCKET, Key="new.txt", Body=b"new")

        # Use a cutoff far in the future to filter out everything
        future = datetime.now(timezone.utc) + timedelta(hours=1)
        keys = service._list_objects(since=future)
        assert keys == []

        # Use a cutoff far in the past to include everything
        past = datetime(2000, 1, 1, tzinfo=timezone.utc)
        keys = service._list_objects(since=past)
        assert "old.txt" in keys
        assert "new.txt" in keys

    def test_transfer_object(self, service, s3_client, producer_bucket, consumer_bucket):
        content = b"test data content"
        s3_client.put_object(Bucket=PRODUCER_BUCKET, Key="test.txt", Body=content)

        size = service._transfer_object("test.txt")

        assert size == len(content)
        response = s3_client.get_object(Bucket=CONSUMER_BUCKET, Key="test.txt")
        assert response["Body"].read() == content

    def test_should_skip_when_not_in_consumer(self, service, s3_client, producer_bucket, consumer_bucket):
        s3_client.put_object(Bucket=PRODUCER_BUCKET, Key="new.txt", Body=b"data")
        assert service._should_skip("new.txt") is False

    def test_should_skip_when_same_size(self, service, s3_client, producer_bucket, consumer_bucket):
        content = b"same content"
        s3_client.put_object(Bucket=PRODUCER_BUCKET, Key="existing.txt", Body=content)
        s3_client.put_object(Bucket=CONSUMER_BUCKET, Key="existing.txt", Body=content)

        assert service._should_skip("existing.txt") is True

    def test_should_skip_when_different_size(self, service, s3_client, producer_bucket, consumer_bucket):
        s3_client.put_object(Bucket=PRODUCER_BUCKET, Key="changed.txt", Body=b"new version")
        s3_client.put_object(Bucket=CONSUMER_BUCKET, Key="changed.txt", Body=b"old")

        assert service._should_skip("changed.txt") is False

    def test_execute_transfers_all_objects(self, service, sample_objects, consumer_bucket, s3_client):
        result = service.execute()

        assert len(result.transferred) == len(sample_objects)
        assert len(result.failed) == 0
        assert result.bytes_transferred > 0

        # Verify all objects are in consumer bucket
        for key, body in sample_objects.items():
            response = s3_client.get_object(Bucket=CONSUMER_BUCKET, Key=key)
            assert response["Body"].read() == body

    def test_execute_skips_existing_objects(self, service, s3_client, producer_bucket, consumer_bucket):
        content = b"already transferred"
        s3_client.put_object(Bucket=PRODUCER_BUCKET, Key="done.txt", Body=content)
        s3_client.put_object(Bucket=CONSUMER_BUCKET, Key="done.txt", Body=content)

        result = service.execute()

        assert "done.txt" in result.skipped
        assert "done.txt" not in result.transferred

    def test_execute_isolates_per_object_errors(self, service, s3_client, producer_bucket, consumer_bucket):
        s3_client.put_object(Bucket=PRODUCER_BUCKET, Key="good.txt", Body=b"good")
        s3_client.put_object(Bucket=PRODUCER_BUCKET, Key="bad.txt", Body=b"bad")

        original_transfer = service._transfer_object

        def failing_transfer(key):
            if key == "bad.txt":
                raise Exception("simulated failure")
            return original_transfer(key)

        service._transfer_object = failing_transfer
        result = service.execute()

        assert "good.txt" in result.transferred
        assert len(result.failed) == 1
        assert result.failed[0].key == "bad.txt"

    def test_execute_writes_sync_marker(self, service, sample_objects, consumer_bucket, s3_client):
        service.execute()

        response = s3_client.get_object(
            Bucket=CONSUMER_BUCKET,
            Key="_s3_hydration_last_sync",
        )
        marker_body = response["Body"].read().decode()
        # Should be a valid ISO timestamp
        datetime.fromisoformat(marker_body)

    def test_execute_no_marker_on_empty_transfer(self, service, consumer_bucket, s3_client):
        result = service.execute()

        assert len(result.transferred) == 0
        with pytest.raises(s3_client.exceptions.NoSuchKey):
            s3_client.get_object(
                Bucket=CONSUMER_BUCKET,
                Key="_s3_hydration_last_sync",
            )

    def test_get_last_sync_time_no_marker(self, service, consumer_bucket):
        assert service._get_last_sync_time() is None

    def test_get_last_sync_time_with_marker(self, service, s3_client, consumer_bucket):
        s3_client.put_object(
            Bucket=CONSUMER_BUCKET,
            Key="_s3_hydration_last_sync",
            Body=b"2024-01-01T00:00:00+00:00",
        )
        result = service._get_last_sync_time()
        assert result is not None

    def test_assume_role_failure_raises_transfer_error(self, config):
        service = S3TransferService(config)
        with patch("src.transfer.boto3.client") as mock_client:
            mock_sts = MagicMock()
            mock_sts.assume_role.side_effect = Exception("Access Denied")
            mock_client.return_value = mock_sts

            with pytest.raises(TransferError, match="Failed to assume producer role"):
                service._assume_producer_role()


class TestTransferResult:
    def test_defaults(self):
        result = TransferResult()
        assert result.transferred == []
        assert result.skipped == []
        assert result.failed == []
        assert result.bytes_transferred == 0
