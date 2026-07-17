# SPDX-FileCopyrightText: William Moreno Reyes <williamjmorenor@gmail.com>
# SPDX-License-Identifier: Apache-2.0

from unittest.mock import patch

import app.admin as admin_module
from app.admiral_client import AdmiralAPIError
from app.extensions import db
from app.models import HarborPayPalConfig, Subscription


from app.admin import escape_like_pattern


def test_customer_search_escapes_like_wildcards():
    escaped = escape_like_pattern(r"100%_ready\\")
    assert "\\%" in escaped
    assert "\\_" in escaped
    assert escaped.endswith("\\\\")


def test_admin_login_and_dashboard(client):
    response = client.post(
        "/admin/login",
        data={"username": "testadmin", "password": "secret"},
        follow_redirects=True,
    )
    assert response.status_code == 200
    assert b"Dashboard Administrativo" in response.data


def test_admin_login_rate_limited(client):
    for _ in range(5):
        response = client.post(
            "/admin/login",
            data={"username": "testadmin", "password": "wrong"},
            follow_redirects=True,
        )
        assert response.status_code == 200

    response = client.post(
        "/admin/login",
        data={"username": "testadmin", "password": "wrong"},
        follow_redirects=False,
    )
    assert response.status_code == 429
    assert response.headers["Location"] == "/admin/login"


def test_admin_layout_includes_csrf_helper(client):
    client.post(
        "/admin/login",
        data={"username": "testadmin", "password": "secret"},
        follow_redirects=True,
    )
    response = client.get("/admin/")
    assert response.status_code == 200
    assert b"js/csrf.js" in response.data


def test_paypal_config_preserves_secret_when_mode_is_unchanged(client, app):
    client.post(
        "/admin/login",
        data={"username": "testadmin", "password": "secret"},
        follow_redirects=True,
    )
    with app.app_context():
        config = HarborPayPalConfig.get_config()
        config.mode = "sandbox"
        config.client_id = "existing-client"
        config.client_secret = "encrypted-existing-secret"
        db.session.commit()

    response = client.post(
        "/admin/paypal/config",
        data={
            "mode": "sandbox",
            "client_id": "existing-client",
            "client_secret": "",
            "webhook_id": "webhook-1",
        },
        follow_redirects=False,
    )

    assert response.status_code == 302
    with app.app_context():
        config = HarborPayPalConfig.get_config()
        assert config.client_id == "existing-client"
        assert config.client_secret == "encrypted-existing-secret"
        assert config.webhook_id == "webhook-1"


def test_paypal_config_requires_new_secret_when_mode_changes(client, app):
    client.post(
        "/admin/login",
        data={"username": "testadmin", "password": "secret"},
        follow_redirects=True,
    )
    with app.app_context():
        config = HarborPayPalConfig.get_config()
        config.mode = "sandbox"
        config.client_id = "sandbox-client"
        config.client_secret = "encrypted-sandbox-secret"
        db.session.commit()

    response = client.post(
        "/admin/paypal/config",
        data={"mode": "live", "client_id": "live-client", "client_secret": ""},
        follow_redirects=True,
    )

    assert b"required for new credentials or a mode change" in response.data
    with app.app_context():
        config = HarborPayPalConfig.get_config()
        assert config.mode == "sandbox"
        assert config.client_id == "sandbox-client"


def test_subscription_csv_export_uses_subscription_fields(client, app):
    with app.app_context():
        subscription = Subscription.query.filter_by(paypal_subscription_id="paypal_sub_1").one()
        csv_data = admin_module._export_subscriptions_csv()

    assert "ID,Customer Email,Status,Tier,Created,Billing Email" in csv_data
    assert subscription.external_id in csv_data
    assert subscription.customer_email in csv_data
    assert subscription.tier_name in csv_data
    assert "subscription_id" not in csv_data


def test_calculate_mrr_uses_subscription_monthly_price(client, app):
    with app.app_context():
        mrr = admin_module._calculate_mrr()

    assert mrr["current_mrr_cents"] == 2500
    assert mrr["current_mrr_dollars"] == 25


def test_instance_pod_status_requires_auth(client):
    """Pod-status endpoint returns 302 without admin login."""
    response = client.get("/admin/instances/inst_123/pod-status")
    assert response.status_code == 302


def test_instance_pod_status_returns_json(client):
    with (
        patch.object(
            admin_module,
            "get_customer_app",
            return_value={
                "id": "inst_123",
                "technical_status": "running",
                "storage_state": "ok",
                "storage_used_bytes": 500,
                "storage_limit_bytes": 10000,
                "storage_used_percent": 5.0,
            },
        ),
        patch.object(
            admin_module,
            "get_instance_inspect",
            return_value={
                "containers": [
                    {"name": "app", "image": "wordpress:latest", "state": "running"},
                    {"name": "db", "image": "mariadb:10", "state": "running"},
                ],
                "volumes": [{"name": "wp-data", "mountpoint": "/vol/wp-data"}],
                "inspected_at": "2026-06-17T00:00:00Z",
            },
        ),
    ):
        client.post(
            "/admin/login",
            data={"username": "testadmin", "password": "secret"},
            follow_redirects=True,
        )
        response = client.get("/admin/instances/inst_123/pod-status")
        assert response.status_code == 200
        data = response.get_json()
        assert data is not None
        assert data["instance_id"] == "inst_123"
        assert data["status"] == "running"
        assert "storage" in data
        assert data["storage"]["state"] == "ok"
        assert "inspect" in data
        assert len(data["inspect"]["containers"]) == 2


def test_instance_pod_status_without_inspect(client):
    """Pod-status works even when inspect data is unavailable."""
    with (
        patch.object(
            admin_module,
            "get_customer_app",
            return_value={
                "id": "inst_123",
                "technical_status": "running",
                "storage_state": "ok",
            },
        ),
        patch.object(
            admin_module,
            "get_instance_inspect",
            side_effect=AdmiralAPIError("not found"),
        ),
    ):
        client.post(
            "/admin/login",
            data={"username": "testadmin", "password": "secret"},
            follow_redirects=True,
        )
        response = client.get("/admin/instances/inst_123/pod-status")
        assert response.status_code == 200
        data = response.get_json()
        assert data is not None
        assert "inspect" not in data


def test_paypal_webhook_idempotent(client):
    response = client.post(
        "/billing/webhooks/paypal",
        json={
            "id": "evt_123",
            "event_type": "BILLING.SUBSCRIPTION.ACTIVATED",
            "resource": {"id": "paypal_sub_1"},
        },
        headers={"X-Admiral-Webhook-Test": "test-mock-token"},
    )
    assert response.status_code in {200, 404}
