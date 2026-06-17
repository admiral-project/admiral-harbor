# SPDX-FileCopyrightText: William Moreno Reyes <williamjmorenor@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Tests for CSRF role binding token generation and validation."""

from flask import session


def test_generate_csrf_token_public(app):
    with app.test_request_context():
        from app.csrf import generate_csrf_token, _csrf_role

        assert _csrf_role() == "public"
        token = generate_csrf_token()
        assert token == session["csrf_token"]
        assert session["csrf_data"]["role"] == "public"


def test_csrf_role_admin(app):
    with app.test_request_context():
        from app.csrf import _csrf_role
        from flask_login import login_user
        from app.models import HarborAdminUser
        from app.extensions import db

        admin = db.session.query(HarborAdminUser).filter_by(username="testadmin").one()
        login_user(admin)
        assert _csrf_role() == "admin"


def test_generate_csrf_token_reuses_same_role(app):
    with app.test_request_context():
        from app.csrf import generate_csrf_token

        t1 = generate_csrf_token()
        t2 = generate_csrf_token()
        assert t1 == t2


def test_generate_csrf_token_creates_new_on_role_change(app):
    with app.test_request_context():
        from app.csrf import generate_csrf_token

        t1 = generate_csrf_token()
        session["csrf_data"]["role"] = "different"
        t2 = generate_csrf_token()
        assert t1 != t2


def test_validate_csrf_skips_safe_methods(app):
    with app.test_request_context(method="GET"):
        from app.csrf import validate_csrf_request

        assert validate_csrf_request() is None


def test_validate_csrf_skips_exempt_endpoint_via_real_request(client):
    response = client.post(
        "/billing/webhooks/paypal",
        json={
            "id": "evt_test",
            "event_type": "BILLING.SUBSCRIPTION.ACTIVATED",
            "resource": {"id": "paypal_sub_1"},
        },
    )
    assert response.status_code in {200, 404}


def test_generate_csrf_token_admin_role(app):
    with app.test_request_context():
        from app.csrf import generate_csrf_token, _csrf_role
        from flask_login import login_user
        from app.models import HarborAdminUser
        from app.extensions import db

        admin = db.session.query(HarborAdminUser).filter_by(username="testadmin").one()
        login_user(admin)
        assert _csrf_role() == "admin"
        generate_csrf_token()
        assert session["csrf_data"]["role"] == "admin"


def test_generate_csrf_token_customer_role(app):
    with app.test_request_context():
        from app.csrf import generate_csrf_token, _csrf_role

        session["customer_email"] = "user@example.com"
        assert _csrf_role() == "customer"
        generate_csrf_token()
        assert session["csrf_data"]["role"] == "customer"


def test_role_switch_invalidates_old_token(app):
    with app.test_request_context():
        from app.csrf import generate_csrf_token

        t1 = generate_csrf_token()
        session["csrf_data"]["role"] = "admin"
        t2 = generate_csrf_token()
        assert t1 != t2
        session["csrf_data"]["role"] = "customer"
        t3 = generate_csrf_token()
        assert t2 != t3


def test_csrf_token_exposed_in_response_headers(client):
    response = client.get("/")
    assert "X-CSRF-Token" in response.headers


def test_csrf_token_in_context_processor(client):
    response = client.get("/")
    assert b"csrf_token" in response.data or b'name="csrf_token"' in response.data
