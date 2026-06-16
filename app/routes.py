# SPDX-FileCopyrightText: William Moreno Reyes <williamjmorenor@gmail.com>
# SPDX-License-Identifier: Apache-2.0

from datetime import datetime, timedelta
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
from werkzeug.utils import secure_filename

from app import admiral_client
from app.admiral_client import AdmiralAPIError
from app.branding import get_tax_rates
from app.config import overdue_policy
from app.paypal import (
    PayPalError,
    create_subscription,
    get_subscription,
    verify_webhook_signature,
)
from app.extensions import db
from app.identity import current_customer, login_required
from app.models import (
    AppCourse,
    AppCourseTierDiscount,
    AuditLog,
    BillingEvent,
    CatalogApp,
    CatalogAppTier,
    Customer,
    CustomerApp,
    InstanceEvent,
    Invoice,
    LMSSettings,
    Order,
    Payment,
    RestoreRequest,
    Subscription,
    SupportIncident,
    UploadedBackup,
    compute_sha256,
)

bp = Blueprint("main", __name__)


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


OPERATIONAL_STATUSES = {
    "pending_provision": "Provisioning",
    "provisioning": "Provisioning",
    "running": "Running",
    "stopped": "Paused",
    "paused": "Paused",
    "backup_running": "Backup pending",
    "restoring": "Restore in progress",
    "deprovisioning": "Cancelling",
    "deprovisioned": "Cancelled",
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
    "backup_running": "attention",
    "restoring": "attention",
    "warning": "attention",
    "critical": "danger",
    "over_quota": "danger",
    "suspended": "danger",
    "failed": "danger",
    "deprovisioning": "danger",
    "deprovisioned": "danger",
    "cancelled": "danger",
}


def _local_catalog():
    return (
        db.session.query(CatalogApp)
        .filter_by(catalog_enabled=True)
        .order_by(CatalogApp.sort_order.asc(), CatalogApp.name.asc())
        .all()
    )


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


def _sync_remote_instances(customer):
    items = []
    try:
        items = admiral_client.list_customer_apps(customer.public_id)
    except AdmiralAPIError:
        return []
    synced = []
    for item in items:
        subscription = (
            db.session.query(Subscription)
            .filter_by(instance_id=item["id"])
            .one_or_none()
        )
        if subscription is None:
            continue
        app = (
            db.session.query(CustomerApp)
            .filter_by(instance_id=item["id"])
            .one_or_none()
        )
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
        app.storage_status = item.get("storage_state", app.storage_status)
        app.tier_name = item.get("tier_name", app.tier_name)
        app.domain = item.get("hostname", app.domain)
        subscription.instance_id = item["id"]
        synced.append(item)
    db.session.commit()
    return synced


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


def _local_app(slug):
    return db.session.query(CatalogApp).filter_by(upstream_app_id=slug).one_or_none()


def _course_price(course_id, tier_name):
    course = db.session.get(AppCourse, course_id)
    if course is None:
        return None
    price = course.base_price_cents
    discount = (
        db.session.query(AppCourseTierDiscount)
        .filter_by(app_course_id=course.id, tier_name=tier_name)
        .one_or_none()
    )
    if discount is not None:
        price = max(0, int(price - (price * discount.discount_percent / 100)))
    return price


def _tax_percent(country):
    return get_tax_rates().get((country or "").upper(), 0)


def _provision_from_order(order):
    customer = db.session.query(Customer).filter_by(email=order.customer_email).one()
    subscription = Subscription(
        customer_email=order.customer_email,
        app_slug=order.app_slug,
        status="paid",
        monthly_price_cents=order.monthly_price_cents,
        tier_name=order.tier_name,
        requires_billing=order.requires_billing,
        next_billing_at=order.next_billing_at
        or (datetime.utcnow() + timedelta(days=30)).date().isoformat(),
        billing_email=order.billing_email or order.customer_email,
        tax_percent=order.tax_percent,
        paypal_subscription_id=order.paypal_subscription_id,
        paypal_plan_id=order.paypal_plan_id,
    )
    db.session.add(subscription)
    db.session.flush()

    response = admiral_client.provision_app(
        order.app_slug, order.tier_name, customer.public_id
    )
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


def _provision_subscription(subscription):
    customer = (
        db.session.query(Customer).filter_by(email=subscription.customer_email).one()
    )
    response = admiral_client.provision_app(
        subscription.app_slug, subscription.tier_name, customer.public_id
    )
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
            customer_email=subscription.customer_email,
            instance_id=instance_id,
            app_slug=subscription.app_slug,
            domain=hostname,
            status="provisioning",
            backup_status="pending",
            storage_status="ok",
            tier_name=subscription.tier_name,
        )
    )
    subscription.instance_id = instance_id
    subscription.status = "active"
    db.session.commit()
    _event(
        instance_id,
        subscription.customer_email,
        "payment_received",
        f"Payment confirmed for {subscription.app_slug}.",
    )
    return instance_id, credentials


@bp.route("/")
def index():
    apps = [app.as_dict() for app in _local_catalog()]
    return render_template(
        "index.html",
        apps=apps,
        overdue_policy=overdue_policy(current_app.config),
        customer=current_customer(),
    )


@bp.route("/catalog")
def catalog():
    apps = [app.as_dict() for app in _local_catalog()]
    return render_template(
        "index.html",
        apps=apps,
        overdue_policy=overdue_policy(current_app.config),
        customer=current_customer(),
    )


@bp.route("/apps/")
def apps_index():
    return redirect(url_for("main.catalog"))


@bp.route("/health")
def health():
    return jsonify({"status": "healthy"})


@bp.route("/branding/<kind>")
def portal_asset(kind):
    if kind not in {"logo", "favicon"}:
        return jsonify({"error": "asset not found"}), 404
    filename = current_app.config["HARBOR_UPLOAD_DIR"]
    asset_name = f"portal-{kind}"
    branding_dir = Path(filename) / "branding"
    stored = None
    for candidate in branding_dir.glob(f"{asset_name}.*"):
        stored = candidate
        break
    if stored is None:
        fallback = "favicon.ico" if kind == "favicon" else "admiral-harbor.png"
        stored = Path(current_app.static_folder) / "img" / fallback
    response = send_file(stored)
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    return response


@bp.route("/catalog-assets/<slug>/<filename>")
def catalog_asset(slug, filename):
    slug = secure_filename((slug or "").strip())
    filename = secure_filename((filename or "").strip())
    if not slug or not filename:
        return jsonify({"error": "asset not found"}), 404
    catalog_dir = Path(current_app.config["HARBOR_UPLOAD_DIR"]) / "catalog" / slug
    stored = catalog_dir / filename
    if not stored.exists() or not stored.is_file():
        return jsonify({"error": "asset not found"}), 404
    response = send_file(stored)
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    return response


@bp.route("/dashboard")
@login_required
def dashboard():
    customer = current_customer()
    _sync_remote_instances(customer)
    subscriptions = [sub.as_dict() for sub in _customer_subscriptions(customer)]
    customer_apps = [item.as_dict() for item in _customer_instances(customer)]
    monthly_total = sum(
        sub["monthly_price_cents"]
        for sub in subscriptions
        if sub["status"] != "cancelled"
    )
    return render_template(
        "dashboard.html",
        subscriptions=subscriptions,
        customer_apps=customer_apps,
        monthly_total_cents=monthly_total,
        overdue_policy=overdue_policy(current_app.config),
        customer=customer,
    )


@bp.route("/apps/<slug>")
def app_detail(slug):
    app = _local_app(slug)
    if app is None or not app.catalog_enabled:
        return jsonify({"error": "app not found"}), 404
    remote = {}
    try:
        remote = admiral_client.get_app(slug)
    except AdmiralAPIError:
        remote = {"tiers": []}
    settings = LMSSettings.singleton()
    courses = (
        db.session.query(AppCourse)
        .filter_by(app_slug=slug, active=True)
        .order_by(AppCourse.course_code.asc())
        .all()
    )
    return render_template(
        "app_detail.html",
        app=app.as_dict(),
        remote=remote,
        lms_enabled=settings.enabled,
        courses=courses,
        customer=current_customer(),
    )


@bp.route("/apps/<slug>/deploy", methods=["POST"])
@login_required
def deploy_app(slug):
    customer = current_customer()
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
            .filter_by(
                customer_email=customer.email,
                app_slug=slug,
            )
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

    tax_pct = _tax_percent(customer.country)
    total_cents = tier["price_monthly_cents"] + int(
        tier["price_monthly_cents"] * tax_pct / 100
    )
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
    if (
        requires_billing
        and current_app.config.get("HARBOR_PAYPAL_MODE", "mock") != "mock"
        and not paypal_plan_id
    ):
        flash("PayPal plan is not configured for this tier.", "error")
        return redirect(url_for("main.app_detail", slug=slug))

    order = Order(
        customer_email=customer.email,
        app_slug=slug,
        tier_name=tier_name,
        monthly_price_cents=tier["price_monthly_cents"],
        tax_percent=tax_pct,
        tax_cents=int(tier["price_monthly_cents"] * tax_pct / 100),
        total_cents=total_cents,
        requires_billing=requires_billing,
        next_billing_at=(datetime.utcnow() + timedelta(days=30)).date().isoformat(),
        billing_email=customer.email,
    )
    db.session.add(order)
    db.session.commit()
    _audit(
        customer.email,
        "order_created",
        "order",
        order.order_id,
        f"Order for {slug} ({tier_name}) — ${total_cents/100:.2f}",
    )

    if requires_billing:
        try:
            plan_id = paypal_plan_id or f"{slug}:{tier_name}"
            return_url = url_for(
                "main.billing_return", order_id=order.order_id, _external=True
            )
            cancel_url = url_for(
                "main.billing_cancel", order_id=order.order_id, _external=True
            )
            paypal_sub = create_subscription(
                plan_id, return_url, cancel_url, custom_id=order.order_id
            )
            order.paypal_subscription_id = paypal_sub["id"]
            order.paypal_plan_id = plan_id
            db.session.commit()
            approval_link = next(
                (
                    link["href"]
                    for link in paypal_sub.get("links", [])
                    if link["rel"] == "approve"
                ),
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
        return redirect(url_for("main.billing"))

    try:
        from app.catalog_service import validate_before_provisioning

        # Validate app and tier availability in real-time
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
    return redirect(url_for("main.provision_success", instance_id=instance_id))


@bp.route("/billing")
@login_required
def billing():
    customer = current_customer()
    orders = (
        db.session.query(Order)
        .filter_by(customer_email=customer.email)
        .order_by(Order.created_at.desc())
        .all()
    )
    subscriptions = _customer_subscriptions(customer)
    invoices = (
        db.session.query(Invoice)
        .filter_by(customer_email=customer.email)
        .order_by(Invoice.created_at.desc())
        .all()
    )
    return render_template(
        "billing.html",
        orders=orders,
        subscriptions=subscriptions,
        invoices=invoices,
        customer=customer,
    )


@bp.route("/billing/return", methods=["GET"])
@login_required
def billing_return():
    customer = current_customer()
    order_id = request.args.get("order_id", "")
    token = request.args.get("token", "").strip()
    order = (
        db.session.query(Order)
        .filter_by(order_id=order_id, customer_email=customer.email)
        .one_or_none()
    )
    if order is None:
        flash("Order not found.", "error")
        return redirect(url_for("main.billing"))
    if order.status == "paid":
        return redirect(
            url_for(
                "main.billing_receipt", invoice_id=order.subscription_external_id or ""
            )
        )
    if order.paypal_subscription_id and token and token != order.paypal_subscription_id:
        flash("PayPal return token does not match the order.", "error")
        return redirect(url_for("main.billing"))
    if token and not order.paypal_subscription_id:
        order.paypal_subscription_id = token
        db.session.commit()

    try:
        paypal_sub = get_subscription(order.paypal_subscription_id)
    except PayPalError as exc:
        flash(f"PayPal verification failed: {exc}", "error")
        return redirect(url_for("main.billing"))

    if paypal_sub.get("status") in ("ACTIVE", "APPROVED"):
        if current_app.config.get("HARBOR_PAYPAL_MODE", "mock") == "mock":
            try:
                instance_id, subscription, credentials = _provision_from_order(order)
            except AdmiralAPIError as exc:
                order.status = "failed"
                db.session.commit()
                flash(f"Provisioning failed: {exc}", "error")
                return redirect(url_for("main.billing"))

            tax_cents = int(order.monthly_price_cents * order.tax_percent / 100)
            total_cents = order.monthly_price_cents + tax_cents
            invoice = Invoice(
                subscription_external_id=subscription.external_id,
                customer_email=customer.email,
                app_slug=order.app_slug,
                tier_name=order.tier_name,
                subtotal_cents=order.monthly_price_cents,
                tax_percent=order.tax_percent,
                tax_cents=tax_cents,
                total_cents=total_cents,
                status="paid",
                paypal_transaction_id=order.paypal_subscription_id,
                period_start=order.next_billing_at,
                period_end=(datetime.utcnow() + timedelta(days=30)).date().isoformat(),
            )
            db.session.add(invoice)
            payment = Payment(
                order_id=order.order_id,
                subscription_external_id=subscription.external_id,
                customer_email=customer.email,
                provider="paypal",
                provider_reference=order.paypal_subscription_id,
                amount_cents=total_cents,
                status="completed",
            )
            db.session.add(payment)
            db.session.commit()
            _audit(
                customer.email,
                "payment_completed",
                "order",
                order.order_id,
                f"Payment confirmed for {order.app_slug} ({order.tier_name}) — ${total_cents/100:.2f}",
            )
            session["provision_credentials"] = credentials if credentials else []
            session["provision_instance_id"] = instance_id
            return redirect(url_for("main.provision_success", instance_id=instance_id))

        order.status = "approved"
        db.session.commit()
        flash("PayPal subscription approved. Waiting for webhook confirmation.", "info")
        return redirect(url_for("main.billing"))

    flash("PayPal payment not completed.", "error")
    return redirect(url_for("main.dashboard"))


@bp.route("/billing/cancel")
@login_required
def billing_cancel():
    customer = current_customer()
    order_id = request.args.get("order_id", "")
    order = (
        db.session.query(Order)
        .filter_by(order_id=order_id, customer_email=customer.email)
        .one_or_none()
    )
    if order is None:
        flash("Order not found.", "error")
        return redirect(url_for("main.billing"))
    order.status = "cancelled"
    db.session.commit()
    flash("PayPal checkout cancelled.", "info")
    return redirect(url_for("main.billing"))


@bp.route("/billing/receipt/<invoice_id>")
@login_required
def billing_receipt(invoice_id):
    customer = current_customer()
    invoice = (
        db.session.query(Invoice)
        .filter_by(invoice_id=invoice_id, customer_email=customer.email)
        .one_or_none()
    )
    if invoice is None:
        flash("Invoice not found.", "error")
        return redirect(url_for("main.billing"))
    subscription = (
        db.session.query(Subscription)
        .filter_by(external_id=invoice.subscription_external_id)
        .one_or_none()
    )
    return render_template(
        "receipt.html", invoice=invoice, subscription=subscription, customer=customer
    )


@bp.route("/billing/webhooks/paypal", methods=["POST"])
def paypal_webhook():
    if not verify_webhook_signature(request.headers, request.get_data(as_text=True)):
        return jsonify({"error": "webhook signature verification failed"}), 403

    event = request.get_json(silent=True) or {}
    event_id = event.get("id", "")
    event_type = event.get("event_type", "unknown")
    resource = event.get("resource") or {}
    subscription_id = (
        resource.get("billing_agreement_id")
        or resource.get("subscription_id")
        or resource.get("id")
    )
    if not event_id or not subscription_id:
        return jsonify({"error": "invalid webhook payload"}), 400
    if (
        db.session.query(BillingEvent).filter_by(event_id=event_id).one_or_none()
        is not None
    ):
        return jsonify({"status": "duplicate"}), 200
    subscription = (
        db.session.query(Subscription)
        .filter_by(paypal_subscription_id=subscription_id)
        .one_or_none()
    )
    if subscription is None:
        return jsonify({"error": "subscription not found"}), 404
    order = (
        db.session.query(Order)
        .filter_by(paypal_subscription_id=subscription_id)
        .one_or_none()
    )
    status = subscription.status
    if event_type in {"BILLING.SUBSCRIPTION.ACTIVATED", "PAYMENT.SALE.COMPLETED"}:
        status = "active"
    elif event_type in {"PAYMENT.SALE.DENIED", "BILLING.SUBSCRIPTION.SUSPENDED"}:
        status = "past_due"
    elif event_type in {
        "BILLING.SUBSCRIPTION.CANCELLED",
        "BILLING.SUBSCRIPTION.EXPIRED",
    }:
        status = "cancelled"
    subscription.status = status
    if status == "active" and not subscription.instance_id:
        try:
            _provision_subscription(subscription)
        except AdmiralAPIError as exc:
            subscription.status = "suspended"
            db.session.add(
                BillingEvent(
                    event_id=event_id,
                    subscription_external_id=subscription.external_id,
                    event_type=event_type,
                    status="failed_provision",
                    payload_json=admiral_client.dump_json(
                        {"event": event, "error": str(exc)}
                    ),
                )
            )
            db.session.commit()
            return jsonify({"status": "failed_provision", "error": str(exc)}), 502
        if order is not None and not order.subscription_external_id:
            order.subscription_external_id = subscription.external_id
            if order.status == "pending_payment":
                order.status = "approved"

    if event_type == "PAYMENT.SALE.COMPLETED":
        tax_cents = int(
            subscription.monthly_price_cents * subscription.tax_percent / 100
        )
        total_cents = subscription.monthly_price_cents + tax_cents
        existing = (
            db.session.query(Invoice).filter_by(paypal_event_id=event_id).one_or_none()
        )
        if existing is None:
            invoice = Invoice(
                subscription_external_id=subscription.external_id,
                customer_email=subscription.customer_email,
                app_slug=subscription.app_slug,
                tier_name=subscription.tier_name,
                subtotal_cents=subscription.monthly_price_cents,
                tax_percent=subscription.tax_percent,
                tax_cents=tax_cents,
                total_cents=total_cents,
                status="paid",
                paypal_transaction_id=resource.get("id", ""),
                paypal_event_id=event_id,
                period_start=(datetime.utcnow() - timedelta(days=30))
                .date()
                .isoformat(),
                period_end=datetime.utcnow().date().isoformat(),
            )
            db.session.add(invoice)
            db.session.add(
                Payment(
                    order_id=(
                        order.order_id
                        if order is not None
                        else subscription.external_id
                    ),
                    subscription_external_id=subscription.external_id,
                    customer_email=subscription.customer_email,
                    provider="paypal",
                    provider_reference=resource.get("id", ""),
                    amount_cents=total_cents,
                    status="completed",
                )
            )
        subscription.next_billing_at = (
            (datetime.utcnow() + timedelta(days=30)).date().isoformat()
        )
        if order is not None:
            order.status = "paid"
            if not order.subscription_external_id:
                order.subscription_external_id = subscription.external_id
    db.session.add(
        BillingEvent(
            event_id=event_id,
            subscription_external_id=subscription.external_id,
            event_type=event_type,
            status=status,
            payload_json=admiral_client.dump_json(event),
        )
    )
    db.session.commit()
    if event_type == "PAYMENT.SALE.COMPLETED":
        _audit(
            subscription.customer_email,
            "billing_payment_received",
            "subscription",
            subscription.external_id,
            f"Payment received for {subscription.app_slug} ({subscription.tier_name})",
        )
    elif event_type in ("BILLING.SUBSCRIPTION.ACTIVATED",):
        _audit(
            subscription.customer_email,
            "subscription_activated",
            "subscription",
            subscription.external_id,
            f"Subscription activated for {subscription.app_slug}",
        )
    elif event_type in (
        "BILLING.SUBSCRIPTION.CANCELLED",
        "BILLING.SUBSCRIPTION.EXPIRED",
    ):
        _audit(
            subscription.customer_email,
            "subscription_cancelled",
            "subscription",
            subscription.external_id,
            f"Subscription {event_type.split('.')[-1].lower()} for {subscription.app_slug}",
        )
    return jsonify({"status": status})


@bp.route("/instances/<instance_id>")
@login_required
def instance_detail(instance_id):
    customer = current_customer()
    instance = (
        db.session.query(CustomerApp)
        .filter_by(instance_id=instance_id, customer_email=customer.email)
        .one_or_none()
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
        instance.storage_status = remote_state.get(
            "storage_state", instance.storage_status
        )
        db.session.commit()
    except AdmiralAPIError:
        remote_state = None
    return render_template(
        "instance_detail.html",
        instance=instance.as_dict(),
        app=app.as_dict() if app else None,
        backups=backups,
        uploaded_backups=uploaded,
        events=events,
        incidents=incidents,
        remote_state=remote_state,
        operational_label=OPERATIONAL_STATUSES.get(
            instance.status, instance.status.title()
        ),
        storage_label=STORAGE_STATUSES.get(
            instance.storage_status, instance.storage_status.title()
        ),
        backup_label=BACKUP_STATUSES.get(
            instance.backup_status, instance.backup_status.title()
        ),
        operational_tone=STATUS_TONES.get(instance.status, "attention"),
        storage_tone=STATUS_TONES.get(instance.storage_status, "attention"),
        backup_tone=STATUS_TONES.get(instance.backup_status, "attention"),
        remote_tone=STATUS_TONES.get(
            (remote_state or {}).get("technical_status", ""), "attention"
        ),
        customer=customer,
    )


@bp.route("/instances/<instance_id>/welcome")
@login_required
def provision_success(instance_id):
    customer = current_customer()
    instance = (
        db.session.query(CustomerApp)
        .filter_by(instance_id=instance_id, customer_email=customer.email)
        .one_or_none()
    )
    if instance is None:
        flash("Instance not found.", "error")
        return redirect(url_for("main.dashboard"))
    credentials = session.pop("provision_credentials", [])
    sess_id = session.pop("provision_instance_id", None)
    if sess_id != instance_id:
        # Credentials not in session; try fetching from admirald API
        try:
            credentials = admiral_client.get_instance_credentials(instance_id)
        except AdmiralAPIError:
            credentials = []
    return render_template(
        "provision_confirmation.html",
        instance=instance,
        credentials=credentials,
        customer=customer,
    )


@bp.route("/instances/<instance_id>/credentials.json")
@login_required
def instance_credentials(instance_id):
    customer = current_customer()
    instance = (
        db.session.query(CustomerApp)
        .filter_by(instance_id=instance_id, customer_email=customer.email)
        .one_or_none()
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
def instance_action(instance_id):
    customer = current_customer()
    instance = (
        db.session.query(CustomerApp)
        .filter_by(instance_id=instance_id, customer_email=customer.email)
        .one_or_none()
    )
    if instance is None:
        flash("Instance not found.", "error")
        return redirect(url_for("main.dashboard"))
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
            flash(
                f"Restart queued via operations {response['operation_id']}.", "success"
            )
        elif requested == "resize":
            if instance.status not in {"paused", "stopped"}:
                flash("Tier changes require the app to be paused first.", "error")
                return redirect(
                    url_for("main.instance_detail", instance_id=instance_id)
                )
            response = admiral_client.action(
                instance.instance_id, "resize", tier=tier_name
            )
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
            flash(
                f"Resize queued with operation {response['operation_id']}.", "success"
            )
        elif requested == "cancel":
            if confirm_text != instance.app_slug:
                flash(
                    "Cancellation confirmation text does not match the application slug.",
                    "error",
                )
                return redirect(
                    url_for("main.instance_detail", instance_id=instance_id)
                )
            response = admiral_client.action(instance.instance_id, "deprovision")
            subscription = db.session.get(Subscription, instance.subscription_id)
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
            response = admiral_client.action(
                instance.instance_id, mapped[requested], service=service
            )
            _event(
                instance.instance_id,
                customer.email,
                f"{requested}_requested",
                f"Action {requested} requested.",
            )
            flash(
                f"Action queued with operation {response['operation_id']}.", "success"
            )
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
    return redirect(url_for("main.instance_detail", instance_id=instance_id))


@bp.route("/instances/<instance_id>/backups/upload", methods=["POST"])
@login_required
def upload_backup(instance_id):
    customer = current_customer()
    instance = (
        db.session.query(CustomerApp)
        .filter_by(instance_id=instance_id, customer_email=customer.email)
        .one_or_none()
    )
    if instance is None:
        flash("Instance not found.", "error")
        return redirect(url_for("main.dashboard"))
    uploaded = request.files.get("backup_file")
    if uploaded is None or uploaded.filename == "":
        flash("Backup file is required.", "error")
        return redirect(url_for("main.instance_detail", instance_id=instance_id))
    filename = secure_filename(uploaded.filename)
    max_bytes = current_app.config["HARBOR_MAX_BACKUP_UPLOAD_BYTES"]
    if request.content_length is not None and request.content_length > max_bytes + 4096:
        flash("Backup file exceeds allowed size.", "error")
        return redirect(url_for("main.instance_detail", instance_id=instance_id))
    destination = (
        Path(current_app.config["HARBOR_UPLOAD_DIR"])
        / customer.public_id
        / instance.instance_id
    )
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
                    return redirect(
                        url_for("main.instance_detail", instance_id=instance_id)
                    )
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
    return redirect(url_for("main.instance_detail", instance_id=instance_id))


@bp.route("/uploaded-backups/<backup_id>/download")
@login_required
def download_uploaded_backup(backup_id):
    customer = current_customer()
    backup = (
        db.session.query(UploadedBackup)
        .filter_by(backup_id=backup_id, customer_email=customer.email)
        .one_or_none()
    )
    if backup is None:
        return jsonify({"error": "backup not found"}), 404
    return send_file(
        backup.stored_path, as_attachment=True, download_name=backup.original_filename
    )


@bp.route("/api/v1/backups/uploads/<backup_id>/download")
def api_download_uploaded_backup(backup_id):
    token = request.headers.get("X-Admiral-Token", "")
    if token != current_app.config["ADMIRAL_SHARED_TOKEN"]:
        return jsonify({"error": "unauthorized"}), 401
    backup = (
        db.session.query(UploadedBackup).filter_by(backup_id=backup_id).one_or_none()
    )
    if backup is None:
        return jsonify({"error": "backup not found"}), 404
    return send_file(
        backup.stored_path, as_attachment=True, download_name=backup.original_filename
    )


@bp.route("/instances/<instance_id>/restore", methods=["POST"])
@login_required
def request_restore(instance_id):
    customer = current_customer()
    instance = (
        db.session.query(CustomerApp)
        .filter_by(instance_id=instance_id, customer_email=customer.email)
        .one_or_none()
    )
    if instance is None:
        flash("Instance not found.", "error")
        return redirect(url_for("main.dashboard"))
    source_kind = request.form.get("source_kind", "uploaded")
    source_backup_id = request.form.get("source_backup_id", "").strip()
    service_name = request.form.get("service_name", "db").strip()
    confirm_text = request.form.get("confirm_text", "").strip()
    if confirm_text != instance.app_slug:
        flash("Restore confirmation text does not match the application slug.", "error")
        return redirect(url_for("main.instance_detail", instance_id=instance_id))
    if instance.status not in {"paused", "stopped"}:
        flash("Restore is only allowed when the app is paused.", "error")
        return redirect(url_for("main.instance_detail", instance_id=instance_id))

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
            return redirect(url_for("main.instance_detail", instance_id=instance_id))
        external_url = current_app.config["HARBOR_EXTERNAL_URL"]
        source = {
            "type": "https",
            "uri": f"{external_url}/api/v1/backups/uploads/{uploaded.backup_id}",
            "checksum": uploaded.checksum_sha256,
            "size_bytes": uploaded.size_bytes,
        }
    try:
        response = admiral_client.restore_backup(
            backup_id, instance.instance_id, service_name, source=source
        )
    except AdmiralAPIError as exc:
        flash(f"Restore request failed: {exc}", "error")
        return redirect(url_for("main.instance_detail", instance_id=instance_id))
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
    return redirect(url_for("main.instance_detail", instance_id=instance_id))


@bp.route("/mock-paypal/approve")
def mock_paypal_approve():
    subscription_id = request.args.get("subscription_id", "")
    return_url = request.args.get("return_url", "")
    if not return_url and not subscription_id:
        return jsonify({"error": "missing subscription_id or return_url"}), 400
    from urllib.parse import urlencode, urlparse, parse_qs, urlunparse

    parsed = list(urlparse(return_url))
    query = dict(parse_qs(parsed[4]))
    query["token"] = subscription_id
    parsed[4] = urlencode(query, doseq=True)
    return redirect(urlunparse(parsed))


@bp.route("/instances/<instance_id>/incidents", methods=["POST"])
@login_required
def create_incident(instance_id):
    customer = current_customer()
    instance = (
        db.session.query(CustomerApp)
        .filter_by(instance_id=instance_id, customer_email=customer.email)
        .one_or_none()
    )
    if instance is None:
        flash("Instance not found.", "error")
        return redirect(url_for("main.dashboard"))
    attachment = request.files.get("attachment")
    attachment_name = (
        secure_filename(attachment.filename)
        if attachment and attachment.filename
        else None
    )
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
    return redirect(url_for("main.instance_detail", instance_id=instance_id))


@bp.route("/help")
@login_required
def help_center():
    customer = current_customer()
    incidents = (
        db.session.query(SupportIncident)
        .filter_by(customer_email=customer.email)
        .order_by(SupportIncident.created_at.desc())
        .all()
    )
    return render_template("help.html", incidents=incidents, customer=customer)


@bp.route("/help/courses/<int:course_id>/enroll", methods=["POST"])
@login_required
def enroll_course(course_id):
    customer = current_customer()
    course = db.session.get(AppCourse, course_id)
    if course is None:
        flash("Course not found.", "error")
        return redirect(url_for("main.help_center"))
    subscription = (
        db.session.query(Subscription)
        .filter_by(customer_email=customer.email, app_slug=course.app_slug)
        .order_by(Subscription.created_at.desc())
        .first()
    )
    if subscription is None or subscription.status != "active":
        flash("Active subscription required to enroll.", "error")
        return redirect(url_for("main.help_center"))
    price = _course_price(course.id, subscription.tier_name)
    student_email = request.form.get("student_email", "").strip().lower()
    if not student_email:
        flash("Student email is required.", "error")
        return redirect(url_for("main.help_center"))
    if price > 0 and request.form.get("payment_confirmed") != "yes":
        flash("Payment confirmation required for paid course enrollment.", "error")
        return redirect(url_for("main.help_center"))
    flash(
        f"Enrollment prepared for {student_email} on {course.course_code}. Final price: ${price / 100:.2f}.",
        "success",
    )
    return redirect(url_for("main.help_center"))
