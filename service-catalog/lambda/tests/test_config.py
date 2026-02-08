"""Tests for configuration loading."""

import pytest

from src.config import Config
from src.exceptions import ConfigurationError


class TestConfig:
    def test_from_env_valid(self, env_vars):
        config = Config.from_env()

        assert config.producer_bucket == env_vars["PRODUCER_BUCKET"]
        assert config.consumer_bucket == env_vars["CONSUMER_BUCKET"]
        assert config.cross_account_role_arn == env_vars["CROSS_ACCOUNT_ROLE_ARN"]
        assert config.external_id == env_vars["EXTERNAL_ID"]
        assert config.consumer_kms_key_id == env_vars["CONSUMER_KMS_KEY_ID"]
        assert config.producer_kms_key_arn == env_vars["PRODUCER_KMS_KEY_ARN"]

    def test_from_env_defaults(self, env_vars):
        config = Config.from_env()

        assert config.transfer_prefix == ""
        assert config.marker_key == "_s3_hydration_last_sync"

    def test_from_env_custom_prefix(self, env_vars, monkeypatch):
        monkeypatch.setenv("TRANSFER_PREFIX", "data/")
        config = Config.from_env()

        assert config.transfer_prefix == "data/"

    def test_from_env_custom_marker(self, env_vars, monkeypatch):
        monkeypatch.setenv("MARKER_KEY", "custom_marker")
        config = Config.from_env()

        assert config.marker_key == "custom_marker"

    @pytest.mark.parametrize(
        "missing_var",
        [
            "PRODUCER_BUCKET",
            "CONSUMER_BUCKET",
            "CROSS_ACCOUNT_ROLE_ARN",
            "EXTERNAL_ID",
            "CONSUMER_KMS_KEY_ID",
            "PRODUCER_KMS_KEY_ARN",
        ],
    )
    def test_from_env_missing_required(self, env_vars, monkeypatch, missing_var):
        monkeypatch.delenv(missing_var)

        with pytest.raises(ConfigurationError, match=missing_var):
            Config.from_env()

    def test_config_is_frozen(self, env_vars):
        config = Config.from_env()

        with pytest.raises(AttributeError):
            config.producer_bucket = "other-bucket"
