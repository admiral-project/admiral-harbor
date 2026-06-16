# SPDX-FileCopyrightText: William Moreno Reyes <williamjmorenor@gmail.com>
# SPDX-License-Identifier: Apache-2.0

import logging
from datetime import datetime

import json

from app import admiral_client
from app.extensions import db
from app.models import CatalogApp, CatalogAppTier, CatalogSyncAudit

logger = logging.getLogger("admiral-harbor")


class SyncException(Exception):
    """Raised when catalog synchronization fails"""

    pass


def sync_catalog(origin="manual", actor=None):
    """
    Synchronize applications from admirald.

    Args:
        origin: 'manual' or 'systemd_timer'
        actor: username if origin is 'manual'

    Returns:
        dict with sync results and audit record
    """
    audit = CatalogSyncAudit(origin=origin, actor=actor, status="in_progress")
    audit.started_at = datetime.utcnow()
    db.session.add(audit)
    db.session.flush()  # Get execution_id

    try:
        # Check if another sync is running
        running = (
            db.session.query(CatalogSyncAudit)
            .filter(
                CatalogSyncAudit.execution_id != audit.execution_id,
                CatalogSyncAudit.status == "in_progress",
            )
            .first()
        )
        if running:
            raise SyncException("Sync already in progress")

        db.session.commit()

        # Fetch apps from admirald
        logger.info(f"Starting catalog sync from admirald (origin={origin})")
        apps_response = admiral_client.list_apps()
        if not apps_response:
            apps_response = []

        # Track upstream app IDs
        upstream_ids = set()
        synced_count = 0
        updated_count = 0

        for app_data in apps_response:
            app_id = app_data.get("name")
            if not app_id:
                logger.warning(f"Skipping app without name: {app_data}")
                continue

            upstream_ids.add(app_id)

            # Find or create local record
            app = CatalogApp.query.filter_by(upstream_app_id=app_id).first()

            # Build technical snapshot
            technical_snapshot = json.dumps(app_data, default=str)

            if app is None:
                # New app discovered
                app = CatalogApp(
                    upstream_app_id=app_id,
                    name=app_data.get("display_name", app_id),
                    one_liner="",  # Requires manual entry
                    description_md="",  # Requires manual entry
                    sync_status="synced",
                    upstream_present=True,
                    upstream_availability=app_data.get("availability", "available"),
                    upstream_revision=app_data.get("revision", 0),
                    upstream_checksum=app_data.get("checksum", ""),
                    technical_snapshot=technical_snapshot,
                    catalog_enabled=False,  # Manual enablement required
                    synced_at=datetime.utcnow(),
                )
                db.session.add(app)
                synced_count += 1
                logger.info(f"New app discovered: {app_id}")
            else:
                # Update existing app with upstream values
                app.upstream_present = True
                app.upstream_availability = app_data.get("availability", "available")
                app.upstream_revision = app_data.get("revision", 0)
                app.upstream_checksum = app_data.get("checksum", "")
                app.technical_snapshot = technical_snapshot
                app.sync_status = "synced"
                app.synced_at = datetime.utcnow()
                app.sync_last_error = None
                updated_count += 1

            # Sync tiers
            db.session.flush()
            upstream_tier_ids = set()
            raw_yaml = app_data.get("raw_yaml", "")
            if raw_yaml:
                parsed = admiral_client.parse_tiers_from_yaml(raw_yaml)
                tier_data_list = admiral_client.normalize_tiers(parsed)
            else:
                tier_data_list = []

            for tier_data in tier_data_list:
                tier_id = tier_data.get("name")
                if not tier_id:
                    continue
                upstream_tier_ids.add(tier_id)
                tier = CatalogAppTier.query.filter_by(
                    catalog_app_id=app.id,
                    upstream_tier_id=tier_id,
                ).first()
                if tier is None:
                    tier = CatalogAppTier(
                        catalog_app_id=app.id,
                        upstream_tier_id=tier_id,
                        upstream_present=True,
                        upstream_availability="available",
                        technical_snapshot=json.dumps(tier_data, default=str),
                        display_name=tier_data.get("display_name", tier_id),
                        display_order=tier_data.get("sort_order", 0),
                    )
                    db.session.add(tier)
                else:
                    tier.upstream_present = True
                    tier.upstream_availability = "available"
                    tier.technical_snapshot = json.dumps(tier_data, default=str)
                    tier.display_name = tier_data.get("display_name", tier_id)

            # Mark tiers no longer present upstream
            for tier in CatalogAppTier.query.filter(
                CatalogAppTier.catalog_app_id == app.id,
                ~CatalogAppTier.upstream_tier_id.in_(upstream_tier_ids),
                CatalogAppTier.upstream_present,
            ).all():
                tier.upstream_present = False

        # Mark apps not in upstream response as missing
        missing_count = 0
        missing_apps = CatalogApp.query.filter(
            ~CatalogApp.upstream_app_id.in_(upstream_ids),
            CatalogApp.upstream_present,
        ).all()
        for app in missing_apps:
            app.upstream_present = False
            app.sync_status = "synced"
            app.synced_at = datetime.utcnow()
            missing_count += 1
            logger.info(f"App marked as missing upstream: {app.upstream_app_id}")

        db.session.commit()

        # Record successful sync
        audit.status = "success"
        audit.apps_synced = synced_count
        audit.apps_updated = updated_count
        audit.apps_marked_missing = missing_count
        audit.total_apps_processed = synced_count + updated_count + missing_count
        audit.completed_at = datetime.utcnow()
        db.session.commit()

        logger.info(
            f"Catalog sync completed: {synced_count} new, {updated_count} updated, {missing_count} marked missing"
        )

        return {
            "success": True,
            "execution_id": audit.execution_id,
            "synced": synced_count,
            "updated": updated_count,
            "marked_missing": missing_count,
            "total": synced_count + updated_count + missing_count,
        }

    except SyncException as e:
        error_msg = str(e)
        audit.status = "failure"
        audit.error_message = error_msg
        audit.completed_at = datetime.utcnow()
        db.session.commit()

        logger.info(f"Catalog sync skipped: {error_msg}")
        return {
            "success": False,
            "execution_id": audit.execution_id,
            "error": error_msg,
        }
    except Exception as e:
        error_msg = str(e)
        audit.status = "failure"
        audit.error_message = error_msg
        audit.completed_at = datetime.utcnow()
        db.session.commit()

        logger.error(f"Catalog sync failed: {error_msg}", exc_info=True)
        return {
            "success": False,
            "execution_id": audit.execution_id,
            "error": error_msg,
        }


def get_last_sync():
    """Get the last successful sync audit record"""
    return (
        CatalogSyncAudit.query.filter_by(status="success")
        .order_by(CatalogSyncAudit.completed_at.desc())
        .first()
    )


def is_app_publishable(app):
    """
    Check if app meets all conditions to be visible in catalog.

    Conditions:
    - upstream_present == True
    - upstream_availability == "available"
    - sync_status == "synced"
    - catalog_enabled == True
    """
    return (
        app.upstream_present
        and app.upstream_availability == "available"
        and app.sync_status == "synced"
        and app.catalog_enabled
    )


def get_app_status_label(app):
    """Derive human-readable status label for app"""
    if not app.upstream_present:
        return "Ausente en admirald"
    if app.sync_status == "sync_error":
        return "Error de sincronización"
    if app.upstream_availability != "available":
        return "No disponible"
    if not app.catalog_enabled:
        return "Oculto localmente"
    if not app.one_liner or not app.description_md:
        return "Pendiente de completar catálogo"
    return "Publicado"


def validate_before_provisioning(app_slug, tier_id):
    """
    Validate that an app and tier are available for provisioning.

    Calls admirald to confirm real-time availability before provisioning starts.

    Args:
        app_slug: upstream_app_id
        tier_id: tier name

    Returns:
        dict with validation result and details
    """
    try:
        # Check local catalog first
        app = CatalogApp.query.filter_by(upstream_app_id=app_slug).first()
        if not app:
            return {
                "valid": False,
                "reason": "app_not_in_catalog",
                "message": "Application not found in local catalog",
            }

        if not app.upstream_present:
            return {
                "valid": False,
                "reason": "app_missing_upstream",
                "message": "Application no longer available",
            }

        if app.sync_status != "synced":
            return {
                "valid": False,
                "reason": "app_sync_error",
                "message": "Application sync error, retry later",
            }

        # Validate in real-time with admirald
        result = admiral_client.validate_provisioning(
            app_slug,
            tier_id,
            expected_revision=app.upstream_revision,
            expected_checksum=app.upstream_checksum,
        )

        if not result.get("valid", False):
            reason = result.get("reason", "unknown")
            messages = {
                "app_not_found": "Application not found in admirald",
                "app_not_available": "Application is not available for new instances",
                "tier_not_found": "Tier not found for this application",
                "revision_mismatch": "Application definition has changed, refresh catalog and try again",
                "checksum_mismatch": "Application definition has changed, refresh catalog and try again",
            }
            return {
                "valid": False,
                "reason": reason,
                "message": messages.get(reason, f"Validation failed: {reason}"),
            }

        return {
            "valid": True,
            "reason": "ok",
            "message": "Ready to provision",
            "revision": result.get("revision"),
            "checksum": result.get("checksum"),
        }

    except admiral_client.AdmiralAPIError as e:
        logger.error(f"Validation error for {app_slug}/{tier_id}: {str(e)}")
        return {
            "valid": False,
            "reason": "validation_error",
            "message": "Cannot reach admirald, try again later",
        }
    except Exception as e:
        logger.error(f"Unexpected validation error: {str(e)}", exc_info=True)
        return {
            "valid": False,
            "reason": "internal_error",
            "message": "Internal validation error",
        }
