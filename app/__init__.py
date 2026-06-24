# SPDX-FileCopyrightText: William Moreno Reyes <williamjmorenor@gmail.com>
# SPDX-License-Identifier: Apache-2.0

import os

from flask import Flask, request
from flask_login import current_user
from pathlib import Path
from sqlalchemy import inspect
import logging

from app.admin import bp as admin_bp
from app.branding import ensure_default_portal_settings, get_portal_branding
from app.auth import bp as auth_bp
from app.catalog import bp as catalog_bp
from app.client import bp as client_bp
from app.csrf import init_csrf_protection
from app.extensions import alembic, db, login_manager
from app.markdown import render_markdown
from app.secrets_manager import SecretsManager
from app.identity import (
    check_session_idle_timeout,
    current_admin,
    current_customer,
)
from app.models import HarborAdminUser
from app.security import init_security_headers, validate_production_config
from app.portal import bp as main_bp
from werkzeug.middleware.proxy_fix import ProxyFix

logger = logging.getLogger("admiral-harbor")


def _database_has_tables() -> bool:
    try:
        tables = inspect(db.engine).get_table_names()
        required = {"harbor_admin_user", "customer", "catalog_app"}
        return required.issubset(tables)
    except Exception:
        return False


def create_app(config_object="app.config.Config"):
    app = Flask(__name__, instance_relative_config=True)
    app.config.from_object(config_object)
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_prefix=1)
    Path(app.config["HARBOR_UPLOAD_DIR"]).mkdir(parents=True, exist_ok=True)
    validate_production_config(app.config)

    db.init_app(app)
    alembic.init_app(app)
    login_manager.init_app(app)
    login_manager.login_view = "admin.login_page"
    init_csrf_protection(app)
    init_security_headers(app)

    # Initialize SecretsManager with the encryption key
    master_key = app.config.get("HARBOR_ENCRYPTION_KEY", "")
    import app.extensions as ext

    ext.secrets = SecretsManager(master_key)

    @login_manager.user_loader
    def load_admin(username):
        from app.models import HarborAdminUser

        return (
            db.session.query(HarborAdminUser).filter_by(username=username).one_or_none()
        )

    app.register_blueprint(auth_bp)
    app.register_blueprint(admin_bp)
    app.register_blueprint(catalog_bp)
    app.register_blueprint(client_bp)
    app.register_blueprint(main_bp)

    import json as _json

    @app.template_filter("from_json")
    def from_json_filter(value):
        try:
            return _json.loads(value)
        except (TypeError, ValueError, _json.JSONDecodeError):
            return {}

    @app.template_filter("render_markdown")
    def render_markdown_filter(value):
        return render_markdown(value)

    @app.context_processor
    def inject_principals():
        portal_branding = get_portal_branding()
        ctx = {**portal_branding}
        if request.endpoint:
            if request.endpoint.startswith("admin."):
                ctx["admin_user"] = current_admin()
                ctx["current_user"] = current_user
            elif request.endpoint.startswith("client."):
                ctx["customer"] = current_customer()
            else:
                ctx["customer"] = current_customer()
                ctx["current_user"] = current_user
        return ctx

    @app.before_request
    def _check_session_idle():
        from flask import jsonify as _jsonify, flash as _flash

        if request.endpoint in ("static",):
            return
        result = check_session_idle_timeout()
        if result == "expired":
            _flash("Your session has expired. Please log in again.", "warning")
            if request.path.startswith("/api/"):
                return _jsonify({"error": "session expired"}), 401
            from flask import redirect, url_for

            return redirect(url_for("main.index"))

    @app.errorhandler(401)
    def _unauthorized_error(_error):
        if request.path.startswith("/api/"):
            from flask import jsonify as _jsonify

            return _jsonify({"error": "unauthorized"}), 401
        return "Unauthorized", 401

    @app.errorhandler(403)
    def _forbidden_error(_error):
        if request.path.startswith("/api/"):
            from flask import jsonify as _jsonify

            return _jsonify({"error": "forbidden"}), 403
        return "Forbidden", 403

    with app.app_context():
        try:
            if _database_has_tables():
                alembic.upgrade()
            else:
                db.create_all()
                alembic.stamp()

            ensure_default_portal_settings()
            bootstrap_admin_user = app.config.get("HARBOR_BOOTSTRAP_ADMIN_USER", "")
            bootstrap_admin_password = app.config.get(
                "HARBOR_BOOTSTRAP_ADMIN_PASSWORD", ""
            )
            bootstrap_admin_display_name = app.config.get(
                "HARBOR_BOOTSTRAP_ADMIN_DISPLAY_NAME",
                "Harbor Bootstrap Admin",
            )
            env = os.environ.get("ENV", "").lower()
            if not bootstrap_admin_user or not bootstrap_admin_password:
                if env in ("dev", "development"):
                    logger.warning(
                        "HARBOR_BOOTSTRAP_ADMIN_USER/HARBOR_BOOTSTRAP_ADMIN_PASSWORD are not set; "
                        "falling back to insecure bootstrap defaults (acceptable for development)"
                    )
                    bootstrap_admin_user = "admin"  # nosec - dev fallback only
                    bootstrap_admin_password = "secret"  # nosec - dev fallback only
                else:
                    raise ValueError(
                        "HARBOR_BOOTSTRAP_ADMIN_USER and HARBOR_BOOTSTRAP_ADMIN_PASSWORD "
                        "must be set (set ENV=dev or ENV=development for development defaults)"
                    )
            HarborAdminUser.ensure_default_admin(
                username=bootstrap_admin_user,
                password=bootstrap_admin_password,
                display_name=bootstrap_admin_display_name,
            )

        except Exception as e:
            logger.error(f"Application startup failed: {str(e)}", exc_info=True)

    return app
