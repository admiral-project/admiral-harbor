#!/usr/bin/env python
# SPDX-FileCopyrightText: William Moreno Reyes <williamjmorenor@gmail.com>
# SPDX-License-Identifier: Apache-2.0

"""
Harbor catalog synchronization CLI.

Usage:
  python -m cli sync           - Run full catalog sync
  python -m cli sync --status  - Show last sync status
"""

import sys
import logging

from app import create_app
from app.catalog_service import sync_catalog, get_last_sync, get_app_status_label
from app.extensions import db
from app.models import CatalogApp

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
logger = logging.getLogger("admiral-harbor-cli")


def cmd_sync():
    """Execute catalog synchronization"""
    app = create_app()
    with app.app_context():
        logger.info("Starting catalog synchronization...")
        result = sync_catalog(origin="systemd_timer", actor=None)

        if result["success"]:
            logger.info(
                f"Sync completed: {result['synced']} new, {result['updated']} updated, "
                f"{result['marked_missing']} marked missing"
            )
            return 0
        else:
            logger.error(f"Sync failed: {result.get('error', 'Unknown error')}")
            return 1


def cmd_status():
    """Show catalog sync status"""
    app = create_app()
    with app.app_context():
        last_sync = get_last_sync()
        if not last_sync:
            print("No successful sync yet")
            return 0

        print(f"Last successful sync: {last_sync.completed_at}")
        print(f"Apps synced: {last_sync.apps_synced}")
        print(f"Apps updated: {last_sync.apps_updated}")
        print(f"Apps marked missing: {last_sync.apps_marked_missing}")

        # Show app status summary
        apps = db.session.query(CatalogApp).all()
        status_counts = {}
        for app in apps:
            label = get_app_status_label(app)
            status_counts[label] = status_counts.get(label, 0) + 1

        print("\nApp status distribution:")
        for label, count in sorted(status_counts.items()):
            print(f"  {label}: {count}")

        return 0


def cmd_list():
    """List all catalog apps"""
    app = create_app()
    with app.app_context():
        apps = db.session.query(CatalogApp).all()
        for app_record in apps:
            label = get_app_status_label(app_record)
            print(f"{app_record.upstream_app_id:30} {label:30} rev={app_record.upstream_revision}")
        return 0


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python -m cli {sync|status|list}")
        sys.exit(1)

    command = sys.argv[1]

    if command == "sync":
        sys.exit(cmd_sync())
    elif command == "status":
        sys.exit(cmd_status())
    elif command == "list":
        sys.exit(cmd_list())
    else:
        print(f"Unknown command: {command}")
        sys.exit(1)
