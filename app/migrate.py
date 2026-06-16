# SPDX-FileCopyrightText: William Moreno Reyes <williamjmorenor@gmail.com>
# SPDX-License-Identifier: Apache-2.0

import logging

from sqlalchemy import inspect, text

from app.extensions import db

logger = logging.getLogger("admiral-harbor")


def run_migrations():
    inspector = inspect(db.engine)
    columns = {c["name"] for c in inspector.get_columns("catalog_app")}

    with db.engine.begin() as conn:
        if "revision" in columns and "upstream_revision" not in columns:
            logger.info("Migration 0001: renaming revision column to upstream_revision")
            conn.execute(
                text(
                    "ALTER TABLE catalog_app RENAME COLUMN revision TO upstream_revision"
                )
            )
        if "checksum" in columns and "upstream_checksum" not in columns:
            logger.info("Migration 0001: renaming checksum column to upstream_checksum")
            conn.execute(
                text(
                    "ALTER TABLE catalog_app RENAME COLUMN checksum TO upstream_checksum"
                )
            )
        if "availability" in columns and "upstream_availability" not in columns:
            logger.info(
                "Migration 0001: renaming availability column to upstream_availability"
            )
            conn.execute(
                text(
                    "ALTER TABLE catalog_app RENAME COLUMN availability TO upstream_availability"
                )
            )

        if "upstream_revision" not in columns:
            logger.info(
                "Migration 0001: adding upstream_revision column to catalog_app"
            )
            conn.execute(
                text(
                    "ALTER TABLE catalog_app ADD COLUMN upstream_revision INTEGER NOT NULL DEFAULT 0"
                )
            )
        if "upstream_checksum" not in columns:
            logger.info(
                "Migration 0001: adding upstream_checksum column to catalog_app"
            )
            conn.execute(
                text("ALTER TABLE catalog_app ADD COLUMN upstream_checksum VARCHAR(64)")
            )
        if "upstream_availability" not in columns:
            logger.info(
                "Migration 0001: adding upstream_availability column to catalog_app"
            )
            conn.execute(
                text(
                    "ALTER TABLE catalog_app ADD COLUMN upstream_availability VARCHAR(20) NOT NULL DEFAULT 'available'"
                )
            )

        if "technical_snapshot" not in columns:
            logger.info(
                "Migration 0002: adding technical_snapshot column to catalog_app"
            )
            conn.execute(
                text("ALTER TABLE catalog_app ADD COLUMN technical_snapshot TEXT")
            )

    # Ensure catalog_app_tier table exists
    tier_columns = {c["name"] for c in inspector.get_columns("catalog_app_tier")}
    with db.engine.begin() as conn:
        if "id" not in tier_columns:
            logger.info("Migration 0002: creating catalog_app_tier table")
            conn.execute(text("""
                CREATE TABLE catalog_app_tier (
                    id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
                    catalog_app_id INTEGER NOT NULL REFERENCES catalog_app(id),
                    upstream_tier_id VARCHAR(120) NOT NULL,
                    upstream_present BOOLEAN NOT NULL DEFAULT 1,
                    upstream_availability VARCHAR(20) NOT NULL DEFAULT 'available',
                    technical_snapshot TEXT,
                    display_name VARCHAR(255),
                    commercial_description TEXT,
                    display_order INTEGER NOT NULL DEFAULT 0,
                    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE (catalog_app_id, upstream_tier_id)
                )
            """))

    # Ensure harbor_admin_user has new columns
    admin_columns = {c["name"] for c in inspector.get_columns("harbor_admin_user")}
    with db.engine.begin() as conn:
        if "is_active" not in admin_columns:
            logger.info("Migration 0003: adding is_active column to harbor_admin_user")
            conn.execute(
                text(
                    "ALTER TABLE harbor_admin_user ADD COLUMN is_active BOOLEAN NOT NULL DEFAULT 1"
                )
            )
        if "updated_at" not in admin_columns:
            logger.info("Migration 0003: adding updated_at column to harbor_admin_user")
            conn.execute(
                text(
                    "ALTER TABLE harbor_admin_user ADD COLUMN updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP"
                )
            )

    customer_columns = {c["name"] for c in inspector.get_columns("customer")}
    # Migration 0005: add marketplace link columns to catalog_app
    catalog_columns = {c["name"] for c in inspector.get_columns("catalog_app")}
    with db.engine.begin() as conn:
        if "repository_url" not in catalog_columns:
            logger.info("Migration 0005: adding repository_url to catalog_app")
            conn.execute(
                text("ALTER TABLE catalog_app ADD COLUMN repository_url VARCHAR(1024)")
            )
        if "support_url" not in catalog_columns:
            logger.info("Migration 0005: adding support_url to catalog_app")
            conn.execute(
                text("ALTER TABLE catalog_app ADD COLUMN support_url VARCHAR(1024)")
            )

    with db.engine.begin() as conn:
        if "is_active" not in customer_columns:
            logger.info("Migration 0004: adding is_active column to customer")
            conn.execute(
                text(
                    "ALTER TABLE customer ADD COLUMN is_active BOOLEAN NOT NULL DEFAULT 1"
                )
            )
        if "blocked_at" not in customer_columns:
            logger.info("Migration 0004: adding blocked_at column to customer")
            conn.execute(text("ALTER TABLE customer ADD COLUMN blocked_at DATETIME"))
        if "signup_status" not in customer_columns:
            logger.info("Migration 0004: adding signup_status column to customer")
            conn.execute(
                text(
                    "ALTER TABLE customer ADD COLUMN signup_status VARCHAR(30) NOT NULL DEFAULT 'pending'"
                )
            )
        if "email_confirmation_token_hash" not in customer_columns:
            logger.info(
                "Migration 0004: adding email_confirmation_token_hash column to customer"
            )
            conn.execute(
                text(
                    "ALTER TABLE customer ADD COLUMN email_confirmation_token_hash VARCHAR(128)"
                )
            )
        if "email_confirmation_sent_at" not in customer_columns:
            logger.info(
                "Migration 0004: adding email_confirmation_sent_at column to customer"
            )
            conn.execute(
                text(
                    "ALTER TABLE customer ADD COLUMN email_confirmation_sent_at DATETIME"
                )
            )
        if "email_confirmed_at" not in customer_columns:
            logger.info("Migration 0004: adding email_confirmed_at column to customer")
            conn.execute(
                text("ALTER TABLE customer ADD COLUMN email_confirmed_at DATETIME")
            )
        if "reviewed_at" not in customer_columns:
            logger.info("Migration 0004: adding reviewed_at column to customer")
            conn.execute(text("ALTER TABLE customer ADD COLUMN reviewed_at DATETIME"))
        if "reviewed_by" not in customer_columns:
            logger.info("Migration 0004: adding reviewed_by column to customer")
            conn.execute(
                text("ALTER TABLE customer ADD COLUMN reviewed_by VARCHAR(255)")
            )
        if "rejection_reason" not in customer_columns:
            logger.info("Migration 0004: adding rejection_reason column to customer")
            conn.execute(text("ALTER TABLE customer ADD COLUMN rejection_reason TEXT"))
        if "updated_at" not in customer_columns:
            logger.info("Migration 0004: adding updated_at column to customer")
            conn.execute(
                text(
                    "ALTER TABLE customer ADD COLUMN updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP"
                )
            )
