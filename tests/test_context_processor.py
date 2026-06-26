# SPDX-FileCopyrightText: William Moreno Reyes <williamjmorenor@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Tests for context_processor injection of principals (admin_user, customer, current_user)."""


def test_context_processor_injects_customer_on_client_routes(client):
    client.post("/auth/login", json={"email": "user@example.com", "password": "secret"})
    response = client.get("/client/")
    assert response.status_code == 200
    assert b"Your managed apps" in response.data


def test_context_processor_injects_admin_on_admin_routes(client):
    client.post(
        "/admin/login",
        data={"username": "testadmin", "password": "secret"},
        follow_redirects=True,
    )
    response = client.get("/admin/")
    assert response.status_code == 200
    assert b"testadmin" in response.data or b"Admin" in response.data or b"dashboard" in response.data.lower()


def test_context_processor_renders_public_pages_without_auth(client):
    response = client.get("/")
    assert response.status_code == 200


def test_context_processor_provides_portal_branding(client):
    response = client.get("/")
    assert response.status_code == 200
    assert b"Admiral" in response.data or b"Harbor" in response.data
