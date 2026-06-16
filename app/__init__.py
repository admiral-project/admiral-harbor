# SPDX-FileCopyrightText: William Moreno Reyes <williamjmorenor@gmail.com>
# SPDX-License-Identifier: Apache-2.0

import os

from flask import Flask
from flask_login import current_user
from pathlib import Path
from sqlalchemy import inspect
import logging

from app.admin import bp as admin_bp
from app.branding import ensure_default_portal_settings, get_portal_branding
from app.auth import bp as auth_bp
from app.catalog import bp as catalog_bp
from app.customer import customer_bp
from app.csrf import init_csrf_protection
from app.extensions import alembic, db, login_manager
from app.identity import current_admin, current_customer
from app.models import HarborAdminUser
from app.security import init_security_headers, validate_production_config
from app.routes import bp as main_bp

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
    Path(app.config["HARBOR_UPLOAD_DIR"]).mkdir(parents=True, exist_ok=True)
    validate_production_config(app.config)

    db.init_app(app)
    alembic.init_app(app)
    login_manager.init_app(app)
    login_manager.login_view = "admin.login_page"
    init_csrf_protection(app)
    init_security_headers(app)

    @login_manager.user_loader
    def load_admin(username):
        from app.models import HarborAdminUser

        return (
            db.session.query(HarborAdminUser).filter_by(username=username).one_or_none()
        )

    app.register_blueprint(auth_bp)
    app.register_blueprint(admin_bp)
    app.register_blueprint(catalog_bp)
    app.register_blueprint(customer_bp)
    app.register_blueprint(main_bp)

    import json as _json

    @app.template_filter("from_json")
    def from_json_filter(value):
        try:
            return _json.loads(value)
        except (TypeError, ValueError, _json.JSONDecodeError):
            return {}

    @app.context_processor
    def inject_principals():
        portal_branding = get_portal_branding()
        return {
            "customer": current_customer(),
            "admin_user": current_admin(),
            "current_user": current_user,
            **portal_branding,
        }

    with app.app_context():
        try:
            if not _database_has_tables():
                db.create_all()

            ensure_default_portal_settings()

            ensure_default_portal_settings()
            bootstrap_admin_user = app.config.get("HARBOR_BOOTSTRAP_ADMIN_USER", "")
            bootstrap_admin_password = app.config.get(
                "HARBOR_BOOTSTRAP_ADMIN_PASSWORD", ""
            )
            bootstrap_admin_display_name = app.config.get(
                "HARBOR_BOOTSTRAP_ADMIN_DISPLAY_NAME",
                "Harbor Bootstrap Admin",
            )
            is_production = os.environ.get("ENV", "").lower() == "production"
            if not bootstrap_admin_user or not bootstrap_admin_password:
                if is_production:
                    raise ValueError(
                        "HARBOR_BOOTSTRAP_ADMIN_USER and HARBOR_BOOTSTRAP_ADMIN_PASSWORD "
                        "must be set when ENV=production"
                    )
                logger.warning(
                    "HARBOR_BOOTSTRAP_ADMIN_USER/HARBOR_BOOTSTRAP_ADMIN_PASSWORD are not set; "
                    "falling back to insecure bootstrap defaults"
                )
                bootstrap_admin_user = "admin"
                bootstrap_admin_password = "secret"
            HarborAdminUser.ensure_default_admin(
                username=bootstrap_admin_user,
                password=bootstrap_admin_password,
                display_name=bootstrap_admin_display_name,
            )

            # Initial handshake: sync catalog from admirald on startup
            try:
                from app.catalog_service import sync_catalog

                logger.info("Executing initial catalog sync handshake...")
                result = sync_catalog(origin="startup", actor=None)
                if result["success"]:
                    logger.info(
                        f"Initial sync: {result['synced']} new, {result['updated']} updated, "
                        f"{result['marked_missing']} marked missing"
                    )
                else:
                    logger.warning(
                        f"Initial sync failed: {result.get('error', 'Unknown error')}"
                    )
            except Exception as e:
                logger.error(f"Initial sync error: {str(e)}", exc_info=True)
        except Exception as e:
            logger.error(f"Application startup failed: {str(e)}", exc_info=True)

    return app
