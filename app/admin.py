# SPDX-FileCopyrightText: William Moreno Reyes <williamjmorenor@gmail.com>
# SPDX-License-Identifier: Apache-2.0

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
import json
from flask import (
    Blueprint,
    abort,
    current_app,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    session,
    url_for,
    send_file,
)
from flask_login import current_user, login_user, logout_user

from datetime import UTC, datetime, timedelta
from sqlalchemy import func
import csv
import io


from app.extensions import db
from app.branding import (
    get_portal_branding,
    get_tax_rates,
    save_catalog_asset,
    set_portal_currency,
    set_portal_tos_url,
    set_tax_rates,
    update_portal_branding,
)
from app.identity import admin_required, create_user_session, clear_user_session
from app.rate_limit import RateLimiter
from app.admiral_client import (
    AdmiralAPIError,
    get_operation,
    list_apps,
    provision_app,
    get_customer_app,
    get_instance_inspect,
    list_backups,
    get_backup,
)
from app.models import (
    AppCourse,
    AppCourseTierDiscount,
    AuditLog,
    BillingEvent,
    CatalogApp,
    CatalogAppTier,
    CatalogSyncAudit,
    Customer,
    CustomerApp,
    CustomerFiscalRequest,
    FiscalTreatmentType,
    HarborAdminUser,
    HarborMeta,
    Invoice,
    LMSSettings,
    Subscription,
    SupportIncident,
    Payment,
    HarborPayPalConfig,
)
from app.countries import COUNTRIES, COUNTRY_NAMES

bp = Blueprint("admin", __name__, url_prefix="/admin")
ph = PasswordHasher()
admin_login_limiter = RateLimiter(max_attempts=5, window_seconds=60)


@bp.before_request
def _ensure_authenticated():
    if request.endpoint in (
        "admin.login_page",
        "admin.login",
        "admin.logout",
        "static",
    ):
        return None
    if session.get("customer_email"):
        abort(403)
    if not current_user.is_authenticated:
        return redirect(url_for("admin.login_page"))


def _set_admin_session(admin):
    session["admin_username"] = admin.username
    session["admin_display_name"] = admin.display_name
    create_user_session("admin", admin.username)


@bp.route("/login", methods=["GET"])
def login_page():
    return render_template("admin_login.html")


@bp.route("/login", methods=["POST"])
def login():
    username = request.form.get("username", "").strip()
    password = request.form.get("password", "")
    ip = request.remote_addr or "unknown"
    allowed, remaining = admin_login_limiter.is_allowed(f"admin-login:{ip}")
    if not allowed:
        current_app.logger.warning(
            "admin login rate limited",
            extra={"ip": ip, "remaining_seconds": remaining},
        )
        flash(
            f"Too many admin login attempts. Try again in {remaining} second(s).",
            "error",
        )
        return redirect(url_for("admin.login_page")), 429
    admin = db.session.query(HarborAdminUser).filter_by(username=username).one_or_none()
    if admin is None:
        current_app.logger.warning(
            "admin login failed",
            extra={"username": username, "reason": "user_not_found", "ip": ip},
        )
        flash("Invalid admin credentials.", "error")
        return redirect(url_for("admin.login_page"))
    try:
        ph.verify(admin.password_hash, password)
    except VerifyMismatchError:
        current_app.logger.warning(
            "admin login failed",
            extra={"username": username, "reason": "invalid_password", "ip": ip},
        )
        flash("Invalid admin credentials.", "error")
        return redirect(url_for("admin.login_page"))
    session.pop("customer_token", None)
    session.pop("customer_email", None)
    session.pop("customer_public_id", None)
    _set_admin_session(admin)
    admin_login_limiter.reset(f"admin-login:{ip}")
    login_user(admin)
    db.session.add(
        AuditLog(
            actor=username,
            action="admin_login",
            detail=f"Admin {username} logged in",
            ip_address=request.remote_addr or "",
        )
    )
    db.session.commit()
    return redirect(url_for("admin.dashboard"))


@bp.route("/logout", methods=["POST"])
def logout():
    username = current_user.username if current_user.is_authenticated else "unknown"
    db.session.add(
        AuditLog(
            actor=username,
            action="admin_logout",
            detail=f"Admin {username} logged out",
            ip_address=request.remote_addr or "",
        )
    )
    db.session.commit()
    clear_user_session()
    logout_user()
    session.clear()
    return redirect(url_for("main.index"))


def _get_admirald_status():
    """Check admirald connection and sync status."""
    try:
        apps = list_apps()
        return {
            "status": "operativa",
            "last_check": datetime.now(UTC),
            "app_count": len(apps) if apps else 0,
        }
    except Exception as e:
        return {
            "status": "no_disponible",
            "last_check": None,
            "error": str(e),
        }


def _get_platform_status():
    """Derive comprehensive platform status from multiple conditions.

    Returns dict with:
    - status: operativo, atencion_requerida, degradado, incidente_critico
    - color: #27ae60 (green), #f39c12 (orange), #e67e22 (dark orange), #e74c3c (red)
    - conditions: list of condition details
    """
    conditions = []
    severity = 0  # 0=green, 1=yellow, 2=orange, 3=red

    # Check admirald connection (most critical)
    admirald_status = _get_admirald_status()
    if admirald_status["status"] == "no_disponible":
        conditions.append(
            {
                "issue": "admirald unavailable",
                "severity": 3,
                "description": "Control plane connection lost",
            }
        )
        severity = max(severity, 3)
    else:
        conditions.append(
            {
                "issue": "admirald connected",
                "severity": 0,
                "description": f"Apps synced: {admirald_status.get('app_count', 0)}",
            }
        )

    # Check payment health
    failed_payments = db.session.query(Payment).filter_by(status="failed").count()
    failed_payments_cents = db.session.query(func.sum(Payment.amount_cents)).filter_by(status="failed").scalar() or 0

    if failed_payments >= 10:
        conditions.append(
            {
                "issue": "critical payment failures",
                "severity": 3,
                "description": f"{failed_payments} failed, ${failed_payments_cents / 100:.2f} at risk",
            }
        )
        severity = max(severity, 3)
    elif failed_payments >= 3:
        conditions.append(
            {
                "issue": "multiple payment failures",
                "severity": 2,
                "description": f"{failed_payments} failed",
            }
        )
        severity = max(severity, 2)
    elif failed_payments > 0:
        conditions.append(
            {
                "issue": "payment failures",
                "severity": 1,
                "description": f"{failed_payments} failed",
            }
        )
        severity = max(severity, 1)
    else:
        conditions.append(
            {
                "issue": "payments healthy",
                "severity": 0,
                "description": "No failed payments",
            }
        )

    # Check subscription health
    active_subs = db.session.query(Subscription).filter_by(status="active").count()
    total_subs = db.session.query(Subscription).count()
    inactive_subs = total_subs - active_subs

    if inactive_subs > total_subs * 0.3 and total_subs > 0:  # >30% inactive
        conditions.append(
            {
                "issue": "high inactivity",
                "severity": 2,
                "description": f"{inactive_subs}/{total_subs} inactive",
            }
        )
        severity = max(severity, 2)
    else:
        conditions.append(
            {
                "issue": "subscriptions healthy",
                "severity": 0,
                "description": f"{active_subs}/{total_subs} active",
            }
        )

    # Check ticket workload
    open_tickets = db.session.query(SupportIncident).filter(SupportIncident.status.in_(["open", "pending"])).count()
    high_priority_tickets = (
        db.session.query(SupportIncident)
        .filter(
            SupportIncident.priority == "high",
            SupportIncident.status.in_(["open", "pending"]),
        )
        .count()
    )

    if high_priority_tickets >= 5:
        conditions.append(
            {
                "issue": "high priority tickets backlog",
                "severity": 2,
                "description": f"{high_priority_tickets} high-priority open",
            }
        )
        severity = max(severity, 2)
    elif open_tickets > 20:
        conditions.append(
            {
                "issue": "ticket backlog",
                "severity": 1,
                "description": f"{open_tickets} open",
            }
        )
        severity = max(severity, 1)
    else:
        conditions.append(
            {
                "issue": "tickets manageable",
                "severity": 0,
                "description": f"{open_tickets} open",
            }
        )

    # Map severity to status and color
    status_map = {
        0: ("operativo", "#27ae60"),  # green
        1: ("atencion_requerida", "#f39c12"),  # orange
        2: ("degradado", "#e67e22"),  # dark orange
        3: ("incidente_critico", "#e74c3c"),  # red
    }

    status, color = status_map[severity]

    return {
        "status": status,
        "color": color,
        "severity": severity,
        "conditions": conditions,
        "last_updated": datetime.now(UTC),
    }


def _calculate_mrr():
    """Calculate Monthly Recurring Revenue from active subscriptions.

    Returns dict with current month MRR and comparison with previous month.
    """
    now = datetime.now(UTC)
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

    # Get active subscriptions with their recurring amounts
    active_subs = db.session.query(Subscription).filter_by(status="active").all()

    current_mrr_cents = 0
    for sub in active_subs:
        # Assume subscription has a tier amount in cents (monthly)
        if hasattr(sub, "tier_amount_cents") and sub.tier_amount_cents:
            current_mrr_cents += sub.tier_amount_cents

    # Compare with last month's MRR (subscriptions active on last day of prev month)
    last_month_start = month_start - timedelta(days=1)
    last_month_start = last_month_start.replace(day=1)

    # Approximate: use invoices from previous month
    last_month_revenue = (
        db.session.query(func.sum(Invoice.total_cents))
        .filter(
            Invoice.created_at >= last_month_start,
            Invoice.created_at < month_start,
            Invoice.status == "paid",
        )
        .scalar()
        or 0
    )

    # Calculate growth percentage
    growth_pct = 0
    if last_month_revenue > 0:
        growth_pct = ((current_mrr_cents - last_month_revenue) / last_month_revenue) * 100

    return {
        "current_mrr_cents": current_mrr_cents,
        "current_mrr_dollars": current_mrr_cents / 100,
        "previous_month_cents": last_month_revenue,
        "previous_month_dollars": last_month_revenue / 100,
        "growth_pct": growth_pct,
    }


def _get_recent_activity(limit=10):
    """Get consolidated recent activity across all modules.

    Returns list of dicts with timestamp, actor, action, resource info.
    """
    activity = []

    # Recent audit logs
    audit_logs = db.session.query(AuditLog).order_by(AuditLog.created_at.desc()).limit(limit).all()

    for log in audit_logs:
        activity.append(
            {
                "timestamp": log.created_at,
                "type": "audit",
                "actor": log.actor,
                "action": log.action,
                "resource_type": log.resource_type,
                "resource_id": log.resource_id,
                "detail": log.detail,
            }
        )

    # Recent tickets
    tickets = db.session.query(SupportIncident).order_by(SupportIncident.created_at.desc()).limit(5).all()

    for ticket in tickets:
        activity.append(
            {
                "timestamp": ticket.created_at,
                "type": "ticket",
                "actor": "customer",
                "action": "ticket_created",
                "resource_type": "ticket",
                "resource_id": ticket.incident_id,
                "detail": ticket.subject[:80],
            }
        )

    # Sort by timestamp descending
    activity.sort(key=lambda x: x["timestamp"], reverse=True)

    return activity[:limit]


def _export_subscriptions_csv():
    """Generate CSV export of all subscriptions."""
    output = io.StringIO()
    writer = csv.writer(output)

    writer.writerow(["ID", "Customer Email", "Status", "Tier", "Created", "Billing Email"])

    subs = db.session.query(Subscription).order_by(Subscription.created_at.desc()).all()
    for sub in subs:
        writer.writerow(
            [
                sub.external_id or "",
                sub.customer_email,
                sub.status,
                sub.tier_name or "",
                sub.created_at.strftime("%Y-%m-%d %H:%M") if sub.created_at else "",
                sub.billing_email or "",
            ]
        )

    return output.getvalue()


def _export_payments_csv():
    """Generate CSV export of all payments."""
    output = io.StringIO()
    writer = csv.writer(output)

    writer.writerow(["ID", "Subscription", "Amount", "Status", "Provider", "Created", "Updated"])

    payments = db.session.query(Payment).order_by(Payment.created_at.desc()).all()
    for payment in payments:
        writer.writerow(
            [
                payment.payment_id or "",
                payment.subscription_external_id or "",
                f"${payment.amount_cents / 100:.2f}" if payment.amount_cents else "",
                payment.status,
                payment.provider or "",
                (payment.created_at.strftime("%Y-%m-%d %H:%M") if payment.created_at else ""),
                (payment.updated_at.strftime("%Y-%m-%d %H:%M") if payment.updated_at else ""),
            ]
        )

    return output.getvalue()


def _export_tickets_csv():
    """Generate CSV export of all tickets."""
    output = io.StringIO()
    writer = csv.writer(output)

    writer.writerow(["ID", "Subject", "Customer", "Status", "Priority", "Assigned To", "Created"])

    tickets = db.session.query(SupportIncident).order_by(SupportIncident.created_at.desc()).all()
    for ticket in tickets:
        writer.writerow(
            [
                ticket.incident_id or "",
                ticket.subject or "",
                ticket.customer_email or "",
                ticket.status,
                ticket.priority,
                ticket.assigned_to or "Unassigned",
                (ticket.created_at.strftime("%Y-%m-%d %H:%M") if ticket.created_at else ""),
            ]
        )

    return output.getvalue()


def _calculate_sla_deadlines(priority="medium"):
    """Calculate response and resolution deadlines based on priority.

    SLA Levels:
    - critical: 1h response, 8h resolution
    - high: 2h response, 24h resolution
    - medium: 4h response, 72h resolution
    - low: 8h response, 5 days resolution
    """
    now = datetime.now(UTC)

    sla_config = {
        "critical": {"response_hours": 1, "resolution_hours": 8},
        "high": {"response_hours": 2, "resolution_hours": 24},
        "medium": {"response_hours": 4, "resolution_hours": 72},
        "low": {"response_hours": 8, "resolution_hours": 120},  # 5 days
    }

    config = sla_config.get(priority, sla_config["medium"])

    response_deadline = now + timedelta(hours=config["response_hours"])
    resolution_deadline = now + timedelta(hours=config["resolution_hours"])

    return {
        "response_deadline": response_deadline,
        "resolution_deadline": resolution_deadline,
        "response_hours": config["response_hours"],
        "resolution_hours": config["resolution_hours"],
    }


def _get_sla_status(ticket):
    """Get SLA status for a ticket.

    Returns dict with:
    - sla_status: compliant, warning, violated, resolved
    - time_remaining: timedelta or None
    - percent_used: 0-100
    """
    now = datetime.now(UTC)

    # If resolved, check if resolution deadline was met
    if ticket.resolved_at:
        if ticket.resolution_deadline and ticket.resolved_at > ticket.resolution_deadline:
            return {
                "sla_status": "violated",
                "detail": "Resolution SLA violated",
                "time_remaining": None,
                "percent_used": 100,
            }
        return {
            "sla_status": "resolved",
            "detail": "Completed within SLA",
            "time_remaining": None,
            "percent_used": 100,
        }

    # Check response deadline
    if ticket.response_deadline and not ticket.assigned_to:
        if now > ticket.response_deadline:
            return {
                "sla_status": "violated",
                "detail": "Response SLA violated (not assigned)",
                "time_remaining": None,
                "percent_used": 100,
            }
        time_remaining = ticket.response_deadline - now
        total_response_time = ticket.response_deadline - ticket.created_at
        percent_used = int(
            (total_response_time.total_seconds() - time_remaining.total_seconds())
            / total_response_time.total_seconds()
            * 100
        )
        if percent_used > 75:
            status = "warning"
        else:
            status = "compliant"
        return {
            "sla_status": status,
            "detail": "Response SLA pending",
            "time_remaining": time_remaining,
            "percent_used": percent_used,
        }

    # Check resolution deadline
    if ticket.resolution_deadline:
        if now > ticket.resolution_deadline:
            return {
                "sla_status": "violated",
                "detail": "Resolution SLA violated",
                "time_remaining": None,
                "percent_used": 100,
            }
        time_remaining = ticket.resolution_deadline - now
        total_resolution_time = ticket.resolution_deadline - ticket.created_at
        percent_used = int(
            (total_resolution_time.total_seconds() - time_remaining.total_seconds())
            / total_resolution_time.total_seconds()
            * 100
        )
        if percent_used > 75:
            status = "warning"
        else:
            status = "compliant"
        return {
            "sla_status": status,
            "detail": "Resolution SLA pending",
            "time_remaining": time_remaining,
            "percent_used": percent_used,
        }

    return {
        "sla_status": "unknown",
        "detail": "No SLA deadline set",
        "time_remaining": None,
        "percent_used": 0,
    }


def _format_timedelta(td):
    """Format timedelta as readable string."""
    if not td:
        return "—"

    total_seconds = int(td.total_seconds())
    hours, remainder = divmod(total_seconds, 3600)
    minutes = remainder // 60

    if hours > 0:
        return f"{hours}h {minutes}m"
    return f"{minutes}m"


@bp.route("/")
@admin_required
def dashboard():
    """Dashboard administrativo con 5 bloques funcionales."""
    now = datetime.now(UTC)
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

    # Bloque A: Estado general de la plataforma
    admirald_status = _get_admirald_status()
    last_sync_meta = HarborMeta.get("last_catalog_sync_at")
    last_sync = None
    if last_sync_meta:
        try:
            last_sync = datetime.fromisoformat(last_sync_meta)
        except (ValueError, TypeError):
            pass

    published_apps = db.session.query(CatalogApp).filter_by(catalog_enabled=True).count()

    # Bloque B: Resumen comercial mensual
    current_month_revenue = (
        db.session.query(func.sum(Invoice.total_cents))
        .filter(Invoice.created_at >= month_start, Invoice.status == "paid")
        .scalar()
        or 0
    )

    last_month_start = month_start - timedelta(days=1)
    last_month_start = last_month_start.replace(day=1)
    last_month_end = month_start - timedelta(days=1)
    last_month_revenue = (
        db.session.query(func.sum(Invoice.total_cents))
        .filter(
            Invoice.created_at >= last_month_start,
            Invoice.created_at <= last_month_end,
            Invoice.status == "paid",
        )
        .scalar()
        or 0
    )

    subscriptions_total = db.session.query(Subscription).count()
    subscriptions_active = db.session.query(Subscription).filter_by(status="active").count()
    subscriptions_new = db.session.query(Subscription).filter(Subscription.created_at >= month_start).count()
    subscriptions_cancelled = db.session.query(Subscription).filter(Subscription.status == "cancelled").count()

    pending_payments = db.session.query(func.sum(Payment.amount_cents)).filter_by(status="pending").scalar() or 0
    failed_payments = db.session.query(func.sum(Payment.amount_cents)).filter_by(status="failed").scalar() or 0

    failed_payments_count = db.session.query(Payment).filter_by(status="failed").count()

    # Bloque C: Operaciones técnicas
    instances_active = db.session.query(Subscription).filter_by(status="active").count()
    instances_paused = db.session.query(Subscription).filter_by(status="paused").count()
    instances_provisioning = db.session.query(Subscription).filter_by(status="provisioning").count()
    instances_error = db.session.query(Subscription).filter(Subscription.status.in_(["failed", "error"])).count()

    # Bloque D: Soporte
    tickets_open = db.session.query(SupportIncident).filter(SupportIncident.status.in_(["open", "pending"])).count()
    tickets_unassigned = db.session.query(SupportIncident).filter(SupportIncident.assigned_to.is_(None)).count()
    tickets_high_priority = db.session.query(SupportIncident).filter_by(priority="high").count()

    # Bloque E: Actividad reciente (últimos eventos)
    recent_events = db.session.query(AuditLog).order_by(AuditLog.created_at.desc()).limit(20).all()

    platform_status = _get_platform_status()

    return render_template(
        "admin_dashboard_new.html",
        # Bloque A
        admirald_status=admirald_status,
        last_sync=last_sync,
        published_apps=published_apps,
        # Bloque B
        current_month_revenue=current_month_revenue,
        last_month_revenue=last_month_revenue,
        subscriptions_total=subscriptions_total,
        subscriptions_active=subscriptions_active,
        subscriptions_new=subscriptions_new,
        subscriptions_cancelled=subscriptions_cancelled,
        pending_payments_total=pending_payments,
        failed_payments_total=failed_payments,
        failed_payments_count=failed_payments_count,
        # Bloque C
        instances_active=instances_active,
        instances_paused=instances_paused,
        instances_provisioning=instances_provisioning,
        instances_error=instances_error,
        # Bloque D
        tickets_open=tickets_open,
        tickets_unassigned=tickets_unassigned,
        tickets_high_priority=tickets_high_priority,
        # Bloque E
        recent_events=recent_events,
        # Status
        platform_status=platform_status,
    )


@bp.route("/subscriptions")
@admin_required
def subscriptions_list():
    status_filter = request.args.get("status", "")
    query = db.session.query(Subscription)
    if status_filter:
        query = query.filter(Subscription.status == status_filter)
    subscriptions = query.order_by(Subscription.created_at.desc()).all()
    total = len(subscriptions)
    return render_template(
        "admin_subscriptions_list.html",
        subscriptions=subscriptions,
        total=total,
        status_filter=status_filter,
    )


@bp.route("/billing")
@admin_required
def billing():
    subscriptions = db.session.query(Subscription).order_by(Subscription.created_at.desc()).all()
    invoices = db.session.query(Invoice).order_by(Invoice.created_at.desc()).all()
    events = db.session.query(BillingEvent).order_by(BillingEvent.created_at.desc()).limit(50).all()
    return render_template(
        "admin_billing.html",
        subscriptions=subscriptions,
        invoices=invoices,
        events=events,
    )


@bp.route("/billing/invoices/<invoice_id>")
@admin_required
def invoice_detail(invoice_id):
    invoice = db.session.query(Invoice).filter_by(invoice_id=invoice_id).one_or_none()
    if invoice is None:
        flash("Invoice not found.", "error")
        return redirect(url_for("admin.billing"))
    subscription = db.session.query(Subscription).filter_by(external_id=invoice.subscription_external_id).one_or_none()
    return render_template("admin_invoice.html", invoice=invoice, subscription=subscription)


@bp.route("/metrics")
@admin_required
def metrics():
    """Display MRR, historical metrics, and performance indicators."""
    now = datetime.now(UTC)
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

    # Current month MRR
    mrr_data = _calculate_mrr()

    # Revenue comparison: last 6 months
    monthly_revenue = {}
    for i in range(5, -1, -1):
        current = month_start - timedelta(days=i * 30)
        current_month = current.replace(day=1)
        next_month = (current_month + timedelta(days=32)).replace(day=1)

        revenue = (
            db.session.query(func.sum(Invoice.total_cents))
            .filter(
                Invoice.created_at >= current_month,
                Invoice.created_at < next_month,
                Invoice.status == "paid",
            )
            .scalar()
            or 0
        )

        month_key = current_month.strftime("%Y-%m")
        monthly_revenue[month_key] = revenue / 100

    # Subscription metrics
    total_subs = db.session.query(Subscription).count()
    active_subs = db.session.query(Subscription).filter_by(status="active").count()
    cancelled_month = (
        db.session.query(Subscription)
        .filter(Subscription.status == "cancelled", Subscription.created_at >= month_start)
        .count()
    )
    churn_rate = (cancelled_month / active_subs * 100) if active_subs > 0 else 0

    # Payment metrics
    failed_payments = db.session.query(Payment).filter_by(status="failed").count()
    failed_payments_total_cents = (
        db.session.query(func.sum(Payment.amount_cents)).filter_by(status="failed").scalar() or 0
    )

    # Recent activity
    recent_activity = _get_recent_activity(limit=20)

    return render_template(
        "admin_metrics.html",
        mrr_data=mrr_data,
        monthly_revenue=monthly_revenue,
        total_subs=total_subs,
        active_subs=active_subs,
        churn_rate=churn_rate,
        failed_payments_count=failed_payments,
        failed_payments_total_dollars=failed_payments_total_cents / 100,
        recent_activity=recent_activity,
    )


@bp.route("/status")
@admin_required
def status_overview():
    """Display platform status overview with traffic light indicator."""
    status_data = _get_platform_status()

    # Calculate summary metrics for context
    admirald_status = _get_admirald_status()
    total_subs = db.session.query(Subscription).count()
    active_subs = db.session.query(Subscription).filter_by(status="active").count()
    failed_payments = db.session.query(Payment).filter_by(status="failed").count()
    open_tickets = db.session.query(SupportIncident).filter(SupportIncident.status.in_(["open", "pending"])).count()

    return render_template(
        "admin_status.html",
        status=status_data,
        admirald_connected=admirald_status["status"] == "operativa",
        total_subs=total_subs,
        active_subs=active_subs,
        failed_payments=failed_payments,
        open_tickets=open_tickets,
    )


@bp.route("/export/subscriptions.csv")
@admin_required
def export_subscriptions():
    """Export all subscriptions to CSV."""
    csv_data = _export_subscriptions_csv()
    return send_file(
        io.BytesIO(csv_data.encode("utf-8")),
        mimetype="text/csv",
        as_attachment=True,
        download_name=f"subscriptions_{datetime.now(UTC).strftime('%Y%m%d_%H%M%S')}.csv",
    )


@bp.route("/export/payments.csv")
@admin_required
def export_payments():
    """Export all payments to CSV."""
    csv_data = _export_payments_csv()
    return send_file(
        io.BytesIO(csv_data.encode("utf-8")),
        mimetype="text/csv",
        as_attachment=True,
        download_name=f"payments_{datetime.now(UTC).strftime('%Y%m%d_%H%M%S')}.csv",
    )


@bp.route("/export/tickets.csv")
@admin_required
def export_tickets():
    """Export all tickets to CSV."""
    csv_data = _export_tickets_csv()
    return send_file(
        io.BytesIO(csv_data.encode("utf-8")),
        mimetype="text/csv",
        as_attachment=True,
        download_name=f"tickets_{datetime.now(UTC).strftime('%Y%m%d_%H%M%S')}.csv",
    )


@bp.route("/apps")
@admin_required
def apps_list():
    """List all apps with commercial settings."""
    page = request.args.get("page", 1, type=int)
    per_page = 20

    query = db.session.query(CatalogApp).order_by(CatalogApp.sort_order.asc(), CatalogApp.name.asc())
    total = query.count()
    apps = query.offset((page - 1) * per_page).limit(per_page).all()
    pages = max(1, (total + per_page - 1) // per_page)

    # Build tier counts per app
    app_ids = [a.id for a in apps]
    tier_counts = {}
    if app_ids:
        rows = (
            db.session.query(CatalogAppTier.catalog_app_id, db.func.count(CatalogAppTier.id))
            .filter(
                CatalogAppTier.catalog_app_id.in_(app_ids),
                CatalogAppTier.upstream_present,
            )
            .group_by(CatalogAppTier.catalog_app_id)
            .all()
        )
        tier_counts = {row[0]: row[1] for row in rows}

    return render_template(
        "admin_apps_list.html",
        apps=apps,
        page=page,
        pages=pages,
        total=total,
        app_tier_counts=tier_counts,
    )


@bp.route("/apps/<upstream_app_id>", methods=["GET", "POST"])
@admin_required
def app_content(upstream_app_id):
    app = db.session.query(CatalogApp).filter_by(upstream_app_id=upstream_app_id).one_or_none()
    if app is None:
        flash("Catalog app not found.", "error")
        return redirect(url_for("admin.apps_list"))
    if request.method == "POST":
        app.name = request.form.get("name", "").strip() or app.name
        app.one_liner = request.form.get("one_liner", "").strip() or app.one_liner
        app.description_md = request.form.get("description_md", "").strip() or app.description_md
        logo_file = request.files.get("logo_file")
        try:
            if logo_file and logo_file.filename:
                stored_name = save_catalog_asset(logo_file, app.upstream_app_id)
                app.logo_url = url_for("main.catalog_asset", slug=app.upstream_app_id, filename=stored_name)
        except ValueError as exc:
            flash(f"Logo not updated: {exc}", "error")
            return redirect(url_for("admin.app_content", upstream_app_id=upstream_app_id))
        app.homepage_url = request.form.get("homepage_url", "").strip() or None
        app.repository_url = request.form.get("repository_url", "").strip() or None
        app.documentation_url = request.form.get("documentation_url", "").strip() or None
        app.bug_tracker_url = request.form.get("bug_tracker_url", "").strip() or None
        app.support_url = request.form.get("support_url", "").strip() or None
        app.sort_order = request.form.get("sort_order", type=int, default=0)
        app.catalog_enabled = request.form.get("published") == "on"

        # Update tier commercial descriptions
        tier_ids = request.form.getlist("tier_id")
        tier_descriptions = request.form.getlist("tier_commercial_description")
        for tid, desc in zip(tier_ids, tier_descriptions):
            tier = db.session.query(CatalogAppTier).filter_by(id=int(tid), catalog_app_id=app.id).one_or_none()
            if tier:
                tier.commercial_description = desc.strip() or None
        db.session.add(
            AuditLog(
                actor=session.get("admin_username", "admin"),
                action="update_app_content",
                resource_type="app",
                resource_id=upstream_app_id,
                detail=f"Updated commercial content for app {app.name}",
                ip_address=request.remote_addr or "",
            )
        )
        db.session.commit()
        flash("Commercial app content updated.", "success")
        return redirect(url_for("admin.app_content", upstream_app_id=upstream_app_id))
    courses = (
        db.session.query(AppCourse).filter_by(app_slug=upstream_app_id).order_by(AppCourse.course_code.asc()).all()
    )
    discounts = db.session.query(AppCourseTierDiscount).all()
    tiers = (
        db.session.query(CatalogAppTier)
        .filter_by(catalog_app_id=app.id)
        .order_by(CatalogAppTier.display_order.asc(), CatalogAppTier.upstream_tier_id.asc())
        .all()
    )
    return render_template(
        "admin_app_content_new.html",
        app=app,
        courses=courses,
        discounts=discounts,
        tiers=tiers,
    )


@bp.route("/apps/<upstream_app_id>/tiers/<int:tier_id>/paypal-plan", methods=["POST"])
@admin_required
def update_tier_paypal_plan(upstream_app_id, tier_id):
    app = db.session.query(CatalogApp).filter_by(upstream_app_id=upstream_app_id).one_or_none()
    if app is None:
        flash("Catalog app not found.", "error")
        return redirect(url_for("admin.apps_list"))
    tier = db.session.query(CatalogAppTier).filter_by(id=tier_id, catalog_app_id=app.id).one_or_none()
    if tier is None:
        flash("Catalog tier not found.", "error")
        return redirect(url_for("admin.app_content", upstream_app_id=upstream_app_id))

    tier.paypal_plan_id = request.form.get("paypal_plan_id", "").strip() or None
    db.session.add(
        AuditLog(
            actor=session.get("admin_username", "admin"),
            action="update_paypal_plan_id",
            resource_type="catalog_app_tier",
            resource_id=str(tier.id),
            detail=f"Updated PayPal plan for {app.upstream_app_id}:{tier.upstream_tier_id}",
            ip_address=request.remote_addr or "",
        )
    )
    db.session.commit()
    flash("PayPal plan updated.", "success")
    return redirect(url_for("admin.app_content", upstream_app_id=upstream_app_id))


@bp.route("/lms", methods=["GET", "POST"])
@admin_required
def lms_settings():
    settings = LMSSettings.singleton()
    if request.method == "POST":
        from app.extensions import secrets as ext_secrets

        settings.base_url = request.form.get("base_url", "").strip() or None
        api_key = request.form.get("api_key", "").strip()
        if api_key:
            settings.encrypted_api_key = ext_secrets.encrypt(api_key)
        settings.enabled = request.form.get("enabled") == "on" and bool(settings.base_url)
        db.session.commit()
        flash("NOW-LMS settings updated.", "success")
        return redirect(url_for("admin.lms_settings"))
    return render_template("admin_lms.html", settings=settings)


@bp.route("/branding", methods=["GET", "POST"])
@admin_required
def portal_branding():
    settings = get_portal_branding()
    tax_rates = get_tax_rates()
    tax_rates_json = json.dumps(tax_rates, indent=2, sort_keys=True)
    if request.method == "POST":
        portal_name = request.form.get("portal_name", "").strip() or settings["portal_name"]
        portal_description = request.form.get("portal_description", "").strip() or settings["portal_description"]
        logo_file = request.files.get("portal_logo")
        favicon_file = request.files.get("portal_favicon")
        tax_rates_raw = request.form.get("tax_rates_json", "").strip()
        tos_url = request.form.get("portal_tos_url", "").strip()
        portal_currency = request.form.get("portal_currency", "").strip().upper()
        try:
            if tax_rates_raw:
                parsed_tax_rates = json.loads(tax_rates_raw)
                if not isinstance(parsed_tax_rates, dict):
                    raise ValueError("Tax rates must be a JSON object")
                set_tax_rates(parsed_tax_rates)
            update_portal_branding(
                portal_name,
                portal_description,
                logo_file=logo_file if logo_file and logo_file.filename else None,
                favicon_file=(favicon_file if favicon_file and favicon_file.filename else None),
            )
            set_portal_tos_url(tos_url)
            if portal_currency:
                set_portal_currency(portal_currency)
        except (ValueError, TypeError, json.JSONDecodeError) as exc:
            flash(f"Portal branding not updated: {exc}", "error")
            return redirect(url_for("admin.portal_branding"))
        flash("Portal branding updated.", "success")
        return redirect(url_for("admin.portal_branding"))
    return render_template(
        "admin_portal_branding.html",
        settings=settings,
        tax_rates_json=tax_rates_json,
    )


@bp.route("/apps/<slug>/courses", methods=["POST"])
@admin_required
def add_course(slug):
    course = AppCourse(
        app_slug=slug,
        course_code=request.form.get("course_code", "").strip(),
        course_type=request.form.get("course_type", "course").strip(),
        base_price_cents=int(request.form.get("base_price_cents", "0") or 0),
        active=request.form.get("active") == "on",
    )
    db.session.add(course)
    db.session.commit()
    flash("Course association created.", "success")
    return redirect(url_for("admin.app_content", upstream_app_id=slug))


@bp.route("/courses/<int:course_id>/discounts", methods=["POST"])
@admin_required
def add_course_discount(course_id):
    discount = AppCourseTierDiscount(
        app_course_id=course_id,
        tier_name=request.form.get("tier_name", "").strip(),
        discount_percent=int(request.form.get("discount_percent", "0") or 0),
    )
    db.session.add(discount)
    db.session.commit()
    flash("Tier discount created.", "success")
    course = db.session.get(AppCourse, course_id)
    return redirect(url_for("admin.app_content", upstream_app_id=course.app_slug if course else ""))


@bp.route("/audit-log")
@admin_required
def audit_log():
    page = request.args.get("page", 1, type=int)
    per_page = 50
    query = db.session.query(AuditLog).order_by(AuditLog.created_at.desc())
    total = query.count()
    entries = query.offset((page - 1) * per_page).limit(per_page).all()
    pages = max(1, (total + per_page - 1) // per_page)
    return render_template(
        "admin_audit_log_new.html",
        entries=entries,
        page=page,
        pages=pages,
    )


@bp.route("/instances/test")
@admin_required
def test_instances():
    test_subs = (
        db.session.query(Subscription).filter_by(is_test_app=True).order_by(Subscription.created_at.desc()).all()
    )
    return render_template("admin_test_instances.html", subscriptions=test_subs)


@bp.route("/instances/create", methods=["GET", "POST"])
@admin_required
def create_test_instance():
    if request.method == "POST":
        app_slug = request.form.get("app_slug", "").strip()
        tier_name = request.form.get("tier_name", "").strip()
        if not app_slug or not tier_name:
            flash("App and tier are required.", "error")
            return redirect(url_for("admin.create_test_instance"))

        sub = Subscription(
            customer_email="admin@test",
            app_slug=app_slug,
            status="active",
            tier_name=tier_name,
            monthly_price_cents=0,
            requires_billing=False,
            is_test_app=True,
        )
        db.session.add(sub)
        db.session.flush()

        domain = f"{sub.external_id}.test.qa.admiral.test"

        try:
            result = provision_app(app_slug, tier_name, customer_id="test")
            operation_id = result.get("operation_id", "")
            if not operation_id:
                raise AdmiralAPIError("No operation_id in provision response")
            op = get_operation(operation_id)
            instance_id = op.get("instance_id", "")
            if not instance_id:
                raise AdmiralAPIError("No instance_id in operation response")
        except AdmiralAPIError as exc:
            db.session.rollback()
            flash(f"Provisioning failed: {exc}", "error")
            return redirect(url_for("admin.create_test_instance"))

        customer_app = CustomerApp(
            subscription_id=sub.id,
            customer_email="admin@test",
            instance_id=instance_id,
            app_slug=app_slug,
            domain=domain,
            status="provisioning",
            tier_name=tier_name,
        )
        db.session.add(customer_app)
        sub.instance_id = instance_id
        db.session.add(
            AuditLog(
                actor=session.get("admin_username", "admin"),
                action="create_test_instance",
                detail=f"Created test instance {instance_id} for app {app_slug} tier {tier_name}",
                ip_address=request.remote_addr or "",
            )
        )
        db.session.commit()
        flash(f"Test instance {instance_id} created.", "success")
        return redirect(url_for("admin.test_instances"))

    try:
        all_apps = list_apps()
        apps = [a for a in all_apps if a.get("status", "").lower() == "active"]
    except AdmiralAPIError:
        apps = []
    return render_template("admin_create_test_instance.html", apps=apps)


@bp.route("/catalog/sync", methods=["POST"])
@admin_required
def sync_catalog():
    from app.catalog_service import sync_catalog as service_sync_catalog
    from app.models import AuditLog

    username = session.get("admin_username", "admin")
    result = service_sync_catalog(origin="manual", actor=username)

    if result["success"]:
        db.session.add(
            AuditLog(
                actor=username,
                action="sync_catalog",
                detail=f"Synced catalog: {result['synced']} new, {result['updated']} updated, {result['marked_missing']} marked missing",  # noqa: E501
                ip_address=request.remote_addr or "",
            )
        )
        db.session.commit()
        flash(
            f"Catalog synced: {result['synced']} new, {result['updated']} updated, {result['marked_missing']} marked missing.",  # noqa: E501
            "success",
        )
    else:
        db.session.add(
            AuditLog(
                actor=username,
                action="sync_catalog_failed",
                detail=f"Catalog sync failed: {result.get('error', 'Unknown error')}",
                ip_address=request.remote_addr or "",
            )
        )
        db.session.commit()
        flash(f"Catalog sync failed: {result.get('error', 'Unknown error')}", "error")

    return redirect(url_for("admin.dashboard"))


@bp.route("/catalog/sync-history")
@admin_required
def catalog_sync_history():
    """View catalog sync audit records."""
    page = request.args.get("page", 1, type=int)
    per_page = 25
    query = db.session.query(CatalogSyncAudit).order_by(CatalogSyncAudit.started_at.desc())
    total = query.count()
    syncs = query.offset((page - 1) * per_page).limit(per_page).all()
    pages = max(1, (total + per_page - 1) // per_page)
    return render_template(
        "admin_catalog_sync_history.html",
        syncs=syncs,
        page=page,
        pages=pages,
        total=total,
    )


@bp.route("/apps/<upstream_app_id>/availability", methods=["POST"])
@admin_required
def app_set_availability(upstream_app_id):
    """Toggle app availability in admirald."""
    from app.admiral_client import update_availability, AdmiralAPIError

    availability = request.form.get("availability", "available")
    reason = request.form.get("reason", "").strip()
    try:
        update_availability(upstream_app_id, availability, reason)
        db.session.add(
            AuditLog(
                actor=session.get("admin_username", "admin"),
                action="set_app_availability",
                resource_type="app",
                resource_id=upstream_app_id,
                detail=f"Set availability to {availability}: {reason or 'no reason'}",
                ip_address=request.remote_addr or "",
            )
        )
        db.session.commit()
        flash(f"App {upstream_app_id} availability set to '{availability}'.", "success")
    except AdmiralAPIError as e:
        flash(f"Failed to update availability: {e}", "error")
    return redirect(url_for("admin.app_content", upstream_app_id=upstream_app_id))


# ===== SETTINGS (DB-managed commercial config) =====


@bp.route("/settings", methods=["GET", "POST"])
@admin_required
def harbor_settings():
    """Edit commercial settings persisted in database (no shell access needed)."""
    from app.settings import (
        get_smtp_from,
        set_smtp_from,
        get_external_url,
        set_external_url,
        get_max_backup_upload_bytes,
        set_max_backup_upload_bytes,
        get_overdue_policy_version,
        set_overdue_policy_version,
        get_overdue_suspend_after_days,
        set_overdue_suspend_after_days,
        get_overdue_deprovision_after_days,
        set_overdue_deprovision_after_days,
        get_overdue_last_backup_retention_days,
        set_overdue_last_backup_retention_days,
    )

    if request.method == "POST":
        set_smtp_from(request.form.get("smtp_from", "").strip())
        set_external_url(request.form.get("external_url", "").strip())
        try:
            set_max_backup_upload_bytes(int(request.form.get("max_backup_upload_bytes", "536870912")))
        except (ValueError, TypeError):
            pass
        set_overdue_policy_version(request.form.get("overdue_policy_version", "overdue-policy-v1").strip())
        try:
            set_overdue_suspend_after_days(int(request.form.get("overdue_suspend_after_days", "5")))
            set_overdue_deprovision_after_days(int(request.form.get("overdue_deprovision_after_days", "10")))
            set_overdue_last_backup_retention_days(int(request.form.get("overdue_last_backup_retention_days", "15")))
        except (ValueError, TypeError):
            pass

        db.session.add(
            AuditLog(
                actor=session.get("admin_username", "admin"),
                action="update_harbor_settings",
                detail="Updated harbor commercial settings",
                ip_address=request.remote_addr or "",
            )
        )
        db.session.commit()
        flash("Settings saved.", "success")
        return redirect(url_for("admin.harbor_settings"))

    return render_template(
        "admin_settings.html",
        smtp_from=get_smtp_from(),
        external_url=get_external_url(),
        max_backup_upload_bytes=get_max_backup_upload_bytes(),
        overdue_policy_version=get_overdue_policy_version(),
        overdue_suspend_after_days=get_overdue_suspend_after_days(),
        overdue_deprovision_after_days=get_overdue_deprovision_after_days(),
        overdue_last_backup_retention_days=get_overdue_last_backup_retention_days(),
        overdue_policy_doc_url="https://docs.admiral.app/operations/overdue-policy",
    )


# ===== NUEVAS RUTAS FASE 1 =====


@bp.route("/integration-status")
@admin_required
def integration_status():
    """Estado de integración con admirald."""
    admirald_status = _get_admirald_status()
    last_sync_meta = HarborMeta.get("last_catalog_sync_at")
    last_sync = None
    if last_sync_meta:
        try:
            last_sync = datetime.fromisoformat(last_sync_meta)
        except (ValueError, TypeError):
            pass

    sync_history = (
        db.session.query(AuditLog).filter_by(action="sync_catalog").order_by(AuditLog.created_at.desc()).limit(20).all()
    )

    return render_template(
        "admin_integration_status.html",
        admirald_status=admirald_status,
        last_sync=last_sync,
        sync_history=sync_history,
        now=datetime.now(UTC),
    )


@bp.route("/users")
@admin_required
def list_users():
    """List all admin users."""
    users = db.session.query(HarborAdminUser).order_by(HarborAdminUser.created_at.desc()).all()
    return render_template("admin_users_list.html", users=users)


@bp.route("/users/create", methods=["GET", "POST"])
@admin_required
def create_user():
    """Admin creation is disabled in the Harbor UI."""
    flash(
        "Harbor does not create admin users from the UI. Use the bootstrap CLI path instead.",
        "warning",
    )
    return redirect(url_for("admin.review_user"))


@bp.route("/users/<int:user_id>")
@admin_required
def user_detail(user_id):
    """View admin user details."""
    user = db.session.query(HarborAdminUser).get_or_404(user_id)
    return render_template("admin_user_detail.html", user=user)


@bp.route("/users/<int:user_id>/edit", methods=["GET", "POST"])
@admin_required
def edit_user(user_id):
    """Edit admin user."""
    user = db.session.query(HarborAdminUser).get_or_404(user_id)
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        display_name = request.form.get("display_name", "").strip()
        password = request.form.get("password", "").strip()
        current_password = request.form.get("current_password", "").strip()

        if not username:
            flash("Username is required.", "error")
            return redirect(url_for("admin.edit_user", user_id=user.id))

        if username != user.username:
            existing = db.session.query(HarborAdminUser).filter_by(username=username).one_or_none()
            if existing:
                flash("Username already exists.", "error")
                return redirect(url_for("admin.edit_user", user_id=user.id))

        user.username = username
        if display_name:
            user.display_name = display_name
        if password:
            if not current_password:
                flash("Current password is required to set a new password.", "error")
                return redirect(url_for("admin.edit_user", user_id=user.id))
            try:
                ph.verify(user.password_hash, current_password)
            except Exception:
                flash("Current password is incorrect.", "error")
                return redirect(url_for("admin.edit_user", user_id=user.id))
            user.password_hash = ph.hash(password)

        db.session.add(
            AuditLog(
                actor=session.get("admin_username", "admin"),
                action="edit_user",
                detail=f"Edited admin user {user.username} (id={user.id})",
                ip_address=request.remote_addr or "",
            )
        )
        db.session.commit()
        flash("User updated successfully.", "success")
        return redirect(url_for("admin.list_users"))

    return render_template("admin_user_edit.html", user=user)


@bp.route("/users/<int:user_id>/delete", methods=["POST"])
@admin_required
def delete_user(user_id):
    """Delete admin user."""
    user = db.session.query(HarborAdminUser).get_or_404(user_id)

    # Prevent self-deletion
    current_username = session.get("admin_username", "")
    if user.username == current_username:
        flash("You cannot delete yourself.", "error")
        return redirect(url_for("admin.list_users"))

    # Prevent deletion of last admin
    admin_count = db.session.query(HarborAdminUser).count()
    if admin_count <= 1:
        flash("Cannot delete the last admin user.", "error")
        return redirect(url_for("admin.list_users"))

    username = user.username
    db.session.add(
        AuditLog(
            actor=current_username,
            action="delete_user",
            detail=f"Deleted admin user {username} (id={user.id})",
            ip_address=request.remote_addr or "",
        )
    )
    db.session.delete(user)
    db.session.commit()
    flash(f"User {username} deleted.", "success")
    return redirect(url_for("admin.list_users"))


@bp.route("/users/<int:user_id>/toggle-active", methods=["POST"])
@admin_required
def toggle_user_active(user_id):
    """Toggle active/inactive for an admin user."""
    user = db.session.query(HarborAdminUser).get_or_404(user_id)

    # Prevent deactivating yourself
    current_username = session.get("admin_username", "")
    if user.username == current_username:
        flash("You cannot deactivate yourself.", "error")
        return redirect(url_for("admin.list_users"))

    # Prevent deactivating last active admin
    if user.is_active:
        active_count = db.session.query(HarborAdminUser).filter_by(is_active=True).count()
        if active_count <= 1:
            flash("Cannot deactivate the last active admin user.", "error")
            return redirect(url_for("admin.list_users"))

    user.is_active = not user.is_active
    status = "activated" if user.is_active else "deactivated"
    db.session.add(
        AuditLog(
            actor=current_username,
            action="toggle_user_active",
            detail=f"{status} admin user {user.username} (id={user.id})",
            ip_address=request.remote_addr or "",
        )
    )
    db.session.commit()
    flash(f"User {user.username} {status}.", "success")
    return redirect(url_for("admin.list_users"))


@bp.route("/customers")
@admin_required
def customers_list():
    """List all customers."""
    q = request.args.get("q", "").strip()
    page = request.args.get("page", 1, type=int)
    per_page = 25

    query = db.session.query(Customer)
    if q:
        query = query.filter(
            db.or_(
                Customer.email.ilike(f"%{q}%"),
                Customer.display_name.ilike(f"%{q}%"),
                Customer.public_id.ilike(f"%{q}%"),
            )
        )

    total = query.count()
    customers = query.order_by(Customer.created_at.desc()).offset((page - 1) * per_page).limit(per_page).all()

    return render_template(
        "admin_customers_list.html",
        customers=customers,
        q=q,
        page=page,
        total=total,
        pages=(total + per_page - 1) // per_page,
    )


@bp.route("/customers/<int:customer_id>")
@admin_required
def customer_detail(customer_id):
    """View customer details."""
    customer = db.session.query(Customer).get_or_404(customer_id)
    subscriptions = (
        db.session.query(Subscription)
        .filter_by(customer_email=customer.email)
        .order_by(Subscription.created_at.desc())
        .all()
    )
    return render_template(
        "admin_customer_detail.html",
        customer=customer,
        subscriptions=subscriptions,
    )


@bp.route("/customers/<int:customer_id>/toggle-active", methods=["POST"])
@admin_required
def toggle_customer_active(customer_id):
    """Block or unblock a customer."""
    customer = db.session.query(Customer).get_or_404(customer_id)
    customer.is_active = not customer.is_active
    customer.blocked_at = datetime.now(UTC) if not customer.is_active else None
    status = "blocked" if not customer.is_active else "unblocked"
    db.session.add(
        AuditLog(
            actor=session.get("admin_username", "admin"),
            action="toggle_customer_active",
            detail=f"{status} customer {customer.email} (id={customer.id})",
            ip_address=request.remote_addr or "",
        )
    )
    db.session.commit()
    flash(f"Customer {customer.email} {status}.", "success")
    return redirect(url_for("admin.customers_list"))


@bp.route("/review_user")
@bp.route("/review-user")
@admin_required
def review_user():
    pending_customers = (
        db.session.query(Customer).filter(Customer.signup_status == "pending").order_by(Customer.created_at.asc()).all()
    )
    return render_template("admin_review_user.html", customers=pending_customers)


@bp.route("/review_user/<int:customer_id>/approve", methods=["POST"])
@bp.route("/review-user/<int:customer_id>/approve", methods=["POST"])
@admin_required
def approve_reviewed_user(customer_id):
    customer = db.session.query(Customer).get_or_404(customer_id)
    customer.signup_status = "active"
    customer.is_active = True
    customer.blocked_at = None
    customer.reviewed_at = datetime.now(UTC)
    customer.reviewed_by = session.get("admin_username", "admin")
    customer.rejection_reason = None
    db.session.add(
        AuditLog(
            actor=session.get("admin_username", "admin"),
            action="approve_customer_signup",
            detail=f"Approved customer signup for {customer.email} (id={customer.id})",
            ip_address=request.remote_addr or "",
        )
    )
    db.session.commit()
    flash(f"Customer {customer.email} approved.", "success")
    return redirect(url_for("admin.review_user"))


@bp.route("/review_user/<int:customer_id>/reject", methods=["POST"])
@bp.route("/review-user/<int:customer_id>/reject", methods=["POST"])
@admin_required
def reject_reviewed_user(customer_id):
    customer = db.session.query(Customer).get_or_404(customer_id)
    reason = request.form.get("rejection_reason", "").strip()
    customer.signup_status = "rejected"
    customer.is_active = False
    customer.blocked_at = datetime.now(UTC)
    customer.reviewed_at = datetime.now(UTC)
    customer.reviewed_by = session.get("admin_username", "admin")
    customer.rejection_reason = reason or None
    db.session.add(
        AuditLog(
            actor=session.get("admin_username", "admin"),
            action="reject_customer_signup",
            detail=f"Rejected customer signup for {customer.email} (id={customer.id})",
            ip_address=request.remote_addr or "",
        )
    )
    db.session.commit()
    flash(f"Customer {customer.email} rejected.", "success")
    return redirect(url_for("admin.review_user"))


@bp.route("/paypal/config", methods=["GET", "POST"])
@admin_required
def paypal_config():
    """Configure PayPal credentials."""
    from app.extensions import secrets as ext_secrets

    config = HarborPayPalConfig.get_config()

    if request.method == "POST":
        mode = request.form.get("mode", "sandbox").strip()
        client_id = request.form.get("client_id", "").strip()
        client_secret = request.form.get("client_secret", "").strip()
        webhook_id = request.form.get("webhook_id", "").strip()

        if not client_id or not client_secret:
            flash("Client ID and Client Secret are required.", "error")
            return redirect(url_for("admin.paypal_config"))

        config.mode = mode
        config.client_id = client_id
        config.client_secret = ext_secrets.encrypt(client_secret)
        config.webhook_id = webhook_id or None

        db.session.add(
            AuditLog(
                actor=session.get("admin_username", "admin"),
                action="update_paypal_config",
                detail=f"PayPal config updated: mode={mode}",
                ip_address=request.remote_addr or "",
            )
        )
        db.session.commit()

        flash("PayPal configuration updated successfully.", "success")
        return redirect(url_for("admin.paypal_config"))

    return render_template("admin_paypal_config.html", config=config)


@bp.route("/payments")
@admin_required
def payments_list():
    """List all payments with filters."""
    status_filter = request.args.get("status", None)
    page = request.args.get("page", 1, type=int)
    per_page = 50

    query = db.session.query(Payment).order_by(Payment.created_at.desc())

    if status_filter:
        query = query.filter_by(status=status_filter)

    total = query.count()
    payments = query.offset((page - 1) * per_page).limit(per_page).all()
    pages = max(1, (total + per_page - 1) // per_page)

    return render_template(
        "admin_payments.html",
        payments=payments,
        page=page,
        pages=pages,
        status_filter=status_filter,
        total=total,
    )


# ===== FASE 2: OPERACIONES Y SOPORTE =====


@bp.route("/instances")
@admin_required
def instances_list():
    """List all instances from admirald."""
    try:
        # Get all subscriptions with their instances
        subscriptions = (
            db.session.query(Subscription)
            .filter(Subscription.instance_id.isnot(None))
            .order_by(Subscription.created_at.desc())
            .all()
        )

        # Enrich with admirald data
        instances_data = []
        for sub in subscriptions:
            try:
                admirald_data = get_customer_app(sub.instance_id)
                instances_data.append({"subscription": sub, "admirald": admirald_data, "error": None})
            except AdmiralAPIError as e:
                instances_data.append({"subscription": sub, "admirald": None, "error": str(e)})

        return render_template(
            "admin_instances.html",
            instances=instances_data,
            total=len(instances_data),
        )
    except Exception as e:
        flash(f"Error loading instances: {str(e)}", "error")
        return render_template("admin_instances.html", instances=[], total=0)


@bp.route("/instances/<instance_id>/pod-status")
@admin_required
def instance_pod_status(instance_id):
    """JSON endpoint returning pod status, services, and disk usage."""
    try:
        instance = get_customer_app(instance_id)
    except AdmiralAPIError:
        return jsonify({"error": "Instance not found"}), 404

    inspect_data = None
    try:
        inspect_data = get_instance_inspect(instance_id)
    except AdmiralAPIError:
        pass

    status = instance.get("technical_status", "unknown")
    storage_state = instance.get("storage_state", "unknown")
    storage_used = instance.get("storage_used_bytes", 0)
    storage_limit = instance.get("storage_limit_bytes", 0)
    storage_pct = instance.get("storage_used_percent", 0.0)

    pod_info = {
        "instance_id": instance_id,
        "status": status,
        "node_id": instance.get("node_id", ""),
        "hostname": instance.get("hostname", ""),
        "health_status": instance.get("health_status", "unknown"),
        "storage": {
            "state": storage_state,
            "used_bytes": storage_used,
            "limit_bytes": storage_limit,
            "used_percent": storage_pct,
        },
    }

    if inspect_data:
        pod_info["inspect"] = inspect_data

    return jsonify(pod_info)


@bp.route("/instances/<instance_id>")
@admin_required
def instance_detail(instance_id):
    """View details of a single instance."""
    try:
        # Get from database
        customer_app = db.session.query(CustomerApp).filter_by(instance_id=instance_id).one_or_none()
        if not customer_app:
            flash("Instance not found.", "error")
            return redirect(url_for("admin.instances_list"))

        # Get from admirald
        admirald_data = get_customer_app(instance_id)

        # Get associated subscription
        subscription = db.session.query(Subscription).filter_by(id=customer_app.subscription_id).one_or_none()

        # Get backups
        backups = []
        try:
            backups_response = list_backups(instance_id)
            if isinstance(backups_response, list):
                backups = backups_response
        except AdmiralAPIError:
            pass

        return render_template(
            "admin_instance_detail.html",
            customer_app=customer_app,
            subscription=subscription,
            admirald=admirald_data,
            backups=backups,
        )
    except AdmiralAPIError as e:
        flash(f"Error loading instance: {str(e)}", "error")
        return redirect(url_for("admin.instances_list"))


@bp.route("/backups")
@admin_required
def backups_list():
    """List all backups from admirald."""
    try:
        # Get all instances
        subscriptions = db.session.query(Subscription).filter(Subscription.instance_id.isnot(None)).all()

        # Collect backups from all instances
        all_backups = []
        for sub in subscriptions:
            try:
                backups = list_backups(sub.instance_id)
                if isinstance(backups, list):
                    for backup in backups:
                        backup["subscription"] = sub
                        all_backups.append(backup)
            except AdmiralAPIError:
                pass

        # Sort by created_at desc
        all_backups.sort(key=lambda x: x.get("created_at", ""), reverse=True)

        # Filter by status if requested
        status_filter = request.args.get("status", None)
        if status_filter:
            all_backups = [b for b in all_backups if b.get("status") == status_filter]

        return render_template(
            "admin_backups.html",
            backups=all_backups,
            status_filter=status_filter,
            total=len(all_backups),
        )
    except Exception as e:
        flash(f"Error loading backups: {str(e)}", "error")
        return render_template("admin_backups.html", backups=[], status_filter=None, total=0)


@bp.route("/backups/<backup_id>")
@admin_required
def backup_detail(backup_id):
    """View details of a single backup."""
    try:
        backup = get_backup(backup_id)

        # Find associated subscription
        subscription = None
        if "instance_id" in backup:
            capp = db.session.query(CustomerApp).filter_by(instance_id=backup["instance_id"]).one_or_none()
            if capp:
                subscription = db.session.query(Subscription).filter_by(id=capp.subscription_id).one_or_none()

        return render_template(
            "admin_backup_detail.html",
            backup=backup,
            subscription=subscription,
        )
    except AdmiralAPIError as e:
        flash(f"Error loading backup: {str(e)}", "error")
        return redirect(url_for("admin.backups_list"))


@bp.route("/tickets")
@admin_required
def tickets_list():
    """List all support tickets."""
    page = request.args.get("page", 1, type=int)
    per_page = 20

    # Filter options
    status_filter = request.args.get("status", None)
    priority_filter = request.args.get("priority", None)
    assigned_filter = request.args.get("assigned", None)

    query = db.session.query(SupportIncident).order_by(SupportIncident.created_at.desc())

    if status_filter:
        query = query.filter_by(status=status_filter)
    if priority_filter:
        query = query.filter_by(priority=priority_filter)
    if assigned_filter == "unassigned":
        query = query.filter_by(assigned_to=None)
    elif assigned_filter:
        query = query.filter_by(assigned_to=assigned_filter)

    total = query.count()
    tickets = query.offset((page - 1) * per_page).limit(per_page).all()
    pages = max(1, (total + per_page - 1) // per_page)

    return render_template(
        "admin_tickets.html",
        tickets=tickets,
        page=page,
        pages=pages,
        total=total,
        status_filter=status_filter,
        priority_filter=priority_filter,
        assigned_filter=assigned_filter,
    )


@bp.route("/tickets/<ticket_id>")
@admin_required
def ticket_detail(ticket_id):
    """View and edit a single ticket."""
    ticket = db.session.query(SupportIncident).filter_by(incident_id=ticket_id).one_or_none()
    if not ticket:
        flash("Ticket not found.", "error")
        return redirect(url_for("admin.tickets_list"))

    # Calculate SLA status
    sla_status = _get_sla_status(ticket)
    time_remaining_formatted = _format_timedelta(sla_status["time_remaining"])

    return render_template(
        "admin_ticket_detail.html",
        ticket=ticket,
        sla_status=sla_status,
        time_remaining_formatted=time_remaining_formatted,
    )


@bp.route("/tickets/<ticket_id>/assign", methods=["POST"])
@admin_required
def ticket_assign(ticket_id):
    """Assign a ticket to an admin."""
    ticket = db.session.query(SupportIncident).filter_by(incident_id=ticket_id).one_or_none()
    if not ticket:
        flash("Ticket not found.", "error")
        return redirect(url_for("admin.tickets_list"))

    assigned_to = request.form.get("assigned_to", "").strip()
    ticket.assigned_to = assigned_to or None

    # If not assigned previously, set SLA deadlines now
    if assigned_to and not ticket.response_deadline:
        sla_deadlines = _calculate_sla_deadlines(ticket.priority)
        ticket.response_deadline = sla_deadlines["response_deadline"]
        ticket.resolution_deadline = sla_deadlines["resolution_deadline"]

    db.session.add(
        AuditLog(
            actor=session.get("admin_username", "admin"),
            action="assign_ticket",
            resource_type="ticket",
            resource_id=ticket_id,
            detail=f"Ticket assigned to {assigned_to or 'unassigned'}",
            ip_address=request.remote_addr or "",
        )
    )
    db.session.commit()

    flash(f"Ticket assigned to {assigned_to or 'unassigned'}.", "success")
    return redirect(url_for("admin.ticket_detail", ticket_id=ticket_id))


@bp.route("/tickets/<ticket_id>/status", methods=["POST"])
@admin_required
def ticket_update_status(ticket_id):
    """Update ticket status."""
    ticket = db.session.query(SupportIncident).filter_by(incident_id=ticket_id).one_or_none()
    if not ticket:
        flash("Ticket not found.", "error")
        return redirect(url_for("admin.tickets_list"))

    new_status = request.form.get("status", "").strip()
    old_status = ticket.status
    ticket.status = new_status

    # Mark as resolved when status changes to resolved/closed
    if new_status in ["resolved", "closed"] and not ticket.resolved_at:
        ticket.resolved_at = datetime.now(UTC)
        # Check if SLA was violated
        if ticket.resolution_deadline and ticket.resolved_at > ticket.resolution_deadline:
            ticket.sla_violated = True

    db.session.add(
        AuditLog(
            actor=session.get("admin_username", "admin"),
            action="update_ticket_status",
            resource_type="ticket",
            resource_id=ticket_id,
            detail=f"Status changed from {old_status} to {new_status}",
            ip_address=request.remote_addr or "",
        )
    )
    db.session.commit()

    flash(f"Ticket status updated to {new_status}.", "success")
    return redirect(url_for("admin.ticket_detail", ticket_id=ticket_id))


@bp.route("/tickets/<ticket_id>/notes", methods=["POST"])
@admin_required
def ticket_add_note(ticket_id):
    """Add internal note to ticket."""
    ticket = db.session.query(SupportIncident).filter_by(incident_id=ticket_id).one_or_none()
    if not ticket:
        flash("Ticket not found.", "error")
        return redirect(url_for("admin.tickets_list"))

    note = request.form.get("note", "").strip()
    if note:
        timestamp = datetime.now(UTC).strftime("%Y-%m-%d %H:%M")
        actor = session.get("admin_username", "admin")
        formatted_note = f"[{timestamp}] {actor}: {note}\n"
        ticket.internal_notes = (ticket.internal_notes or "") + formatted_note

        db.session.add(
            AuditLog(
                actor=actor,
                action="add_ticket_note",
                resource_type="ticket",
                resource_id=ticket_id,
                detail="Added internal note",
                ip_address=request.remote_addr or "",
            )
        )
        db.session.commit()

        flash("Note added.", "success")

    return redirect(url_for("admin.ticket_detail", ticket_id=ticket_id))


@bp.route("/sla")
@admin_required
def sla_dashboard():
    """Display SLA compliance metrics and violations."""
    # Get all tickets
    all_tickets = db.session.query(SupportIncident).all()

    # Calculate SLA metrics
    sla_compliant = 0
    sla_violated = 0
    sla_warning = 0
    sla_unknown = 0
    violations_by_priority = {"critical": 0, "high": 0, "medium": 0, "low": 0}

    violations = []

    for ticket in all_tickets:
        sla_status = _get_sla_status(ticket)

        if sla_status["sla_status"] == "compliant":
            sla_compliant += 1
        elif sla_status["sla_status"] == "warning":
            sla_warning += 1
        elif sla_status["sla_status"] == "violated":
            sla_violated += 1
            if ticket.priority in violations_by_priority:
                violations_by_priority[ticket.priority] += 1
            violations.append(
                {
                    "ticket": ticket,
                    "sla_status": sla_status,
                    "time_remaining": _format_timedelta(sla_status["time_remaining"]),
                }
            )
        else:
            sla_unknown += 1

    # Calculate compliance percentage
    total = len(all_tickets)
    compliance_pct = (sla_compliant / total * 100) if total > 0 else 0

    # Get recent violations
    violations_sorted = sorted(violations, key=lambda x: x["ticket"].created_at, reverse=True)[:10]

    return render_template(
        "admin_sla.html",
        sla_compliant=sla_compliant,
        sla_violated=sla_violated,
        sla_warning=sla_warning,
        sla_unknown=sla_unknown,
        compliance_pct=compliance_pct,
        total_tickets=total,
        violations_by_priority=violations_by_priority,
        violations=violations_sorted,
    )


# ── Tax Rates ──────────────────────────────────────────────────────────────────


@bp.route("/tax-rates", methods=["GET", "POST"])
@admin_required
def tax_rates():
    from app.branding import get_tax_rates, set_tax_rates

    rates = get_tax_rates()
    if request.method == "POST":
        action = request.form.get("action")
        if action == "add":
            code = request.form.get("country_code", "").strip().upper()
            try:
                pct = float(request.form.get("tax_percent", "0"))
            except ValueError:
                flash("Porcentaje inválido.", "error")
                return redirect(url_for("admin.tax_rates"))
            if not code or pct < 0:
                flash("País y porcentaje son requeridos.", "error")
                return redirect(url_for("admin.tax_rates"))
            rates[code] = pct
            set_tax_rates(rates)
            flash(f"Tasa actualizada: {code} → {pct}%", "success")
        elif action == "delete":
            code = request.form.get("country_code", "").strip().upper()
            rates.pop(code, None)
            set_tax_rates(rates)
            flash(f"Tasa eliminada para {code}.", "success")
        return redirect(url_for("admin.tax_rates"))

    rate_rows = [
        {
            "code": code,
            "country_name": COUNTRY_NAMES.get(code, code),
            "rate": rate,
        }
        for code, rate in sorted(rates.items())
    ]
    return render_template(
        "admin_tax_rates.html",
        rates=rate_rows,
        countries=COUNTRIES,
    )


# ── Fiscal Treatment Types ─────────────────────────────────────────────────────


@bp.route("/fiscal-types", methods=["GET", "POST"])
@admin_required
def fiscal_types():
    if request.method == "POST":
        code = request.form.get("country_code", "").strip().upper()
        name = request.form.get("name", "").strip()
        description = request.form.get("description", "").strip() or None
        direction = request.form.get("direction", "+")
        is_optional = request.form.get("is_optional", "1") == "1"
        requires_evidence = bool(request.form.get("requires_evidence"))
        try:
            percent = float(request.form.get("percent", "0"))
        except ValueError:
            flash("Porcentaje inválido.", "error")
            return redirect(url_for("admin.fiscal_types"))
        if not code or not name or direction not in ("+", "-") or percent <= 0:
            flash(
                "Todos los campos son requeridos y el porcentaje debe ser mayor a 0.",
                "error",
            )
            return redirect(url_for("admin.fiscal_types"))
        t = FiscalTreatmentType(
            country_code=code,
            name=name,
            description=description,
            direction=direction,
            percent=percent,
            is_optional=is_optional,
            requires_evidence=requires_evidence,
            is_active=True,
        )
        db.session.add(t)
        db.session.add(
            AuditLog(
                actor=session.get("admin_username", "admin"),
                action="fiscal_type_created",
                resource_type="FiscalTreatmentType",
                resource_id="new",
                detail=f"Created fiscal treatment {name} for {code} ({direction}{percent}%)",
                ip_address=request.remote_addr or "",
            )
        )
        db.session.commit()
        flash(f"Tratamiento '{name}' creado.", "success")
        return redirect(url_for("admin.fiscal_types"))

    types_raw = (
        db.session.query(FiscalTreatmentType).order_by(FiscalTreatmentType.country_code, FiscalTreatmentType.name).all()
    )
    types = [
        {
            **t.__dict__,
            "id": t.id,
            "country_name": COUNTRY_NAMES.get(t.country_code, t.country_code),
            "name": t.name,
            "description": t.description,
            "direction": t.direction,
            "percent": float(t.percent),
            "is_optional": t.is_optional,
            "requires_evidence": t.requires_evidence,
            "is_active": t.is_active,
        }
        for t in types_raw
    ]
    return render_template(
        "admin_fiscal_types.html",
        types=types,
        countries=COUNTRIES,
    )


@bp.route("/fiscal-types/<int:type_id>/toggle", methods=["POST"])
@admin_required
def fiscal_type_toggle(type_id):
    t = db.session.get(FiscalTreatmentType, type_id)
    if not t:
        flash("Tratamiento no encontrado.", "error")
        return redirect(url_for("admin.fiscal_types"))
    t.is_active = not t.is_active
    db.session.commit()
    flash(
        f"Tratamiento '{t.name}' {'activado' if t.is_active else 'desactivado'}.",
        "success",
    )
    return redirect(url_for("admin.fiscal_types"))


@bp.route("/fiscal-types/<int:type_id>/delete", methods=["POST"])
@admin_required
def fiscal_type_delete(type_id):
    t = db.session.get(FiscalTreatmentType, type_id)
    if not t:
        flash("Tratamiento no encontrado.", "error")
        return redirect(url_for("admin.fiscal_types"))
    db.session.delete(t)
    db.session.commit()
    flash(f"Tratamiento '{t.name}' eliminado.", "success")
    return redirect(url_for("admin.fiscal_types"))


# ── Fiscal Requests ────────────────────────────────────────────────────────────


@bp.route("/fiscal-requests")
@admin_required
def fiscal_requests_list():
    status_filter = request.args.get("status", "").strip() or None
    q = db.session.query(CustomerFiscalRequest)
    if status_filter:
        q = q.filter_by(status=status_filter)
    requests_list = q.order_by(CustomerFiscalRequest.created_at.desc()).all()
    pending_count = db.session.query(CustomerFiscalRequest).filter_by(status="pending").count()
    return render_template(
        "admin_fiscal_requests.html",
        requests=requests_list,
        status_filter=status_filter,
        pending_count=pending_count,
    )


@bp.route("/fiscal-requests/<request_id>")
@admin_required
def fiscal_request_detail(request_id):
    req = db.session.query(CustomerFiscalRequest).filter_by(request_id=request_id).one_or_none()
    if not req:
        flash("Solicitud no encontrada.", "error")
        return redirect(url_for("admin.fiscal_requests_list"))
    customer = db.session.query(Customer).filter_by(email=req.customer_email).one_or_none()
    return render_template(
        "admin_fiscal_request_detail.html",
        req=req,
        customer=customer,
    )


@bp.route("/fiscal-requests/<request_id>/evidence")
@admin_required
def fiscal_request_evidence(request_id):
    from flask import send_file

    req = db.session.query(CustomerFiscalRequest).filter_by(request_id=request_id).one_or_none()
    if not req or not req.evidence_path:
        flash("Evidencia no encontrada.", "error")
        return redirect(url_for("admin.fiscal_request_detail", request_id=request_id))
    return send_file(
        req.evidence_path,
        as_attachment=True,
        download_name=req.evidence_original_name or "evidencia",
    )


@bp.route("/fiscal-requests/<request_id>/approve", methods=["POST"])
@admin_required
def fiscal_request_approve(request_id):
    req = db.session.query(CustomerFiscalRequest).filter_by(request_id=request_id).one_or_none()
    if not req:
        flash("Solicitud no encontrada.", "error")
        return redirect(url_for("admin.fiscal_requests_list"))
    reviewer_notes = request.form.get("reviewer_notes", "").strip() or None
    req.status = "approved"
    req.reviewer_notes = reviewer_notes
    req.reviewed_by = session.get("admin_username", "admin")
    req.reviewed_at = datetime.now(UTC)
    db.session.add(
        AuditLog(
            actor=req.reviewed_by,
            action="fiscal_request_approved",
            resource_type="CustomerFiscalRequest",
            resource_id=req.request_id,
            detail=f"Fiscal request {request_id} approved for {req.customer_email}",
            ip_address=request.remote_addr or "",
        )
    )
    db.session.commit()
    flash(
        "Solicitud aprobada. El ajuste fiscal se aplicará a las próximas órdenes del cliente.",
        "success",
    )
    return redirect(url_for("admin.fiscal_request_detail", request_id=request_id))


@bp.route("/fiscal-requests/<request_id>/revoke", methods=["POST"])
@admin_required
def fiscal_request_revoke(request_id):
    req = db.session.query(CustomerFiscalRequest).filter_by(request_id=request_id).one_or_none()
    if not req:
        flash("Solicitud no encontrada.", "error")
        return redirect(url_for("admin.fiscal_requests_list"))
    reviewer_notes = request.form.get("reviewer_notes", "").strip()
    if not reviewer_notes:
        flash("El motivo de rechazo es requerido.", "error")
        return redirect(url_for("admin.fiscal_request_detail", request_id=request_id))
    req.status = "revoked"
    req.reviewer_notes = reviewer_notes
    req.reviewed_by = session.get("admin_username", "admin")
    req.reviewed_at = datetime.now(UTC)
    db.session.add(
        AuditLog(
            actor=req.reviewed_by,
            action="fiscal_request_revoked",
            resource_type="CustomerFiscalRequest",
            resource_id=req.request_id,
            detail=f"Fiscal request {request_id} revoked for {req.customer_email}: {reviewer_notes}",
            ip_address=request.remote_addr or "",
        )
    )
    db.session.commit()
    flash("Solicitud revocada.", "success")
    return redirect(url_for("admin.fiscal_request_detail", request_id=request_id))
