# SPDX-FileCopyrightText: William Moreno Reyes <williamjmorenor@gmail.com>
# SPDX-License-Identifier: Apache-2.0

import ipaddress
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
from urllib.parse import urlencode, urlparse, parse_qs, urlunparse
from uuid import uuid4

from sqlalchemy import text

from flask import (
    Blueprint,
    current_app,
    jsonify,
    redirect,
    render_template,
    request,
    send_file,
    url_for,
)
from werkzeug.utils import secure_filename

from app import admiral_client
from app.admiral_client import AdmiralAPIError
from app.config import overdue_policy
from app.extensions import db
from app.fiscal import contract_snapshot, gate as fiscal_gate
from app.identity import current_customer
from app.models import (
    AppCourse,
    AuditLog,
    BillingEvent,
    CatalogApp,
    Customer,
    CustomerApp,
    InstanceEvent,
    Invoice,
    LMSSettings,
    Order,
    Payment,
    Subscription,
    UploadedBackup,
)
from app.paypal import verify_webhook_signature
from app.rate_limit import RateLimiter

bp = Blueprint("main", __name__)
api_token_limiter = RateLimiter(max_attempts=10, window_seconds=300)


def _webhook_transmission_is_fresh(headers):
    """Reject stale PayPal deliveries while retaining event-id idempotence."""
    from app.paypal import _is_mock

    if _is_mock():
        return True
    value = headers.get("PAYPAL-TRANSMISSION-TIME", "").strip()
    if not value:
        return False
    try:
        transmitted_at = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    max_age = int(current_app.config.get("HARBOR_PAYPAL_WEBHOOK_MAX_AGE_SECONDS", 300))
    age = (datetime.now(UTC) - transmitted_at).total_seconds()
    return -max_age <= age <= max_age


def _local_catalog():
    return (
        db.session.query(CatalogApp)
        .filter_by(catalog_enabled=True)
        .order_by(CatalogApp.sort_order.asc(), CatalogApp.name.asc())
        .all()
    )


def _local_app(slug):
    return db.session.query(CatalogApp).filter_by(upstream_app_id=slug).one_or_none()


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


def _is_allowed_ip(remote_addr):
    if not remote_addr:
        return False
    try:
        addr = ipaddress.ip_address(remote_addr)
        if addr == ipaddress.ip_address("127.0.0.1"):
            return True
        if addr == ipaddress.ip_address("::1"):
            return True
        if addr in ipaddress.ip_network("10.99.0.0/16"):
            return True
    except ValueError:
        return False
    return False


def _same_origin(url_a, url_b):
    parsed_a = urlparse(url_a)
    parsed_b = urlparse(url_b)
    return parsed_a.scheme == parsed_b.scheme and parsed_a.netloc == parsed_b.netloc and parsed_a.netloc != ""


def _provision_subscription(subscription):
    customer = db.session.query(Customer).filter_by(email=subscription.customer_email).one()
    response = admiral_client.provision_app(subscription.app_slug, subscription.tier_name, customer.public_id)
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


def _create_subscription_from_order(order):
    """Materialize the local subscription for a PayPal-created order.

    Live checkout persists the PayPal subscription ID on the order before the
    buyer leaves Harbor. The first webhook may arrive before the browser return
    and must be able to complete that order idempotently.
    """
    subscription = Subscription(
        customer_email=order.customer_email,
        app_slug=order.app_slug,
        status="pending",
        monthly_price_cents=order.monthly_price_cents,
        tier_name=order.tier_name,
        requires_billing=order.requires_billing,
        next_billing_at=order.next_billing_at,
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
    order.subscription_external_id = subscription.external_id
    if order.status == "pending_payment":
        order.status = "approved"
    return subscription


def _sale_matches_billing_contract(resource, order, subscription):
    amount = resource.get("amount") if isinstance(resource, dict) else None
    if not isinstance(amount, dict):
        return False
    value = amount.get("total", amount.get("value"))
    currency = str(amount.get("currency", amount.get("currency_code", ""))).upper()
    expected_cents = order.total_cents if order is not None else subscription.total_cents
    expected_currency = (order.currency if order is not None else "USD").upper()
    try:
        paid_amount = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return False
    expected_amount = Decimal(expected_cents) / Decimal(100)
    return paid_amount == expected_amount and currency == expected_currency


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
    if not _is_allowed_ip(request.remote_addr):
        return jsonify({"status": "forbidden"}), 403
    return jsonify({"status": "healthy"})


@bp.route("/ready")
def ready():
    if not _is_allowed_ip(request.remote_addr):
        return jsonify({"status": "forbidden"}), 403

    timestamp = datetime.now(UTC).isoformat()
    result = {
        "status": "ok",
        "database": "ok",
        "admirald": "ok",
        "timestamp": timestamp,
    }
    status_code = 200
    errors = []

    try:
        db.session.execute(text("SELECT 1"))
    except Exception as exc:
        result["database"] = "error"
        current_app.logger.warning("readiness check: database error", extra={"error": str(exc)})
        errors.append("database: unavailable")

    try:
        admiral_client._request("GET", "/api/v1/status", timeout=10)
    except Exception as exc:
        result["admirald"] = "error"
        current_app.logger.warning("readiness check: admirald error", extra={"error": str(exc)})
        errors.append("admirald: unavailable")

    if errors:
        result["status"] = "degraded"
        result["error"] = "; ".join(errors)
        status_code = 503

    return jsonify(result), status_code


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
    customer = current_customer()
    gate = fiscal_gate(customer) if customer is not None else None
    courses = (
        db.session.query(AppCourse).filter_by(app_slug=slug, active=True).order_by(AppCourse.course_code.asc()).all()
    )
    tier_quotes = {}
    if customer is not None:
        for tier in remote.get("tiers", []):
            tier_quotes[tier["name"]] = contract_snapshot(
                customer,
                tier.get("price_monthly_cents", 0),
            )
    return render_template(
        "app_detail.html",
        app=app.as_dict(),
        remote=remote,
        lms_enabled=settings.enabled,
        courses=courses,
        customer=customer,
        fiscal_gate=gate,
        tier_quotes=tier_quotes,
    )


@bp.route("/billing/webhooks/paypal", methods=["POST"])
def paypal_webhook():
    if not _webhook_transmission_is_fresh(request.headers):
        return jsonify({"error": "stale or invalid webhook transmission time"}), 400
    if not verify_webhook_signature(request.headers, request.get_data(as_text=True)):
        return jsonify({"error": "webhook signature verification failed"}), 403

    event = request.get_json(silent=True) or {}
    event_id = event.get("id", "")
    event_type = event.get("event_type", "unknown")
    resource = event.get("resource") or {}
    subscription_id = resource.get("billing_agreement_id") or resource.get("subscription_id") or resource.get("id")
    if not event_id or not subscription_id:
        return jsonify({"error": "invalid webhook payload"}), 400

    # Lock the order before looking up the subscription. The browser return
    # path takes the same lock, so return + webhook cannot both provision an
    # instance for one checkout.
    order = db.session.query(Order).filter_by(paypal_subscription_id=subscription_id).with_for_update().one_or_none()
    subscription = (
        db.session.query(Subscription).filter_by(paypal_subscription_id=subscription_id).with_for_update().one_or_none()
    )
    if subscription is None:
        if order is None:
            return jsonify({"error": "subscription not found"}), 404
        subscription = _create_subscription_from_order(order)

    if event_type == "PAYMENT.SALE.COMPLETED" and not _sale_matches_billing_contract(resource, order, subscription):
        db.session.rollback()
        return jsonify({"error": "payment amount or currency does not match billing contract"}), 409

    if db.session.query(BillingEvent).filter_by(event_id=event_id).one_or_none() is not None:
        db.session.commit()
        return jsonify({"status": "duplicate"}), 200

    status = subscription.status
    if event_type in {"BILLING.SUBSCRIPTION.ACTIVATED", "PAYMENT.SALE.COMPLETED"}:
        status = "active"
    elif event_type in {
        "PAYMENT.SALE.DENIED",
        "BILLING.SUBSCRIPTION.SUSPENDED",
        "PAYMENT.SALE.REFUNDED",
        "PAYMENT.SALE.REVERSED",
        "CUSTOMER.DISPUTE.CREATED",
    }:
        status = "past_due"
    elif event_type in {
        "BILLING.SUBSCRIPTION.CANCELLED",
        "BILLING.SUBSCRIPTION.EXPIRED",
    }:
        status = "cancelled"
    subscription.status = status
    if event_type == "PAYMENT.SALE.COMPLETED" and not subscription.instance_id:
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
                    payload_json=admiral_client.dump_json({"event": event, "error": str(exc)}),
                )
            )
            db.session.commit()
            return jsonify({"status": "failed_provision", "error": str(exc)}), 502
        if order is not None and not order.subscription_external_id:
            order.subscription_external_id = subscription.external_id
            if order.status == "pending_payment":
                order.status = "approved"

    if event_type == "PAYMENT.SALE.COMPLETED":
        existing = db.session.query(Invoice).filter_by(paypal_event_id=event_id).one_or_none()
        if existing is None:
            invoice = Invoice(
                subscription_external_id=subscription.external_id,
                customer_email=subscription.customer_email,
                app_slug=subscription.app_slug,
                tier_name=subscription.tier_name,
                subtotal_cents=subscription.monthly_price_cents,
                tax_percent=subscription.tax_percent,
                tax_cents=subscription.tax_cents,
                fiscal_adjustment_cents=subscription.fiscal_adjustment_cents,
                total_cents=subscription.total_cents,
                fiscal_country_code=subscription.fiscal_country_code,
                fiscal_snapshot_json=subscription.fiscal_snapshot_json,
                status="paid",
                paypal_transaction_id=resource.get("id", ""),
                paypal_event_id=event_id,
                period_start=(datetime.now(UTC) - timedelta(days=30)).date().isoformat(),
                period_end=datetime.now(UTC).date().isoformat(),
            )
            db.session.add(invoice)
            db.session.add(
                Payment(
                    order_id=(order.order_id if order is not None else subscription.external_id),
                    subscription_external_id=subscription.external_id,
                    customer_email=subscription.customer_email,
                    provider="paypal",
                    provider_reference=resource.get("id", ""),
                    amount_cents=subscription.total_cents,
                    status="completed",
                )
            )
        subscription.next_billing_at = (datetime.now(UTC) + timedelta(days=30)).date().isoformat()
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
    elif event_type in (
        "PAYMENT.SALE.REFUNDED",
        "PAYMENT.SALE.REVERSED",
        "CUSTOMER.DISPUTE.CREATED",
    ):
        _audit(
            subscription.customer_email,
            "billing_payment_reversed",
            "subscription",
            subscription.external_id,
            f"Payment reversal/dispute ({event_type}) for {subscription.app_slug}",
        )
    return jsonify({"status": status})


@bp.route("/mock-paypal/approve", methods=["GET", "POST"])
def mock_paypal_approve():
    if current_app.config.get("HARBOR_PAYPAL_MODE", "mock") != "mock":
        return jsonify({"error": "not found"}), 404

    values = request.args if request.method == "GET" else request.form
    subscription_id = values.get("subscription_id", "")
    return_url = values.get("return_url", "")
    if not return_url or not subscription_id:
        return jsonify({"error": "missing subscription_id or return_url"}), 400

    external_url = current_app.config.get("HARBOR_EXTERNAL_URL", "")
    if not external_url or not _same_origin(return_url, external_url):
        return jsonify({"error": "invalid return_url"}), 400

    if request.method == "GET":
        return render_template(
            "mock_paypal_approve.html",
            subscription_id=subscription_id,
            return_url=return_url,
        )

    parsed = list(urlparse(return_url))
    query = dict(parse_qs(parsed[4]))
    query["token"] = subscription_id
    parsed[4] = urlencode(query, doseq=True)
    return redirect(urlunparse(parsed))


@bp.route("/api/v1/backups/uploads/<backup_id>/download")
def api_download_uploaded_backup(backup_id):
    token = request.headers.get("X-Admiral-Token", "")
    ip = request.remote_addr or "unknown"
    limiter_key = f"api-token:{ip}"
    allowed, _remaining = api_token_limiter.is_allowed(limiter_key)
    if not allowed:
        return jsonify({"error": "too many authentication failures"}), 429
    if token not in (
        current_app.config.get("ADMIRAL_HARBOR_API_TOKEN", ""),
        current_app.config.get("ADMIRAL_ADMIN_TOKEN", ""),
    ):
        current_app.logger.warning(
            "uploaded backup download authentication failed",
            extra={"status": 401, "ip": ip, "backup_id": backup_id},
        )
        return jsonify({"error": "unauthorized"}), 401
    api_token_limiter.reset(limiter_key)
    backup = db.session.query(UploadedBackup).filter_by(backup_id=backup_id).one_or_none()
    if backup is None:
        return jsonify({"error": "backup not found"}), 404
    return send_file(backup.stored_path, as_attachment=True, download_name=backup.original_filename)
