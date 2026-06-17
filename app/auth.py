# SPDX-FileCopyrightText: William Moreno Reyes <williamjmorenor@gmail.com>
# SPDX-License-Identifier: Apache-2.0

from datetime import UTC, datetime, timedelta
from email.message import EmailMessage
from hashlib import sha256
import smtplib
import ssl
from secrets import token_urlsafe

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
from flask import (
    Blueprint,
    current_app,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from flask_login import logout_user

from app.extensions import db
from app.identity import current_customer, login_required
from app.models import AuditLog, Customer
from app.rate_limit import RateLimiter

_COUNTRIES = [
    ("NI", "Nicaragua"),
]

ph = PasswordHasher()
bp = Blueprint("auth", __name__, url_prefix="/auth")
login_limiter = RateLimiter(max_attempts=5, window_seconds=60)


def _login_customer(customer):
    session["customer_token"] = f"customer:{customer.public_id}"
    session["customer_email"] = customer.email
    session["customer_public_id"] = customer.public_id


def _token_hash(token):
    return sha256(token.encode("utf-8")).hexdigest()


def _confirmation_url(token):
    external_base = current_app.config.get("HARBOR_EXTERNAL_URL", "").rstrip("/")
    path = url_for("auth.confirm_email", token=token)
    if external_base:
        return f"{external_base}{path}"
    return path


def _confirmation_is_expired(customer):
    sent_at = customer.email_confirmation_sent_at
    if sent_at is None:
        return True
    ttl_hours = current_app.config.get("HARBOR_EMAIL_CONFIRMATION_TTL_HOURS", 72)
    return datetime.now(UTC) > sent_at + timedelta(hours=ttl_hours)


def _send_confirmation_email(customer, token):
    host = (current_app.config.get("HARBOR_SMTP_HOST") or "").strip()
    if not host:
        return False

    message = EmailMessage()
    message["Subject"] = (
        f"Confirm your Harbor account for {current_app.config['HARBOR_PORTAL_NAME']}"
    )
    message["From"] = current_app.config.get("HARBOR_SMTP_FROM", "noreply@example.com")
    message["To"] = customer.email
    message.set_content(
        "Hello {name},\n\n"
        "Your Harbor account was created.\n"
        "Confirm your email with this link:\n\n"
        "{url}\n\n"
        "If you cannot use email confirmation, a Harbor administrator can approve your account manually.\n".format(
            name=customer.display_name,
            url=_confirmation_url(token),
        )
    )

    port = int(current_app.config.get("HARBOR_SMTP_PORT", 587))
    username = (current_app.config.get("HARBOR_SMTP_USERNAME") or "").strip()
    password = current_app.config.get("HARBOR_SMTP_PASSWORD") or ""
    use_tls = bool(current_app.config.get("HARBOR_SMTP_USE_TLS", True))
    use_ssl = bool(current_app.config.get("HARBOR_SMTP_USE_SSL", False))
    context = ssl.create_default_context()

    if use_ssl:
        smtp_factory = smtplib.SMTP_SSL
    else:
        smtp_factory = smtplib.SMTP

    with smtp_factory(host, port, timeout=10) as smtp:
        if use_tls and not use_ssl:
            smtp.starttls(context=context)
        if username:
            smtp.login(username, password)
        smtp.send_message(message)
    return True


@bp.route("/login", methods=["GET"])
def login_page():
    return render_template("auth_login.html")


@bp.route("/register", methods=["GET"])
def register_page():
    return render_template("auth_register.html", countries=_COUNTRIES)


@bp.route("/login", methods=["POST"])
def login():
    ip = request.remote_addr or "unknown"
    allowed, remaining = login_limiter.is_allowed(ip)
    if not allowed:
        if request.is_json:
            return jsonify({
                "error": f"Too many login attempts. Try again in {remaining} second(s)."
            }), 429
        flash("Demasiados intentos de inicio de sesion. Intenta de nuevo en 30 segundos.", "error")
        return redirect(url_for("auth.login_page"))

    if request.content_type and "application/json" not in request.content_type:
        payload = request.form
    else:
        payload = request.get_json(silent=True) or {}
    email = payload.get("email", "").strip().lower()
    password = payload.get("password", "")
    if not email or not password:
        if request.is_json:
            return jsonify({"error": "email and password are required"}), 400
        flash("Email and password are required.", "error")
        return redirect(url_for("auth.login_page"))

    customer = db.session.query(Customer).filter_by(email=email).one_or_none()
    if customer is None:
        if request.is_json:
            return jsonify({"error": "invalid credentials"}), 401
        flash("Invalid credentials.", "error")
        return redirect(url_for("auth.login_page"))

    try:
        ph.verify(customer.password_hash, password)
    except VerifyMismatchError:
        if request.is_json:
            return jsonify({"error": "invalid credentials"}), 401
        flash("Invalid credentials.", "error")
        return redirect(url_for("auth.login_page"))

    if not customer.can_access():
        if request.is_json:
            return jsonify({"error": "account pending activation"}), 403
        if customer.signup_status == "rejected":
            flash("Your account was rejected by Harbor administration.", "error")
        else:
            flash("Your account is pending activation.", "warning")
        return redirect(url_for("auth.login_page"))

    logout_user()
    _login_customer(customer)
    login_limiter.reset(ip)
    if request.is_json:
        return jsonify(
            {"status": "ok", "email": email, "public_id": customer.public_id}
        )
    return redirect(url_for("main.dashboard"))


@bp.route("/register", methods=["POST"])
def register():
    if request.content_type and "application/json" not in request.content_type:
        payload = request.form
    else:
        payload = request.get_json(silent=True) or {}
    email = payload.get("email", "").strip().lower()
    password = payload.get("password", "")
    display_name = payload.get("display_name", "").strip()
    country = payload.get("country", "").strip().upper()
    accept_terms = bool(payload.get("accept_terms"))
    if not email or not password or not display_name:
        if request.is_json:
            return (
                jsonify({"error": "display_name, email and password are required"}),
                400,
            )
        flash("Display name, email and password are required.", "error")
        return redirect(url_for("auth.register_page"))
    if not accept_terms:
        if request.is_json:
            return jsonify({"error": "terms acceptance required"}), 400
        flash("Terms acceptance required.", "error")
        return redirect(url_for("auth.register_page"))
    if db.session.query(Customer).filter_by(email=email).one_or_none() is not None:
        if request.is_json:
            return jsonify({"error": "customer already exists"}), 409
        flash("Customer already exists.", "error")
        return redirect(url_for("auth.register_page"))

    customer = Customer(
        email=email,
        display_name=display_name,
        password_hash=ph.hash(password),
        country=country or None,
        signup_status="pending",
        email_confirmation_token_hash=None,
        email_confirmation_sent_at=None,
        email_confirmed_at=None,
        terms_policy_version=current_app.config["HARBOR_OVERDUE_POLICY_VERSION"],
        terms_accepted_at=datetime.now(UTC),
    )
    confirmation_token = token_urlsafe(32)
    customer.email_confirmation_token_hash = _token_hash(confirmation_token)
    customer.email_confirmation_sent_at = datetime.now(UTC)
    db.session.add(customer)
    db.session.add(
        AuditLog(
            actor=email,
            action="signup_requested",
            detail=f"Signup requested for {email}",
            ip_address=request.remote_addr or "",
        )
    )
    db.session.commit()

    email_sent = False
    email_error = None
    try:
        email_sent = _send_confirmation_email(customer, confirmation_token)
    except (
        Exception
    ) as exc:  # pragma: no cover - email transport failures are runtime dependent
        email_error = str(exc)
        current_app.logger.warning(
            "Could not send Harbor confirmation email for %s: %s", email, exc
        )

    if email_sent:
        db.session.add(
            AuditLog(
                actor=email,
                action="signup_confirmation_email_sent",
                detail=f"Confirmation email sent to {email}",
                ip_address=request.remote_addr or "",
            )
        )
        db.session.commit()
    elif email_error:
        db.session.add(
            AuditLog(
                actor=email,
                action="signup_confirmation_email_failed",
                detail=f"Confirmation email failed for {email}: {email_error}",
                ip_address=request.remote_addr or "",
            )
        )
        db.session.commit()

    if request.is_json:
        response = {
            "status": "pending",
            "customer": customer.as_dict(),
            "email_sent": email_sent,
        }
        if email_error:
            response["email_error"] = email_error
        return jsonify(response), 202

    if email_sent:
        flash(
            "Cuenta creada. Revisa tu correo para confirmarla o espera la aprobación del admin de Harbor.",
            "success",
        )
    else:
        flash(
            "Cuenta creada. Harbor no pudo enviar el correo de confirmación, así que el admin de Harbor puede aprobarla manualmente.",
            "warning",
        )
    return redirect(url_for("auth.login_page"))


@bp.route("/confirm/<token>", methods=["GET"])
def confirm_email(token):
    token = (token or "").strip()
    if not token:
        flash("Confirmation token is missing.", "error")
        return redirect(url_for("auth.login_page"))

    token_hash = _token_hash(token)
    customer = (
        db.session.query(Customer)
        .filter_by(email_confirmation_token_hash=token_hash)
        .one_or_none()
    )
    if customer is None:
        flash("Confirmation link is invalid or expired.", "error")
        return redirect(url_for("auth.login_page"))
    if customer.signup_status == "rejected":
        flash("This account was rejected by Harbor administration.", "error")
        return redirect(url_for("auth.login_page"))
    if _confirmation_is_expired(customer):
        flash(
            "Confirmation link expired. Ask Harbor administration to approve your account.",
            "error",
        )
        return redirect(url_for("auth.login_page"))

    customer.email_confirmed_at = datetime.now(UTC)
    customer.email_confirmation_token_hash = None
    customer.signup_status = "active"
    customer.reviewed_at = customer.reviewed_at or datetime.now(UTC)
    customer.reviewed_by = customer.reviewed_by or "email-confirmation"
    db.session.add(
        AuditLog(
            actor=customer.email,
            action="email_confirmed",
            detail=f"Email confirmed for {customer.email}",
            ip_address=request.remote_addr or "",
        )
    )
    db.session.commit()
    flash("Email confirmed. You can now log in.", "success")
    return redirect(url_for("auth.login_page"))


@bp.route("/logout", methods=["POST"])
def logout():
    session.pop("customer_token", None)
    session.pop("customer_email", None)
    session.pop("customer_public_id", None)
    session.clear()
    if request.is_json or request.headers.get("Accept") == "application/json":
        return jsonify({"status": "logged_out"})
    return redirect(url_for("main.index"))


@bp.route("/me")
def me():
    customer = current_customer()
    if customer is None:
        return jsonify({"error": "not authenticated"}), 401
    return jsonify(
        {
            "email": customer.email,
            "authenticated": True,
            "public_id": customer.public_id,
        }
    )


@bp.route("/terms")
def terms():
    return jsonify(
        {
            "policy_version": current_app.config["HARBOR_OVERDUE_POLICY_VERSION"],
            "grace_before_suspend_days": current_app.config[
                "HARBOR_OVERDUE_SUSPEND_AFTER_DAYS"
            ],
            "additional_grace_before_deprovision_days": current_app.config[
                "HARBOR_OVERDUE_DEPROVISION_AFTER_DAYS"
            ],
            "last_backup_retention_days": current_app.config[
                "HARBOR_OVERDUE_LAST_BACKUP_RETENTION_DAYS"
            ],
            "requires_acceptance_at_signup": True,
        }
    )


@bp.route("/profile", methods=["GET", "POST"])
@login_required
def profile():
    customer = current_customer()
    if request.method == "POST":
        display_name = request.form.get("display_name", "").strip()
        country = request.form.get("country", "").strip().upper()
        new_password = request.form.get("new_password", "").strip()
        current_password = request.form.get("current_password", "")

        if display_name:
            customer.display_name = display_name
        if country:
            customer.country = country
        if new_password and current_password:
            try:
                ph.verify(customer.password_hash, current_password)
                customer.password_hash = ph.hash(new_password)
            except VerifyMismatchError:
                flash("Current password is incorrect.", "error")
                return redirect(url_for("auth.profile"))
        db.session.add(
            AuditLog(
                actor=customer.email,
                action="profile_updated",
                detail="Profile updated",
                ip_address=request.remote_addr or "",
            )
        )
        db.session.commit()
        flash("Profile updated.", "success")
        return redirect(url_for("auth.profile"))
    return render_template("auth_profile.html", customer=customer, countries=_COUNTRIES)
