# SPDX-FileCopyrightText: William Moreno Reyes <williamjmorenor@gmail.com>
# SPDX-License-Identifier: Apache-2.0

import pytest

from app.extensions import db
from app.models import HarborPayPalConfig
from app.paypal import PayPalError, _base_url, _db_paypal_config, _get_access_token, paypal_mode


def test_base_url_uses_live_endpoint_even_with_stale_sandbox_config(app, monkeypatch):
    app.config["HARBOR_PAYPAL_BASE_URL"] = "https://api-m.sandbox.paypal.com"
    monkeypatch.setattr(
        "app.paypal._db_paypal_config",
        lambda: {"mode": "live", "client_id": "id", "client_secret": "secret"},
    )

    with app.app_context():
        assert _base_url() == "https://api-m.paypal.com"


def test_base_url_rejects_unknown_mode(app, monkeypatch):
    monkeypatch.setattr(
        "app.paypal._db_paypal_config",
        lambda: {"mode": "invalid", "client_id": "id", "client_secret": "secret"},
    )

    with app.app_context(), pytest.raises(PayPalError, match="Invalid PayPal mode"):
        _base_url()


def test_access_token_requires_credentials_outside_mock(app, monkeypatch):
    monkeypatch.setattr(
        "app.paypal._db_paypal_config",
        lambda: {"mode": "live", "client_id": "", "client_secret": ""},
    )

    with app.app_context(), pytest.raises(PayPalError, match="Client ID"):
        _get_access_token()


def test_database_mode_overrides_process_default(app):
    app.config["HARBOR_PAYPAL_MODE"] = "mock"

    with app.app_context():
        db.session.add(HarborPayPalConfig(mode="live"))
        db.session.commit()

        assert paypal_mode() == "live"


def test_partial_database_credentials_fall_back_per_field(app):
    app.config.update(
        HARBOR_PAYPAL_MODE="live",
        HARBOR_PAYPAL_CLIENT_ID="env-client",
        HARBOR_PAYPAL_CLIENT_SECRET="env-secret",
        HARBOR_PAYPAL_WEBHOOK_ID="env-webhook",
    )

    with app.app_context():
        db.session.add(HarborPayPalConfig(mode="live", client_id="db-client"))
        db.session.commit()

        assert _db_paypal_config() == {
            "mode": "live",
            "client_id": "db-client",
            "client_secret": "env-secret",
            "webhook_id": "env-webhook",
        }


def test_production_database_config_does_not_fall_back_to_environment(app, monkeypatch):
    monkeypatch.setenv("ENV", "production")
    app.config.update(
        HARBOR_PAYPAL_MODE="live",
        HARBOR_PAYPAL_CLIENT_ID="env-client",
        HARBOR_PAYPAL_CLIENT_SECRET="env-secret",
        HARBOR_PAYPAL_WEBHOOK_ID="env-webhook",
    )

    with app.app_context():
        db.session.add(HarborPayPalConfig(mode="live", client_id="db-client"))
        db.session.commit()

        assert _db_paypal_config() == {
            "mode": "live",
            "client_id": "db-client",
            "client_secret": "",
            "webhook_id": "",
        }


def test_production_requires_database_paypal_config(app, monkeypatch):
    monkeypatch.setenv("ENV", "production")
    with app.app_context(), pytest.raises(PayPalError, match="database configuration"):
        _db_paypal_config()
