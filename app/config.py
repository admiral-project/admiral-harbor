# SPDX-FileCopyrightText: William Moreno Reyes <williamjmorenor@gmail.com>
# SPDX-License-Identifier: Apache-2.0

import os

from app.security import get_required_env_var


class Config:
    SECRET_KEY = get_required_env_var(
        "HARBOR_SECRET_KEY",
        default="dev-secret-change-me-in-production-64-chars-minimum-required",
        prod_mode=True,
    )
    # Flask-Alembic treats script_location as the directory containing revision
    # files (unlike Alembic's conventional migrations/ directory). Paths are
    # resolved relative to app.root_path, which is the app/ package.
    ALEMBIC = {
        "script_location": "../migrations/versions",
    }
    SQLALCHEMY_DATABASE_URI = os.environ.get(
        "HARBOR_DATABASE_URL",
        "sqlite:///harbor.db",
    )
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    ADMIRAL_API_URL = os.environ.get("ADMIRAL_API_URL", "https://127.0.0.1:8443")
    # Harbor must use its scoped token. The admin token remains supported only
    # for local development and older deployments during migration.
    ADMIRAL_ADMIN_TOKEN = os.environ.get("ADMIRAL_ADMIN_TOKEN", "")
    ADMIRAL_HARBOR_API_TOKEN = os.environ.get("ADMIRAL_HARBOR_API_TOKEN", "")
    ADMIRAL_CA_FILE = os.environ.get("ADMIRAL_CA_FILE", "")
    ADMIRAL_INSECURE_SKIP_VERIFY = os.environ.get("ADMIRAL_INSECURE_SKIP_VERIFY", "0") == "1"
    HARBOR_UPLOAD_DIR = os.environ.get("HARBOR_UPLOAD_DIR", "instance/uploads")
    HARBOR_MAX_BACKUP_UPLOAD_BYTES = int(os.environ.get("HARBOR_MAX_BACKUP_UPLOAD_BYTES", str(512 * 1024 * 1024)))
    HARBOR_BACKUP_DOWNLOAD_TTL_SECONDS = int(os.environ.get("HARBOR_BACKUP_DOWNLOAD_TTL_SECONDS", "600"))
    HARBOR_MAX_FISCAL_EVIDENCE_BYTES = int(os.environ.get("HARBOR_MAX_FISCAL_EVIDENCE_BYTES", str(10 * 1024 * 1024)))
    HARBOR_ENCRYPTION_KEY = os.environ.get("HARBOR_ENCRYPTION_KEY", "")
    HARBOR_PAYPAL_CLIENT_ID = os.environ.get("HARBOR_PAYPAL_CLIENT_ID", "")
    HARBOR_PAYPAL_CLIENT_SECRET = os.environ.get("HARBOR_PAYPAL_CLIENT_SECRET", "")
    HARBOR_PAYPAL_WEBHOOK_ID = os.environ.get("HARBOR_PAYPAL_WEBHOOK_ID", "")
    HARBOR_PAYPAL_MODE = os.environ.get("HARBOR_PAYPAL_MODE", "mock")
    HARBOR_PAYPAL_WEBHOOK_MAX_AGE_SECONDS = int(os.environ.get("HARBOR_PAYPAL_WEBHOOK_MAX_AGE_SECONDS", "300"))
    HARBOR_PAYPAL_RETURN_URL = os.environ.get("HARBOR_PAYPAL_RETURN_URL", "https://localhost:5000/billing/return")
    HARBOR_PAYPAL_CANCEL_URL = os.environ.get("HARBOR_PAYPAL_CANCEL_URL", "https://localhost:5000/billing/cancel")
    HARBOR_SMTP_FROM = os.environ.get("HARBOR_SMTP_FROM", "noreply@example.com")
    HARBOR_SMTP_HOST = os.environ.get("HARBOR_SMTP_HOST", "")
    HARBOR_SMTP_PORT = int(os.environ.get("HARBOR_SMTP_PORT", "587"))
    HARBOR_SMTP_USERNAME = os.environ.get("HARBOR_SMTP_USERNAME", "")
    HARBOR_SMTP_PASSWORD = os.environ.get("HARBOR_SMTP_PASSWORD", "")
    HARBOR_SMTP_USE_TLS = os.environ.get("HARBOR_SMTP_USE_TLS", "1") == "1"
    HARBOR_SMTP_USE_SSL = os.environ.get("HARBOR_SMTP_USE_SSL", "0") == "1"
    HARBOR_EMAIL_CONFIRMATION_TTL_HOURS = int(os.environ.get("HARBOR_EMAIL_CONFIRMATION_TTL_HOURS", "72"))
    HARBOR_PORTAL_NAME = "Admiral Harbor"
    HARBOR_PORTAL_DESCRIPTION = "Customer portal"
    HARBOR_EXTERNAL_URL = os.environ.get("HARBOR_EXTERNAL_URL", "https://localhost:5000")
    HARBOR_OVERDUE_POLICY_VERSION = os.environ.get("HARBOR_OVERDUE_POLICY_VERSION", "overdue-policy-v1")
    HARBOR_OVERDUE_SUSPEND_AFTER_DAYS = int(os.environ.get("HARBOR_OVERDUE_SUSPEND_AFTER_DAYS", "5"))
    HARBOR_OVERDUE_DEPROVISION_AFTER_DAYS = int(os.environ.get("HARBOR_OVERDUE_DEPROVISION_AFTER_DAYS", "10"))
    HARBOR_OVERDUE_LAST_BACKUP_RETENTION_DAYS = int(os.environ.get("HARBOR_OVERDUE_LAST_BACKUP_RETENTION_DAYS", "15"))
    HARBOR_CSRF_CHECK_IN_TESTS = os.environ.get("HARBOR_CSRF_CHECK_IN_TESTS", "0") == "1"
    HARBOR_BOOTSTRAP_ADMIN_USER = os.environ.get("HARBOR_BOOTSTRAP_ADMIN_USER", "")
    HARBOR_BOOTSTRAP_ADMIN_PASSWORD = os.environ.get("HARBOR_BOOTSTRAP_ADMIN_PASSWORD", "")
    HARBOR_BOOTSTRAP_ADMIN_DISPLAY_NAME = os.environ.get(
        "HARBOR_BOOTSTRAP_ADMIN_DISPLAY_NAME", "Harbor Bootstrap Admin"
    )
    HARBOR_DEFAULT_CURRENCY = os.environ.get("HARBOR_DEFAULT_CURRENCY", "USD")
    SESSION_COOKIE_SECURE = os.environ.get("SESSION_COOKIE_SECURE", "1") == "1"
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Strict"


def overdue_policy(config):
    return {
        "policy_version": config["HARBOR_OVERDUE_POLICY_VERSION"],
        "grace_before_suspend_days": config["HARBOR_OVERDUE_SUSPEND_AFTER_DAYS"],
        "additional_grace_before_deprovision_days": config["HARBOR_OVERDUE_DEPROVISION_AFTER_DAYS"],
        "last_backup_retention_days": config["HARBOR_OVERDUE_LAST_BACKUP_RETENTION_DAYS"],
        "requires_acceptance_at_signup": True,
    }
