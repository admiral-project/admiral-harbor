# SPDX-FileCopyrightText: William Moreno Reyes <williamjmorenor@gmail.com>
# SPDX-License-Identifier: Apache-2.0

import logging
import os

from flask import Flask

logger = logging.getLogger("admiral-harbor")


def _warn_default(name, value):
    if (
        not value
        or value.startswith("dev-")
        or value == "dev-secret-change-me"
        or value == "dev-token"
        or value == "dev-encryption-key"
    ):
        logger.warning(
            "%s is using a development default; set it explicitly for production", name
        )


def validate_production_config(config):
    secret_key = config.get("SECRET_KEY", "")
    admiral_token = config.get("ADMIRAL_ADMIN_TOKEN", "")
    encryption_key = config.get("HARBOR_ENCRYPTION_KEY", "")
    database_url = config.get("SQLALCHEMY_DATABASE_URI", "")

    _warn_default("SECRET_KEY", secret_key)
    _warn_default("ADMIRAL_ADMIN_TOKEN", admiral_token)
    _warn_default("HARBOR_ENCRYPTION_KEY", encryption_key)

    if os.environ.get("ENV", "").lower() != "production":
        return

    errors = []

    if (
        not secret_key
        or secret_key.startswith("dev-")
        or secret_key == "dev-secret-change-me"  # nosec B105 - checking for dev value
    ):
        errors.append("SECRET_KEY must be replaced before production")
    if len(secret_key) < 32:
        errors.append("SECRET_KEY must be at least 32 characters in production")

    if (
        not admiral_token
        or admiral_token.startswith("dev-")
        or admiral_token == "dev-token"  # nosec B105 - checking for dev value
    ):
        errors.append("ADMIRAL_ADMIN_TOKEN must be replaced before production")

    if (
        not encryption_key
        or encryption_key.startswith("dev-")
        or encryption_key == "dev-encryption-key"
    ):
        errors.append("HARBOR_ENCRYPTION_KEY must be replaced before production")

    if database_url.startswith("sqlite:///"):
        errors.append(
            "SQLALCHEMY_DATABASE_URI must not use the SQLite development default in production"
        )

    if config.get("ADMIRAL_INSECURE_SKIP_VERIFY"):
        errors.append("ADMIRAL_INSECURE_SKIP_VERIFY must be false in production")

    if errors:
        raise ValueError(
            "Production security validation failed:\n- " + "\n- ".join(errors)
        )


def init_security_headers(app: Flask) -> None:
    @app.after_request
    def add_security_headers(response):
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-XSS-Protection"] = "1; mode=block"
        response.headers["Strict-Transport-Security"] = (
            "max-age=31536000; includeSubDomains; preload"
        )
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; "
            "script-src 'self' 'unsafe-eval' https://unpkg.com https://cdnjs.cloudflare.com; "
            "style-src 'self' https://cdnjs.cloudflare.com https://unpkg.com; "
            "img-src 'self' data:; "
            "font-src 'self' https://cdnjs.cloudflare.com https://unpkg.com; "
            "connect-src 'self'; "
            "frame-ancestors 'none'; "
            "base-uri 'self'; "
            "form-action 'self'"
        )
        response.headers["Referrer-Policy"] = "same-origin"
        response.headers["Permissions-Policy"] = (
            "geolocation=(), microphone=(), camera=(), usb=(), payment=()"
        )
        return response

    logger.info("Security headers initialized")
