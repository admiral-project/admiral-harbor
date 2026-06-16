# SPDX-FileCopyrightText: William Moreno Reyes <williamjmorenor@gmail.com>
# SPDX-License-Identifier: Apache-2.0

from functools import wraps
from flask import (
    Blueprint,
    flash,
    redirect,
    render_template,
    request,
    session,
    url_for,
    g,
)
from sqlalchemy import func

from app.extensions import db
from app.models import (
    Customer,
    Subscription,
    SupportIncident,
    Payment,
    AuditLog,
    SubscriptionChange,
    CustomerReply,
)

customer_bp = Blueprint("customer", __name__, url_prefix="/customer")


def _set_customer_session(customer):
    session["customer_id"] = customer.id
    session["customer_email"] = customer.email


def customer_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        customer_id = session.get("customer_id")
        if not customer_id:
            flash("Please log in as customer", "warning")
            return redirect(url_for("customer.login_page"))

        customer = db.session.query(Customer).get(customer_id)
        if not customer or not customer.can_access():
            session.clear()
            flash("Customer not found", "error")
            return redirect(url_for("customer.login_page"))

        g.customer = customer
        return f(*args, **kwargs)

    return decorated_function


@customer_bp.route("/login", methods=["GET"])
def login_page():
    if session.get("customer_id"):
        return redirect(url_for("customer.dashboard"))
    return render_template("customer_login.html")


@customer_bp.route("/login", methods=["POST"])
def login():
    email = request.form.get("email", "").strip()
    password = request.form.get("password", "").strip()

    if not email or not password:
        flash("Email and password required", "error")
        return redirect(url_for("customer.login_page"))

    from argon2 import PasswordHasher
    from argon2.exceptions import VerifyMismatchError, InvalidHash

    ph = PasswordHasher()
    customer = db.session.query(Customer).filter_by(email=email).one_or_none()

    if not customer:
        flash("Invalid email or password", "error")
        return redirect(url_for("customer.login_page"))
    if not customer.can_access():
        flash("Your account is pending approval.", "warning")
        return redirect(url_for("customer.login_page"))

    try:
        ph.verify(customer.password_hash, password)
    except (VerifyMismatchError, InvalidHash):
        flash("Invalid email or password", "error")
        return redirect(url_for("customer.login_page"))

    _set_customer_session(customer)
    db.session.add(
        AuditLog(
            actor=email,
            action="customer_login",
            detail=f"Customer {email} logged in",
            ip_address=request.remote_addr or "",
        )
    )
    db.session.commit()

    flash(f"Welcome {customer.display_name}", "success")
    return redirect(url_for("customer.dashboard"))


@customer_bp.route("/logout", methods=["POST"])
@customer_required
def logout():
    email = g.customer.email
    db.session.add(
        AuditLog(
            actor=email,
            action="customer_logout",
            detail=f"Customer {email} logged out",
            ip_address=request.remote_addr or "",
        )
    )
    db.session.commit()
    session.pop("customer_id", None)
    session.pop("customer_email", None)
    flash("Logged out successfully", "success")
    return redirect(url_for("customer.login_page"))


@customer_bp.route("/", methods=["GET"])
@customer_required
def dashboard():
    customer = g.customer

    # Get subscriptions
    subscriptions = (
        db.session.query(Subscription).filter_by(customer_id=customer.id).all()
    )

    # Get instances from subscriptions
    instances = []
    for sub in subscriptions:
        if sub.instance_id:
            instances.append(
                {
                    "subscription_id": sub.id,
                    "instance_id": sub.instance_id,
                    "app_id": sub.app_id,
                    "tier": sub.tier,
                    "status": sub.status,
                    "renewal_date": sub.renewal_date,
                }
            )

    # Get open tickets
    open_tickets = (
        db.session.query(SupportIncident)
        .filter_by(customer_id=customer.id)
        .filter(SupportIncident.status != "closed")
        .count()
    )

    # Get failed payments
    failed_payments = (
        db.session.query(Payment)
        .filter_by(customer_id=customer.id, status="failed")
        .count()
    )

    # Get pending payments
    pending_payments = (
        db.session.query(Payment)
        .filter_by(customer_id=customer.id, status="pending")
        .count()
    )

    # Current month revenue/usage
    from datetime import UTC, datetime

    now = datetime.now(UTC)
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

    total_charged_cents = (
        db.session.query(func.sum(Payment.amount_cents))
        .filter(
            Payment.customer_id == customer.id,
            Payment.status == "completed",
            Payment.created_at >= month_start,
        )
        .scalar()
        or 0
    )

    return render_template(
        "customer_dashboard.html",
        customer=customer,
        subscriptions=subscriptions,
        instances=instances,
        open_tickets=open_tickets,
        failed_payments=failed_payments,
        pending_payments=pending_payments,
        total_charged_cents=total_charged_cents,
    )


@customer_bp.route("/subscriptions", methods=["GET"])
@customer_required
def subscriptions_list():
    customer = g.customer
    page = request.args.get("page", 1, type=int)
    per_page = 10

    query = db.session.query(Subscription).filter_by(customer_id=customer.id)

    # Filtering
    status = request.args.get("status")
    if status:
        query = query.filter_by(status=status)

    paginated = query.paginate(page=page, per_page=per_page)

    return render_template(
        "customer_subscriptions_list.html",
        subscriptions=paginated.items,
        paginated=paginated,
        status_filter=status,
    )


@customer_bp.route("/subscriptions/<int:subscription_id>", methods=["GET"])
@customer_required
def subscription_detail(subscription_id):
    customer = g.customer
    subscription = db.session.query(Subscription).get(subscription_id)

    if not subscription or subscription.customer_id != customer.id:
        flash("Subscription not found", "error")
        return redirect(url_for("customer.subscriptions_list"))

    # Get related payments
    payments = (
        db.session.query(Payment)
        .filter_by(customer_id=customer.id, subscription_id=subscription_id)
        .order_by(Payment.created_at.desc())
        .limit(10)
        .all()
    )

    return render_template(
        "customer_subscription_detail.html",
        subscription=subscription,
        payments=payments,
    )


@customer_bp.route("/support", methods=["GET"])
@customer_required
def support_list():
    customer = g.customer
    page = request.args.get("page", 1, type=int)
    per_page = 10

    query = db.session.query(SupportIncident).filter_by(customer_id=customer.id)

    # Filtering by status
    status = request.args.get("status")
    if status:
        query = query.filter_by(status=status)

    paginated = query.order_by(SupportIncident.created_at.desc()).paginate(
        page=page, per_page=per_page
    )

    return render_template(
        "customer_support_list.html",
        tickets=paginated.items,
        paginated=paginated,
        status_filter=status,
    )


@customer_bp.route("/support/create", methods=["GET"])
@customer_required
def support_create_page():
    subscriptions = (
        db.session.query(Subscription).filter_by(customer_id=g.customer.id).all()
    )
    return render_template("customer_support_create.html", subscriptions=subscriptions)


@customer_bp.route("/support/create", methods=["POST"])
@customer_required
def support_create():
    customer = g.customer
    subject = request.form.get("subject", "").strip()
    description = request.form.get("description", "").strip()
    priority = request.form.get("priority", "medium").strip()
    subscription_id = request.form.get("subscription_id", type=int)

    if not subject or not description:
        flash("Subject and description required", "error")
        return redirect(url_for("customer.support_create_page"))

    if priority not in ["low", "medium", "high", "critical"]:
        priority = "medium"

    # Verify subscription belongs to customer
    if subscription_id:
        sub = db.session.query(Subscription).get(subscription_id)
        if not sub or sub.customer_id != customer.id:
            subscription_id = None

    ticket = SupportIncident(
        customer_id=customer.id,
        subscription_id=subscription_id,
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
    return redirect(url_for("customer.support_detail", ticket_id=ticket.id))


@customer_bp.route("/support/<int:ticket_id>", methods=["GET"])
@customer_required
def support_detail(ticket_id):
    customer = g.customer
    ticket = db.session.query(SupportIncident).get(ticket_id)

    if not ticket or ticket.customer_id != customer.id:
        flash("Ticket not found", "error")
        return redirect(url_for("customer.support_list"))

    # Get only customer-visible messages (no internal notes)
    # For now, return the ticket and description as the conversation starter
    conversation = [
        {
            "author": customer.email,
            "message": ticket.description,
            "timestamp": ticket.created_at,
            "is_internal": False,
        }
    ]

    # If there are admin replies, get them (filter internal notes)
    # TODO: Add customer_reply or conversation model if needed

    return render_template(
        "customer_support_detail.html", ticket=ticket, conversation=conversation
    )


@customer_bp.route("/profile", methods=["GET"])
@customer_required
def profile():
    return render_template("customer_profile.html", customer=g.customer)


@customer_bp.route("/profile/edit", methods=["POST"])
@customer_required
def profile_edit():
    customer = g.customer
    display_name = request.form.get("display_name", "").strip()
    country = request.form.get("country", "").strip()

    if not display_name:
        flash("Display name required", "error")
        return redirect(url_for("customer.profile"))

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
    return redirect(url_for("customer.profile"))


# ============================================================================
# PHASE 4.2: Subscription Management
# ============================================================================


@customer_bp.route("/subscriptions/<int:subscription_id>/upgrade", methods=["GET"])
@customer_required
def subscription_upgrade_page(subscription_id):
    customer = g.customer
    subscription = db.session.query(Subscription).get(subscription_id)

    if not subscription or subscription.customer_id != customer.id:
        flash("Subscription not found", "error")
        return redirect(url_for("customer.subscriptions_list"))

    # In production, get available tiers from catalog
    available_tiers = ["starter", "professional", "enterprise"]

    return render_template(
        "customer_subscription_upgrade.html",
        subscription=subscription,
        available_tiers=available_tiers,
    )


@customer_bp.route("/subscriptions/<int:subscription_id>/upgrade", methods=["POST"])
@customer_required
def subscription_upgrade(subscription_id):
    customer = g.customer
    subscription = db.session.query(Subscription).get(subscription_id)

    if not subscription or subscription.customer_id != customer.id:
        flash("Subscription not found", "error")
        return redirect(url_for("customer.subscriptions_list"))

    new_tier = request.form.get("new_tier", "").strip()
    if new_tier == subscription.tier:
        flash("Please select a different tier", "error")
        return redirect(
            url_for(
                "customer.subscription_upgrade_page", subscription_id=subscription_id
            )
        )

    old_tier = subscription.tier
    old_amount = subscription.amount_cents

    # Record the change
    change = SubscriptionChange(
        subscription_id=subscription.id,
        change_type="tier_change",
        old_tier=old_tier,
        new_tier=new_tier,
        old_amount_cents=old_amount,
        new_amount_cents=subscription.amount_cents,
        reason="Customer self-service tier change",
        created_by=customer.email,
    )
    db.session.add(change)

    subscription.tier = new_tier

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
        f"Subscription upgraded to {new_tier}. Changes effective next billing cycle.",
        "success",
    )
    return redirect(
        url_for("customer.subscription_detail", subscription_id=subscription_id)
    )


@customer_bp.route("/subscriptions/<int:subscription_id>/cancel", methods=["GET"])
@customer_required
def subscription_cancel_page(subscription_id):
    customer = g.customer
    subscription = db.session.query(Subscription).get(subscription_id)

    if not subscription or subscription.customer_id != customer.id:
        flash("Subscription not found", "error")
        return redirect(url_for("customer.subscriptions_list"))

    return render_template(
        "customer_subscription_cancel.html", subscription=subscription
    )


@customer_bp.route("/subscriptions/<int:subscription_id>/cancel", methods=["POST"])
@customer_required
def subscription_cancel(subscription_id):
    customer = g.customer
    subscription = db.session.query(Subscription).get(subscription_id)

    if not subscription or subscription.customer_id != customer.id:
        flash("Subscription not found", "error")
        return redirect(url_for("customer.subscriptions_list"))

    reason = request.form.get("reason", "").strip()
    confirm = request.form.get("confirm") == "on"

    if not confirm:
        flash("Please confirm cancellation", "error")
        return redirect(
            url_for(
                "customer.subscription_cancel_page", subscription_id=subscription_id
            )
        )

    subscription.status = "cancelled"

    change = SubscriptionChange(
        subscription_id=subscription.id,
        change_type="cancellation",
        old_tier=subscription.tier,
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
    return redirect(url_for("customer.subscriptions_list"))


# ============================================================================
# PHASE 4.3: Advanced Support - Customer Replies & Filtering
# ============================================================================


@customer_bp.route("/support/<int:ticket_id>/reply", methods=["POST"])
@customer_required
def support_reply(ticket_id):
    customer = g.customer
    ticket = db.session.query(SupportIncident).get(ticket_id)

    if not ticket or ticket.customer_id != customer.id:
        flash("Ticket not found", "error")
        return redirect(url_for("customer.support_list"))

    message = request.form.get("message", "").strip()
    if not message:
        flash("Message cannot be empty", "error")
        return redirect(url_for("customer.support_detail", ticket_id=ticket_id))

    reply = CustomerReply(ticket_id=ticket.id, customer_id=customer.id, message=message)
    db.session.add(reply)

    # Update ticket updated_at
    from datetime import UTC, datetime

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
    return redirect(url_for("customer.support_detail", ticket_id=ticket_id))


@customer_bp.route("/support/<int:ticket_id>", methods=["GET"])
@customer_required
def support_detail_updated(ticket_id):
    customer = g.customer
    ticket = db.session.query(SupportIncident).get(ticket_id)

    if not ticket or ticket.customer_id != customer.id:
        flash("Ticket not found", "error")
        return redirect(url_for("customer.support_list"))

    # Build conversation: initial message + all customer replies
    conversation = [
        {
            "author": customer.email,
            "message": ticket.description,
            "timestamp": ticket.created_at,
            "is_internal": False,
            "is_customer": True,
        }
    ]

    # Add all customer replies
    replies = (
        db.session.query(CustomerReply)
        .filter_by(ticket_id=ticket.id, customer_id=customer.id)
        .order_by(CustomerReply.created_at.asc())
        .all()
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

    # Calculate SLA status (customer-friendly format)
    sla_status = None
    if ticket.response_deadline or ticket.resolution_deadline:
        from datetime import UTC, datetime

        now = datetime.now(UTC)

        if ticket.resolution_deadline and now > ticket.resolution_deadline:
            sla_status = {"status": "overdue", "type": "resolution"}
        elif (
            ticket.response_deadline
            and now > ticket.response_deadline
            and not ticket.assigned_to
        ):
            sla_status = {"status": "overdue", "type": "response"}
        else:
            # Calculate time remaining
            if ticket.resolution_deadline:
                remaining = (ticket.resolution_deadline - now).total_seconds() / 3600
                if remaining > 0:
                    sla_status = {
                        "status": "on_track",
                        "hours_remaining": int(remaining),
                    }

    return render_template(
        "customer_support_detail_v2.html",
        ticket=ticket,
        conversation=conversation,
        sla_status=sla_status,
    )
