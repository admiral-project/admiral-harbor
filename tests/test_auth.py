# SPDX-FileCopyrightText: William Moreno Reyes <williamjmorenor@gmail.com>
# SPDX-License-Identifier: Apache-2.0

from unittest.mock import patch
from app.models import Customer
from app.extensions import db
from datetime import datetime, UTC


def test_login_logout_me(client):
    response = client.post("/auth/login", json={"email": "user@example.com", "password": "secret"})
    assert response.status_code == 200
    assert response.json["email"] == "user@example.com"

    response = client.get("/auth/me")
    assert response.status_code == 200
    assert response.json["authenticated"] is True

    response = client.post("/auth/logout", follow_redirects=False)
    assert response.status_code == 302

    response = client.get("/auth/me")
    assert response.status_code == 401
    assert response.json["error"] == "unauthorized"


def test_register_requires_terms(client):
    response = client.post(
        "/auth/register",
        json={
            "display_name": "New Customer",
            "email": "new@example.com",
            "password": "secret",
        },
    )
    assert response.status_code == 400
    assert response.json["error"] == "terms acceptance required"


def test_register_accepts_terms(client, app):
    with patch("app.auth._generic_auth_failure", side_effect=lambda: ("fail", 401)):  # dummy
        with patch("app.auth._send_confirmation_email", return_value=True):
            response = client.post(
                "/auth/register",
                json={
                    "display_name": "New Customer",
                    "email": "new2@example.com",
                    "password": "valid-customer-password",
                    "accept_terms": True,
                },
            )
    assert response.status_code == 202
    assert response.json["customer"]["terms_policy_version"] == "overdue-policy-v1"


def test_login_failure_returns_generic_unauthorized(client):
    from argon2.exceptions import VerifyMismatchError
    from unittest.mock import Mock, patch

    mock_hasher = Mock()
    mock_hasher.verify.side_effect = VerifyMismatchError

    with patch("app.auth.ph", mock_hasher):
        response = client.post(
            "/auth/login",
            json={"email": "user@example.com", "password": "secret"},
        )
    assert response.status_code == 401
    assert response.json["error"] == "unauthorized"


def test_register_rejects_weak_password(client):
    response = client.post(
        "/auth/register",
        json={
            "display_name": "Weak Password",
            "email": "weak@example.com",
            "password": "short",
            "accept_terms": True,
        },
    )
    assert response.status_code == 400
    assert "at least 12" in response.json["error"]


def test_login_missing_fields(client):
    response = client.post("/auth/login", json={"email": "only@email.com"})
    assert response.status_code == 400


def test_login_non_existent_user(client):
    response = client.post("/auth/login", json={"email": "nonexistent@test.com", "password": "pass"})
    assert response.status_code == 401


def test_register_missing_fields(client):
    response = client.post("/auth/register", json={"email": "test@test.com"})
    assert response.status_code == 400


def test_register_rejects_invalid_email(client):
    response = client.post(
        "/auth/register",
        json={
            "display_name": "Invalid Email",
            "email": "invalid-email",
            "password": "secret",
            "accept_terms": True,
        },
    )
    assert response.status_code == 400
    assert response.json["error"] == "invalid email format"


def test_register_duplicate_email(client, app):
    response = client.post(
        "/auth/register",
        json={
            "display_name": "Duplicate",
            "email": "user@example.com",
            "password": "secret",
            "accept_terms": True,
        },
    )
    assert response.status_code == 409


def test_confirm_email_success(client, app):
    from app.auth import _token_hash

    token = "sometoken"
    h = _token_hash(token)

    with app.app_context():
        c = Customer.query.filter_by(email="user@example.com").first()
        c.email_confirmation_token_hash = h
        c.email_confirmation_sent_at = datetime.now(UTC)
        c.signup_status = "pending"
        db.session.commit()

    response = client.get(f"/auth/confirm/{token}")
    assert response.status_code == 200
    response = client.post(f"/auth/confirm/{token}")
    assert response.status_code == 302

    with app.app_context():
        c = Customer.query.filter_by(email="user@example.com").first()
        assert c.signup_status == "active"
        assert c.email_confirmed_at is not None


def test_confirm_email_invalid_token(client):
    response = client.get("/auth/confirm/wrongtoken")
    assert response.status_code == 302


def test_profile_update(client, app):
    # First login
    client.post("/auth/login", json={"email": "user@example.com", "password": "secret"})

    response = client.post(
        "/auth/profile", data={"display_name": "Updated Name", "country": "US"}, follow_redirects=True
    )

    assert response.status_code == 200
    assert b"Profile updated" in response.data

    with app.app_context():
        c = Customer.query.filter_by(email="user@example.com").first()
        assert c.display_name == "Updated Name"
        assert c.country == "US"


def test_auth_confirmation_expired_helper(app):
    from app.auth import _confirmation_is_expired

    with app.app_context():
        c = Customer(email="exp@test.com", display_name="Exp")
        assert _confirmation_is_expired(c) is True


def test_send_confirmation_email_no_host(app):
    from app.auth import _send_confirmation_email

    app.config["HARBOR_SMTP_HOST"] = ""
    with app.app_context():
        c = Customer(email="exp@test.com", display_name="Exp")
        assert _send_confirmation_email(c, "token") is False


def test_send_confirmation_email_with_ssl(app):
    from app.auth import _send_confirmation_email

    app.config["HARBOR_SMTP_HOST"] = "localhost"
    app.config["HARBOR_SMTP_USE_SSL"] = True
    app.config["HARBOR_SMTP_PORT"] = 465
    with app.test_request_context():
        c = Customer(email="exp@test.com", display_name="Exp")
        with patch("smtplib.SMTP_SSL"):
            assert _send_confirmation_email(c, "token") is True


def test_send_confirmation_email_with_tls(app):
    from app.auth import _send_confirmation_email

    app.config["HARBOR_SMTP_HOST"] = "localhost"
    app.config["HARBOR_SMTP_USE_SSL"] = False
    app.config["HARBOR_SMTP_USE_TLS"] = True
    app.config["HARBOR_SMTP_PORT"] = 587
    with app.test_request_context():
        c = Customer(email="exp@test.com", display_name="Exp")
        with patch("smtplib.SMTP"):
            assert _send_confirmation_email(c, "token") is True


def test_login_rate_limiter(client):
    from app.auth import login_limiter

    login_limiter.reset("127.0.0.1")
    # Exceed limit
    for _ in range(6):
        client.post("/auth/login", json={"email": "wrong@example.com", "password": "wrong"})
    response = client.post("/auth/login", json={"email": "wrong@example.com", "password": "wrong"})
    assert response.status_code == 429


def test_register_rate_limiter(client):
    from app.auth import register_limiter

    register_limiter.reset("127.0.0.1")
    # Exceed limit (max 3)
    for _ in range(4):
        client.post("/auth/register", json={"email": "wrong@example.com", "password": "wrong"})
    response = client.post("/auth/register", json={"email": "wrong@example.com", "password": "wrong"})
    assert response.status_code == 429


def test_login_form_and_failures(client, app):
    # Form post failure
    response = client.post(
        "/auth/login", data={"email": "nonexistent@test.com", "password": "pass"}, follow_redirects=True
    )
    assert response.status_code == 200
    assert b"Invalid credentials" in response.data

    # Form post missing
    response = client.post("/auth/login", data={"email": "nonexistent@test.com"}, follow_redirects=True)
    assert response.status_code == 200
    assert b"Email and password are required" in response.data


def test_login_account_pending_rejected(client, app):
    with app.app_context():
        # Create a pending customer
        p = Customer(
            email="pending@test.com",
            display_name="Pending",
            password_hash=Customer.query.filter_by(email="user@example.com").one().password_hash,
            signup_status="pending",
        )
        # Create a rejected customer
        r = Customer(
            email="rejected@test.com",
            display_name="Rejected",
            password_hash=Customer.query.filter_by(email="user@example.com").one().password_hash,
            signup_status="rejected",
        )
        db.session.add(p)
        db.session.add(r)
        db.session.commit()

    # Login rejected html
    response = client.post(
        "/auth/login", data={"email": "rejected@test.com", "password": "secret"}, follow_redirects=True
    )
    assert b"rejected" in response.data

    # Login rejected json
    response = client.post("/auth/login", json={"email": "rejected@test.com", "password": "secret"})
    assert response.status_code == 403


def test_register_html_fails(client):
    response = client.post("/auth/register", data={"email": "test@test.com"}, follow_redirects=True)
    assert b"required" in response.data


def test_register_duplicate_html(client):
    response = client.post(
        "/auth/register",
        data={
            "display_name": "Duplicate",
            "email": "user@example.com",
            "password": "valid-customer-password",
            "accept_terms": "on",
        },
        follow_redirects=True,
    )
    assert b"Customer already exists" in response.data


def test_confirm_email_edge_cases(client, app):
    from app.auth import _token_hash

    with app.app_context():
        r = Customer(
            email="rej_confirm@test.com",
            display_name="Rej",
            password_hash="some-hash",
            signup_status="rejected",
            email_confirmation_token_hash=_token_hash("rej_token"),
        )
        db.session.add(r)
        db.session.commit()

    # Conf missing token
    response = client.get("/auth/confirm/%20", follow_redirects=True)
    assert b"missing" in response.data

    # Conf rejected customer
    response = client.get("/auth/confirm/rej_token", follow_redirects=True)
    assert b"rejected" in response.data


def test_logout_json(client):
    response = client.post("/auth/logout", headers={"Accept": "application/json"})
    assert response.json == {"status": "logged_out"}


def test_profile_password_update(client, app):
    client.post("/auth/login", json={"email": "user@example.com", "password": "secret"})

    # Weak password
    response = client.post(
        "/auth/profile",
        data={"display_name": "Updated Name", "new_password": "weak", "current_password": "secret"},
        follow_redirects=True,
    )
    assert b"at least 12" in response.data

    # Correct update
    response = client.post(
        "/auth/profile",
        data={"display_name": "Updated Name", "new_password": "new-valid-password-123", "current_password": "secret"},
        follow_redirects=True,
    )
    assert b"Profile updated" in response.data

    # Wrong current password
    response = client.post(
        "/auth/profile",
        data={"display_name": "Updated Name", "new_password": "new-valid-password-456", "current_password": "wrong"},
        follow_redirects=True,
    )
    assert b"incorrect" in response.data
