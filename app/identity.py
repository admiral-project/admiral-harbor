from datetime import UTC, datetime
from functools import wraps
from secrets import token_urlsafe

from flask import abort, flash, g, redirect, request, session, url_for
from flask_login import current_user

from app.extensions import db
from app.models import Customer, UserSession

SESSION_TIMEOUT_ADMIN_MINUTES = 30
SESSION_TIMEOUT_CUSTOMER_MINUTES = 120


def _now():
    """Return timezone-aware UTC datetime normalized for DB storage.

    SQLite may return naive datetimes, so we store and compare
    using tzinfo-naive values internally to avoid TypeError.
    """
    return datetime.now(UTC).replace(tzinfo=None)


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
        if current_user.is_authenticated:
            abort(403)
        customer = current_customer()
        if customer is None:
            if request.path.startswith("/api/"):
                abort(401)
            pending = session.get("customer_email")
            if pending and db.session.query(Customer).filter_by(email=pending).one_or_none() is not None:
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
        if session.get("customer_email") or not current_user.is_authenticated:
            abort(403)
        if not current_user.is_authenticated:
            return redirect(url_for("admin.login_page", next=request.path))
        return view(*args, **kwargs)

    return wrapped


def create_user_session(user_type, user_identifier):
    session_id = token_urlsafe(32)
    session["_session_id"] = session_id
    record = UserSession(
        session_id=session_id,
        user_type=user_type,
        user_identifier=user_identifier,
        last_activity_at=_now(),
    )
    db.session.add(record)
    db.session.commit()


def clear_user_session():
    session_id = session.pop("_session_id", None)
    if session_id:
        db.session.query(UserSession).filter_by(session_id=session_id).delete()
        db.session.commit()


def check_session_idle_timeout():
    session_id = session.get("_session_id")
    if not session_id:
        return None
    record = db.session.query(UserSession).filter_by(session_id=session_id).one_or_none()
    if record is None:
        session.pop("_session_id", None)
        return None
    now = _now()
    idle_seconds = (now - record.last_activity_at).total_seconds()
    timeout_seconds = (
        SESSION_TIMEOUT_ADMIN_MINUTES * 60 if record.user_type == "admin" else SESSION_TIMEOUT_CUSTOMER_MINUTES * 60
    )
    if idle_seconds > timeout_seconds:
        session.clear()
        db.session.query(UserSession).filter_by(session_id=session_id).delete()
        db.session.commit()
        return "expired"
    record.last_activity_at = now
    db.session.commit()
    return None
