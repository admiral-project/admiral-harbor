from functools import wraps

from flask import abort, flash, g, redirect, request, session, url_for
from flask_login import current_user

from app.extensions import db
from app.models import Customer


def current_customer():
    email = session.get("customer_email")
    if not email:
        return None
    customer = db.session.query(Customer).filter_by(email=email).one_or_none()
    if customer is None or not customer.can_access():
        return None
    return customer


def current_admin():
    if current_user.is_authenticated:
        return current_user
    return None


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        customer = current_customer()
        if customer is None:
            if request.path.startswith("/api/"):
                abort(401)
            pending = session.get("customer_email")
            if (
                pending
                and db.session.query(Customer).filter_by(email=pending).one_or_none()
                is not None
            ):
                session.pop("customer_token", None)
                session.pop("customer_email", None)
                session.pop("customer_public_id", None)
                flash("Your account is pending activation.", "warning")
            return redirect(url_for("auth.login_page", next=request.path))
        return view(*args, **kwargs)

    return wrapped


def customer_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if current_user.is_authenticated:
            abort(403)
        customer = current_customer()
        if customer is None:
            abort(403)
        g.customer = customer
        return view(*args, **kwargs)

    return wrapped


def admin_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if session.get("customer_email"):
            abort(403)
        return view(*args, **kwargs)

    return wrapped
