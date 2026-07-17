# SPDX-FileCopyrightText: William Moreno Reyes <williamjmorenor@gmail.com>
# SPDX-License-Identifier: Apache-2.0

import logging
import os
from typing import Optional

from flask import Flask

logger = logging.getLogger("admiral-harbor")


def validate_password_strength(password: str, minimum_length: int = 12) -> str | None:
    """Return an operator-facing error when a password is too weak."""
    if len(password) < minimum_length:
        return f"Password must be at least {minimum_length} characters long."
    return None


def _warn_default(name, value):
    if not value or value.startswith("dev-") or value == "dev-token" or value == "dev-encryption-key":
        logger.warning("%s is using a development default; set it explicitly for production", name)


def get_required_env_var(name: str, default: Optional[str] = None, prod_mode: bool = False) -> str:
    value = os.environ.get(name)
    if not value:
        is_production = os.environ.get("ENV", "").lower() == "production"
        if is_production and prod_mode:
            raise ValueError(
                f"SECURITY: Required environment variable {name} not set in production. "
                f"Set {name} before starting the application."
            )
        if default:
            logger.warning(
                "Environment variable %s not set; using development default. "
                "This value is required for production and must be set explicitly.",
                name,
            )
            return default
        if is_production:
            raise ValueError(f"SECURITY: Environment variable {name} is required but not set")
        logger.warning("Environment variable %s not set", name)
        return ""
    return value


def validate_production_config(config):
    secret_key = config.get("SECRET_KEY", "")
    harbor_token = config.get("ADMIRAL_HARBOR_API_TOKEN", "")
    encryption_key = config.get("HARBOR_ENCRYPTION_KEY", "")
    database_url = config.get("SQLALCHEMY_DATABASE_URI", "")

    _warn_default("SECRET_KEY", secret_key)
    if harbor_token:
        _warn_default("ADMIRAL_HARBOR_API_TOKEN", harbor_token)
    _warn_default("HARBOR_ENCRYPTION_KEY", encryption_key)

    paypal_mode = config.get("HARBOR_PAYPAL_MODE", "mock")
    if paypal_mode == "mock":
        logger.warning(
            "HARBOR_PAYPAL_MODE is 'mock': no real PayPal calls will be made. "
            "Set HARBOR_PAYPAL_MODE=sandbox or live for payment processing."
        )

    if os.environ.get("ENV", "").lower() != "production":
        return

    errors = []

    if not secret_key or secret_key.startswith("dev-"):
        errors.append("SECRET_KEY must be replaced before production")
    if len(secret_key) < 32:
        errors.append("SECRET_KEY must be at least 32 characters in production")

    if not harbor_token or harbor_token.startswith("dev-"):
        errors.append("ADMIRAL_HARBOR_API_TOKEN must be replaced before production")

    if not encryption_key or encryption_key.startswith("dev-") or encryption_key == "dev-encryption-key":
        errors.append("HARBOR_ENCRYPTION_KEY must be replaced before production")

    if database_url.startswith("sqlite:///"):
        errors.append("SQLALCHEMY_DATABASE_URI must not use the SQLite development default in production")

    if config.get("ADMIRAL_INSECURE_SKIP_VERIFY"):
        errors.append("ADMIRAL_INSECURE_SKIP_VERIFY must be false in production")

    if paypal_mode == "mock":
        errors.append("HARBOR_PAYPAL_MODE must not be 'mock' in production; use 'sandbox' or 'live'")

    if errors:
        raise ValueError("Production security validation failed:\n- " + "\n- ".join(errors))


def init_security_headers(app: Flask) -> None:
    @app.after_request
    def add_security_headers(response):
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-XSS-Protection"] = "1; mode=block"
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains; preload"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; "
            "script-src 'self' https://unpkg.com https://cdnjs.cloudflare.com; "
            "style-src 'self' https://cdnjs.cloudflare.com https://unpkg.com; "
            "img-src 'self' data:; "
            "font-src 'self' https://cdnjs.cloudflare.com https://unpkg.com; "
            "connect-src 'self'; "
            "frame-ancestors 'none'; "
            "base-uri 'self'; "
            "form-action 'self'"
        )
        response.headers["Referrer-Policy"] = "same-origin"
        response.headers["Permissions-Policy"] = "geolocation=(), microphone=(), camera=(), usb=(), payment=()"
        return response

    logger.info("Security headers initialized")
