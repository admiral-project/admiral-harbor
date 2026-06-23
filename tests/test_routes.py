# SPDX-FileCopyrightText: William Moreno Reyes <williamjmorenor@gmail.com>
# SPDX-License-Identifier: Apache-2.0

from types import SimpleNamespace

from app import create_app
from app.extensions import db
from app.models import Invoice, Order, Payment, Subscription
from app.paypal import create_subscription


def test_health(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json == {"status": "healthy"}


def test_health_rejects_external_ip(client):
    response = client.get(
        "/health",
        environ_overrides={"REMOTE_ADDR": "198.51.100.10"},
    )
    assert response.status_code == 403
    assert response.json == {"status": "forbidden"}


def test_index(client):
    response = client.get("/")
    assert response.status_code == 200
    assert b"Deploy managed applications" in response.data
    assert b"Deploy managed applications" in response.data
    assert b"WordPress" in response.data


def test_index_uses_one_liner_in_catalog_cards(client):
    response = client.get("/")
    body = response.get_data(as_text=True)
    assert "Managed publishing for teams that want to ship content fast." in body
    assert "Managed **WordPress** hosting" not in body


def test_app_detail_page(client):
    response = client.get("/apps/wordpress")
    assert response.status_code == 200
    assert b"Sign in to deploy" in response.data or b"Deploy" in response.data
    body = response.get_data(as_text=True)
    assert "<strong>WordPress</strong>" in body
    assert '<a href="https://example.com">backups</a>' in body


def test_client_dashboard(client):
    client.post("/auth/login", json={"email": "user@example.com", "password": "secret"})
    response = client.get("/client/")
    assert response.status_code == 200
    assert b"Your managed apps" in response.data
    assert b"My Applications" in response.data
    assert b"Active apps" in response.data
    assert b"Monthly total" in response.data


def test_client_instance_detail_page(client):
    client.post("/auth/login", json={"email": "user@example.com", "password": "secret"})
    response = client.get("/client/instances/inst_123")
    assert response.status_code == 200
    assert b"wordpress.example.com" in response.data
    assert b"Restore" in response.data


def test_terms_policy(client):
    response = client.get("/auth/terms")
    assert response.status_code == 200
    assert response.json["policy_version"] == "overdue-policy-v1"
    assert response.json["grace_before_suspend_days"] == 5
    assert response.json["additional_grace_before_deprovision_days"] == 10
    assert response.json["last_backup_retention_days"] == 15


def test_terms_policy_override():
    app = create_app()
    app.config.update(
        TESTING=True,
        SECRET_KEY="test-secret",
        SQLALCHEMY_DATABASE_URI="sqlite://",
        ADMIRAL_API_URL="https://admirald.test:8443",
        ADMIRAL_ADMIN_TOKEN="test-token",
        ADMIRAL_CA_FILE="",
        HARBOR_OVERDUE_SUSPEND_AFTER_DAYS=7,
        HARBOR_OVERDUE_DEPROVISION_AFTER_DAYS=14,
        HARBOR_OVERDUE_LAST_BACKUP_RETENTION_DAYS=21,
    )
    with app.test_client() as test_client:
        response = test_client.get("/auth/terms")
        assert response.status_code == 200
        assert response.json["grace_before_suspend_days"] == 7
        assert response.json["additional_grace_before_deprovision_days"] == 14
        assert response.json["last_backup_retention_days"] == 21


def test_client_billing_page(client):
    client.post("/auth/login", json={"email": "user@example.com", "password": "secret"})
    response = client.get("/client/billing")
    assert response.status_code == 200
    assert b"Billing" in response.data
    assert b"wordpress" in response.data


def test_paypal_create_subscription_live_payload(monkeypatch, app):
    app.config.update(
        HARBOR_PAYPAL_MODE="live",
        HARBOR_PAYPAL_BASE_URL="https://api-m.paypal.com",
        HARBOR_PAYPAL_CLIENT_ID="client",
        HARBOR_PAYPAL_CLIENT_SECRET="secret",
    )

    captured = {}

    def fake_post(url, headers=None, data=None, json=None, timeout=None):
        captured["url"] = url
        captured["headers"] = headers
        captured["json"] = json
        return SimpleNamespace(
            raise_for_status=lambda: None,
            json=lambda: {
                "id": "P-123",
                "status": "APPROVAL_PENDING",
                "links": [{"rel": "approve", "href": "https://paypal.test/approve"}],
            },
        )

    monkeypatch.setattr("app.paypal._get_access_token", lambda: "token")
    monkeypatch.setattr("app.paypal.requests.post", fake_post)

    with app.app_context():
        result = create_subscription(
            "P-PLAN-123", "https://return", "https://cancel", custom_id="ord_1"
        )

    assert result["id"] == "P-123"
    assert captured["url"] == "https://api-m.paypal.com/v1/billing/subscriptions"
    assert captured["headers"]["Authorization"] == "Bearer token"
    assert captured["json"]["plan_id"] == "P-PLAN-123"
    assert captured["json"]["custom_id"] == "ord_1"
    assert captured["json"]["application_context"] == {
        "return_url": "https://return",
        "cancel_url": "https://cancel",
    }
    assert "tax" not in captured["json"]


def test_paypal_create_subscription_live_payload_supports_amount_override(
    monkeypatch, app
):
    app.config.update(
        HARBOR_PAYPAL_MODE="live",
        HARBOR_PAYPAL_BASE_URL="https://api-m.paypal.com",
        HARBOR_PAYPAL_CLIENT_ID="client",
        HARBOR_PAYPAL_CLIENT_SECRET="secret",
    )

    captured = {}

    def fake_post(url, headers=None, data=None, json=None, timeout=None):
        captured["json"] = json
        return SimpleNamespace(
            raise_for_status=lambda: None,
            json=lambda: {
                "id": "P-123",
                "status": "APPROVAL_PENDING",
                "links": [{"rel": "approve", "href": "https://paypal.test/approve"}],
            },
        )

    monkeypatch.setattr("app.paypal._get_access_token", lambda: "token")
    monkeypatch.setattr("app.paypal.requests.post", fake_post)

    with app.app_context():
        create_subscription(
            "P-PLAN-123",
            "https://return",
            "https://cancel",
            custom_id="ord_1",
            amount_cents=11300,
        )

    assert captured["json"]["plan_override"]["billing_cycles"][0]["pricing_scheme"][
        "fixed_price"
    ] == {"value": "113.00", "currency_code": "USD"}


def test_paypal_return_live_marks_order_approved_without_provision(
    client, monkeypatch, app
):
    app.config.update(HARBOR_PAYPAL_MODE="live")

    with app.app_context():
        order = Order(
            customer_email="user@example.com",
            app_slug="wordpress",
            tier_name="starter",
            monthly_price_cents=2500,
            tax_percent=0,
            tax_cents=0,
            total_cents=2500,
            requires_billing=True,
            status="pending_payment",
            paypal_subscription_id="sub_123",
        )
        db.session.add(order)
        db.session.commit()
        order_id = order.order_id

    monkeypatch.setattr(
        "app.client.get_subscription",
        lambda subscription_id: {"id": subscription_id, "status": "ACTIVE"},
    )

    client.post("/auth/login", json={"email": "user@example.com", "password": "secret"})
    response = client.get(
        f"/client/billing/return?order_id={order_id}&token=sub_123",
        follow_redirects=False,
    )
    assert response.status_code in {302, 303}

    with app.app_context():
        stored = db.session.query(Order).filter_by(order_id=order_id).one()
        assert stored.status == "approved"
        assert db.session.query(Invoice).count() == 0
        assert db.session.query(Payment).count() == 0


def test_mock_paypal_approve_requires_mock_mode(client, app):
    app.config.update(HARBOR_PAYPAL_MODE="live")

    response = client.get(
        "/mock-paypal/approve?subscription_id=sub_123&return_url=https://localhost:5000/billing/return"
    )

    assert response.status_code == 404
    assert response.json == {"error": "not found"}


def test_mock_paypal_approve_rejects_cross_origin_return_url(client, app):
    app.config.update(HARBOR_PAYPAL_MODE="mock")

    response = client.get(
        "/mock-paypal/approve?subscription_id=sub_123&return_url=https://evil.example.com/billing/return"
    )

    assert response.status_code == 400
    assert response.json == {"error": "invalid return_url"}


def test_mock_paypal_approve_redirects_same_origin_return_url(client, app):
    app.config.update(HARBOR_PAYPAL_MODE="mock")

    response = client.get(
        "/mock-paypal/approve?subscription_id=sub_123&return_url=https://localhost:5000/billing/return"
    )

    assert response.status_code in {302, 303}
    assert response.headers["Location"] == "https://localhost:5000/billing/return?token=sub_123"


def test_uploaded_backup_download_temporarily_blocks_repeated_invalid_token(client):
    for _ in range(10):
        response = client.get(
            "/api/v1/backups/uploads/bad/download",
            headers={"X-Admiral-Token": "wrong"},
        )
        assert response.status_code == 401
        assert response.json["error"] == "unauthorized"

    response = client.get(
        "/api/v1/backups/uploads/bad/download",
        headers={"X-Admiral-Token": "wrong"},
    )
    assert response.status_code == 429
    assert response.json["error"] == "too many authentication failures"


def test_paypal_webhook_sale_completed_uses_billing_agreement_id(client):
    with client.application.app_context():
        subscription = Subscription(
            customer_email="user@example.com",
            app_slug="wordpress",
            status="pending",
            monthly_price_cents=2500,
            tier_name="starter",
            paypal_subscription_id="sub_123",
            tax_percent=15,
            tax_cents=375,
            fiscal_adjustment_cents=-50,
            total_cents=2825,
            fiscal_country_code="NI",
            fiscal_snapshot_json='{"base_tax_percent":15,"country_code":"NI","fiscal_adjustment_cents":-50,"line_items":[{"amount_cents":375,"direction":"+","is_optional":false,"kind":"base_tax","name":"Base tax","percent":15},{"amount_cents":-50,"direction":"-","is_optional":true,"kind":"treatment","name":"Retencion IR","percent":2.0,"treatment_type_id":1}],"subtotal_cents":2500,"tax_cents":375,"tax_percent":15,"total_cents":2825}',
        )
        db.session.add(subscription)
        db.session.flush()
        order = Order(
            customer_email="user@example.com",
            app_slug="wordpress",
            tier_name="starter",
            monthly_price_cents=2500,
            tax_percent=15,
            tax_cents=375,
            fiscal_adjustment_cents=-50,
            total_cents=2825,
            fiscal_country_code="NI",
            fiscal_snapshot_json=subscription.fiscal_snapshot_json,
            requires_billing=True,
            status="approved",
            paypal_subscription_id="sub_123",
        )
        db.session.add(order)
        db.session.commit()
        order_id = order.order_id

    response = client.post(
        "/billing/webhooks/paypal",
        json={
            "id": "evt_123",
            "event_type": "PAYMENT.SALE.COMPLETED",
            "resource": {
                "id": "sale_123",
                "billing_agreement_id": "sub_123",
            },
        },
    )
    assert response.status_code == 200
    assert response.json["status"] == "active"

    with client.application.app_context():
        stored_order = db.session.query(Order).filter_by(order_id=order_id).one()
        stored_subscription = (
            db.session.query(Subscription)
            .filter_by(paypal_subscription_id="sub_123")
            .one()
        )
        invoice = db.session.query(Invoice).filter_by(paypal_event_id="evt_123").one()
        payment = db.session.query(Payment).filter_by(order_id=order_id).one()
        assert stored_order.status == "paid"
        assert stored_subscription.instance_id is not None
        assert invoice.paypal_transaction_id == "sale_123"
        assert invoice.tax_cents == 375
        assert invoice.fiscal_adjustment_cents == -50
        assert invoice.total_cents == 2825
        assert payment.provider_reference == "sale_123"
        assert payment.amount_cents == 2825
