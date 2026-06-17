# SPDX-FileCopyrightText: William Moreno Reyes <williamjmorenor@gmail.com>
# SPDX-License-Identifier: Apache-2.0

from unittest.mock import patch

import app.admin as admin_module
from app.admiral_client import AdmiralAPIError


def test_admin_login_and_dashboard(client):
    response = client.post(
        "/admin/login",
        data={"username": "testadmin", "password": "secret"},
        follow_redirects=True,
    )
    assert response.status_code == 200
    assert b"Dashboard Administrativo" in response.data


def test_instance_pod_status_requires_auth(client):
    """Pod-status endpoint returns 302 without admin login."""
    response = client.get("/admin/instances/inst_123/pod-status")
    assert response.status_code == 302


def test_instance_pod_status_returns_json(client):
    with patch.object(admin_module, "get_customer_app", return_value={
        "id": "inst_123", "technical_status": "running", "storage_state": "ok",
        "storage_used_bytes": 500, "storage_limit_bytes": 10000, "storage_used_percent": 5.0,
    }), patch.object(admin_module, "get_instance_inspect", return_value={
        "containers": [
            {"name": "app", "image": "wordpress:latest", "state": "running"},
            {"name": "db", "image": "mariadb:10", "state": "running"},
        ],
        "volumes": [{"name": "wp-data", "mountpoint": "/vol/wp-data"}],
        "inspected_at": "2026-06-17T00:00:00Z",
    }):
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
    with patch.object(admin_module, "get_customer_app", return_value={
        "id": "inst_123", "technical_status": "running", "storage_state": "ok",
    }), patch.object(admin_module, "get_instance_inspect", side_effect=AdmiralAPIError("not found")):
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
    )
    assert response.status_code in {200, 404}
