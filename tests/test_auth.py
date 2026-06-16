# SPDX-FileCopyrightText: William Moreno Reyes <williamjmorenor@gmail.com>
# SPDX-License-Identifier: Apache-2.0


def test_login_logout_me(client):
    response = client.post(
        "/auth/login", json={"email": "user@example.com", "password": "secret"}
    )
    assert response.status_code == 200
    assert response.json["email"] == "user@example.com"

    response = client.get("/auth/me")
    assert response.status_code == 200
    assert response.json["authenticated"] is True

    response = client.post("/auth/logout", follow_redirects=False)
    assert response.status_code == 302

    response = client.get("/auth/me")
    assert response.status_code == 401


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


def test_register_accepts_terms(client):
    response = client.post(
        "/auth/register",
        json={
            "display_name": "New Customer",
            "email": "new@example.com",
            "password": "secret",
            "accept_terms": True,
        },
    )
    assert response.status_code == 201
    assert response.json["customer"]["terms_policy_version"] == "overdue-policy-v1"
