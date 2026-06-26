# SPDX-FileCopyrightText: William Moreno Reyes <williamjmorenor@gmail.com>
# SPDX-License-Identifier: Apache-2.0

from datetime import UTC, datetime, timedelta
import os
import tempfile
from pathlib import Path
from uuid import uuid4

from flask import (
    Blueprint,
    current_app,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    send_file,
    session,
    url_for,
)
from sqlalchemy import func
from werkzeug.utils import secure_filename

from app import admiral_client
from app.admiral_client import AdmiralAPIError
from app.config import overdue_policy
from app.extensions import db
from app.fiscal import (
    acceptance_snapshot,
    contract_snapshot,
    gate as fiscal_gate,
)
from app.identity import current_customer, customer_required, login_required
from app.models import (
    AppCourse,
    AppCourseTierDiscount,
    AuditLog,
    CatalogApp,
    CatalogAppTier,
    Customer,
    CustomerApp,
    CustomerFiscalRequest,
    CustomerReply,
    FiscalTreatmentType,
    InstanceEvent,
    Invoice,
    Order,
    Payment,
    RestoreRequest,
    Subscription,
    SubscriptionChange,
    SupportIncident,
    UploadedBackup,
    compute_sha256,
)
from app.paypal import (
    PayPalError,
    cancel_subscription as paypal_cancel_subscription,
    create_subscription,
    get_subscription,
)

bp = Blueprint("client", __name__, url_prefix="/client")

OPERATIONAL_STATUSES = {
    "pending_provision": "Provisioning",
    "provisioning": "Provisioning",
    "initializing": "Inicializando",
    "running": "Running",
    "stopped": "Paused",
    "paused": "Paused",
    "backup_running": "Backup pending",
    "restoring": "Restore in progress",
    "deprovisioning": "Cancelling",
    "deprovisioned": "Cancelled",
    "setup_failed": "Error de inicialización",
    "failed": "Error",
}

STORAGE_STATUSES = {
    "ok": "Storage OK",
    "warning": "Storage warning",
    "critical": "Storage critical",
    "over_quota": "Storage critical",
    "grace_period": "Storage warning",
    "suspended": "Storage critical",
    "unknown": "Storage unknown",
}

BACKUP_STATUSES = {
    "ok": "Backup OK",
    "pending": "Backup pending",
    "succeeded": "Backup OK",
    "failed": "Backup failed",
    "restoring": "Restore in progress",
}

STATUS_TONES = {
    "running": "ok",
    "active": "ok",
    "succeeded": "ok",
    "ok": "ok",
    "healthy": "ok",
    "paused": "attention",
    "stopped": "attention",
    "pending": "attention",
    "pending_provision": "attention",
    "provisioning": "attention",
    "initializing": "attention",
    "backup_running": "attention",
    "restoring": "attention",
    "warning": "attention",
    "critical": "danger",
    "over_quota": "danger",
    "suspended": "danger",
    "setup_failed": "danger",
    "failed": "danger",
    "deprovisioning": "danger",
    "deprovisioned": "danger",
    "cancelled": "danger",
}


def _customer_subscriptions(customer):
    return (
        db.session.query(Subscription)
        .filter_by(customer_email=customer.email)
        .order_by(Subscription.created_at.desc())
        .all()
    )


def _customer_instances(customer):
    return (
        db.session.query(CustomerApp)
        .filter_by(customer_email=customer.email)
        .order_by(CustomerApp.created_at.desc())
        .all()
    )


def _sync_remote_instances(customer):
    items = []
    try:
        items = admiral_client.list_customer_apps(customer.public_id)
    except AdmiralAPIError:
        return []
    synced = []
    for item in items:
        subscription = db.session.query(Subscription).filter_by(instance_id=item["id"]).one_or_none()
        if subscription is None:
            continue
        app = db.session.query(CustomerApp).filter_by(instance_id=item["id"]).one_or_none()
        if app is None:
            app = CustomerApp(
                subscription_id=subscription.id,
                customer_email=customer.email,
                instance_id=item["id"],
                app_slug=item["app_definition_name"],
                domain=item.get("hostname", item["id"]),
            )
            db.session.add(app)
        app.status = item.get("technical_status", app.status)
        app.commercial_status = item.get("commercial_status", app.commercial_status)
        app.storage_status = item.get("storage_state", app.storage_status)
        app.tier_name = item.get("tier_name", app.tier_name)
        app.domain = item.get("hostname", app.domain)
        subscription.instance_id = item["id"]
        synced.append(item)
    db.session.commit()
    return synced


def _local_catalog():
    return (
        db.session.query(CatalogApp)
        .filter_by(catalog_enabled=True)
        .order_by(CatalogApp.sort_order.asc(), CatalogApp.name.asc())
        .all()
    )


def _local_app(slug):
    return db.session.query(CatalogApp).filter_by(upstream_app_id=slug).one_or_none()


def _course_price(course_id, tier_name):
    course = db.session.get(AppCourse, course_id)
    if course is None:
        return None
    price = course.base_price_cents
    discount = (
        db.session.query(AppCourseTierDiscount).filter_by(app_course_id=course.id, tier_name=tier_name).one_or_none()
    )
    if discount is not None:
        price = max(0, int(price - (price * discount.discount_percent / 100)))
    return price


def _audit(actor, action, resource_type="", resource_id="", detail=""):
    db.session.add(
        AuditLog(
            actor=actor,
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            detail=detail,
            ip_address=request.remote_addr or "",
        )
    )
    db.session.commit()


def _event(instance_id, customer_email, event_type, message):
    db.session.add(
        InstanceEvent(
            instance_id=instance_id,
            customer_email=customer_email,
            event_type=event_type,
            message=message,
        )
    )
    db.session.commit()


def _provision_from_order(order):
    customer = db.session.query(Customer).filter_by(email=order.customer_email).one()
    subscription = Subscription(
        customer_email=order.customer_email,
        app_slug=order.app_slug,
        status="paid",
        monthly_price_cents=order.monthly_price_cents,
        tier_name=order.tier_name,
        requires_billing=order.requires_billing,
        next_billing_at=order.next_billing_at or (datetime.now(UTC) + timedelta(days=30)).date().isoformat(),
        billing_email=order.billing_email or order.customer_email,
        tax_percent=order.tax_percent,
        tax_cents=order.tax_cents,
        fiscal_adjustment_cents=order.fiscal_adjustment_cents,
        total_cents=order.total_cents,
        fiscal_country_code=order.fiscal_country_code,
        fiscal_snapshot_json=order.fiscal_snapshot_json,
        paypal_subscription_id=order.paypal_subscription_id,
        paypal_plan_id=order.paypal_plan_id,
    )
    db.session.add(subscription)
    db.session.flush()

    response = admiral_client.provision_app(order.app_slug, order.tier_name, customer.public_id)
    credentials = response.get("credentials", [])
    hostname = response.get("hostname", "")
    operation_id = response.get("operation_id", "")
    instance_id = ""
    if operation_id:
        try:
            op = admiral_client.get_operation(operation_id)
            instance_id = op.get("instance_id", "")
        except AdmiralAPIError:
            pass
    if not instance_id:
        instance_id = f"inst_{uuid4().hex[:16]}"
    if not hostname:
        hostname = instance_id

    db.session.add(
        CustomerApp(
            subscription_id=subscription.id,
            customer_email=customer.email,
            instance_id=instance_id,
            app_slug=order.app_slug,
            domain=hostname,
            status="provisioning",
            backup_status="pending",
            storage_status="ok",
            tier_name=order.tier_name,
            next_billing_at=subscription.next_billing_at,
        )
    )
    subscription.instance_id = instance_id
    subscription.status = "active"
    order.subscription_external_id = subscription.external_id
    order.status = "paid"
    db.session.commit()
    _event(
        instance_id,
        customer.email,
        "payment_received",
        f"Payment confirmed for {order.app_slug}.",
    )
    return instance_id, subscription, credentials


# ── Dashboard ────────────────────────────────────────────────────────────────


@bp.route("/")
@login_required
@customer_required
def dashboard():
    customer = current_customer()
    _sync_remote_instances(customer)
    subscriptions = [sub.as_dict() for sub in _customer_subscriptions(customer)]
    customer_apps = [item.as_dict() for item in _customer_instances(customer)]
    monthly_total = sum(sub["monthly_price_cents"] for sub in subscriptions if sub["status"] != "cancelled")
    open_tickets = (
        db.session.query(SupportIncident)
        .filter_by(customer_email=customer.email)
        .filter(SupportIncident.status != "closed")
        .count()
    )
    failed_payments = db.session.query(Payment).filter_by(customer_email=customer.email, status="failed").count()
    pending_payments = db.session.query(Payment).filter_by(customer_email=customer.email, status="pending").count()
    now = datetime.now(UTC)
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    total_charged_cents = (
        db.session.query(func.sum(Payment.amount_cents))
        .filter(
            Payment.customer_email == customer.email,
            Payment.status == "completed",
            Payment.created_at >= month_start,
        )
        .scalar()
        or 0
    )
    fiscal_reminder = None
    gate = fiscal_gate(customer)
    if gate["configured"]:
        fiscal_reminder = {
            "country": gate["country_code"],
            "types": gate["available_optional_types"],
            "mandatory_accepted": gate["mandatory_accepted"],
            "pending_requests": gate["pending_requests"],
            "requires_review": gate["requires_review"],
        }
    return render_template(
        "client_dashboard.html",
        subscriptions=subscriptions,
        customer_apps=customer_apps,
        monthly_total_cents=monthly_total,
        overdue_policy=overdue_policy(current_app.config),
        open_tickets=open_tickets,
        failed_payments=failed_payments,
        pending_payments=pending_payments,
        total_charged_cents=total_charged_cents,
        fiscal_reminder=fiscal_reminder,
        fiscal_gate=gate,
        operational_labels=OPERATIONAL_STATUSES,
        operational_tones=STATUS_TONES,
    )


# ── Subscriptions ────────────────────────────────────────────────────────────


@bp.route("/subscriptions")
@login_required
@customer_required
def subscriptions_list():
    customer = current_customer()
    page = request.args.get("page", 1, type=int)
    per_page = 10
    query = db.session.query(Subscription).filter_by(customer_email=customer.email)
    status = request.args.get("status")
    if status:
        query = query.filter_by(status=status)
    paginated = query.paginate(page=page, per_page=per_page)
    return render_template(
        "client_subscriptions_list.html",
        subscriptions=paginated.items,
        paginated=paginated,
        status_filter=status,
    )


@bp.route("/subscriptions/<int:subscription_id>")
@login_required
@customer_required
def subscription_detail(subscription_id):
    customer = current_customer()
    subscription = db.session.get(Subscription, subscription_id)
    if not subscription or subscription.customer_email != customer.email:
        flash("Subscription not found", "error")
        return redirect(url_for("client.subscriptions_list"))
    payments = (
        db.session.query(Payment)
        .filter_by(
            customer_email=customer.email,
            subscription_external_id=subscription.external_id,
        )
        .order_by(Payment.created_at.desc())
        .limit(10)
        .all()
    )
    return render_template(
        "client_subscription_detail.html",
        subscription=subscription,
        payments=payments,
    )


@bp.route("/subscriptions/<int:subscription_id>/upgrade", methods=["GET"])
@login_required
@customer_required
def subscription_upgrade_page(subscription_id):
    customer = current_customer()
    subscription = db.session.get(Subscription, subscription_id)
    if not subscription or subscription.customer_email != customer.email:
        flash("Subscription not found", "error")
        return redirect(url_for("client.subscriptions_list"))
    available_tiers = ["starter", "professional", "enterprise"]
    return render_template(
        "client_subscription_upgrade.html",
        subscription=subscription,
        available_tiers=available_tiers,
    )


@bp.route("/subscriptions/<int:subscription_id>/upgrade", methods=["POST"])
@login_required
@customer_required
def subscription_upgrade(subscription_id):
    customer = current_customer()
    subscription = db.session.get(Subscription, subscription_id)
    if not subscription or subscription.customer_email != customer.email:
        flash("Subscription not found", "error")
        return redirect(url_for("client.subscriptions_list"))
    new_tier = request.form.get("new_tier", "").strip()
    if new_tier == subscription.tier_name:
        flash("Please select a different tier", "error")
        return redirect(url_for("client.subscription_upgrade_page", subscription_id=subscription_id))
    old_tier = subscription.tier_name
    old_amount = subscription.monthly_price_cents
    change = SubscriptionChange(
        subscription_id=subscription.id,
        change_type="tier_change",
        old_tier=old_tier,
        new_tier=new_tier,
        old_amount_cents=old_amount,
        new_amount_cents=subscription.monthly_price_cents,
        reason="Customer self-service tier change",
        created_by=customer.email,
    )
    db.session.add(change)
    subscription.tier_name = new_tier
    db.session.add(
        AuditLog(
            actor=customer.email,
            action="subscription_tier_changed",
            resource_type="Subscription",
            resource_id=subscription.id,
            detail=f"Tier changed from {old_tier} to {new_tier}",
            ip_address=request.remote_addr or "",
        )
    )
    db.session.commit()
    flash(
        "Subscription upgraded to {new_tier}. Changes effective next billing cycle.",
        "success",
    )
    return redirect(url_for("client.subscription_detail", subscription_id=subscription_id))


@bp.route("/subscriptions/<int:subscription_id>/cancel", methods=["GET"])
@login_required
@customer_required
def subscription_cancel_page(subscription_id):
    customer = current_customer()
    subscription = db.session.get(Subscription, subscription_id)
    if not subscription or subscription.customer_email != customer.email:
        flash("Subscription not found", "error")
        return redirect(url_for("client.subscriptions_list"))
    return render_template("client_subscription_cancel.html", subscription=subscription)


@bp.route("/subscriptions/<int:subscription_id>/cancel", methods=["POST"])
@login_required
@customer_required
def subscription_cancel(subscription_id):
    customer = current_customer()
    subscription = db.session.get(Subscription, subscription_id)
    if not subscription or subscription.customer_email != customer.email:
        flash("Subscription not found", "error")
        return redirect(url_for("client.subscriptions_list"))
    reason = request.form.get("reason", "").strip()
    confirm = request.form.get("confirm") == "on"
    if not confirm:
        flash("Please confirm cancellation", "error")
        return redirect(url_for("client.subscription_cancel_page", subscription_id=subscription_id))
    if subscription.paypal_subscription_id:
        try:
            paypal_cancel_subscription(
                subscription.paypal_subscription_id,
                reason or "Cancelled by customer",
            )
        except PayPalError as exc:
            current_app.logger.warning(
                "PayPal cancel failed for subscription %s: %s",
                subscription.paypal_subscription_id,
                exc,
            )
            flash(
                "We could not cancel the PayPal subscription. Please try again.",
                "error",
            )
            return redirect(url_for("client.subscription_cancel_page", subscription_id=subscription_id))
    subscription.status = "cancelled"
    change = SubscriptionChange(
        subscription_id=subscription.id,
        change_type="cancellation",
        old_tier=subscription.tier_name,
        reason=reason,
        created_by=customer.email,
    )
    db.session.add(change)
    db.session.add(
        AuditLog(
            actor=customer.email,
            action="subscription_cancelled",
            resource_type="Subscription",
            resource_id=subscription.id,
            detail=f"Subscription cancelled. Reason: {reason}",
            ip_address=request.remote_addr or "",
        )
    )
    db.session.commit()
    flash("Subscription cancelled successfully.", "success")
    return redirect(url_for("client.subscriptions_list"))


# ── Billing ──────────────────────────────────────────────────────────────────


@bp.route("/billing")
@login_required
@customer_required
def billing():
    customer = current_customer()
    orders = db.session.query(Order).filter_by(customer_email=customer.email).order_by(Order.created_at.desc()).all()
    subscriptions = _customer_subscriptions(customer)
    invoices = (
        db.session.query(Invoice).filter_by(customer_email=customer.email).order_by(Invoice.created_at.desc()).all()
    )
    return render_template(
        "client_billing.html",
        orders=orders,
        subscriptions=subscriptions,
        invoices=invoices,
    )


@bp.route("/billing/return")
@login_required
@customer_required
def billing_return():
    customer = current_customer()
    order_id = request.args.get("order_id", "")
    token = request.args.get("token", "").strip()
    order = db.session.query(Order).filter_by(order_id=order_id, customer_email=customer.email).one_or_none()
    if order is None:
        flash("Order not found.", "error")
        return redirect(url_for("client.billing"))
    if order.status == "paid":
        return redirect(
            url_for(
                "client.billing_receipt",
                invoice_id=order.subscription_external_id or "",
            )
        )
    if order.paypal_subscription_id and token and token != order.paypal_subscription_id:
        flash("PayPal return token does not match the order.", "error")
        return redirect(url_for("client.billing"))
    if token and not order.paypal_subscription_id:
        order.paypal_subscription_id = token
        db.session.commit()

    try:
        paypal_sub = get_subscription(order.paypal_subscription_id)
    except PayPalError as exc:
        flash(f"PayPal verification failed: {exc}", "error")
        return redirect(url_for("client.billing"))

    if paypal_sub.get("status") in ("ACTIVE", "APPROVED"):
        if current_app.config.get("HARBOR_PAYPAL_MODE", "mock") == "mock":
            try:
                instance_id, subscription, credentials = _provision_from_order(order)
            except AdmiralAPIError as exc:
                order.status = "failed"
                db.session.commit()
                flash(f"Provisioning failed: {exc}", "error")
                return redirect(url_for("client.billing"))

            invoice = Invoice(
                subscription_external_id=subscription.external_id,
                customer_email=customer.email,
                app_slug=order.app_slug,
                tier_name=order.tier_name,
                subtotal_cents=order.monthly_price_cents,
                tax_percent=order.tax_percent,
                tax_cents=order.tax_cents,
                fiscal_adjustment_cents=order.fiscal_adjustment_cents,
                total_cents=order.total_cents,
                fiscal_country_code=order.fiscal_country_code,
                fiscal_snapshot_json=order.fiscal_snapshot_json,
                status="paid",
                paypal_transaction_id=order.paypal_subscription_id,
                period_start=order.next_billing_at,
                period_end=(datetime.now(UTC) + timedelta(days=30)).date().isoformat(),
            )
            db.session.add(invoice)
            payment = Payment(
                order_id=order.order_id,
                subscription_external_id=subscription.external_id,
                customer_email=customer.email,
                provider="paypal",
                provider_reference=order.paypal_subscription_id,
                amount_cents=order.total_cents,
                status="completed",
            )
            db.session.add(payment)
            db.session.commit()
            _audit(
                customer.email,
                "payment_completed",
                "order",
                order.order_id,
                f"Payment confirmed for {order.app_slug} ({order.tier_name}) — ${order.total_cents / 100:.2f}",
            )
            session["provision_credentials"] = credentials if credentials else []
            session["provision_instance_id"] = instance_id
            return redirect(url_for("client.provision_success", instance_id=instance_id))

        order.status = "approved"
        db.session.commit()
        flash("PayPal subscription approved. Waiting for webhook confirmation.", "info")
        return redirect(url_for("client.billing"))

    flash("PayPal payment not completed.", "error")
    return redirect(url_for("client.dashboard"))


@bp.route("/billing/cancel")
@login_required
@customer_required
def billing_cancel():
    customer = current_customer()
    order_id = request.args.get("order_id", "")
    order = db.session.query(Order).filter_by(order_id=order_id, customer_email=customer.email).one_or_none()
    if order is None:
        flash("Order not found.", "error")
        return redirect(url_for("client.billing"))
    order.status = "cancelled"
    db.session.commit()
    flash("PayPal checkout cancelled.", "info")
    return redirect(url_for("client.billing"))


@bp.route("/billing/receipt/<invoice_id>")
@login_required
@customer_required
def billing_receipt(invoice_id):
    customer = current_customer()
    invoice = db.session.query(Invoice).filter_by(invoice_id=invoice_id, customer_email=customer.email).one_or_none()
    if invoice is None:
        flash("Invoice not found.", "error")
        return redirect(url_for("client.billing"))
    subscription = db.session.query(Subscription).filter_by(external_id=invoice.subscription_external_id).one_or_none()
    return render_template("client_receipt.html", invoice=invoice, subscription=subscription)


# ── Deploy ───────────────────────────────────────────────────────────────────


@bp.route("/apps/<slug>/deploy", methods=["POST"])
@login_required
@customer_required
def deploy_app(slug):
    customer = current_customer()
    gate = fiscal_gate(customer)
    if gate["configured"]:
        if not gate["mandatory_accepted"]:
            flash(
                "Review and accept the mandatory fiscal configuration before contracting apps.",
                "error",
            )
            return redirect(url_for("main.app_detail", slug=slug))
        if gate["pending_requests"]:
            flash(
                "You have fiscal requests pending review. Contracting is blocked until Harbor resolves them.",
                "error",
            )
            return redirect(url_for("main.app_detail", slug=slug))
    tier_name = request.form.get("tier_name", "").strip()
    remote = admiral_client.get_app(slug)
    tier = next((item for item in remote["tiers"] if item["name"] == tier_name), None)
    if tier is None:
        flash("Selected tier not found.", "error")
        return redirect(url_for("main.app_detail", slug=slug))

    requires_billing = not tier.get("free") and tier["price_monthly_cents"] > 0

    if not requires_billing:
        existing_free = (
            db.session.query(CustomerApp)
            .filter_by(customer_email=customer.email, app_slug=slug)
            .filter(
                CustomerApp.status.notin_(["deprovisioned", "cancelled"]),
            )
            .count()
        )
        if existing_free > 0:
            flash(
                "You already have an instance of this app. Free tier is limited to one instance per app.",
                "error",
            )
            return redirect(url_for("main.app_detail", slug=slug))

    base_cents = tier["price_monthly_cents"]
    fiscal_contract = contract_snapshot(customer, base_cents)
    local_tier = (
        db.session.query(CatalogAppTier)
        .join(CatalogApp)
        .filter(
            CatalogApp.upstream_app_id == slug,
            CatalogAppTier.upstream_tier_id == tier_name,
        )
        .one_or_none()
    )
    paypal_plan_id = local_tier.paypal_plan_id if local_tier else ""
    if requires_billing and current_app.config.get("HARBOR_PAYPAL_MODE", "mock") != "mock" and not paypal_plan_id:
        flash("PayPal plan is not configured for this tier.", "error")
        return redirect(url_for("main.app_detail", slug=slug))

    order = Order(
        customer_email=customer.email,
        app_slug=slug,
        tier_name=tier_name,
        monthly_price_cents=base_cents,
        tax_percent=fiscal_contract["tax_percent"],
        tax_cents=fiscal_contract["tax_cents"],
        fiscal_adjustment_cents=fiscal_contract["fiscal_adjustment_cents"],
        total_cents=fiscal_contract["total_cents"],
        fiscal_country_code=fiscal_contract["country_code"],
        fiscal_snapshot_json=fiscal_contract["snapshot_json"],
        requires_billing=requires_billing,
        next_billing_at=(datetime.now(UTC) + timedelta(days=30)).date().isoformat(),
        billing_email=customer.email,
    )
    db.session.add(order)
    db.session.commit()
    _audit(
        customer.email,
        "order_created",
        "order",
        order.order_id,
        f"Order for {slug} ({tier_name}) — ${order.total_cents / 100:.2f}",
    )

    if requires_billing:
        try:
            plan_id = paypal_plan_id or f"{slug}:{tier_name}"
            return_url = url_for("client.billing_return", order_id=order.order_id, _external=True)
            cancel_url = url_for("client.billing_cancel", order_id=order.order_id, _external=True)
            paypal_sub = create_subscription(
                plan_id,
                return_url,
                cancel_url,
                custom_id=order.order_id,
                amount_cents=order.total_cents,
                currency=order.currency,
            )
            order.paypal_subscription_id = paypal_sub["id"]
            order.paypal_plan_id = plan_id
            db.session.commit()
            approval_link = next(
                (link["href"] for link in paypal_sub.get("links", []) if link["rel"] == "approve"),
                None,
            )
            if approval_link:
                return redirect(approval_link)
        except PayPalError as exc:
            order.status = "failed"
            db.session.commit()
            flash(f"PayPal checkout failed: {exc}", "error")
            return redirect(url_for("main.app_detail", slug=slug))
        flash(
            "Order created. Confirm payment from Billing to provision the app.",
            "success",
        )
        return redirect(url_for("client.billing"))

    try:
        from app.catalog_service import validate_before_provisioning

        validation = validate_before_provisioning(slug, tier_name)
        if not validation["valid"]:
            flash(f"Cannot provision: {validation['message']}", "error")
            return redirect(url_for("main.app_detail", slug=slug))

        instance_id, subscription, credentials = _provision_from_order(order)
    except AdmiralAPIError as exc:
        order.status = "failed"
        db.session.commit()
        flash(f"Provisioning failed: {exc}", "error")
        return redirect(url_for("main.app_detail", slug=slug))

    _audit(
        customer.email,
        "app_provisioned",
        "instance",
        instance_id,
        f"Instance for {slug} ({tier_name}) queued for provisioning",
    )
    _event(
        instance_id,
        customer.email,
        "provision_requested",
        f"Provision requested for {slug} ({tier_name}).",
    )
    session["provision_credentials"] = credentials if credentials else []
    session["provision_instance_id"] = instance_id
    return redirect(url_for("client.provision_success", instance_id=instance_id))


@bp.route("/fiscal/accept", methods=["POST"])
@login_required
@customer_required
def accept_fiscal_terms():
    customer = current_customer()
    gate = fiscal_gate(customer)
    if not gate["configured"]:
        return redirect(request.form.get("next") or url_for("client.dashboard"))
    if request.form.get("accept_mandatory") != "on":
        flash("You must accept the mandatory fiscal configuration to continue.", "error")
        return redirect(request.form.get("next") or url_for("client.dashboard"))
    customer.fiscal_acceptance_country_code = gate["country_code"]
    customer.fiscal_acceptance_snapshot_json = acceptance_snapshot(customer.country)
    customer.fiscal_accepted_at = datetime.now(UTC)
    db.session.commit()
    _audit(
        customer.email,
        "fiscal_terms_accepted",
        "Customer",
        customer.public_id,
        f"Accepted fiscal configuration for {gate['country_code']}",
    )
    flash("Mandatory fiscal configuration accepted.", "success")
    return redirect(request.form.get("next") or url_for("client.dashboard"))


# ── Instances ────────────────────────────────────────────────────────────────


@bp.route("/instances/<instance_id>")
@login_required
@customer_required
def instance_detail(instance_id):
    customer = current_customer()
    instance = (
        db.session.query(CustomerApp).filter_by(instance_id=instance_id, customer_email=customer.email).one_or_none()
    )
    if instance is None:
        return jsonify({"error": "instance not found"}), 404
    app = _local_app(instance.app_slug)
    backups = []
    try:
        backups = admiral_client.list_backups(instance.instance_id)
    except AdmiralAPIError:
        backups = []
    uploaded = (
        db.session.query(UploadedBackup)
        .filter_by(customer_email=customer.email, app_slug=instance.app_slug)
        .order_by(UploadedBackup.created_at.desc())
        .all()
    )
    events = (
        db.session.query(InstanceEvent)
        .filter_by(instance_id=instance.instance_id, customer_email=customer.email)
        .order_by(InstanceEvent.created_at.desc())
        .all()
    )
    incidents = (
        db.session.query(SupportIncident)
        .filter_by(instance_id=instance.instance_id, customer_email=customer.email)
        .order_by(SupportIncident.created_at.desc())
        .all()
    )
    remote_state = None
    try:
        remote_state = admiral_client.get_customer_app(instance.instance_id)
        instance.status = remote_state.get("technical_status", instance.status)
        instance.commercial_status = remote_state.get("commercial_status", instance.commercial_status)
        instance.storage_status = remote_state.get("storage_state", instance.storage_status)
        db.session.commit()
    except AdmiralAPIError:
        remote_state = None
    return render_template(
        "client_instance_detail.html",
        instance=instance.as_dict(),
        app=app.as_dict() if app else None,
        backups=backups,
        uploaded_backups=uploaded,
        events=events,
        incidents=incidents,
        remote_state=remote_state,
        operational_label=OPERATIONAL_STATUSES.get(instance.status, instance.status.title()),
        storage_label=STORAGE_STATUSES.get(instance.storage_status, instance.storage_status.title()),
        backup_label=BACKUP_STATUSES.get(instance.backup_status, instance.backup_status.title()),
        operational_tone=STATUS_TONES.get(instance.status, "attention"),
        storage_tone=STATUS_TONES.get(instance.storage_status, "attention"),
        backup_tone=STATUS_TONES.get(instance.backup_status, "attention"),
        remote_tone=STATUS_TONES.get((remote_state or {}).get("technical_status", ""), "attention"),
    )


@bp.route("/instances/<instance_id>/welcome")
@login_required
@customer_required
def provision_success(instance_id):
    customer = current_customer()
    instance = (
        db.session.query(CustomerApp).filter_by(instance_id=instance_id, customer_email=customer.email).one_or_none()
    )
    if instance is None:
        flash("Instance not found.", "error")
        return redirect(url_for("client.dashboard"))
    credentials = session.pop("provision_credentials", [])
    sess_id = session.pop("provision_instance_id", None)
    if sess_id != instance_id:
        try:
            credentials = admiral_client.get_instance_credentials(instance_id)
        except AdmiralAPIError:
            credentials = []
    return render_template(
        "client_provision_confirmation.html",
        instance=instance,
        credentials=credentials,
    )


@bp.route("/instances/<instance_id>/credentials.json")
@login_required
@customer_required
def instance_credentials(instance_id):
    customer = current_customer()
    instance = (
        db.session.query(CustomerApp).filter_by(instance_id=instance_id, customer_email=customer.email).one_or_none()
    )
    if instance is None:
        return jsonify({"error": "instance not found"}), 404
    try:
        credentials = admiral_client.get_instance_credentials(instance_id)
        return jsonify(credentials)
    except AdmiralAPIError:
        return jsonify({"error": "failed to fetch credentials"}), 502


@bp.route("/instances/<instance_id>/actions", methods=["POST"])
@login_required
@customer_required
def instance_action(instance_id):
    customer = current_customer()
    instance = (
        db.session.query(CustomerApp).filter_by(instance_id=instance_id, customer_email=customer.email).one_or_none()
    )
    if instance is None:
        flash("Instance not found.", "error")
        return redirect(url_for("client.dashboard"))
    requested = request.form.get("action", "").strip()
    tier_name = request.form.get("tier_name", "").strip()
    service = request.form.get("service", "").strip() or None
    confirm_text = request.form.get("confirm_text", "").strip()
    try:
        if requested == "restart":
            admiral_client.action(instance.instance_id, "stop")
            response = admiral_client.action(instance.instance_id, "start")
            _event(
                instance.instance_id,
                customer.email,
                "restart_requested",
                "Application restart requested.",
            )
            flash(f"Restart queued via operations {response['operation_id']}.", "success")
        elif requested == "resize":
            if instance.status not in {"paused", "stopped"}:
                flash("Tier changes require the app to be paused first.", "error")
                return redirect(url_for("client.instance_detail", instance_id=instance_id))
            response = admiral_client.action(instance.instance_id, "resize", tier=tier_name)
            instance.tier_name = tier_name
            subscription = db.session.get(Subscription, instance.subscription_id)
            if subscription is not None:
                subscription.tier_name = tier_name
            _event(
                instance.instance_id,
                customer.email,
                "tier_change_requested",
                f"Tier change requested to {tier_name}.",
            )
            flash(f"Resize queued with operation {response['operation_id']}.", "success")
        elif requested == "cancel":
            if confirm_text != instance.app_slug:
                flash(
                    "Cancellation confirmation text does not match the application slug.",
                    "error",
                )
                return redirect(url_for("client.instance_detail", instance_id=instance_id))
            subscription = db.session.get(Subscription, instance.subscription_id)
            if subscription:
                if subscription.paypal_subscription_id:
                    try:
                        paypal_cancel_subscription(
                            subscription.paypal_subscription_id,
                            "Instance cancelled by customer",
                        )
                    except PayPalError as exc:
                        current_app.logger.warning(
                            "PayPal cancel failed for subscription %s: %s",
                            subscription.paypal_subscription_id,
                            exc,
                        )
                        flash(
                            "We could not cancel the PayPal subscription. Please try again.",
                            "error",
                        )
                        return redirect(url_for("client.instance_detail", instance_id=instance_id))
            response = admiral_client.action(instance.instance_id, "deprovision")
            if subscription:
                subscription.status = "cancelled"
            _event(
                instance.instance_id,
                customer.email,
                "cancel_requested",
                "Cancellation requested.",
            )
            flash(
                f"Cancellation queued with operation {response['operation_id']}.",
                "success",
            )
        else:
            mapped = {"pause": "pause", "resume": "resume", "backup": "backup"}
            response = admiral_client.action(instance.instance_id, mapped[requested], service=service)
            _event(
                instance.instance_id,
                customer.email,
                f"{requested}_requested",
                f"Action {requested} requested.",
            )
            flash(f"Action queued with operation {response['operation_id']}.", "success")
        if requested == "pause":
            instance.status = "paused"
        elif requested == "resume":
            instance.status = "running"
        elif requested == "backup":
            instance.backup_status = "pending"
        db.session.commit()
        _audit(
            customer.email,
            f"instance_{requested}",
            "instance",
            instance_id,
            f"Action {requested} on {instance.app_slug}",
        )
    except AdmiralAPIError as exc:
        flash(f"Action failed: {exc}", "error")
    return redirect(url_for("client.instance_detail", instance_id=instance_id))


@bp.route("/instances/<instance_id>/backups/upload", methods=["POST"])
@login_required
@customer_required
def upload_backup(instance_id):
    customer = current_customer()
    instance = (
        db.session.query(CustomerApp).filter_by(instance_id=instance_id, customer_email=customer.email).one_or_none()
    )
    if instance is None:
        flash("Instance not found.", "error")
        return redirect(url_for("client.dashboard"))
    uploaded = request.files.get("backup_file")
    if uploaded is None or uploaded.filename == "":
        flash("Backup file is required.", "error")
        return redirect(url_for("client.instance_detail", instance_id=instance_id))
    filename = secure_filename(uploaded.filename)
    max_bytes = current_app.config["HARBOR_MAX_BACKUP_UPLOAD_BYTES"]
    if request.content_length is not None and request.content_length > max_bytes + 4096:
        flash("Backup file exceeds allowed size.", "error")
        return redirect(url_for("client.instance_detail", instance_id=instance_id))
    destination = Path(current_app.config["HARBOR_UPLOAD_DIR"]) / customer.public_id / instance.instance_id
    destination.mkdir(parents=True, exist_ok=True)
    path = destination / filename
    temp_path = None
    size = 0
    try:
        with tempfile.NamedTemporaryFile(
            delete=False, dir=destination, prefix=f".{filename}.", suffix=".upload"
        ) as temp_file:
            temp_path = Path(temp_file.name)
            while True:
                chunk = uploaded.stream.read(1024 * 64)
                if not chunk:
                    break
                size += len(chunk)
                if size > max_bytes:
                    flash("Backup file exceeds allowed size.", "error")
                    return redirect(url_for("client.instance_detail", instance_id=instance_id))
                temp_file.write(chunk)
            temp_file.flush()
            os.fsync(temp_file.fileno())
        with temp_path.open("rb") as handle:
            checksum = compute_sha256(handle)
        temp_path.replace(path)
    finally:
        if temp_path is not None and temp_path.exists() and not path.exists():
            temp_path.unlink(missing_ok=True)
    backup = UploadedBackup(
        customer_email=customer.email,
        app_slug=instance.app_slug,
        original_filename=filename,
        stored_path=str(path),
        size_bytes=size,
        checksum_sha256=checksum,
    )
    db.session.add(backup)
    db.session.commit()
    _audit(
        customer.email,
        "backup_uploaded",
        "backup",
        backup.backup_id,
        f"Uploaded {filename} ({size} bytes) for {instance.app_slug}",
    )
    _event(
        instance.instance_id,
        customer.email,
        "backup_uploaded",
        f"External backup {filename} uploaded.",
    )
    flash("Backup uploaded and ready for restore.", "success")
    return redirect(url_for("client.instance_detail", instance_id=instance_id))


@bp.route("/uploaded-backups/<backup_id>/download")
@login_required
@customer_required
def download_uploaded_backup(backup_id):
    customer = current_customer()
    backup = (
        db.session.query(UploadedBackup).filter_by(backup_id=backup_id, customer_email=customer.email).one_or_none()
    )
    if backup is None:
        return jsonify({"error": "backup not found"}), 404
    return send_file(backup.stored_path, as_attachment=True, download_name=backup.original_filename)


@bp.route("/instances/<instance_id>/restore", methods=["POST"])
@login_required
@customer_required
def request_restore(instance_id):
    customer = current_customer()
    instance = (
        db.session.query(CustomerApp).filter_by(instance_id=instance_id, customer_email=customer.email).one_or_none()
    )
    if instance is None:
        flash("Instance not found.", "error")
        return redirect(url_for("client.dashboard"))
    source_kind = request.form.get("source_kind", "uploaded")
    source_backup_id = request.form.get("source_backup_id", "").strip()
    service_name = request.form.get("service_name", "db").strip()
    confirm_text = request.form.get("confirm_text", "").strip()
    if confirm_text != instance.app_slug:
        flash("Restore confirmation text does not match the application slug.", "error")
        return redirect(url_for("client.instance_detail", instance_id=instance_id))
    if instance.status not in {"paused", "stopped"}:
        flash("Restore is only allowed when the app is paused.", "error")
        return redirect(url_for("client.instance_detail", instance_id=instance_id))
    source = {}
    backup_id = source_backup_id
    if source_kind == "uploaded":
        uploaded = (
            db.session.query(UploadedBackup)
            .filter_by(backup_id=source_backup_id, customer_email=customer.email)
            .one_or_none()
        )
        if uploaded is None:
            flash("Uploaded backup not found.", "error")
            return redirect(url_for("client.instance_detail", instance_id=instance_id))
        external_url = current_app.config["HARBOR_EXTERNAL_URL"]
        source = {
            "type": "https",
            "uri": f"{external_url}/api/v1/backups/uploads/{uploaded.backup_id}",
            "checksum": uploaded.checksum_sha256,
            "size_bytes": uploaded.size_bytes,
        }
    try:
        response = admiral_client.restore_backup(backup_id, instance.instance_id, service_name, source=source)
    except AdmiralAPIError as exc:
        flash(f"Restore request failed: {exc}", "error")
        return redirect(url_for("client.instance_detail", instance_id=instance_id))
    restore = RestoreRequest(
        instance_id=instance.instance_id,
        customer_email=customer.email,
        source_backup_id=source_backup_id,
        source_kind=source_kind,
        service_name=service_name,
        status="queued",
        confirm_text=confirm_text,
        operation_id=response["operation_id"],
    )
    db.session.add(restore)
    instance.backup_status = "restoring"
    db.session.commit()
    _audit(
        customer.email,
        "restore_requested",
        "instance",
        instance_id,
        f"Restore from {source_kind}:{source_backup_id} for {instance.app_slug}",
    )
    _event(
        instance.instance_id,
        customer.email,
        "restore_requested",
        f"Restore requested for service {service_name}.",
    )
    flash(f"Restore queued with operation {response['operation_id']}.", "success")
    return redirect(url_for("client.instance_detail", instance_id=instance_id))


@bp.route("/instances/<instance_id>/incidents", methods=["POST"])
@login_required
@customer_required
def create_incident(instance_id):
    customer = current_customer()
    instance = (
        db.session.query(CustomerApp).filter_by(instance_id=instance_id, customer_email=customer.email).one_or_none()
    )
    if instance is None:
        flash("Instance not found.", "error")
        return redirect(url_for("client.dashboard"))
    attachment = request.files.get("attachment")
    attachment_name = secure_filename(attachment.filename) if attachment and attachment.filename else None
    incident = SupportIncident(
        instance_id=instance.instance_id,
        customer_email=customer.email,
        subject=request.form.get("subject", "").strip(),
        description=request.form.get("description", "").strip(),
        priority=request.form.get("priority", "medium").strip(),
        attachment_name=attachment_name,
    )
    db.session.add(incident)
    db.session.commit()
    _event(
        instance.instance_id,
        customer.email,
        "incident_reported",
        f"Incident reported: {incident.subject}.",
    )
    flash("Incident submitted.", "success")
    return redirect(url_for("client.instance_detail", instance_id=instance_id))


# ── Support ──────────────────────────────────────────────────────────────────


@bp.route("/support")
@login_required
@customer_required
def support_list():
    customer = current_customer()
    page = request.args.get("page", 1, type=int)
    per_page = 10
    query = db.session.query(SupportIncident).filter_by(customer_email=customer.email)
    status = request.args.get("status")
    if status:
        query = query.filter_by(status=status)
    paginated = query.order_by(SupportIncident.created_at.desc()).paginate(page=page, per_page=per_page)
    return render_template(
        "client_support_list.html",
        tickets=paginated.items,
        paginated=paginated,
        status_filter=status,
    )


@bp.route("/support/create", methods=["GET"])
@login_required
@customer_required
def support_create_page():
    subscriptions = db.session.query(Subscription).filter_by(customer_email=current_customer().email).all()
    return render_template("client_support_create.html", subscriptions=subscriptions)


@bp.route("/support/create", methods=["POST"])
@login_required
@customer_required
def support_create():
    customer = current_customer()
    subject = request.form.get("subject", "").strip()
    description = request.form.get("description", "").strip()
    priority = request.form.get("priority", "medium").strip()
    subscription_id = request.form.get("subscription_id", type=int)
    if not subject or not description:
        flash("Subject and description required", "error")
        return redirect(url_for("client.support_create_page"))
    if priority not in ["low", "medium", "high", "critical"]:
        priority = "medium"
    if subscription_id:
        sub = db.session.get(Subscription, subscription_id)
        if not sub or sub.customer_email != customer.email:
            subscription_id = None
    ticket = SupportIncident(
        customer_email=customer.email,
        subject=subject,
        description=description,
        priority=priority,
        status="open",
    )
    db.session.add(ticket)
    db.session.commit()
    db.session.add(
        AuditLog(
            actor=customer.email,
            action="ticket_created",
            resource_type="SupportIncident",
            resource_id=ticket.id,
            detail=f"Customer created ticket: {subject}",
            ip_address=request.remote_addr or "",
        )
    )
    db.session.commit()
    flash(f"Ticket #{ticket.id} created successfully", "success")
    return redirect(url_for("client.support_detail", ticket_id=ticket.id))


@bp.route("/support/<int:ticket_id>")
@login_required
@customer_required
def support_detail(ticket_id):
    customer = current_customer()
    ticket = db.session.get(SupportIncident, ticket_id)
    if not ticket or ticket.customer_email != customer.email:
        flash("Ticket not found", "error")
        return redirect(url_for("client.support_list"))
    conversation = [
        {
            "author": customer.email,
            "message": ticket.description,
            "timestamp": ticket.created_at,
            "is_internal": False,
            "is_customer": True,
        }
    ]
    replies = (
        db.session.query(CustomerReply).filter_by(ticket_id=ticket.id).order_by(CustomerReply.created_at.asc()).all()
    )
    for reply in replies:
        conversation.append(
            {
                "author": customer.email,
                "message": reply.message,
                "timestamp": reply.created_at,
                "is_internal": False,
                "is_customer": True,
            }
        )
    sla_status = None
    if ticket.response_deadline or ticket.resolution_deadline:
        now = datetime.now(UTC)
        if ticket.resolution_deadline and now > ticket.resolution_deadline:
            sla_status = {"status": "overdue", "type": "resolution"}
        elif ticket.response_deadline and now > ticket.response_deadline and not ticket.assigned_to:
            sla_status = {"status": "overdue", "type": "response"}
        else:
            if ticket.resolution_deadline:
                remaining = (ticket.resolution_deadline - now).total_seconds() / 3600
                if remaining > 0:
                    sla_status = {
                        "status": "on_track",
                        "hours_remaining": int(remaining),
                    }
    return render_template(
        "client_support_detail.html",
        ticket=ticket,
        conversation=conversation,
        sla_status=sla_status,
    )


@bp.route("/support/<int:ticket_id>/reply", methods=["POST"])
@login_required
@customer_required
def support_reply(ticket_id):
    customer = current_customer()
    ticket = db.session.get(SupportIncident, ticket_id)
    if not ticket or ticket.customer_email != customer.email:
        flash("Ticket not found", "error")
        return redirect(url_for("client.support_list"))
    message = request.form.get("message", "").strip()
    if not message:
        flash("Message cannot be empty", "error")
        return redirect(url_for("client.support_detail", ticket_id=ticket_id))
    reply = CustomerReply(ticket_id=ticket.id, customer_id=customer.id, message=message)
    db.session.add(reply)
    ticket.updated_at = datetime.now(UTC)
    db.session.add(
        AuditLog(
            actor=customer.email,
            action="ticket_reply",
            resource_type="SupportIncident",
            resource_id=ticket.id,
            detail=f"Customer replied to ticket: {message[:50]}...",
            ip_address=request.remote_addr or "",
        )
    )
    db.session.commit()
    flash("Reply sent successfully", "success")
    return redirect(url_for("client.support_detail", ticket_id=ticket_id))


# ── Profile ──────────────────────────────────────────────────────────────────


@bp.route("/profile", methods=["GET"])
@login_required
@customer_required
def profile():
    return render_template("client_profile.html")


@bp.route("/profile/edit", methods=["POST"])
@login_required
@customer_required
def profile_edit():
    customer = current_customer()
    display_name = request.form.get("display_name", "").strip()
    country = request.form.get("country", "").strip()
    if not display_name:
        flash("Display name required", "error")
        return redirect(url_for("client.profile"))
    old_data = f"name={customer.display_name}, country={customer.country}"
    customer.display_name = display_name
    customer.country = country
    db.session.add(
        AuditLog(
            actor=customer.email,
            action="profile_updated",
            resource_type="Customer",
            resource_id=customer.id,
            detail=f"Updated profile: {old_data} -> name={display_name}, country={country}",
            ip_address=request.remote_addr or "",
        )
    )
    db.session.commit()
    flash("Profile updated", "success")
    return redirect(url_for("client.profile"))


# ── Help ─────────────────────────────────────────────────────────────────────


@bp.route("/help")
@login_required
@customer_required
def help_center():
    customer = current_customer()
    incidents = (
        db.session.query(SupportIncident)
        .filter_by(customer_email=customer.email)
        .order_by(SupportIncident.created_at.desc())
        .all()
    )
    return render_template("client_help.html", incidents=incidents)


@bp.route("/help/courses/<int:course_id>/enroll", methods=["POST"])
@login_required
@customer_required
def enroll_course(course_id):
    customer = current_customer()
    course = db.session.get(AppCourse, course_id)
    if course is None:
        flash("Course not found.", "error")
        return redirect(url_for("client.help_center"))
    subscription = (
        db.session.query(Subscription)
        .filter_by(customer_email=customer.email, app_slug=course.app_slug)
        .order_by(Subscription.created_at.desc())
        .first()
    )
    if subscription is None or subscription.status != "active":
        flash("Active subscription required to enroll.", "error")
        return redirect(url_for("client.help_center"))
    price = _course_price(course.id, subscription.tier_name)
    student_email = request.form.get("student_email", "").strip().lower()
    if not student_email:
        flash("Student email is required.", "error")
        return redirect(url_for("client.help_center"))
    if price > 0 and request.form.get("payment_confirmed") != "yes":
        flash("Payment confirmation required for paid course enrollment.", "error")
        return redirect(url_for("client.help_center"))
    flash(
        f"Enrollment prepared for {student_email} on {course.course_code}. Final price: ${price / 100:.2f}.",
        "success",
    )
    return redirect(url_for("client.help_center"))


# ── Fiscal Requests ───────────────────────────────────────────────────────────


@bp.route("/fiscal-requests")
@login_required
@customer_required
def fiscal_requests():
    customer = current_customer()
    gate = fiscal_gate(customer)
    requests_list = (
        db.session.query(CustomerFiscalRequest)
        .filter_by(customer_email=customer.email)
        .order_by(CustomerFiscalRequest.created_at.desc())
        .all()
    )
    country_code = (customer.country or "").upper()
    available_types = (
        db.session.query(FiscalTreatmentType)
        .filter_by(country_code=country_code, is_optional=True, is_active=True)
        .all()
        if country_code
        else []
    )
    active_request_type_ids = {r.treatment_type_id for r in requests_list if r.status in ("pending", "approved")}
    pending_types = [t for t in available_types if t.id not in active_request_type_ids]
    return render_template(
        "client_fiscal_requests.html",
        requests=requests_list,
        customer=customer,
        available_types=available_types,
        pending_types=pending_types,
        fiscal_gate=gate,
    )


@bp.route("/fiscal-requests/new", methods=["GET", "POST"])
@login_required
@customer_required
def fiscal_request_new():
    import os

    customer = current_customer()
    country_code = (customer.country or "").upper()
    available_types_raw = (
        db.session.query(FiscalTreatmentType)
        .filter_by(country_code=country_code, is_optional=True, is_active=True)
        .all()
        if country_code
        else []
    )
    already_active_ids = {
        r.treatment_type_id
        for r in db.session.query(CustomerFiscalRequest)
        .filter(
            CustomerFiscalRequest.customer_email == customer.email,
            CustomerFiscalRequest.status.in_(["pending", "approved"]),
        )
        .all()
    }
    available_types = [t for t in available_types_raw if t.id not in already_active_ids]

    if request.method == "POST":
        type_id_str = request.form.get("treatment_type_id", "").strip()
        if not type_id_str:
            flash("Debes seleccionar un tratamiento fiscal.", "error")
            return render_template(
                "client_fiscal_request_new.html",
                available_types=available_types,
                customer=customer,
            )
        try:
            type_id = int(type_id_str)
        except ValueError:
            flash("Tratamiento inválido.", "error")
            return redirect(url_for("client.fiscal_request_new"))

        treatment = db.session.get(FiscalTreatmentType, type_id)
        if (
            not treatment
            or treatment.country_code != country_code
            or not treatment.is_active
            or not treatment.is_optional
        ):
            flash("Tratamiento no disponible.", "error")
            return redirect(url_for("client.fiscal_request_new"))

        evidence_path = None
        evidence_name = None
        if treatment.requires_evidence:
            ev_file = request.files.get("evidence")
            if not ev_file or ev_file.filename == "":
                flash("Este tratamiento requiere evidencia documental.", "error")
                return render_template(
                    "client_fiscal_request_new.html",
                    available_types=available_types,
                    customer=customer,
                )
            upload_dir = os.path.join(
                current_app.config.get("HARBOR_UPLOAD_DIR", "/tmp/harbor-uploads"),
                "fiscal",
            )
            os.makedirs(upload_dir, exist_ok=True)
            safe_name = compute_sha256(ev_file.read())
            ev_file.stream.seek(0)
            ext = os.path.splitext(ev_file.filename)[-1].lower()
            evidence_name = ev_file.filename
            evidence_path = os.path.join(upload_dir, f"{safe_name}{ext}")
            ev_file.save(evidence_path)

        request_notes = request.form.get("request_notes", "").strip() or None
        fiscal_req = CustomerFiscalRequest(
            customer_email=customer.email,
            treatment_type_id=type_id,
            status="pending",
            evidence_path=evidence_path,
            evidence_original_name=evidence_name,
            request_notes=request_notes,
        )
        db.session.add(fiscal_req)
        _audit(
            customer.email,
            "fiscal_request_submitted",
            "CustomerFiscalRequest",
            fiscal_req.request_id,
            f"Fiscal request for {treatment.name} ({treatment.direction}{treatment.percent}%)",
        )
        db.session.commit()
        flash("Solicitud enviada. El equipo la revisará en breve.", "success")
        return redirect(url_for("client.fiscal_requests"))

    return render_template(
        "client_fiscal_request_new.html",
        available_types=available_types,
        customer=customer,
    )
