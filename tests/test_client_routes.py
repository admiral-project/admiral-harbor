# SPDX-FileCopyrightText: William Moreno Reyes <williamjmorenor@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Tests for customer-protected routes under /client/ blueprint."""

from app.extensions import db
from app.models import Customer, CustomerFiscalRequest, FiscalTreatmentType, Order


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
        assert (
            response.status_code == 302
        ), f"Expected 302 for {url}, got {response.status_code}"


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
        assert (
            response.status_code == 403
        ), f"Expected 403 for {url}, got {response.status_code}"
