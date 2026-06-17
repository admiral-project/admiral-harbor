# SPDX-FileCopyrightText: William Moreno Reyes <williamjmorenor@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Tests for role decorators and identity helpers (customer_required, admin_required, login_required)."""

# ---- current_customer ----


def test_current_customer_returns_customer(client):
    client.post("/auth/login", json={"email": "user@example.com", "password": "secret"})
    response = client.get("/auth/me")
    assert response.status_code == 200
    assert response.json["email"] == "user@example.com"
    assert response.json["authenticated"] is True


def test_current_customer_returns_none_if_no_email(client):
    response = client.get("/auth/me")
    assert response.status_code == 401


# ---- current_admin ----


def test_current_admin_returns_user_when_authenticated(client):
    client.post(
        "/admin/login",
        data={"username": "testadmin", "password": "secret"},
        follow_redirects=False,
    )
    response = client.get("/admin/")
    assert response.status_code == 200


def test_current_admin_returns_none_when_anonymous(client):
    response = client.get("/admin/", follow_redirects=False)
    assert response.status_code == 302


# ---- login_required ----


def test_login_required_passes_authenticated(client):
    client.post("/auth/login", json={"email": "user@example.com", "password": "secret"})
    response = client.get("/auth/profile", follow_redirects=False)
    assert response.status_code == 200


def test_login_required_redirects_anonymous(client):
    client.get("/auth/me")
    response = client.get("/auth/profile", follow_redirects=False)
    assert response.status_code == 302
    assert "/auth/login" in response.location


def test_login_required_blocks_admin(client):
    client.post(
        "/admin/login",
        data={"username": "testadmin", "password": "secret"},
        follow_redirects=False,
    )
    response = client.get("/auth/profile", follow_redirects=False)
    assert response.status_code == 403


# ---- customer_required ----


def test_customer_required_allows_customer(client):
    client.post("/auth/login", json={"email": "user@example.com", "password": "secret"})
    response = client.get("/client/")
    assert response.status_code == 200


def test_customer_required_blocks_admin(client):
    client.post(
        "/admin/login",
        data={"username": "testadmin", "password": "secret"},
        follow_redirects=False,
    )
    response = client.get("/client/")
    assert response.status_code == 403


def test_customer_required_redirects_anonymous(client):
    response = client.get("/client/", follow_redirects=False)
    assert response.status_code == 302


# ---- admin_required (cross-role barrier) ----


def test_admin_required_allows_admin(client):
    client.post(
        "/admin/login",
        data={"username": "testadmin", "password": "secret"},
        follow_redirects=False,
    )
    response = client.get("/admin/")
    assert response.status_code == 200


def test_admin_required_blocks_customer(client):
    client.post("/auth/login", json={"email": "user@example.com", "password": "secret"})
    response = client.get("/admin/", follow_redirects=False)
    assert response.status_code == 302


def test_admin_required_redirects_anonymous_to_login(client):
    response = client.get("/admin/", follow_redirects=False)
    assert response.status_code == 302
    assert "/admin/login" in response.location


# ---- mutual exclusion ----


def test_customer_login_clears_admin_session(client):
    client.post(
        "/admin/login",
        data={"username": "testadmin", "password": "secret"},
        follow_redirects=False,
    )
    client.post("/auth/login", json={"email": "user@example.com", "password": "secret"})
    response = client.get("/auth/me")
    assert response.status_code == 200
    assert response.json["email"] == "user@example.com"
    response = client.get("/admin/", follow_redirects=False)
    assert response.status_code == 302


def test_admin_login_clears_customer_session(client):
    client.post("/auth/login", json={"email": "user@example.com", "password": "secret"})
    client.post(
        "/admin/login",
        data={"username": "testadmin", "password": "secret"},
        follow_redirects=False,
    )
    response = client.get("/auth/me")
    assert response.status_code == 401
    response = client.get("/client/")
    assert response.status_code == 403


# ---- cross-role barrier integration ----


def test_customer_redirected_from_admin_routes(client):
    client.post("/auth/login", json={"email": "user@example.com", "password": "secret"})
    admin_urls = [
        "/admin/",
        "/admin/subscriptions",
        "/admin/billing",
        "/admin/metrics",
        "/admin/status",
        "/admin/apps",
        "/admin/audit-log",
        "/admin/settings",
        "/admin/users",
        "/admin/lms",
        "/admin/branding",
        "/admin/integration-status",
    ]
    for url in admin_urls:
        response = client.get(url, follow_redirects=False)
        assert response.status_code == 302, (
            f"Expected 302 for {url}, got {response.status_code}"
        )


def test_admin_blocked_from_all_client_routes(client):
    client.post(
        "/admin/login",
        data={"username": "testadmin", "password": "secret"},
        follow_redirects=False,
    )
    client_urls = [
        "/client/",
        "/client/billing",
        "/client/instances/inst_123",
        "/client/profile",
        "/client/support",
        "/client/help",
    ]
    for url in client_urls:
        response = client.get(url, follow_redirects=False)
        assert response.status_code == 403, (
            f"Expected 403 for {url}, got {response.status_code}"
        )


def test_logout_clears_customer_session(client):
    client.post("/auth/login", json={"email": "user@example.com", "password": "secret"})
    client.post("/auth/logout")
    response = client.get("/auth/me")
    assert response.status_code == 401


def test_customer_login_redirects_to_client(client):
    response = client.post(
        "/auth/login",
        data={"email": "user@example.com", "password": "secret"},
        follow_redirects=False,
    )
    assert response.status_code == 302
    assert "/client/" in response.location


def test_logout_clears_admin_session(client):
    client.post(
        "/admin/login",
        data={"username": "testadmin", "password": "secret"},
        follow_redirects=False,
    )
    client.post("/admin/logout")
    response = client.get("/admin/", follow_redirects=False)
    assert response.status_code == 302
    assert "/admin/login" in response.location
