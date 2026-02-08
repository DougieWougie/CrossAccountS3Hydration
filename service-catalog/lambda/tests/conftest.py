"""Shared fixtures for S3 Hydration tests."""

from __future__ import annotations

import json
import os
from unittest.mock import MagicMock

import boto3
import pytest
from moto import mock_aws

PRODUCER_BUCKET = "111111111111-producer-data"
CONSUMER_BUCKET = "222222222222-consumer-data"
CROSS_ACCOUNT_ROLE_ARN = "arn:aws:iam::111111111111:role/S3HydrationCrossAccountReadRole"
EXTERNAL_ID = "s3hydration-test-external-id-12345"
REGION = "eu-west-1"


@pytest.fixture
def env_vars(monkeypatch):
    """Set all required environment variables."""
    env = {
        "PRODUCER_BUCKET": PRODUCER_BUCKET,
        "CONSUMER_BUCKET": CONSUMER_BUCKET,
        "CROSS_ACCOUNT_ROLE_ARN": CROSS_ACCOUNT_ROLE_ARN,
        "EXTERNAL_ID": EXTERNAL_ID,
        "CONSUMER_KMS_KEY_ID": "arn:aws:kms:eu-west-1:222222222222:key/consumer-key-id",
        "PRODUCER_KMS_KEY_ARN": "arn:aws:kms:eu-west-1:111111111111:key/producer-key-id",
        "AWS_DEFAULT_REGION": REGION,
    }
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    return env


@pytest.fixture
def aws_mocks():
    """Start moto mocks for S3, STS, KMS, and CloudWatch."""
    with mock_aws():
        yield


@pytest.fixture
def s3_client(aws_mocks):
    """Create a mocked S3 client."""
    return boto3.client("s3", region_name=REGION)


@pytest.fixture
def producer_bucket(s3_client):
    """Create the producer S3 bucket."""
    s3_client.create_bucket(
        Bucket=PRODUCER_BUCKET,
        CreateBucketConfiguration={"LocationConstraint": REGION},
    )
    return PRODUCER_BUCKET


@pytest.fixture
def consumer_bucket(s3_client):
    """Create the consumer S3 bucket."""
    s3_client.create_bucket(
        Bucket=CONSUMER_BUCKET,
        CreateBucketConfiguration={"LocationConstraint": REGION},
    )
    return CONSUMER_BUCKET


@pytest.fixture
def cloudwatch_client(aws_mocks):
    """Create a mocked CloudWatch client."""
    return boto3.client("cloudwatch", region_name=REGION)


@pytest.fixture
def lambda_context():
    """Create a mock Lambda context object."""
    context = MagicMock()
    context.aws_request_id = "test-request-id-123"
    context.function_name = "s3-hydration-transfer"
    context.memory_limit_in_mb = 256
    context.get_remaining_time_in_millis.return_value = 900000
    return context


@pytest.fixture
def sample_objects(s3_client, producer_bucket):
    """Upload sample objects to the producer bucket."""
    objects = {
        "data/file1.csv": b"col1,col2\nval1,val2\n",
        "data/file2.json": json.dumps({"key": "value"}).encode(),
        "data/nested/file3.txt": b"hello world",
    }
    for key, body in objects.items():
        s3_client.put_object(Bucket=PRODUCER_BUCKET, Key=key, Body=body)
    return objects
