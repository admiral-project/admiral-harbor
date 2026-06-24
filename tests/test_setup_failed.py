# SPDX-FileCopyrightText: William Moreno Reyes <williamjmorenor@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""Tests for setup_command flow in Harbor.

Covers:
- _reconcile_setup_failed() cancels PayPal subscription and marks
  subscription as cancelled when commercial_status is 'cancelled' and
  technical_status is 'setup_failed'.
- customer-facing dashboard and instance detail pages show the correct
  contextual message for initializing / setup_failed / running states.
"""

from unittest.mock import patch

from app.extensions import db
from app.models import CustomerApp, Subscription


def _make_app_setup_failed(client):
    with client.application.app_context():
        sub = Subscription(
            customer_email="user@example.com",
            app_slug="erpnext",
            status="active",
            monthly_price_cents=5000,
            tier_name="dev",
            instance_id="inst_setup_fail",
            paypal_subscription_id="PAYPAL-FAIL-001",
            tax_percent=0,
            tax_cents=0,
            fiscal_adjustment_cents=0,
            total_cents=5000,
        )
        db.session.add(sub)
        db.session.commit()
        db.session.add(
            CustomerApp(
                subscription_id=sub.id,
                customer_email="user@example.com",
                instance_id="inst_setup_fail",
                app_slug="erpnext",
                domain="erpnext.example.com",
                status="setup_failed",
                commercial_status="cancelled",
                backup_status="ok",
                storage_status="ok",
                tier_name="dev",
            )
        )
        db.session.commit()
        return sub.id


def test_reconcile_setup_failed_cancels_subscription(client):
    sub_id = _make_app_setup_failed(client)
    with patch("app.paypal.cancel_subscription") as mock_cancel, patch(
        "app.paypal.refund_last_sale"
    ) as mock_refund:
        mock_refund.return_value = "refund-001"
        from worker import _reconcile_setup_failed

        with client.application.app_context():
            actions, errors = _reconcile_setup_failed(client.application)
            sub = db.session.get(Subscription, sub_id)

    assert actions == 1, f"expected 1 action, got {actions}"
    assert errors == 0
    assert sub.status == "cancelled"
    mock_cancel.assert_called_once_with(
        "PAYPAL-FAIL-001",
        reason="Setup command failed, application could not be initialized",
    )
    mock_refund.assert_called_once_with("PAYPAL-FAIL-001")


def test_reconcile_setup_failed_noop_when_already_cancelled(client):
    sub_id = _make_app_setup_failed(client)
    with patch("app.paypal.cancel_subscription") as mock_cancel, patch(
        "app.paypal.refund_last_sale"
    ) as mock_refund:
        with client.application.app_context():
            sub = db.session.get(Subscription, sub_id)
            sub.status = "cancelled"
            db.session.commit()
            from worker import _reconcile_setup_failed

            actions, errors = _reconcile_setup_failed(client.application)

    assert actions == 0
    mock_cancel.assert_not_called()
    mock_refund.assert_not_called()


def test_reconcile_setup_failed_noop_when_no_paypal_id(client):
    from app.models import Subscription as Sub

    with client.application.app_context():
        sub = Sub(
            customer_email="user@example.com",
            app_slug="freeapp",
            status="active",
            monthly_price_cents=0,
            tier_name="free",
            instance_id="inst_free_fail",
            paypal_subscription_id="",
            tax_percent=0,
            tax_cents=0,
            fiscal_adjustment_cents=0,
            total_cents=0,
        )
        db.session.add(sub)
        db.session.commit()
        db.session.add(
            CustomerApp(
                subscription_id=sub.id,
                customer_email="user@example.com",
                instance_id="inst_free_fail",
                app_slug="freeapp",
                domain="freeapp.example.com",
                status="setup_failed",
                commercial_status="cancelled",
                backup_status="ok",
                storage_status="ok",
                tier_name="free",
            )
        )
        db.session.commit()

    with patch("app.paypal.cancel_subscription") as mock_cancel, patch(
        "app.paypal.refund_last_sale"
    ) as mock_refund:
        from worker import _reconcile_setup_failed

        with client.application.app_context():
            actions, errors = _reconcile_setup_failed(client.application)

    assert actions == 0
    mock_cancel.assert_not_called()
    mock_refund.assert_not_called()


def test_dashboard_shows_initializing_message(client):
    from app.models import Subscription as Sub

    with client.application.app_context():
        sub = Sub(
            customer_email="user@example.com",
            app_slug="erpnext",
            status="active",
            monthly_price_cents=5000,
            tier_name="dev",
            instance_id="inst_init",
            paypal_subscription_id="pp-init",
            tax_percent=0,
            tax_cents=0,
            fiscal_adjustment_cents=0,
            total_cents=5000,
        )
        db.session.add(sub)
        db.session.commit()
        db.session.add(
            CustomerApp(
                subscription_id=sub.id,
                customer_email="user@example.com",
                instance_id="inst_init",
                app_slug="erpnext",
                domain="erpnext.example.com",
                status="initializing",
                commercial_status="active",
                backup_status="ok",
                storage_status="ok",
                tier_name="dev",
            )
        )
        db.session.commit()

    client.post(
        "/auth/login",
        json={"email": "user@example.com", "password": "secret"},
    )
    resp = client.get("/client/", follow_redirects=True)
    assert b"inicializando" in resp.data.lower() or b"inicializ" in resp.data.lower()


def test_dashboard_shows_setup_failed_message(client):
    sub_id = _make_app_setup_failed(client)

    client.post(
        "/auth/login",
        json={"email": "user@example.com", "password": "secret"},
    )
    resp = client.get("/client/", follow_redirects=True)
    assert b"reembolso" in resp.data.lower()