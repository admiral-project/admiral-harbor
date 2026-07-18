# SPDX-FileCopyrightText: William Moreno Reyes <williamjmorenor@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Tests for customer-protected routes under /client/ blueprint."""

from io import BytesIO

from app import admiral_client
from app.extensions import db
from app.models import (
    Customer,
    CustomerFiscalRequest,
    FiscalTreatmentType,
    Order,
    Subscription,
)
from app.paypal import PayPalError


def test_client_root(client):
    client.post("/auth/login", json={"email": "user@example.com", "password": "secret"})
    response = client.get("/client/")
    assert response.status_code == 200
    assert b"Your managed apps" in response.data
    assert b"My Applications" in response.data


def test_client_subscriptions(client):
    client.post("/auth/login", json={"email": "user@example.com", "password": "secret"})
    response = client.get("/client/subscriptions")
    assert response.status_code == 200


def test_client_subscription_detail(client):
    client.post("/auth/login", json={"email": "user@example.com", "password": "secret"})
    response = client.get("/client/subscriptions/1")
    assert response.status_code in (200, 404)


def test_client_billing(client):
    client.post("/auth/login", json={"email": "user@example.com", "password": "secret"})
    response = client.get("/client/billing")
    assert response.status_code == 200
    assert b"Billing" in response.data


def test_client_instance_detail(client):
    client.post("/auth/login", json={"email": "user@example.com", "password": "secret"})
    response = client.get("/client/instances/inst_123")
    assert response.status_code == 200
    assert b"wordpress.example.com" in response.data
    assert b'name="attachment"' not in response.data


def test_client_support(client):
    client.post("/auth/login", json={"email": "user@example.com", "password": "secret"})
    response = client.get("/client/support")
    assert response.status_code == 200


def test_client_support_create(client):
    client.post("/auth/login", json={"email": "user@example.com", "password": "secret"})
    response = client.get("/client/support/create")
    assert response.status_code == 200


def test_client_profile(client):
    client.post("/auth/login", json={"email": "user@example.com", "password": "secret"})
    response = client.get("/client/profile")
    assert response.status_code == 200


def test_client_help(client):
    client.post("/auth/login", json={"email": "user@example.com", "password": "secret"})
    response = client.get("/client/help")
    assert response.status_code == 200


def test_client_deploy_requires_auth(client):
    response = client.post("/client/apps/wordpress/deploy", follow_redirects=False)
    assert response.status_code == 302


def test_client_deploy_as_customer(client):
    client.post("/auth/login", json={"email": "user@example.com", "password": "secret"})
    response = client.post(
        "/client/apps/wordpress/deploy",
        data={"tier_name": "starter"},
        follow_redirects=False,
    )
    assert response.status_code in (302, 303)


def test_client_deploy_blocks_until_mandatory_fiscal_terms_are_accepted(client):
    with client.application.app_context():
        customer = db.session.query(Customer).filter_by(email="user@example.com").one()
        customer.country = "NI"
        db.session.commit()

    client.post("/auth/login", json={"email": "user@example.com", "password": "secret"})
    response = client.post(
        "/client/apps/wordpress/deploy",
        data={"tier_name": "starter"},
        follow_redirects=False,
    )

    assert response.status_code in (302, 303)
    assert response.headers["Location"].endswith("/apps/wordpress")

    with client.application.app_context():
        assert db.session.query(Order).count() == 0


def test_client_accepts_mandatory_fiscal_terms_and_persists_snapshot(client):
    with client.application.app_context():
        customer = db.session.query(Customer).filter_by(email="user@example.com").one()
        customer.country = "NI"
        db.session.commit()

    client.post("/auth/login", json={"email": "user@example.com", "password": "secret"})
    response = client.post(
        "/client/fiscal/accept",
        data={"accept_mandatory": "on", "next": "/client/fiscal-requests"},
        follow_redirects=False,
    )

    assert response.status_code in (302, 303)

    with client.application.app_context():
        customer = db.session.query(Customer).filter_by(email="user@example.com").one()
        assert customer.fiscal_acceptance_country_code == "NI"
        assert customer.fiscal_acceptance_snapshot_json is not None
        assert customer.fiscal_accepted_at is not None


def test_fiscal_accept_rejects_external_next_url(client):
    with client.application.app_context():
        customer = db.session.query(Customer).filter_by(email="user@example.com").one()
        customer.country = "NI"
        db.session.commit()

    client.post("/auth/login", json={"email": "user@example.com", "password": "secret"})
    response = client.post(
        "/client/fiscal/accept",
        data={"accept_mandatory": "on", "next": "https://attacker.example/phish"},
        follow_redirects=False,
    )

    assert response.status_code in (302, 303)
    assert response.headers["Location"].endswith("/client/")


def test_fiscal_accept_rejects_scheme_relative_next_url(client):
    client.post("/auth/login", json={"email": "user@example.com", "password": "secret"})
    response = client.post(
        "/client/fiscal/accept",
        data={"next": "//attacker.example/phish"},
        follow_redirects=False,
    )

    assert response.status_code in (302, 303)
    assert response.headers["Location"].endswith("/client/")


def test_client_deploy_uses_contractual_fiscal_snapshot(client):
    with client.application.app_context():
        customer = db.session.query(Customer).filter_by(email="user@example.com").one()
        customer.country = "NI"
        fiscal_type = FiscalTreatmentType(
            country_code="NI",
            name="Retencion IR",
            direction="-",
            percent=2,
            is_optional=True,
            requires_evidence=True,
            is_active=True,
        )
        db.session.add(fiscal_type)
        db.session.flush()
        db.session.add(
            CustomerFiscalRequest(
                customer_email=customer.email,
                treatment_type_id=fiscal_type.id,
                status="approved",
            )
        )
        db.session.commit()

    client.post("/auth/login", json={"email": "user@example.com", "password": "secret"})
    client.post(
        "/client/fiscal/accept",
        data={"accept_mandatory": "on", "next": "/client/fiscal-requests"},
        follow_redirects=False,
    )
    response = client.post(
        "/client/apps/wordpress/deploy",
        data={"tier_name": "starter"},
        follow_redirects=False,
    )

    assert response.status_code in (302, 303)

    with client.application.app_context():
        order = db.session.query(Order).filter_by(customer_email="user@example.com").one()
        assert order.tax_percent == 15
        assert order.tax_cents == 375
        assert order.fiscal_adjustment_cents == -50
        assert order.total_cents == 2825
        assert order.fiscal_country_code == "NI"
        assert '"tax_percent":15' in order.fiscal_snapshot_json
        assert '"name":"Retencion IR"' in order.fiscal_snapshot_json


def test_fiscal_evidence_rejects_disallowed_extension(client):
    with client.application.app_context():
        customer = db.session.query(Customer).filter_by(email="user@example.com").one()
        customer.country = "NI"
        db.session.add(
            FiscalTreatmentType(
                country_code="NI",
                name="RUC",
                direction="-",
                percent=1,
                is_optional=True,
                requires_evidence=True,
                is_active=True,
            )
        )
        db.session.commit()
        treatment_id = db.session.query(FiscalTreatmentType).filter_by(name="RUC").one().id

    client.post("/auth/login", json={"email": "user@example.com", "password": "secret"})
    response = client.post(
        "/client/fiscal-requests/new",
        data={
            "treatment_type_id": str(treatment_id),
            "evidence": (BytesIO(b"<script>alert(1)</script>"), "evidence.svg"),
        },
        content_type="multipart/form-data",
    )
    assert response.status_code == 200
    with client.application.app_context():
        assert db.session.query(CustomerFiscalRequest).count() == 0


def test_fiscal_evidence_rejects_oversized_upload(client):
    with client.application.app_context():
        customer = db.session.query(Customer).filter_by(email="user@example.com").one()
        customer.country = "NI"
        db.session.add(
            FiscalTreatmentType(
                country_code="NI",
                name="Large evidence",
                direction="-",
                percent=1,
                is_optional=True,
                requires_evidence=True,
                is_active=True,
            )
        )
        db.session.commit()
        treatment_id = db.session.query(FiscalTreatmentType).filter_by(name="Large evidence").one().id

    client.application.config["HARBOR_MAX_FISCAL_EVIDENCE_BYTES"] = 4
    client.post("/auth/login", json={"email": "user@example.com", "password": "secret"})
    response = client.post(
        "/client/fiscal-requests/new",
        data={
            "treatment_type_id": str(treatment_id),
            "evidence": (BytesIO(b"12345"), "evidence.pdf"),
        },
        content_type="multipart/form-data",
    )
    assert response.status_code == 200
    with client.application.app_context():
        assert db.session.query(CustomerFiscalRequest).count() == 0


def test_subscription_cancel_blocks_local_cancellation_when_paypal_cancel_fails(client, monkeypatch):
    def fail_cancel(subscription_id, reason):
        raise PayPalError("paypal unavailable")

    monkeypatch.setattr("app.client.paypal_cancel_subscription", fail_cancel)

    client.post("/auth/login", json={"email": "user@example.com", "password": "secret"})
    response = client.post(
        "/client/subscriptions/1/cancel",
        data={"confirm": "on", "reason": "Need to stop"},
        follow_redirects=False,
    )

    assert response.status_code in (302, 303)
    assert response.headers["Location"].endswith("/client/subscriptions/1/cancel")

    with client.application.app_context():
        subscription = db.session.get(Subscription, 1)
        assert subscription is not None
        assert subscription.status == "active"


def test_subscription_cancel_marks_local_cancellation_after_paypal_success(client, monkeypatch):
    calls = []

    def ok_cancel(subscription_id, reason):
        calls.append((subscription_id, reason))

    monkeypatch.setattr("app.client.paypal_cancel_subscription", ok_cancel)

    client.post("/auth/login", json={"email": "user@example.com", "password": "secret"})
    response = client.post(
        "/client/subscriptions/1/cancel",
        data={"confirm": "on", "reason": "Need to stop"},
        follow_redirects=False,
    )

    assert response.status_code in (302, 303)
    assert response.headers["Location"].endswith("/client/subscriptions")
    assert calls == [("paypal_sub_1", "Need to stop")]

    with client.application.app_context():
        subscription = db.session.get(Subscription, 1)
        assert subscription is not None
        assert subscription.status == "cancelled"


def test_instance_cancel_blocks_deprovision_when_paypal_cancel_fails(client, monkeypatch):
    deprovision_calls = []

    def fail_cancel(subscription_id, reason):
        raise PayPalError("paypal unavailable")

    def record_action(instance_id, action_name, tier=None, service=None, customer_id=None):
        deprovision_calls.append((instance_id, action_name))
        return {"operation_id": f"op_{action_name}", "status": "queued"}

    monkeypatch.setattr("app.client.paypal_cancel_subscription", fail_cancel)
    monkeypatch.setattr(admiral_client, "action", record_action)

    client.post("/auth/login", json={"email": "user@example.com", "password": "secret"})
    response = client.post(
        "/client/instances/inst_123/actions",
        data={"action": "cancel", "confirm_text": "wordpress"},
        follow_redirects=False,
    )

    assert response.status_code in (302, 303)
    assert response.headers["Location"].endswith("/client/instances/inst_123")
    assert deprovision_calls == []

    with client.application.app_context():
        subscription = db.session.get(Subscription, 1)
        assert subscription is not None
        assert subscription.status == "active"


def test_instance_cancel_deprovisions_after_paypal_success(client, monkeypatch):
    deprovision_calls = []

    def ok_cancel(subscription_id, reason):
        return None

    def record_action(instance_id, action_name, tier=None, service=None, customer_id=None):
        deprovision_calls.append((instance_id, action_name))
        return {"operation_id": f"op_{action_name}", "status": "queued"}

    monkeypatch.setattr("app.client.paypal_cancel_subscription", ok_cancel)
    monkeypatch.setattr(admiral_client, "action", record_action)

    client.post("/auth/login", json={"email": "user@example.com", "password": "secret"})
    response = client.post(
        "/client/instances/inst_123/actions",
        data={"action": "cancel", "confirm_text": "wordpress"},
        follow_redirects=False,
    )

    assert response.status_code in (302, 303)
    assert response.headers["Location"].endswith("/client/instances/inst_123")
    assert deprovision_calls == [("inst_123", "deprovision")]

    with client.application.app_context():
        subscription = db.session.get(Subscription, 1)
        assert subscription is not None
        assert subscription.status == "cancelled"


def test_client_blocks_anonymous_all_routes(client):
    protected = [
        "/client/",
        "/client/billing",
        "/client/instances/inst_123",
        "/client/profile",
        "/client/support",
        "/client/help",
        "/client/subscriptions",
    ]
    for url in protected:
        response = client.get(url, follow_redirects=False)
        assert response.status_code == 302, f"Expected 302 for {url}, got {response.status_code}"


def test_client_blocks_admin_user(client):
    client.post(
        "/admin/login",
        data={"username": "testadmin", "password": "secret"},
        follow_redirects=False,
    )
    protected = [
        "/client/",
        "/client/billing",
        "/client/instances/inst_123",
        "/client/profile",
        "/client/support",
        "/client/help",
    ]
    for url in protected:
        response = client.get(url, follow_redirects=False)
        assert response.status_code == 403, f"Expected 403 for {url}, got {response.status_code}"


def test_client_support_list_with_filters(client, app):
    from app.models import SupportIncident

    client.post("/auth/login", json={"email": "user@example.com", "password": "secret"})

    with app.app_context():
        # Create ticket
        ticket1 = SupportIncident(
            customer_email="user@example.com", instance_id="inst_123", subject="T1", description="D1", status="open"
        )
        ticket2 = SupportIncident(
            customer_email="user@example.com", instance_id="inst_123", subject="T2", description="D2", status="resolved"
        )
        db.session.add(ticket1)
        db.session.add(ticket2)
        db.session.commit()

    response = client.get("/client/support?status=open")
    assert response.status_code == 200
    assert b"T1" in response.data
    assert b"T2" not in response.data


def test_client_support_create_post(client, app):
    client.post("/auth/login", json={"email": "user@example.com", "password": "secret"})

    # Error subject/description required
    response = client.post("/client/support/create", data={"subject": "", "description": ""}, follow_redirects=True)
    assert b"required" in response.data

    # Successful creation (since we require subscription/instance in standard creation, we should pass subscription_id)
    response = client.post(
        "/client/support/create",
        data={"subject": "My Subject", "description": "My Description", "priority": "high", "subscription_id": "1"},
        follow_redirects=True,
    )
    assert response.status_code == 200
    assert b"My Subject" in response.data


def test_client_support_detail_not_found_and_replies(client, app):
    client.post("/auth/login", json={"email": "user@example.com", "password": "secret"})

    response = client.get("/client/support/999", follow_redirects=True)
    assert b"Ticket not found" in response.data

    from app.models import SupportIncident

    with app.app_context():
        ticket = SupportIncident(
            customer_email="user@example.com", instance_id="inst_123", subject="T3", description="D3", status="open"
        )
        db.session.add(ticket)
        db.session.commit()
        ticket_id = ticket.id

    # Get details
    response = client.get(f"/client/support/{ticket_id}")
    assert response.status_code == 200
    assert b"T3" in response.data

    # Empty reply
    response = client.post(f"/client/support/{ticket_id}/reply", data={"message": ""}, follow_redirects=True)
    assert b"cannot be empty" in response.data

    # Valid reply
    response = client.post(
        f"/client/support/{ticket_id}/reply", data={"message": "A fine reply"}, follow_redirects=True
    )
    assert b"Reply sent successfully" in response.data


def test_client_profile_edit(client, app):
    client.post("/auth/login", json={"email": "user@example.com", "password": "secret"})

    # Missing display name
    response = client.post("/client/profile/edit", data={"display_name": "", "country": "US"}, follow_redirects=True)
    assert b"required" in response.data

    # Valid profile edit
    response = client.post(
        "/client/profile/edit", data={"display_name": "New Acme", "country": "FR"}, follow_redirects=True
    )
    assert b"Profile updated" in response.data

    with app.app_context():
        customer = db.session.query(Customer).filter_by(email="user@example.com").one()
        assert customer.display_name == "New Acme"
        assert customer.country == "FR"


def test_client_help_page(client):
    client.post("/auth/login", json={"email": "user@example.com", "password": "secret"})
    response = client.get("/client/help")
    assert response.status_code == 200
    assert b"Help" in response.data or b"Soporte" in response.data or b"Manual" in response.data


def test_client_billing_receipt(client, app):
    client.post("/auth/login", json={"email": "user@example.com", "password": "secret"})

    # Not found
    response = client.get("/client/billing/receipt/inv_999", follow_redirects=True)
    assert b"not found" in response.data or b"Factura no encontrada" in response.data

    # Success
    from app.models import Invoice

    with app.app_context():
        inv = Invoice(
            invoice_id="inv_123",
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

    response = client.get("/client/billing/receipt/inv_123")
    assert response.status_code == 200
    assert b"inv_123" in response.data


def test_client_instance_credentials(client, monkeypatch):
    client.post("/auth/login", json={"email": "user@example.com", "password": "secret"})

    # Not found
    response = client.get("/client/instances/inst_999/credentials.json")
    assert response.status_code == 404

    # Found
    monkeypatch.setattr(
        "app.admiral_client.get_instance_credentials", lambda i, customer_id=None: {"user": "admin", "pass": "123"}
    )
    response = client.get("/client/instances/inst_123/credentials.json")
    assert response.status_code == 200
    assert response.json["user"] == "admin"

    # API error
    from app.admiral_client import AdmiralAPIError

    def raise_err(*args, **kwargs):
        raise AdmiralAPIError("api err")

    monkeypatch.setattr("app.admiral_client.get_instance_credentials", raise_err)
    response = client.get("/client/instances/inst_123/credentials.json")
    assert response.status_code == 502


def test_client_backup_upload_and_download(client, app):
    client.post("/auth/login", json={"email": "user@example.com", "password": "secret"})

    # Instance not found
    response = client.post("/client/instances/inst_999/backups/upload")
    assert response.status_code == 302

    # File required
    response = client.post("/client/instances/inst_123/backups/upload", follow_redirects=True)
    assert b"required" in response.data

    # Oversized file
    client.application.config["HARBOR_MAX_BACKUP_UPLOAD_BYTES"] = 4
    response = client.post(
        "/client/instances/inst_123/backups/upload",
        data={"backup_file": (BytesIO(b"a" * 100), "backup.zip")},
        follow_redirects=True,
    )
    assert b"exceeds" in response.data

    # Successful upload
    client.application.config["HARBOR_MAX_BACKUP_UPLOAD_BYTES"] = 10000
    response = client.post(
        "/client/instances/inst_123/backups/upload",
        data={"backup_file": (BytesIO(b"valid-backup-data"), "backup.zip")},
        follow_redirects=True,
    )
    assert b"uploaded" in response.data

    from app.models import UploadedBackup

    with app.app_context():
        backup = db.session.query(UploadedBackup).filter_by(customer_email="user@example.com").first()
        assert backup is not None
        backup_id = backup.backup_id

    # Download it
    response = client.get(f"/client/uploaded-backups/{backup_id}/download")
    assert response.status_code == 200
    assert response.data == b"valid-backup-data"

    # Download invalid backup
    response = client.get("/client/uploaded-backups/bk_999/download")
    assert response.status_code == 404


def test_client_subscription_upgrade_routes(client, app):
    client.post("/auth/login", json={"email": "user@example.com", "password": "secret"})

    # Upgrade page
    response = client.get("/client/subscriptions/1/upgrade")
    assert response.status_code == 200

    # Upgrade action - same tier
    response = client.post("/client/subscriptions/1/upgrade", data={"new_tier": "starter"}, follow_redirects=True)
    assert b"select a different tier" in response.data

    # Upgrade action - different tier
    response = client.post("/client/subscriptions/1/upgrade", data={"new_tier": "professional"}, follow_redirects=True)
    assert b"upgraded" in response.data

    with app.app_context():
        sub = db.session.get(Subscription, 1)
        assert sub.tier_name == "professional"


def test_client_course_enrollment(client, app):
    client.post("/auth/login", json={"email": "user@example.com", "password": "secret"})

    from app.models import AppCourse

    # Prepare courses
    with app.app_context():
        course_free = AppCourse(
            app_slug="wordpress", course_code="WP101", course_type="video", base_price_cents=0, active=True
        )
        course_paid = AppCourse(
            app_slug="wordpress", course_code="WP201", course_type="video", base_price_cents=1000, active=True
        )
        db.session.add(course_free)
        db.session.add(course_paid)
        db.session.commit()
        free_id = course_free.id
        paid_id = course_paid.id

    # 1. Course not found
    response = client.post(
        "/client/help/courses/999/enroll", data={"student_email": "student@test.com"}, follow_redirects=True
    )
    assert b"Course not found" in response.data

    # 2. Invalid email format
    response = client.post(
        f"/client/help/courses/{free_id}/enroll", data={"student_email": "bademail"}, follow_redirects=True
    )
    assert b"Invalid student email format" in response.data

    # 3. Free course success
    response = client.post(
        f"/client/help/courses/{free_id}/enroll", data={"student_email": "student@test.com"}, follow_redirects=True
    )
    assert b"Enrollment prepared" in response.data

    # 4. Paid course missing payment confirmation
    response = client.post(
        f"/client/help/courses/{paid_id}/enroll", data={"student_email": "student@test.com"}, follow_redirects=True
    )
    assert b"Payment confirmation required" in response.data

    # 5. Paid course success
    response = client.post(
        f"/client/help/courses/{paid_id}/enroll",
        data={"student_email": "student@test.com", "payment_confirmed": "yes"},
        follow_redirects=True,
    )
    assert b"Enrollment prepared" in response.data
