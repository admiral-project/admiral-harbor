# SPDX-FileCopyrightText: William Moreno Reyes <williamjmorenor@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""
Admiral Harbor reconciliation worker.

Runs periodically (systemd timer or cron) to:
- Enforce payment policy: pause/deprovision past-due subscriptions
- Sync instance state from admirald
- Log audit trail

Usage:
    python worker.py
"""

import logging
import smtplib
import ssl
from datetime import UTC, datetime, timedelta
from email.message import EmailMessage

from sqlalchemy import delete, text

from app import create_app
from app.admiral_client import (
    AdmiralAPIError,
    get_operation,
    list_customer_apps,
)
from app.admiral_client import (
    action as admiral_action,
)
from app.extensions import db
from app.models import (
    Customer,
    CustomerApp,
    HarborMeta,
    Invoice,
    RateLimit,
    RestoreRequest,
    Subscription,
    WorkerLog,
)

logging.basicConfig(
    level=logging.INFO,
    format="[worker] %(levelname)s %(message)s",
)
log = logging.getLogger("worker")


def _try_acquire_worker_lock():
    """Acquire the process-wide PostgreSQL lock used by the worker timer."""
    if db.engine.dialect.name != "postgresql":
        return True
    return bool(db.session.execute(text("SELECT pg_try_advisory_lock(hashtext('admiral_harbor_worker'))")).scalar())


def _release_worker_lock():
    if db.engine.dialect.name == "postgresql":
        db.session.execute(text("SELECT pg_advisory_unlock(hashtext('admiral_harbor_worker'))"))


def _run_worker_step(name, step):
    """Run one reconciliation step without blocking later steps on failure."""
    try:
        return step()
    except Exception:  # pragma: no cover - exercised through worker integration
        log.exception("Worker step failed: %s", name)
        return 0, 1


def _generate_invoices(app):
    from app.paypal import PayPalError
    from app.paypal import get_subscription as paypal_get_sub

    actions = 0
    errors = 0
    today = datetime.now(UTC).date()

    due = (
        db.session.query(Subscription)
        .filter(
            Subscription.status == "active",
            ~Subscription.is_test_app,
            Subscription.next_billing_at.isnot(None),
        )
        .all()
    )

    for sub in due:
        try:
            next_date = datetime.fromisoformat(sub.next_billing_at).date()
        except (ValueError, TypeError):
            continue
        if next_date > today:
            continue

        # For PayPal subscriptions, verify still active remotely
        if sub.paypal_subscription_id:
            try:
                remote = paypal_get_sub(sub.paypal_subscription_id)
                remote_status = remote.get("status", "")
                if remote_status in ("CANCELLED", "SUSPENDED", "EXPIRED"):
                    sub.status = "past_due"
                    db.session.commit()
                    log.info(
                        "Subscription %s: PayPal %s, marking past_due",
                        sub.external_id,
                        remote_status,
                    )
                    actions += 1
                    continue
            except PayPalError as exc:
                log.warning("Subscription %s: PayPal check failed: %s", sub.external_id, exc)
                errors += 1
                continue

        period_start = sub.next_billing_at
        existing = (
            db.session.query(Invoice)
            .filter_by(subscription_external_id=sub.external_id, period_start=period_start)
            .one_or_none()
        )
        if existing is not None:
            sub.next_billing_at = existing.period_end
            continue

        invoice = Invoice(
            subscription_external_id=sub.external_id,
            customer_email=sub.customer_email,
            app_slug=sub.app_slug,
            tier_name=sub.tier_name,
            subtotal_cents=sub.monthly_price_cents,
            tax_percent=sub.tax_percent,
            tax_cents=sub.tax_cents,
            fiscal_adjustment_cents=sub.fiscal_adjustment_cents,
            total_cents=sub.total_cents,
            fiscal_country_code=sub.fiscal_country_code,
            fiscal_snapshot_json=sub.fiscal_snapshot_json,
            # A subscription status is not proof that this billing period was
            # paid. The PayPal webhook/reconciliation path is authoritative.
            status="pending",
            period_start=period_start,
            period_end=(datetime.now(UTC) + timedelta(days=30)).date().isoformat(),
        )
        db.session.add(invoice)
        sub.next_billing_at = (datetime.now(UTC) + timedelta(days=30)).date().isoformat()
        actions += 1
        log.info(
            "Invoice %s generated for subscription %s",
            invoice.invoice_id,
            sub.external_id,
        )

    db.session.commit()
    return actions, errors


def _cleanup_rate_limits(app):
    """Bound rate-limit state without removing active windows."""
    cutoff = datetime.now(UTC).timestamp() - 3600
    removed = db.session.execute(delete(RateLimit).where(RateLimit.window_start < cutoff)).rowcount or 0
    db.session.commit()
    return removed, 0


def _enforce_payment_policy(app):
    overdue_suspend_days = app.config["HARBOR_OVERDUE_SUSPEND_AFTER_DAYS"]
    overdue_deprovision_days = app.config["HARBOR_OVERDUE_DEPROVISION_AFTER_DAYS"]
    now = datetime.now(UTC)
    actions = 0
    errors = 0

    past_due = (
        db.session.query(Subscription)
        .filter(
            Subscription.status == "past_due",
            ~Subscription.is_test_app,
            Subscription.instance_id.isnot(None),
        )
        .all()
    )

    for sub in past_due:
        if not sub.next_billing_at:
            continue
        try:
            next_billing = datetime.fromisoformat(sub.next_billing_at)
            if not isinstance(next_billing, datetime):
                next_billing = datetime(next_billing.year, next_billing.month, next_billing.day)
            overdue_days = (now - next_billing).days
        except (ValueError, TypeError):
            continue

        instance_id = (sub.instance_id or "").strip()
        if not instance_id:
            log.warning("Subscription %s has no instance_id, skipping", sub.external_id)
            continue
        if not instance_id.startswith("inst_"):
            log.warning(
                "Subscription %s has invalid instance_id format: %s, skipping",
                sub.external_id,
                instance_id,
            )
            continue

        if overdue_days >= overdue_deprovision_days:
            log.info(
                "Deprovisioning instance %s (subscription %s)",
                instance_id,
                sub.external_id,
            )
            try:
                admiral_action(instance_id, "deprovision")
                # Mark as suspended, not cancelled: the customer did not request
                # cancellation — the app was removed due to non-payment and can
                # be reactivated once payment is restored.
                sub.status = "suspended"
                actions += 1
            except AdmiralAPIError as exc:
                log.error("Deprovision failed for %s: %s", instance_id, exc)
                errors += 1

        elif overdue_days >= overdue_suspend_days:
            log.info(
                "Pausing instance %s (subscription %s)",
                instance_id,
                sub.external_id,
            )
            try:
                admiral_action(instance_id, "pause")
                actions += 1
            except AdmiralAPIError as exc:
                log.error("Pause failed for %s: %s", instance_id, exc)
                errors += 1

    db.session.commit()
    return actions, errors


def _reconcile_paypal_subscriptions(app):
    from app.paypal import PayPalError
    from app.paypal import get_subscription as paypal_get_sub

    actions = 0
    errors = 0
    active_subs = (
        db.session.query(Subscription)
        .filter(
            Subscription.paypal_subscription_id.isnot(None),
            ~Subscription.is_test_app,
            Subscription.status.in_(["active", "past_due"]),
        )
        .all()
    )

    for sub in active_subs:
        try:
            remote = paypal_get_sub(sub.paypal_subscription_id)
        except PayPalError as exc:
            log.warning("PayPal fetch failed for %s: %s", sub.paypal_subscription_id, exc)
            errors += 1
            continue

        paypal_status = remote.get("status", "")
        if paypal_status == "SUSPENDED" and sub.status == "active":
            log.info(
                "PayPal subscription %s suspended, marking past_due",
                sub.paypal_subscription_id,
            )
            sub.status = "past_due"
            actions += 1
        elif paypal_status == "CANCELLED" and sub.status != "cancelled":
            log.info(
                "PayPal subscription %s cancelled, updating local",
                sub.paypal_subscription_id,
            )
            sub.status = "cancelled"
            actions += 1
        elif paypal_status in ("ACTIVE", "APPROVED") and sub.status == "past_due":
            log.info(
                "PayPal subscription %s reactivated, restoring active",
                sub.paypal_subscription_id,
            )
            sub.status = "active"
            actions += 1

    db.session.commit()
    return actions, errors


ALERT_COOLDOWN_HOURS = 24


def _storage_alert_key(customer_email, instance_id):
    return f"storage_alert_sent_at:{customer_email}:{instance_id}"


def _needs_storage_alert(customer_email, instance_id, new_status):
    storage_warning_states = {"warning", "critical", "over_quota", "grace_period"}
    if new_status not in storage_warning_states:
        return False
    key = _storage_alert_key(customer_email, instance_id)
    last_sent = HarborMeta.get(key)
    if last_sent:
        try:
            elapsed = datetime.now(UTC) - datetime.fromisoformat(last_sent)
            if elapsed.total_seconds() < ALERT_COOLDOWN_HOURS * 3600:
                return False
        except (ValueError, TypeError):
            pass
    return True


def _send_storage_alert(app, customer_email, instance_id, new_status, customer_name=""):
    host = app.config.get("HARBOR_SMTP_HOST", "")
    port = app.config.get("HARBOR_SMTP_PORT", 587)
    username = app.config.get("HARBOR_SMTP_USERNAME", "")
    password = app.config.get("HARBOR_SMTP_PASSWORD", "")
    smtp_from = app.config.get("HARBOR_SMTP_FROM", "")
    use_tls = app.config.get("HARBOR_SMTP_USE_TLS", False)
    use_ssl = app.config.get("HARBOR_SMTP_USE_SSL", False)

    if not host or not smtp_from:
        log.warning("SMTP not configured: cannot send storage alert for %s", customer_email)
        return False

    status_labels = {
        "warning": "warning threshold",
        "critical": "critical threshold",
        "over_quota": "quota exceeded",
        "grace_period": "grace period started",
    }
    label = status_labels.get(new_status, new_status)

    subject = f"[Admiral] Storage {label} for instance {instance_id}"
    body = (
        f"Hello{' ' + customer_name if customer_name else ''},\n\n"
        f"This is an automated notification from Admiral.\n\n"
        f"Your instance {instance_id} has reached the storage {label}.\n"
        f"Current storage state: {new_status}\n\n"
        f"Please review your storage usage and consider upgrading your plan or "
        f"cleaning up unused data to avoid service disruption.\n\n"
        f"If the quota is exceeded for an extended period, the instance may be "
        f"automatically paused.\n\n"
        f"Regards,\nAdmiral Platform"
    )

    try:
        msg = EmailMessage()
        msg["From"] = smtp_from
        msg["To"] = customer_email
        msg["Subject"] = subject
        msg.set_content(body)

        smtp_factory = smtplib.SMTP_SSL if use_ssl else smtplib.SMTP
        with smtp_factory(host, port, timeout=10) as smtp:
            if use_tls and not use_ssl:
                smtp.starttls(context=ssl.create_default_context())
            if username and password:
                smtp.login(username, password)
            smtp.send_message(msg)

        key = _storage_alert_key(customer_email, instance_id)
        HarborMeta.set(key, datetime.now(UTC).isoformat())
        log.info(
            "Storage alert sent to %s for instance %s (state=%s)",
            customer_email,
            instance_id,
            new_status,
        )
        return True
    except Exception as exc:
        log.error("Failed to send storage alert to %s: %s", customer_email, exc)
        return False


def _sync_remote_instances(app):
    actions = 0
    errors = 0

    customers = db.session.query(Customer).all()
    for customer in customers:
        try:
            items = list_customer_apps(customer.public_id)
        except AdmiralAPIError as exc:
            log.error("Sync failed for customer %s: %s", customer.public_id, exc)
            errors += 1
            continue

        for item in items:
            local = db.session.query(CustomerApp).filter_by(instance_id=item["id"]).one_or_none()
            if local is None:
                continue
            old_storage = local.storage_status
            new_storage = item.get("storage_state", old_storage)
            local.status = item.get("technical_status", local.status)
            local.commercial_status = item.get("commercial_status", local.commercial_status)
            local.storage_status = new_storage
            local.setup_timeout_seconds = item.get("setup_timeout_seconds", local.setup_timeout_seconds)
            actions += 1

            if new_storage != old_storage and _needs_storage_alert(customer.email, item["id"], new_storage):
                _send_storage_alert(
                    app,
                    customer.email,
                    item["id"],
                    new_storage,
                    customer_name=customer.display_name,
                )

    db.session.commit()
    return actions, errors


def _reconcile_operations(app):
    actions = 0
    errors = 0

    pending = (
        db.session.query(RestoreRequest)
        .filter(
            RestoreRequest.operation_id.isnot(None),
            RestoreRequest.status.in_(["queued", "pending"]),
        )
        .all()
    )

    for req in pending:
        try:
            op = get_operation(req.operation_id)
        except AdmiralAPIError as exc:
            log.warning("Operation fetch failed for %s: %s", req.operation_id, exc)
            errors += 1
            continue

        op_status = op.get("status", "")
        if op_status == "succeeded":
            req.status = "completed"
            local = db.session.query(CustomerApp).filter_by(instance_id=req.instance_id).one_or_none()
            if local:
                local.backup_status = "ok"
            actions += 1
            log.info("Restore %s completed (op %s)", req.request_id, req.operation_id)
        elif op_status == "failed":
            req.status = "failed"
            local = db.session.query(CustomerApp).filter_by(instance_id=req.instance_id).one_or_none()
            if local:
                local.backup_status = "failed"
            actions += 1
            log.warning("Restore %s failed (op %s)", req.request_id, req.operation_id)

    db.session.commit()
    return actions, errors


def _reconcile_setup_failed(app):
    """Cancel PayPal subscription and refund last payment for instances
    whose setup_command failed.

    When admirald marks an instance as setup_failed it also sets
    commercial_status to 'cancelled'. This function finds subscriptions
    still active (or past_due) whose local CustomerApp has
    commercial_status 'cancelled' and technical_status 'setup_failed',
    cancels the PayPal subscription, and issues a full refund for the
    last captured payment.

    This is a critical business flow: the customer paid for the first
    month, the app failed to initialize, and we must not keep the money.
    """
    from app.paypal import (
        PayPalError,
    )
    from app.paypal import (
        cancel_subscription as paypal_cancel,
    )
    from app.paypal import (
        refund_last_sale as paypal_refund,
    )

    actions = 0
    errors = 0

    apps = (
        db.session.query(CustomerApp)
        .filter(
            CustomerApp.commercial_status == "cancelled",
            CustomerApp.status == "setup_failed",
        )
        .all()
    )

    for local_app in apps:
        subscription = db.session.query(Subscription).filter_by(instance_id=local_app.instance_id).one_or_none()
        if subscription is None:
            continue
        if subscription.status in ("cancelled", "suspended"):
            continue
        if not subscription.paypal_subscription_id:
            continue

        log.info(
            "setup_failed for instance %s: cancelling PayPal subscription %s and refunding last payment",
            local_app.instance_id,
            subscription.paypal_subscription_id,
        )

        refund_id = None
        try:
            refund_id = paypal_refund(subscription.paypal_subscription_id)
        except PayPalError as exc:
            log.error(
                "Refund failed for subscription %s: %s",
                subscription.paypal_subscription_id,
                exc,
            )
            errors += 1

        try:
            paypal_cancel(
                subscription.paypal_subscription_id,
                reason="Setup command failed, application could not be initialized",
            )
        except PayPalError as exc:
            log.error(
                "Cancel failed for subscription %s: %s",
                subscription.paypal_subscription_id,
                exc,
            )
            errors += 1

        subscription.status = "cancelled"
        actions += 1

        db.session.add(
            WorkerLog(
                started_at=datetime.now(UTC),
                completed_at=datetime.now(UTC),
                actions_taken=1,
                errors=errors,
                summary=(f"setup_failed refund+cancel for instance {local_app.instance_id} (refund={refund_id})"),
            )
        )

    db.session.commit()
    return actions, errors


def _last_worker_run_at():
    val = HarborMeta.get("last_worker_run_at")
    if val:
        try:
            return datetime.fromisoformat(val)
        except (ValueError, TypeError):
            return None
    return None


def main():
    app = create_app()
    with app.app_context():
        if not _try_acquire_worker_lock():
            log.info("Another Harbor worker is running; skipping this invocation")
            return
        try:
            _run_worker(app)
        finally:
            _release_worker_lock()
            db.session.commit()


def _run_worker(app):
    started_at = datetime.now(UTC)
    log.info("Worker started at %s", started_at.isoformat())

    total_actions = 0
    total_errors = 0

    ca, ce = _run_worker_step("cleanup_rate_limits", lambda: _cleanup_rate_limits(app))
    total_actions += ca
    total_errors += ce

    gi, ge = _run_worker_step("generate_invoices", lambda: _generate_invoices(app))
    total_actions += gi
    total_errors += ge

    pa, pe = _run_worker_step("enforce_payment_policy", lambda: _enforce_payment_policy(app))
    total_actions += pa
    total_errors += pe

    ra, re = _run_worker_step(
        "reconcile_paypal_subscriptions",
        lambda: _reconcile_paypal_subscriptions(app),
    )
    total_actions += ra
    total_errors += re

    oa, oe = _run_worker_step("reconcile_operations", lambda: _reconcile_operations(app))
    total_actions += oa
    total_errors += oe

    sa, se = _run_worker_step("sync_remote_instances", lambda: _sync_remote_instances(app))
    total_actions += sa
    total_errors += se

    sfa, sfe = _run_worker_step(
        "reconcile_setup_failed",
        lambda: _reconcile_setup_failed(app),
    )
    total_actions += sfa
    total_errors += sfe

    HarborMeta.set("last_worker_run_at", datetime.now(UTC).isoformat())

    summary = f"Actions: {total_actions}, Errors: {total_errors}"
    db.session.add(
        WorkerLog(
            started_at=started_at,
            completed_at=datetime.now(UTC),
            actions_taken=total_actions,
            errors=total_errors,
            summary=summary,
        )
    )
    db.session.commit()

    log.info("Worker finished: %s", summary)


if __name__ == "__main__":
    main()
