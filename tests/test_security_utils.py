# SPDX-FileCopyrightText: William Moreno Reyes <williamjmorenor@gmail.com>
# SPDX-License-Identifier: Apache-2.0

import pytest
from app.security import (
    validate_password_strength,
    get_required_env_var,
    validate_production_config,
)


def test_validate_password_strength():
    # Weak password
    assert validate_password_strength("short") == "Password must be at least 12 characters long."
    # Strong password
    assert validate_password_strength("a_very_long_password") is None
    # Custom minimum length
    assert validate_password_strength("short", minimum_length=5) is None


def test_get_required_env_var_present(monkeypatch):
    monkeypatch.setenv("TEST_VAR", "value")
    assert get_required_env_var("TEST_VAR") == "value"


def test_get_required_env_var_not_present_dev(monkeypatch):
    monkeypatch.delenv("TEST_VAR", raising=False)
    # Default is provided
    assert get_required_env_var("TEST_VAR", default="default_val") == "default_val"
    # No default, not production
    monkeypatch.setenv("ENV", "development")
    assert get_required_env_var("TEST_VAR") == ""


def test_get_required_env_var_not_present_prod(monkeypatch):
    monkeypatch.delenv("TEST_VAR", raising=False)
    monkeypatch.setenv("ENV", "production")

    # Raising error in production if prod_mode is True and var is missing
    with pytest.raises(ValueError, match="Required environment variable TEST_VAR not set in production"):
        get_required_env_var("TEST_VAR", prod_mode=True)

    # If prod_mode is False but no default, raises ValueError because ENV is production
    with pytest.raises(ValueError, match="Environment variable TEST_VAR is required but not set"):
        get_required_env_var("TEST_VAR")


def test_validate_production_config_development(monkeypatch):
    monkeypatch.setenv("ENV", "development")
    # In development, it should log warnings but never raise ValueError
    config = {
        "SECRET_KEY": "dev-key",
        "ADMIRAL_HARBOR_API_TOKEN": "dev-token",
        "HARBOR_ENCRYPTION_KEY": "dev-encryption-key",
        "SQLALCHEMY_DATABASE_URI": "sqlite:///harbor.db",
    }
    # No error raised
    validate_production_config(config)


def test_validate_production_config_production(monkeypatch):
    monkeypatch.setenv("ENV", "production")

    # Correct config
    valid_config = {
        "SECRET_KEY": "a_very_secure_and_long_secret_key_of_32_characters",
        "ADMIRAL_HARBOR_API_TOKEN": "secure-token",
        "HARBOR_ENCRYPTION_KEY": "secure-encryption-key",
        "SQLALCHEMY_DATABASE_URI": "postgresql://localhost/db",
        "ADMIRAL_INSECURE_SKIP_VERIFY": False,
        "HARBOR_PAYPAL_MODE": "live",
    }
    validate_production_config(valid_config)

    # Invalid secret key (starts with dev-)
    invalid_secret = dict(valid_config, SECRET_KEY="dev-secret-key")
    with pytest.raises(ValueError, match="SECRET_KEY must be replaced before production"):
        validate_production_config(invalid_secret)

    # Secret key too short
    short_secret = dict(valid_config, SECRET_KEY="too-short")
    with pytest.raises(ValueError, match="SECRET_KEY must be at least 32 characters in production"):
        validate_production_config(short_secret)

    # API token is dev default or missing
    invalid_token = dict(valid_config, ADMIRAL_HARBOR_API_TOKEN="dev-token")
    with pytest.raises(ValueError, match="ADMIRAL_HARBOR_API_TOKEN must be replaced before production"):
        validate_production_config(invalid_token)

    # Encryption key is dev default or missing
    invalid_enc = dict(valid_config, HARBOR_ENCRYPTION_KEY="dev-encryption-key")
    with pytest.raises(ValueError, match="HARBOR_ENCRYPTION_KEY must be replaced before production"):
        validate_production_config(invalid_enc)

    # Using SQLite in production
    sqlite_uri = dict(valid_config, SQLALCHEMY_DATABASE_URI="sqlite:///harbor.db")
    with pytest.raises(ValueError, match="must not use the SQLite development default in production"):
        validate_production_config(sqlite_uri)

    # Insecure skip verify true in production
    insecure = dict(valid_config, ADMIRAL_INSECURE_SKIP_VERIFY=True)
    with pytest.raises(ValueError, match="ADMIRAL_INSECURE_SKIP_VERIFY must be false in production"):
        validate_production_config(insecure)

    # PayPal mode is mock in production
    mock_paypal = dict(valid_config, HARBOR_PAYPAL_MODE="mock")
    with pytest.raises(ValueError, match="HARBOR_PAYPAL_MODE must not be 'mock' in production"):
        validate_production_config(mock_paypal)
