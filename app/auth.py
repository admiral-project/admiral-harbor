# SPDX-FileCopyrightText: William Moreno Reyes <williamjmorenor@gmail.com>
# SPDX-License-Identifier: Apache-2.0

from datetime import datetime

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
from flask import Blueprint, current_app, flash, jsonify, redirect, render_template, request, session, url_for

from app.extensions import db
from app.identity import current_customer, login_required
from app.models import AuditLog, Customer

_COUNTRIES = [
    ("NI", "Nicaragua"),
]

ph = PasswordHasher()
bp = Blueprint("auth", __name__, url_prefix="/auth")


def _login_customer(customer):
    session["customer_token"] = f"customer:{customer.public_id}"
    session["customer_email"] = customer.email
    session["customer_public_id"] = customer.public_id


@bp.route("/login", methods=["GET"])
def login_page():
    return render_template("auth_login.html")


@bp.route("/register", methods=["GET"])
def register_page():
    return render_template("auth_register.html", countries=_COUNTRIES)


@bp.route("/login", methods=["POST"])
def login():
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

    _login_customer(customer)
    if request.is_json:
        return jsonify({"status": "ok", "email": email, "public_id": customer.public_id})
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
            return jsonify({"error": "display_name, email and password are required"}), 400
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
        terms_policy_version=current_app.config["HARBOR_OVERDUE_POLICY_VERSION"],
        terms_accepted_at=datetime.utcnow(),
    )
    db.session.add(customer)
    db.session.commit()

    _login_customer(customer)
    if request.is_json:
        return jsonify({"status": "ok", "customer": customer.as_dict()}), 201
    return redirect(url_for("main.dashboard"))


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
    return jsonify({"email": customer.email, "authenticated": True, "public_id": customer.public_id})


@bp.route("/terms")
def terms():
    return jsonify(
        {
            "policy_version": current_app.config["HARBOR_OVERDUE_POLICY_VERSION"],
            "grace_before_suspend_days": current_app.config["HARBOR_OVERDUE_SUSPEND_AFTER_DAYS"],
            "additional_grace_before_deprovision_days": current_app.config["HARBOR_OVERDUE_DEPROVISION_AFTER_DAYS"],
            "last_backup_retention_days": current_app.config["HARBOR_OVERDUE_LAST_BACKUP_RETENTION_DAYS"],
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
        db.session.add(AuditLog(actor=customer.email, action="profile_updated", detail="Profile updated", ip_address=request.remote_addr or ""))
        db.session.commit()
        flash("Profile updated.", "success")
        return redirect(url_for("auth.profile"))
    return render_template("auth_profile.html", customer=customer, countries=_COUNTRIES)
