# SPDX-FileCopyrightText: William Moreno Reyes <williamjmorenor@gmail.com>
# SPDX-License-Identifier: Apache-2.0

from app.extensions import db
from app.models import Customer, CustomerApp, RestoreRequest, Subscription
from worker import (
    _reconcile_cancelled_subscriptions,
    _reconcile_operations,
    _reconcile_paypal_subscriptions,
    _run_worker_step,
    _sync_remote_instances,
)


def test_restore_reconciliation_reads_customer_owned_operation(app, monkeypatch):
    with app.app_context():
        customer = Customer(
            email="restore-owner@example.invalid",
            public_id="hcus_restore_owner",
            display_name="Restore Owner",
            password_hash="unused",
            signup_status="active",
        )
        request = RestoreRequest(
            customer_email=customer.email,
            instance_id="inst_restore_owner",
            source_backup_id="bk_restore_owner",
            source_kind="remote",
            service_name="db",
            confirm_text="wordpress",
            operation_id="op_restore_owner",
            status="queued",
        )
        db.session.add_all([customer, request])
        db.session.commit()
        calls = []

        def operation(operation_id, **identity):
            calls.append((operation_id, identity))
            return {"status": "succeeded"}

        monkeypatch.setattr("worker.get_operation", operation)
        assert _reconcile_operations(app) == (1, 0)
        assert request.status == "completed"
        assert calls == [
            ("op_restore_owner", {"instance_id": "inst_restore_owner", "customer_id": "hcus_restore_owner"})
        ]


def test_worker_step_failure_is_reported_without_raising(caplog):
    def failed_step():
        raise RuntimeError("database unavailable")

    with caplog.at_level("ERROR", logger="worker"):
        result = _run_worker_step("reconcile_operations", failed_step)

    assert result == (0, 1)
    assert "Worker step failed: reconcile_operations" in caplog.text


def test_worker_step_returns_actions_and_errors():
    assert _run_worker_step("sync_remote_instances", lambda: (3, 2)) == (3, 2)


def test_cancelled_paypal_subscription_does_not_deprovision_before_prepaid_end(app, monkeypatch):
    with app.app_context():
        customer = Customer(
            email="cancel-retry@example.com",
            public_id="hcus_cancel_retry",
            display_name="Cancel Retry",
            password_hash="unused",
            signup_status="active",
        )
        subscription = Subscription(
            customer_email=customer.email,
            app_slug="wordpress",
            status="active",
            instance_id="inst_cancel_retry",
            paypal_subscription_id="paypal_cancel_retry",
            tier_name="starter",
            total_cents=2500,
            next_billing_at="2099-01-01",
        )
        db.session.add_all([customer, subscription])
        db.session.commit()

        monkeypatch.setattr("app.paypal.get_subscription", lambda _subscription_id: {"status": "CANCELLED"})
        calls = []

        monkeypatch.setattr("worker.admiral_action", lambda *args, **kwargs: calls.append((args, kwargs)))
        actions, errors = _reconcile_paypal_subscriptions(app)
        assert (actions, errors) == (1, 0)
        assert db.session.get(Subscription, subscription.id).status == "cancelled"
        assert calls == []


def test_cancelled_subscription_deprovisions_after_prepaid_end(app, monkeypatch):
    with app.app_context():
        customer = Customer(
            email="cancel-due@example.com",
            public_id="hcus_cancel_due",
            display_name="Acme Studios",
            password_hash="unused",
            signup_status="active",
        )
        subscription = Subscription(
            customer_email=customer.email,
            app_slug="wordpress",
            status="cancelled",
            instance_id="inst_cancel_due",
            tier_name="starter",
            total_cents=2500,
            next_billing_at="2000-01-01",
        )
        db.session.add_all([customer, subscription])
        db.session.commit()
        db.session.add(
            CustomerApp(
                subscription_id=subscription.id,
                customer_email=customer.email,
                instance_id=subscription.instance_id,
                app_slug="wordpress",
                domain="wordpress.cancel-due.example.com",
                status="running",
                next_billing_at="2000-01-01",
            )
        )
        db.session.commit()
        calls = []
        monkeypatch.setattr(
            "worker.admiral_action",
            lambda *args, **kwargs: calls.append((args, kwargs)) or {"operation_id": "op_cancel_due"},
        )

        actions, errors = _reconcile_cancelled_subscriptions(app)
        assert (actions, errors) == (1, 0)
        assert calls == [
            (("inst_cancel_due", "deprovision"), {"customer_id": "hcus_cancel_due"}),
        ]
        assert db.session.query(CustomerApp).filter_by(instance_id="inst_cancel_due").one().status == "deprovisioning"


def test_remote_instance_sync_preserves_cancelled_commercial_state(app, monkeypatch):
    with app.app_context():
        customer = Customer(
            email="cancelled-sync@example.com",
            public_id="hcus_cancelled_sync",
            display_name="Cancelled Sync",
            password_hash="unused",
            signup_status="active",
        )
        subscription = Subscription(
            customer_email=customer.email,
            app_slug="wordpress",
            status="cancelled",
            instance_id="inst_cancelled_sync",
            tier_name="starter",
            total_cents=2500,
            next_billing_at="2099-01-01",
        )
        db.session.add_all([customer, subscription])
        db.session.commit()
        local = CustomerApp(
            subscription_id=subscription.id,
            customer_email=customer.email,
            instance_id=subscription.instance_id,
            app_slug="wordpress",
            domain="wordpress.cancelled-sync.example.com",
            status="running",
            commercial_status="cancelled",
            next_billing_at=subscription.next_billing_at,
        )
        db.session.add(local)
        db.session.commit()

        monkeypatch.setattr(
            "worker.list_customer_apps",
            lambda _customer_id: [
                {
                    "id": subscription.instance_id,
                    "technical_status": "running",
                    "commercial_status": "active",
                    "storage_state": "ok",
                }
            ],
        )

        actions, errors = _sync_remote_instances(app)

        assert (actions, errors) == (1, 0)
        assert local.status == "running"
        assert local.commercial_status == "cancelled"
