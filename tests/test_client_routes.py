# SPDX-FileCopyrightText: William Moreno Reyes <williamjmorenor@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Tests for customer-protected routes under /client/ blueprint."""


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
        data={"tier": "starter"},
        follow_redirects=False,
    )
    assert response.status_code in (302, 303)


def test_client_blocks_anonymous_all_routes(client):
    protected = [
        "/client/", "/client/billing", "/client/instances/inst_123",
        "/client/profile", "/client/support", "/client/help",
        "/client/subscriptions",
    ]
    for url in protected:
        response = client.get(url, follow_redirects=False)
        assert response.status_code == 302, f"Expected 302 for {url}, got {response.status_code}"


def test_client_blocks_admin_user(client):
    client.post("/admin/login", data={"username": "testadmin", "password": "secret"}, follow_redirects=False)
    protected = [
        "/client/", "/client/billing", "/client/instances/inst_123",
        "/client/profile", "/client/support", "/client/help",
    ]
    for url in protected:
        response = client.get(url, follow_redirects=False)
        assert response.status_code == 403, f"Expected 403 for {url}, got {response.status_code}"
