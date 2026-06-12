from functools import wraps

from flask import abort, redirect, request, session, url_for
from flask_login import current_user, login_required as flask_login_required

from app.extensions import db
from app.models import Customer, HarborAdminUser


def current_customer():
    email = session.get("customer_email")
    if not email:
        return None
    return db.session.query(Customer).filter_by(email=email).one_or_none()


def current_admin():
    if current_user.is_authenticated:
        return current_user
    return None


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if current_customer() is None:
            if request.path.startswith("/api/"):
                abort(401)
            return redirect(url_for("auth.login_page", next=request.path))
        return view(*args, **kwargs)

    return wrapped


def admin_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not current_user.is_authenticated:
            if request.path.startswith("/api/"):
                abort(401)
            return redirect(url_for("admin.login_page", next=request.path))
        return view(*args, **kwargs)

    return wrapped
