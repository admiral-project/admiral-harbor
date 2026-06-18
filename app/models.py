# SPDX-FileCopyrightText: William Moreno Reyes <williamjmorenor@gmail.com>
# SPDX-License-Identifier: Apache-2.0

from datetime import UTC, datetime
from hashlib import sha256
import logging
from uuid import uuid4

from argon2 import PasswordHasher
from flask_login import UserMixin
from sqlalchemy.exc import IntegrityError

from app.extensions import db

ph = PasswordHasher()
logger = logging.getLogger("admiral-harbor")


class Customer(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    public_id = db.Column(
        db.String(64),
        unique=True,
        nullable=False,
        index=True,
        default=lambda: f"hcus_{uuid4().hex[:16]}",
    )
    email = db.Column(db.String(255), unique=True, nullable=False, index=True)
    display_name = db.Column(db.String(255), nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    country = db.Column(db.String(4), nullable=True, index=True)
    signup_status = db.Column(
        db.String(30), nullable=False, default="pending", index=True
    )
    is_active = db.Column(db.Boolean, default=True, nullable=False)
    blocked_at = db.Column(db.DateTime, nullable=True)
    email_confirmation_token_hash = db.Column(db.String(128), nullable=True, index=True)
    email_confirmation_sent_at = db.Column(db.DateTime, nullable=True)
    email_confirmed_at = db.Column(db.DateTime, nullable=True)
    reviewed_at = db.Column(db.DateTime, nullable=True)
    reviewed_by = db.Column(db.String(255), nullable=True)
    rejection_reason = db.Column(db.Text, nullable=True)
    terms_policy_version = db.Column(
        db.String(50), nullable=False, default="overdue-policy-v1"
    )
    terms_accepted_at = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(
        db.DateTime, default=lambda: datetime.now(UTC), nullable=False
    )
    updated_at = db.Column(
        db.DateTime,
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
        nullable=False,
    )

    def can_access(self):
        return (
            self.is_active
            and self.signup_status == "active"
            and self.blocked_at is None
        )

    def as_dict(self):
        return {
            "id": self.id,
            "public_id": self.public_id,
            "email": self.email,
            "display_name": self.display_name,
            "country": self.country,
            "signup_status": self.signup_status,
            "is_active": self.is_active,
            "email_confirmed_at": (
                self.email_confirmed_at.isoformat() if self.email_confirmed_at else None
            ),
            "reviewed_at": self.reviewed_at.isoformat() if self.reviewed_at else None,
            "reviewed_by": self.reviewed_by,
            "terms_policy_version": self.terms_policy_version,
            "terms_accepted_at": (
                self.terms_accepted_at.isoformat() if self.terms_accepted_at else None
            ),
        }


class HarborAdminUser(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(255), unique=True, nullable=False, index=True)
    display_name = db.Column(db.String(255), nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    is_active = db.Column(db.Boolean, default=True, nullable=False)
    created_at = db.Column(
        db.DateTime, default=lambda: datetime.now(UTC), nullable=False
    )
    updated_at = db.Column(
        db.DateTime,
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
        nullable=False,
    )

    def get_id(self):
        return self.username

    @classmethod
    def ensure_default_admin(  # nosec B107 - sentinel defaults
        cls, username="", password="", display_name="Harbor Bootstrap Admin"
    ):
        if (
            username
            and db.session.query(cls).filter_by(username=username).one_or_none()
        ):
            return
        if not username or not password:
            return
        if db.session.query(cls).count() > 0:
            return
        try:
            db.session.add(
                cls(
                    username=username,
                    display_name=display_name,
                    password_hash=ph.hash(password),
                )
            )
            db.session.commit()
        except IntegrityError:
            db.session.rollback()
            if db.session.query(cls).filter_by(username=username).one_or_none():
                logger.warning(
                    "Bootstrap admin %r already exists; skipping creation", username
                )
                return
            raise

    def as_dict(self):
        return {"username": self.username, "display_name": self.display_name}


class CatalogApp(db.Model):
    """Application catalog: technical snapshot from admirald + commercial metadata"""

    id = db.Column(db.Integer, primary_key=True)

    # Technical identity from admirald (immutable after creation)
    upstream_app_id = db.Column(db.String(120), unique=True, nullable=False, index=True)

    # Sync status: synced | missing_upstream | sync_error
    sync_status = db.Column(db.String(50), default="synced", nullable=False)

    # Upstream presence and current values (refreshed during sync)
    upstream_present = db.Column(db.Boolean, default=True, nullable=False)
    upstream_availability = db.Column(
        db.String(20), default="available", nullable=False
    )
    upstream_revision = db.Column(db.Integer, default=0, nullable=False)
    upstream_checksum = db.Column(db.String(64), nullable=True)

    # Commercial metadata (editable locally)
    name = db.Column(db.String(255), nullable=False)
    one_liner = db.Column(db.String(255), nullable=False, default="")
    description_md = db.Column(db.Text, nullable=False, default="")
    logo_url = db.Column(db.String(1024), nullable=True)
    homepage_url = db.Column(db.String(1024), nullable=True)
    repository_url = db.Column(db.String(1024), nullable=True)
    documentation_url = db.Column(db.String(1024), nullable=True)
    bug_tracker_url = db.Column(db.String(1024), nullable=True)
    support_url = db.Column(db.String(1024), nullable=True)

    # Visibility control (local editable)
    catalog_enabled = db.Column(db.Boolean, default=False, nullable=False)
    sort_order = db.Column(db.Integer, default=0, nullable=False)

    # Technical snapshot (JSON copy of the definition from admirald)
    technical_snapshot = db.Column(db.Text, nullable=True)

    # Audit timestamps
    synced_at = db.Column(db.DateTime, nullable=True)
    sync_last_attempted_at = db.Column(db.DateTime, nullable=True)
    sync_last_error = db.Column(db.Text, nullable=True)
    created_at = db.Column(
        db.DateTime, default=lambda: datetime.now(UTC), nullable=False
    )
    updated_at = db.Column(
        db.DateTime,
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
        nullable=False,
    )

    @property
    def slug(self):
        return self.upstream_app_id

    def as_dict(self):
        return {
            "upstream_app_id": self.upstream_app_id,
            "slug": self.slug,
            "sync_status": self.sync_status,
            "upstream_present": self.upstream_present,
            "upstream_availability": self.upstream_availability,
            "upstream_revision": self.upstream_revision,
            "upstream_checksum": self.upstream_checksum,
            "name": self.name,
            "one_liner": self.one_liner,
            "description_md": self.description_md,
            "logo_url": self.logo_url,
            "homepage_url": self.homepage_url,
            "repository_url": self.repository_url,
            "documentation_url": self.documentation_url,
            "bug_tracker_url": self.bug_tracker_url,
            "support_url": self.support_url,
            "catalog_enabled": self.catalog_enabled,
            "sort_order": self.sort_order,
            "synced_at": self.synced_at.isoformat() if self.synced_at else None,
            "sync_last_attempted_at": (
                self.sync_last_attempted_at.isoformat()
                if self.sync_last_attempted_at
                else None
            ),
        }


class CatalogAppTier(db.Model):
    """Per-tier technical snapshot synchronized from admirald."""

    id = db.Column(db.Integer, primary_key=True)
    catalog_app_id = db.Column(
        db.Integer, db.ForeignKey("catalog_app.id"), nullable=False, index=True
    )
    upstream_tier_id = db.Column(db.String(120), nullable=False)
    paypal_plan_id = db.Column(db.String(255), nullable=True)
    upstream_present = db.Column(db.Boolean, default=True, nullable=False)
    upstream_availability = db.Column(
        db.String(20), default="available", nullable=False
    )
    technical_snapshot = db.Column(db.Text, nullable=True)
    display_name = db.Column(db.String(255), nullable=True)
    commercial_description = db.Column(db.Text, nullable=True)
    display_order = db.Column(db.Integer, default=0, nullable=False)
    created_at = db.Column(
        db.DateTime, default=lambda: datetime.now(UTC), nullable=False
    )
    updated_at = db.Column(
        db.DateTime,
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
        nullable=False,
    )

    __table_args__ = (
        db.UniqueConstraint(
            "catalog_app_id", "upstream_tier_id", name="uq_catalog_app_tier"
        ),
    )


class CatalogSyncAudit(db.Model):
    """Audit trail for catalog synchronization events"""

    id = db.Column(db.Integer, primary_key=True)

    # Sync execution metadata
    execution_id = db.Column(
        db.String(64),
        unique=True,
        nullable=False,
        index=True,
        default=lambda: f"sync_{uuid4().hex[:16]}",
    )
    origin = db.Column(db.String(50), nullable=False)  # manual | systemd_timer
    actor = db.Column(db.String(255), nullable=True)  # admin username if manual

    # Execution result
    status = db.Column(db.String(20), nullable=False)  # success | failure
    error_message = db.Column(db.Text, nullable=True)

    # Statistics
    apps_synced = db.Column(db.Integer, default=0, nullable=False)
    apps_updated = db.Column(db.Integer, default=0, nullable=False)
    apps_marked_missing = db.Column(db.Integer, default=0, nullable=False)
    total_apps_processed = db.Column(db.Integer, default=0, nullable=False)

    # Timestamps
    started_at = db.Column(
        db.DateTime, nullable=False, default=lambda: datetime.now(UTC)
    )
    completed_at = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(
        db.DateTime, default=lambda: datetime.now(UTC), nullable=False
    )

    def as_dict(self):
        return {
            "execution_id": self.execution_id,
            "origin": self.origin,
            "actor": self.actor,
            "status": self.status,
            "error_message": self.error_message,
            "apps_synced": self.apps_synced,
            "apps_updated": self.apps_updated,
            "apps_marked_missing": self.apps_marked_missing,
            "total_apps_processed": self.total_apps_processed,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "completed_at": (
                self.completed_at.isoformat() if self.completed_at else None
            ),
        }


class Subscription(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    customer_email = db.Column(db.String(255), nullable=False, index=True)
    app_slug = db.Column(db.String(120), nullable=False, index=True)
    status = db.Column(db.String(50), nullable=False, default="active")
    monthly_price_cents = db.Column(db.Integer, default=0, nullable=False)
    tier_name = db.Column(db.String(120), nullable=False, default="starter")
    instance_id = db.Column(db.String(120), unique=True, nullable=True, index=True)
    paypal_subscription_id = db.Column(
        db.String(255), unique=True, nullable=True, index=True
    )
    paypal_plan_id = db.Column(db.String(255), nullable=True)
    requires_billing = db.Column(db.Boolean, default=True, nullable=False)
    next_billing_at = db.Column(db.String(32), nullable=True)
    billing_email = db.Column(db.String(255), nullable=True)
    technical_email = db.Column(db.String(255), nullable=True)
    external_id = db.Column(
        db.String(64),
        unique=True,
        nullable=False,
        default=lambda: f"sub_{uuid4().hex[:20]}",
    )
    tax_percent = db.Column(db.Integer, default=0, nullable=False)
    is_test_app = db.Column(db.Boolean, default=False, nullable=False)
    created_at = db.Column(
        db.DateTime, default=lambda: datetime.now(UTC), nullable=False
    )

    def as_dict(self):
        return {
            "id": self.id,
            "external_id": self.external_id,
            "customer_email": self.customer_email,
            "app_slug": self.app_slug,
            "status": self.status,
            "monthly_price_cents": self.monthly_price_cents,
            "tier_name": self.tier_name,
            "instance_id": self.instance_id,
            "paypal_subscription_id": self.paypal_subscription_id,
            "paypal_plan_id": self.paypal_plan_id,
            "requires_billing": self.requires_billing,
            "is_test_app": self.is_test_app,
            "next_billing_at": self.next_billing_at,
            "billing_email": self.billing_email,
            "technical_email": self.technical_email,
            "tax_percent": self.tax_percent,
        }


class Invoice(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    invoice_id = db.Column(
        db.String(64),
        unique=True,
        nullable=False,
        default=lambda: f"inv_{uuid4().hex[:16]}",
    )
    subscription_external_id = db.Column(db.String(64), nullable=False, index=True)
    customer_email = db.Column(db.String(255), nullable=False, index=True)
    app_slug = db.Column(db.String(120), nullable=False)
    tier_name = db.Column(db.String(120), nullable=False)
    subtotal_cents = db.Column(db.Integer, nullable=False, default=0)
    tax_percent = db.Column(db.Integer, nullable=False, default=0)
    tax_cents = db.Column(db.Integer, nullable=False, default=0)
    total_cents = db.Column(db.Integer, nullable=False, default=0)
    currency = db.Column(db.String(3), nullable=False, default="USD")
    status = db.Column(db.String(30), nullable=False, default="pending")
    paypal_transaction_id = db.Column(db.String(255), nullable=True)
    paypal_event_id = db.Column(db.String(255), nullable=True)
    period_start = db.Column(db.String(16), nullable=True)
    period_end = db.Column(db.String(16), nullable=True)
    created_at = db.Column(
        db.DateTime, default=lambda: datetime.now(UTC), nullable=False
    )

    def as_dict(self):
        return {
            "invoice_id": self.invoice_id,
            "subscription_external_id": self.subscription_external_id,
            "customer_email": self.customer_email,
            "app_slug": self.app_slug,
            "tier_name": self.tier_name,
            "subtotal_cents": self.subtotal_cents,
            "tax_percent": self.tax_percent,
            "tax_cents": self.tax_cents,
            "total_cents": self.total_cents,
            "currency": self.currency,
            "status": self.status,
            "paypal_transaction_id": self.paypal_transaction_id,
            "period_start": self.period_start,
            "period_end": self.period_end,
            "created_at": self.created_at.isoformat(),
        }


class CustomerApp(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    subscription_id = db.Column(
        db.Integer, db.ForeignKey("subscription.id"), nullable=False, index=True
    )
    customer_email = db.Column(db.String(255), nullable=False, index=True)
    instance_id = db.Column(
        db.String(120),
        unique=True,
        nullable=False,
        index=True,
        default=lambda: f"local_{uuid4().hex[:16]}",
    )
    app_slug = db.Column(db.String(120), nullable=False, index=True)
    domain = db.Column(db.String(255), nullable=False)
    status = db.Column(db.String(50), nullable=False, default="running")
    backup_status = db.Column(db.String(50), nullable=False, default="ok")
    storage_status = db.Column(db.String(50), nullable=False, default="ok")
    tier_name = db.Column(db.String(120), nullable=False, default="starter")
    next_billing_at = db.Column(db.String(32), nullable=True)
    created_at = db.Column(
        db.DateTime, default=lambda: datetime.now(UTC), nullable=False
    )

    def as_dict(self):
        return {
            "id": self.id,
            "subscription_id": self.subscription_id,
            "customer_email": self.customer_email,
            "instance_id": self.instance_id,
            "app_slug": self.app_slug,
            "domain": self.domain,
            "status": self.status,
            "backup_status": self.backup_status,
            "storage_status": self.storage_status,
            "tier_name": self.tier_name,
            "next_billing_at": self.next_billing_at,
        }


class InstanceEvent(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    instance_id = db.Column(db.String(120), nullable=False, index=True)
    customer_email = db.Column(db.String(255), nullable=False, index=True)
    event_type = db.Column(db.String(80), nullable=False)
    message = db.Column(db.String(500), nullable=False)
    created_at = db.Column(
        db.DateTime, default=lambda: datetime.now(UTC), nullable=False
    )

    def as_dict(self):
        return {
            "event_type": self.event_type,
            "message": self.message,
            "created_at": self.created_at.isoformat(),
        }


class SupportIncident(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    incident_id = db.Column(
        db.String(64),
        unique=True,
        nullable=False,
        default=lambda: f"inc_{uuid4().hex[:12]}",
    )
    instance_id = db.Column(db.String(120), nullable=False, index=True)
    customer_email = db.Column(db.String(255), nullable=False, index=True)
    subject = db.Column(db.String(255), nullable=False)
    description = db.Column(db.Text, nullable=False)
    priority = db.Column(db.String(20), nullable=False, default="medium")
    attachment_name = db.Column(db.String(255), nullable=True)
    status = db.Column(db.String(30), nullable=False, default="open")
    assigned_to = db.Column(db.String(255), nullable=True, index=True)
    internal_notes = db.Column(db.Text, nullable=True)
    created_at = db.Column(
        db.DateTime, default=lambda: datetime.now(UTC), nullable=False
    )
    updated_at = db.Column(
        db.DateTime,
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
        nullable=False,
    )

    # SLA fields
    response_deadline = db.Column(db.DateTime, nullable=True, index=True)
    resolution_deadline = db.Column(db.DateTime, nullable=True, index=True)
    sla_violated = db.Column(db.Boolean, default=False, nullable=False)
    resolved_at = db.Column(db.DateTime, nullable=True)

    def as_dict(self):
        return {
            "incident_id": self.incident_id,
            "instance_id": self.instance_id,
            "subject": self.subject,
            "description": self.description,
            "priority": self.priority,
            "attachment_name": self.attachment_name,
            "status": self.status,
            "created_at": self.created_at.isoformat(),
        }


class UploadedBackup(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    backup_id = db.Column(
        db.String(64),
        unique=True,
        nullable=False,
        default=lambda: f"upbk_{uuid4().hex[:12]}",
    )
    customer_email = db.Column(db.String(255), nullable=False, index=True)
    app_slug = db.Column(db.String(120), nullable=False, index=True)
    original_filename = db.Column(db.String(255), nullable=False)
    stored_path = db.Column(db.String(1024), nullable=False)
    size_bytes = db.Column(db.Integer, nullable=False)
    checksum_sha256 = db.Column(db.String(64), nullable=False)
    status = db.Column(db.String(30), nullable=False, default="uploaded")
    created_at = db.Column(
        db.DateTime, default=lambda: datetime.now(UTC), nullable=False
    )

    def as_dict(self):
        return {
            "backup_id": self.backup_id,
            "customer_email": self.customer_email,
            "app_slug": self.app_slug,
            "original_filename": self.original_filename,
            "stored_path": self.stored_path,
            "size_bytes": self.size_bytes,
            "checksum_sha256": self.checksum_sha256,
            "status": self.status,
            "created_at": self.created_at.isoformat(),
        }


class RestoreRequest(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    request_id = db.Column(
        db.String(64),
        unique=True,
        nullable=False,
        default=lambda: f"rst_{uuid4().hex[:12]}",
    )
    instance_id = db.Column(db.String(120), nullable=False, index=True)
    customer_email = db.Column(db.String(255), nullable=False, index=True)
    source_backup_id = db.Column(db.String(64), nullable=False)
    source_kind = db.Column(db.String(30), nullable=False)
    service_name = db.Column(db.String(120), nullable=False)
    status = db.Column(db.String(30), nullable=False, default="pending")
    confirm_text = db.Column(db.String(255), nullable=False)
    operation_id = db.Column(db.String(120), nullable=True)
    created_at = db.Column(
        db.DateTime, default=lambda: datetime.now(UTC), nullable=False
    )

    def as_dict(self):
        return {
            "request_id": self.request_id,
            "instance_id": self.instance_id,
            "source_backup_id": self.source_backup_id,
            "source_kind": self.source_kind,
            "service_name": self.service_name,
            "status": self.status,
            "confirm_text": self.confirm_text,
            "operation_id": self.operation_id,
            "created_at": self.created_at.isoformat(),
        }


class BillingEvent(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    event_id = db.Column(db.String(255), unique=True, nullable=False, index=True)
    subscription_external_id = db.Column(db.String(64), nullable=True, index=True)
    event_type = db.Column(db.String(120), nullable=False)
    status = db.Column(db.String(60), nullable=False)
    payload_json = db.Column(db.Text, nullable=False)
    created_at = db.Column(
        db.DateTime, default=lambda: datetime.now(UTC), nullable=False
    )


class HarborMeta(db.Model):
    key = db.Column(db.String(64), primary_key=True)
    value = db.Column(db.Text, nullable=False)
    updated_at = db.Column(
        db.DateTime,
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
        nullable=False,
    )

    @classmethod
    def get(cls, key, default=None):
        row = db.session.query(cls).filter_by(key=key).one_or_none()
        return row.value if row else default

    @classmethod
    def set(cls, key, value):
        row = db.session.query(cls).filter_by(key=key).one_or_none()
        if row is None:
            row = cls(key=key, value=str(value))
            db.session.add(row)
        else:
            row.value = str(value)
        db.session.commit()


class WorkerLog(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    started_at = db.Column(db.DateTime, nullable=False)
    completed_at = db.Column(db.DateTime, nullable=True)
    actions_taken = db.Column(db.Integer, default=0, nullable=False)
    errors = db.Column(db.Integer, default=0, nullable=False)
    summary = db.Column(db.Text, nullable=True)


class LMSSettings(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    base_url = db.Column(db.String(1024), nullable=True)
    encrypted_api_key = db.Column(db.Text, nullable=True)
    enabled = db.Column(db.Boolean, default=False, nullable=False)
    updated_at = db.Column(
        db.DateTime,
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
        nullable=False,
    )

    @classmethod
    def singleton(cls):
        settings = db.session.query(cls).first()
        if settings is None:
            settings = cls()
            db.session.add(settings)
            db.session.commit()
        return settings


class AppCourse(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    app_slug = db.Column(db.String(120), nullable=False, index=True)
    course_code = db.Column(db.String(120), nullable=False)
    course_type = db.Column(db.String(40), nullable=False)
    base_price_cents = db.Column(db.Integer, nullable=False, default=0)
    active = db.Column(db.Boolean, default=True, nullable=False)
    created_at = db.Column(
        db.DateTime, default=lambda: datetime.now(UTC), nullable=False
    )
    updated_at = db.Column(
        db.DateTime,
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
        nullable=False,
    )

    def as_dict(self):
        return {
            "id": self.id,
            "app_slug": self.app_slug,
            "course_code": self.course_code,
            "course_type": self.course_type,
            "base_price_cents": self.base_price_cents,
            "active": self.active,
        }


class AppCourseTierDiscount(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    app_course_id = db.Column(
        db.Integer, db.ForeignKey("app_course.id"), nullable=False, index=True
    )
    tier_name = db.Column(db.String(120), nullable=False)
    discount_percent = db.Column(db.Integer, nullable=False, default=0)
    created_at = db.Column(
        db.DateTime, default=lambda: datetime.now(UTC), nullable=False
    )
    updated_at = db.Column(
        db.DateTime,
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
        nullable=False,
    )

    def as_dict(self):
        return {
            "app_course_id": self.app_course_id,
            "tier_name": self.tier_name,
            "discount_percent": self.discount_percent,
        }


class AuditLog(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    actor = db.Column(db.String(255), nullable=False, default="system")
    action = db.Column(db.String(80), nullable=False, index=True)
    resource_type = db.Column(db.String(40), nullable=False, default="")
    resource_id = db.Column(db.String(120), nullable=False, default="")
    detail = db.Column(db.String(500), nullable=False, default="")
    ip_address = db.Column(db.String(45), nullable=True)
    created_at = db.Column(
        db.DateTime, default=lambda: datetime.now(UTC), nullable=False, index=True
    )


class Order(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    order_id = db.Column(
        db.String(64),
        unique=True,
        nullable=False,
        default=lambda: f"ord_{uuid4().hex[:16]}",
    )
    customer_email = db.Column(db.String(255), nullable=False, index=True)
    app_slug = db.Column(db.String(120), nullable=False)
    tier_name = db.Column(db.String(120), nullable=False)
    monthly_price_cents = db.Column(db.Integer, nullable=False, default=0)
    tax_percent = db.Column(db.Integer, nullable=False, default=0)
    tax_cents = db.Column(db.Integer, nullable=False, default=0)
    total_cents = db.Column(db.Integer, nullable=False, default=0)
    currency = db.Column(db.String(3), nullable=False, default="USD")
    status = db.Column(
        db.String(30), nullable=False, default="pending_payment", index=True
    )
    requires_billing = db.Column(db.Boolean, default=True, nullable=False)
    subscription_external_id = db.Column(db.String(64), nullable=True, index=True)
    paypal_subscription_id = db.Column(db.String(255), nullable=True)
    paypal_plan_id = db.Column(db.String(255), nullable=True)
    next_billing_at = db.Column(db.String(32), nullable=True)
    billing_email = db.Column(db.String(255), nullable=True)
    technical_email = db.Column(db.String(255), nullable=True)
    created_at = db.Column(
        db.DateTime, default=lambda: datetime.now(UTC), nullable=False
    )

    def as_dict(self):
        return {
            "order_id": self.order_id,
            "customer_email": self.customer_email,
            "app_slug": self.app_slug,
            "tier_name": self.tier_name,
            "status": self.status,
            "monthly_price_cents": self.monthly_price_cents,
            "tax_percent": self.tax_percent,
            "tax_cents": self.tax_cents,
            "total_cents": self.total_cents,
            "subscription_external_id": self.subscription_external_id,
        }


class Payment(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    payment_id = db.Column(
        db.String(64),
        unique=True,
        nullable=False,
        default=lambda: f"pay_{uuid4().hex[:16]}",
    )
    order_id = db.Column(db.String(64), nullable=False, index=True)
    subscription_external_id = db.Column(db.String(64), nullable=True, index=True)
    customer_email = db.Column(db.String(255), nullable=False, index=True)
    provider = db.Column(db.String(30), nullable=False, default="manual")
    provider_reference = db.Column(db.String(255), nullable=True)
    amount_cents = db.Column(db.Integer, nullable=False, default=0)
    currency = db.Column(db.String(3), nullable=False, default="USD")
    status = db.Column(db.String(30), nullable=False, default="completed", index=True)
    created_at = db.Column(
        db.DateTime, default=lambda: datetime.now(UTC), nullable=False
    )


class HarborPayPalConfig(db.Model):
    """PayPal configuration singleton."""

    id = db.Column(db.Integer, primary_key=True)
    mode = db.Column(
        db.String(30), nullable=False, default="sandbox"
    )  # sandbox or live
    client_id = db.Column(db.String(255), nullable=True)
    client_secret = db.Column(db.String(255), nullable=True)
    webhook_id = db.Column(db.String(255), nullable=True)
    created_at = db.Column(
        db.DateTime, default=lambda: datetime.now(UTC), nullable=False
    )
    updated_at = db.Column(
        db.DateTime,
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
        nullable=False,
    )

    @staticmethod
    def get_config():
        """Get or create the singleton config."""
        config = db.session.query(HarborPayPalConfig).first()
        if not config:
            config = HarborPayPalConfig()
            db.session.add(config)
            db.session.commit()
        return config


def compute_sha256(fileobj):
    digest = sha256()
    while True:
        chunk = fileobj.read(1024 * 1024)
        if not chunk:
            break
        digest.update(chunk)
    return digest.hexdigest()


class SubscriptionChange(db.Model):
    """Track subscription tier changes and cancellations."""

    id = db.Column(db.Integer, primary_key=True)
    subscription_id = db.Column(
        db.Integer, db.ForeignKey("subscription.id"), nullable=False, index=True
    )
    change_type = db.Column(db.String(30), nullable=False)  # tier_change, cancellation
    old_tier = db.Column(db.String(50), nullable=True)
    new_tier = db.Column(db.String(50), nullable=True)
    old_amount_cents = db.Column(db.Integer, nullable=True)
    new_amount_cents = db.Column(db.Integer, nullable=True)
    reason = db.Column(db.Text, nullable=True)
    created_by = db.Column(db.String(255), nullable=False)  # customer email
    created_at = db.Column(
        db.DateTime, default=lambda: datetime.now(UTC), nullable=False, index=True
    )


class CustomerReply(db.Model):
    """Track customer replies to support tickets."""

    id = db.Column(db.Integer, primary_key=True)
    ticket_id = db.Column(
        db.Integer, db.ForeignKey("support_incident.id"), nullable=False, index=True
    )
    customer_id = db.Column(
        db.Integer, db.ForeignKey("customer.id"), nullable=False, index=True
    )
    message = db.Column(db.Text, nullable=False)
    created_at = db.Column(
        db.DateTime, default=lambda: datetime.now(UTC), nullable=False, index=True
    )


class RateLimit(db.Model):
    """Rate limiting state backed by PostgreSQL for multi-worker support."""

    id = db.Column(db.Integer, primary_key=True)
    identifier = db.Column(db.String(255), nullable=False, index=True)
    window_start = db.Column(db.Float, nullable=False)
    attempts = db.Column(db.Integer, default=0, nullable=False)
