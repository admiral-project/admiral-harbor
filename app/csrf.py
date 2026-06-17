# SPDX-FileCopyrightText: William Moreno Reyes <williamjmorenor@gmail.com>
# SPDX-License-Identifier: Apache-2.0

import hmac
import os

from flask import current_app, jsonify, request, session
from flask_login import current_user

SAFE_METHODS = {"GET", "HEAD", "OPTIONS", "TRACE"}
EXEMPT_ENDPOINTS = {"main.paypal_webhook"}


def _generate_token():
    return os.urandom(32).hex()


def _csrf_role():
    if current_user.is_authenticated:
        return "admin"
    if session.get("customer_email"):
        return "customer"
    return "public"


def generate_csrf_token():
    data = session.get("csrf_data")
    role = _csrf_role()
    if not data or data.get("role") != role:
        token = _generate_token()
        session["csrf_data"] = {"token": token, "role": role}
        session["csrf_token"] = token
        return token
    return data["token"]


def _extract_token():
    token = request.headers.get("X-CSRF-Token", "").strip()
    if token:
        return token

    if request.is_json:
        payload = request.get_json(silent=True) or {}
        token = str(payload.get("csrf_token", "")).strip()
        if token:
            return token

    return request.form.get("csrf_token", "").strip()


def validate_csrf_request():
    if request.method in SAFE_METHODS:
        return None

    if request.endpoint in EXEMPT_ENDPOINTS:
        return None

    if current_app.config.get("TESTING") and not current_app.config.get(
        "HARBOR_CSRF_CHECK_IN_TESTS", False
    ):
        return None

    data = session.get("csrf_data")
    if not data:
        return jsonify({"error": "CSRF token missing"}), 403

    if data.get("role") != _csrf_role():
        return jsonify({"error": "CSRF token invalid"}), 403

    session_token = data.get("token")
    token = _extract_token()
    if not token:
        return jsonify({"error": "CSRF token missing"}), 403

    if not hmac.compare_digest(token, session_token):
        return jsonify({"error": "CSRF token invalid"}), 403

    return None


def init_csrf_protection(app):
    @app.before_request
    def csrf_protect():
        response = validate_csrf_request()
        if response is not None:
            return response

    @app.after_request
    def expose_csrf_token(response):
        token = session.get("csrf_token")
        if token:
            response.headers["X-CSRF-Token"] = token
        return response

    @app.context_processor
    def inject_csrf_token():
        return {"csrf_token": generate_csrf_token}
