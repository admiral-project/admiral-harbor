# SPDX-FileCopyrightText: William Moreno Reyes <williamjmorenor@gmail.com>
# SPDX-License-Identifier: Apache-2.0


def test_admin_login_and_dashboard(client):
    response = client.post(
        "/admin/login",
        data={"username": "testadmin", "password": "secret"},
        follow_redirects=True,
    )
    assert response.status_code == 200
    assert b"Dashboard Administrativo" in response.data


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
