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
