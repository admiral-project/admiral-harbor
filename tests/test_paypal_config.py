# SPDX-FileCopyrightText: William Moreno Reyes <williamjmorenor@gmail.com>
# SPDX-License-Identifier: Apache-2.0

import pytest

from app.extensions import db
from app.models import HarborPayPalConfig
from app.paypal import PayPalError, _base_url, _get_access_token, paypal_mode


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
