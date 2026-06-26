from unittest.mock import patch
from app.catalog_service import sync_catalog
from app.models import CatalogApp, CatalogSyncAudit
from app.extensions import db


def test_sync_catalog_success(app):
    """Test successful catalog synchronization."""
    mock_apps = [
        {
            "name": "test-app",
            "display_name": "Test App",
            "availability": "available",
            "revision": 1,
            "checksum": "abc",
            "raw_yaml": "tiers:\n  starter:\n    cpu: 1",
        }
    ]

    with patch("app.admiral_client.list_apps", return_value=mock_apps):
        with app.app_context():
            # Clear existing data
            db.session.query(CatalogApp).delete()
            db.session.query(CatalogSyncAudit).delete()
            db.session.commit()

            result = sync_catalog(origin="test", actor="tester")
            assert result["success"] is True
            assert result["synced"] == 1

            app_record = CatalogApp.query.filter_by(upstream_app_id="test-app").first()
            assert app_record is not None
            assert app_record.name == "Test App"
            assert app_record.upstream_revision == 1


def test_sync_catalog_missing_upstream(app):
    """Test that apps missing upstream are marked accordingly."""
    with app.app_context():
        # Clear existing data
        db.session.query(CatalogApp).delete()
        db.session.query(CatalogSyncAudit).delete()

        # Pre-populate an app
        existing_app = CatalogApp(upstream_app_id="missing-app", name="Missing App", upstream_present=True)
        db.session.add(existing_app)
        db.session.commit()

    with patch("app.admiral_client.list_apps", return_value=[]):
        with app.app_context():
            result = sync_catalog(origin="test", actor="tester")
            assert result["success"] is True
            assert result["marked_missing"] == 1

            app_record = CatalogApp.query.filter_by(upstream_app_id="missing-app").first()
            assert app_record.upstream_present is False


def test_sync_catalog_already_in_progress(app):
    """Test that concurrent syncs are prevented."""
    with app.app_context():
        # Clear existing data
        db.session.query(CatalogSyncAudit).delete()
        db.session.commit()

        audit = CatalogSyncAudit(origin="test", status="in_progress")
        db.session.add(audit)
        db.session.commit()

        result = sync_catalog(origin="test", actor="tester")
        assert result["success"] is False
        assert "already in progress" in result["error"]
