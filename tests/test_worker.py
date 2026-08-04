# SPDX-FileCopyrightText: William Moreno Reyes <williamjmorenor@gmail.com>
# SPDX-License-Identifier: Apache-2.0

from app.extensions import db
from app.models import Customer, Subscription
from worker import _reconcile_paypal_subscriptions, _run_worker_step


def test_worker_step_failure_is_reported_without_raising(caplog):
    def failed_step():
        raise RuntimeError("database unavailable")

    with caplog.at_level("ERROR", logger="worker"):
        result = _run_worker_step("reconcile_operations", failed_step)

    assert result == (0, 1)
    assert "Worker step failed: reconcile_operations" in caplog.text


def test_worker_step_returns_actions_and_errors():
    assert _run_worker_step("sync_remote_instances", lambda: (3, 2)) == (3, 2)


def test_cancelled_paypal_subscription_retries_deprovision(app, monkeypatch):
    from app.admiral_client import AdmiralAPIError

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
        )
        db.session.add_all([customer, subscription])
        db.session.commit()

        monkeypatch.setattr("app.paypal.get_subscription", lambda _subscription_id: {"status": "CANCELLED"})
        calls = []

        def fail_deprovision(*args, **kwargs):
            calls.append((args, kwargs))
            raise AdmiralAPIError("temporary outage")

        monkeypatch.setattr("worker.admiral_action", fail_deprovision)
        actions, errors = _reconcile_paypal_subscriptions(app)
        assert (actions, errors) == (0, 1)
        assert db.session.get(Subscription, subscription.id).status == "active"

        monkeypatch.setattr(
            "worker.admiral_action",
            lambda *args, **kwargs: {"operation_id": "op_cancel_retry", "status": "queued"},
        )
        actions, errors = _reconcile_paypal_subscriptions(app)
        assert (actions, errors) == (1, 0)
        assert db.session.get(Subscription, subscription.id).status == "cancelled"
        assert calls == [(("inst_cancel_retry", "deprovision"), {"customer_id": "hcus_cancel_retry"})]
