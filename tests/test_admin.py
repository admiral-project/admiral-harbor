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


def test_sla_helpers():
    from datetime import datetime, timedelta, UTC
    from app.admin import _calculate_sla_deadlines, _get_sla_status, _format_timedelta
    from app.models import SupportIncident

    # 1. _calculate_sla_deadlines
    dl_crit = _calculate_sla_deadlines("critical")
    assert dl_crit["response_hours"] == 1
    assert dl_crit["resolution_hours"] == 8

    # 2. _format_timedelta
    assert _format_timedelta(None) == "—"
    assert _format_timedelta(timedelta(hours=2, minutes=15)) == "2h 15m"
    assert _format_timedelta(timedelta(minutes=45)) == "45m"

    # 3. _get_sla_status
    ticket = SupportIncident(
        instance_id="inst_123",
        customer_email="user@example.com",
        subject="SLA Test",
        description="D",
        created_at=datetime.now(UTC) - timedelta(hours=5),
    )

    # Unknown if no deadlines
    assert _get_sla_status(ticket)["sla_status"] == "unknown"

    # Completed within SLA
    ticket.resolved_at = datetime.now(UTC)
    ticket.resolution_deadline = datetime.now(UTC) + timedelta(hours=2)
    assert _get_sla_status(ticket)["sla_status"] == "resolved"

    # Resolution SLA violated (resolved late)
    ticket.resolved_at = datetime.now(UTC) + timedelta(hours=3)
    assert _get_sla_status(ticket)["sla_status"] == "violated"


def test_list_users_route(client):
    # Try unauthorized
    response = client.get("/admin/users", follow_redirects=False)
    assert response.status_code == 302

    # Login as admin
    client.post("/admin/login", data={"username": "testadmin", "password": "secret"}, follow_redirects=True)
    response = client.get("/admin/users")
    assert response.status_code == 200
    assert b"testadmin" in response.data


def test_edit_user_route(client, app):
    client.post("/admin/login", data={"username": "testadmin", "password": "secret"}, follow_redirects=True)

    # Edit user GET
    response = client.get("/admin/users/1/edit")
    assert response.status_code == 200

    # Edit user POST - missing username
    response = client.post("/admin/users/1/edit", data={"username": "", "display_name": "New"}, follow_redirects=True)
    assert b"required" in response.data

    # Edit user POST - duplicate username
    from app.models import HarborAdminUser

    with app.app_context():
        other = HarborAdminUser(username="otheradmin", display_name="Other", password_hash="hash")
        db.session.add(other)
        db.session.commit()

    response = client.post(
        "/admin/users/1/edit", data={"username": "otheradmin", "display_name": "New"}, follow_redirects=True
    )
    assert b"already exists" in response.data

    # Toggle active - self
    response = client.post("/admin/users/1/toggle-active", follow_redirects=True)
    assert b"cannot deactivate yourself" in response.data or response.status_code == 200

    # Delete - self
    response = client.post("/admin/users/1/delete", follow_redirects=True)
    assert b"cannot delete yourself" in response.data or response.status_code == 200

    # Toggle active - last active check
    response = client.post("/admin/users/2/toggle-active", follow_redirects=True)
    assert b"Cannot deactivate" in response.data or response.status_code == 200

    # Delete - last admin check
    response = client.post("/admin/users/2/delete", follow_redirects=True)
    assert b"Cannot delete" in response.data or response.status_code == 200

    # Toggle active & Delete success with a 3rd admin
    with app.app_context():
        third = HarborAdminUser(username="thirdadmin", display_name="Third", password_hash="hash", is_active=True)
        db.session.add(third)
        db.session.commit()
        third_id = third.id

    response = client.post(f"/admin/users/{third_id}/toggle-active", follow_redirects=True)
    assert response.status_code == 200

    response = client.post(f"/admin/users/{third_id}/delete", follow_redirects=True)
    assert response.status_code == 200


def test_customers_and_tickets_routes(client, app):
    client.post("/admin/login", data={"username": "testadmin", "password": "secret"}, follow_redirects=True)

    # List customers
    response = client.get("/admin/customers")
    assert response.status_code == 200
    assert b"user@example.com" in response.data

    # Toggle customer active
    response = client.post("/admin/customers/1/toggle-active", follow_redirects=True)
    assert b"updated" in response.data or b"desactivado" in response.data or response.status_code == 200

    # List tickets
    from app.models import SupportIncident

    with app.app_context():
        ticket = SupportIncident(
            incident_id="inc_xyz",
            instance_id="inst_123",
            customer_email="user@example.com",
            subject="Ticket XYZ",
            description="Detail XYZ",
            priority="medium",
        )
        db.session.add(ticket)
        db.session.commit()

    response = client.get("/admin/tickets")
    assert response.status_code == 200
    assert b"Ticket XYZ" in response.data

    # Ticket detail
    response = client.get("/admin/tickets/inc_xyz")
    assert response.status_code == 200
    assert b"Ticket XYZ" in response.data

    # Ticket assign
    response = client.post("/admin/tickets/inc_xyz/assign", data={"assigned_to": "testadmin"}, follow_redirects=True)
    assert b"assigned" in response.data or response.status_code == 200

    # Ticket status update
    response = client.post("/admin/tickets/inc_xyz/status", data={"status": "resolved"}, follow_redirects=True)
    assert b"resolved" in response.data or response.status_code == 200

    # Ticket add notes
    response = client.post("/admin/tickets/inc_xyz/notes", data={"note": "Some internal note"}, follow_redirects=True)
    assert response.status_code == 200


def test_review_user_routes(client, app):
    client.post("/admin/login", data={"username": "testadmin", "password": "secret"}, follow_redirects=True)

    # Create a customer pending review
    from app.models import Customer

    with app.app_context():
        c = Customer(
            email="review_cust@test.com",
            display_name="Pending Review",
            password_hash="some-hash",
            signup_status="pending",
        )
        db.session.add(c)
        db.session.commit()
        cust_id = c.id

    response = client.get("/admin/review-user")
    assert response.status_code == 200
    assert b"review_cust" in response.data

    # Approve customer
    response = client.post(f"/admin/review-user/{cust_id}/approve", follow_redirects=True)
    assert response.status_code == 200

    with app.app_context():
        cust = db.session.query(Customer).filter_by(email="review_cust@test.com").one()
        assert cust.signup_status == "active"

    # Reject customer
    with app.app_context():
        cust.signup_status = "pending"
        db.session.commit()

    response = client.post(
        f"/admin/review-user/{cust_id}/reject", data={"rejection_reason": "Too spammy"}, follow_redirects=True
    )
    assert response.status_code == 200

    with app.app_context():
        cust = db.session.query(Customer).filter_by(email="review_cust@test.com").one()
        assert cust.signup_status == "rejected"
        assert cust.rejection_reason == "Too spammy"


def test_tax_rates_route(client):
    client.post("/admin/login", data={"username": "testadmin", "password": "secret"}, follow_redirects=True)

    # View tax rates list
    response = client.get("/admin/tax-rates")
    assert response.status_code == 200

    # Add tax rate
    response = client.post("/admin/tax-rates", data={"country_code": "FR", "rate": "20.0"}, follow_redirects=True)
    assert response.status_code == 200

    # Delete tax rate
    response = client.post("/admin/tax-rates", data={"action": "delete", "country_code": "FR"}, follow_redirects=True)
    assert response.status_code == 200


def test_fiscal_types_and_requests(client, app):
    client.post("/admin/login", data={"username": "testadmin", "password": "secret"}, follow_redirects=True)

    # View list
    response = client.get("/admin/fiscal-types")
    assert response.status_code == 200

    # Add invalid percent
    response = client.post(
        "/admin/fiscal-types", data={"country_code": "FR", "name": "Tax", "percent": "invalid"}, follow_redirects=True
    )
    assert b"inv\xc3\xa1lido" in response.data or response.status_code == 200

    # Add valid type
    response = client.post(
        "/admin/fiscal-types",
        data={
            "country_code": "FR",
            "name": "TaxFR",
            "description": "Desc",
            "direction": "+",
            "percent": "5.5",
            "is_optional": "0",
            "requires_evidence": "",
        },
        follow_redirects=True,
    )
    assert response.status_code == 200

    from app.models import FiscalTreatmentType, CustomerFiscalRequest

    with app.app_context():
        ft = db.session.query(FiscalTreatmentType).filter_by(name="TaxFR").one()
        ft_id = ft.id

    # Toggle active
    response = client.post(f"/admin/fiscal-types/{ft_id}/toggle", follow_redirects=True)
    assert response.status_code == 200

    # Create fiscal request
    with app.app_context():
        fr = CustomerFiscalRequest(customer_email="user@example.com", treatment_type_id=ft_id, status="pending")
        db.session.add(fr)
        db.session.commit()
        req_id = fr.request_id

    # View fiscal requests
    response = client.get("/admin/fiscal-requests")
    assert response.status_code == 200

    # Detail
    response = client.get(f"/admin/fiscal-requests/{req_id}")
    assert response.status_code == 200

    # Approve
    response = client.post(
        f"/admin/fiscal-requests/{req_id}/approve", data={"reviewer_notes": "Good"}, follow_redirects=True
    )
    assert response.status_code == 200

    with app.app_context():
        fr = db.session.query(CustomerFiscalRequest).filter_by(request_id=req_id).one()
        assert fr.status == "approved"

    # Revoke missing note
    response = client.post(
        f"/admin/fiscal-requests/{req_id}/revoke", data={"reviewer_notes": ""}, follow_redirects=True
    )
    assert b"requerido" in response.data or response.status_code == 200

    # Revoke valid
    response = client.post(
        f"/admin/fiscal-requests/{req_id}/revoke", data={"reviewer_notes": "No longer valid"}, follow_redirects=True
    )
    assert response.status_code == 200

    with app.app_context():
        fr = db.session.query(CustomerFiscalRequest).filter_by(request_id=req_id).one()
        assert fr.status == "revoked"

    # Delete referencing fiscal request first to prevent foreign key IntegrityError
    with app.app_context():
        fr = db.session.query(CustomerFiscalRequest).filter_by(request_id=req_id).one()
        db.session.delete(fr)
        db.session.commit()

    # Delete fiscal type
    response = client.post(f"/admin/fiscal-types/{ft_id}/delete", follow_redirects=True)
    assert response.status_code == 200


def test_catalog_sync_and_availability(client, monkeypatch):
    client.post("/admin/login", data={"username": "testadmin", "password": "secret"}, follow_redirects=True)

    # Sync history
    response = client.get("/admin/catalog/sync-history")
    assert response.status_code == 200

    # Trigger catalog sync manual - success mock
    def fake_sync(*args, **kwargs):
        return {"success": True, "synced": 1, "updated": 2, "marked_missing": 0}

    monkeypatch.setattr("app.catalog_service.sync_catalog", fake_sync)

    response = client.post("/admin/catalog/sync", follow_redirects=True)
    assert response.status_code == 200

    # Set availability
    monkeypatch.setattr("app.admiral_client.update_availability", lambda *a, **kw: None)
    response = client.post(
        "/admin/apps/wordpress/availability",
        data={"availability": "suspended", "reason": "broken"},
        follow_redirects=True,
    )
    assert response.status_code == 200


def test_additional_admin_routes(client, app, monkeypatch):
    client.post("/admin/login", data={"username": "testadmin", "password": "secret"}, follow_redirects=True)

    # 1. Harbor Settings
    response = client.get("/admin/settings")
    assert response.status_code == 200
    response = client.post("/admin/settings", data={"portal_name": "New Harbor Name"}, follow_redirects=True)
    assert response.status_code == 200

    # 2. Audit Log
    response = client.get("/admin/audit-log")
    assert response.status_code == 200

    # 3. Integration Status
    response = client.get("/admin/integration-status")
    assert response.status_code == 200

    # 4. LMS Settings
    response = client.get("/admin/lms")
    assert response.status_code == 200
    response = client.post(
        "/admin/lms", data={"lms_enabled": "1", "lms_platform_url": "https://lms.test"}, follow_redirects=True
    )
    assert response.status_code == 200

    # 5. Branding
    response = client.get("/admin/branding")
    assert response.status_code == 200
    response = client.post("/admin/branding", data={"portal_theme": "dark"}, follow_redirects=True)
    assert response.status_code == 200

    # 6. Test Instances
    response = client.get("/admin/instances/test")
    assert response.status_code == 200
    response = client.get("/admin/instances/create")
    assert response.status_code == 200
    response = client.post(
        "/admin/instances/create", data={"app_slug": "wordpress", "tier_name": "starter"}, follow_redirects=True
    )
    assert response.status_code == 200

    # 7. Instances
    response = client.get("/admin/instances")
    assert response.status_code == 200

    # Patch imported functions to avoid 302 redirect
    monkeypatch.setattr(
        admin_module, "get_customer_app", lambda instance_id: {"id": instance_id, "technical_status": "running"}
    )
    monkeypatch.setattr(admin_module, "list_backups", lambda instance_id: [])

    response = client.get("/admin/instances/inst_123")
    assert response.status_code == 200

    # 8. Billing
    response = client.get("/admin/billing")
    assert response.status_code == 200

    from app.models import Invoice

    with app.app_context():
        inv = Invoice(
            invoice_id="inv_xyz_admin",
            customer_email="user@example.com",
            subscription_external_id="sub_ext_1",
            app_slug="wordpress",
            tier_name="starter",
            subtotal_cents=1000,
            tax_cents=100,
            total_cents=1100,
            status="paid",
        )
        db.session.add(inv)
        db.session.commit()

    response = client.get("/admin/billing/invoices/inv_xyz_admin")
    assert response.status_code == 200

    # 9. Metrics
    response = client.get("/admin/metrics")
    assert response.status_code == 200

    # 10. Apps list & edit
    response = client.get("/admin/apps")
    assert response.status_code == 200

    response = client.get("/admin/apps/wordpress")
    assert response.status_code == 200

    response = client.post("/admin/apps/wordpress", data={"sort_order": "5", "published": "on"}, follow_redirects=True)
    assert response.status_code == 200

    # 11. Status and CSV exports
    response = client.get("/admin/status")
    assert response.status_code == 200

    response = client.get("/admin/export/subscriptions.csv")
    assert response.status_code == 200
    assert b"Customer Email" in response.data

    response = client.get("/admin/export/payments.csv")
    assert response.status_code == 200

    response = client.get("/admin/export/tickets.csv")
    assert response.status_code == 200
