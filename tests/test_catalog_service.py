from datetime import UTC, datetime, timedelta
from unittest.mock import patch

from app.admiral_client import AdmiralAPIError
from app.catalog_service import (
    get_app_status_label,
    is_app_publishable,
    sync_catalog,
    validate_before_provisioning,
)
from app.extensions import db
from app.models import CatalogApp, CatalogSyncAudit


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

    with patch("app.admiral_client.list_apps", return_value=mock_apps), app.app_context():
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

    with patch("app.admiral_client.list_apps", return_value=[]), app.app_context():
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


def test_sync_catalog_recovers_abandoned_sync(app):
    """An old interrupted sync must not block future timer executions."""
    with app.app_context():
        db.session.query(CatalogSyncAudit).delete()
        abandoned = CatalogSyncAudit(origin="systemd_timer", status="in_progress")
        abandoned.started_at = datetime.now(UTC) - timedelta(minutes=6)
        db.session.add(abandoned)
        db.session.commit()

        with patch("app.admiral_client.list_apps", return_value=[]):
            result = sync_catalog(origin="systemd_timer")

        assert result["success"] is True
        assert abandoned.status == "failure"
        assert abandoned.error_message == "Catalog sync abandoned before completion"


def test_is_app_publishable():
    """Test is_app_publishable logic."""
    app = CatalogApp(
        upstream_present=True,
        upstream_availability="available",
        sync_status="synced",
        catalog_enabled=True,
    )
    assert is_app_publishable(app) is True

    app.upstream_present = False
    assert is_app_publishable(app) is False
    app.upstream_present = True

    app.upstream_availability = "maintenance"
    assert is_app_publishable(app) is False
    app.upstream_availability = "available"

    app.sync_status = "error"
    assert is_app_publishable(app) is False
    app.sync_status = "synced"

    app.catalog_enabled = False
    assert is_app_publishable(app) is False


def test_get_app_status_label():
    """Test get_app_status_label logic."""
    app = CatalogApp(
        upstream_present=True,
        sync_status="synced",
        upstream_availability="available",
        catalog_enabled=True,
        one_liner="One liner",
        description_md="Description",
    )
    assert get_app_status_label(app) == "Publicado"

    app.upstream_present = False
    assert get_app_status_label(app) == "Ausente en admirald"
    app.upstream_present = True

    app.sync_status = "sync_error"
    assert get_app_status_label(app) == "Error de sincronización"
    app.sync_status = "synced"

    app.upstream_availability = "not_available"
    assert get_app_status_label(app) == "No disponible"
    app.upstream_availability = "available"

    app.catalog_enabled = False
    assert get_app_status_label(app) == "Oculto localmente"
    app.catalog_enabled = True

    app.one_liner = ""
    assert get_app_status_label(app) == "Pendiente de completar catálogo"
    app.one_liner = "One liner"

    app.description_md = ""
    assert get_app_status_label(app) == "Pendiente de completar catálogo"


def test_validate_before_provisioning_app_not_found(app):
    with app.app_context():
        db.session.query(CatalogApp).delete()
        db.session.commit()
        result = validate_before_provisioning("no-app", "tier")
        assert result["valid"] is False
        assert result["reason"] == "app_not_in_catalog"


def test_validate_before_provisioning_not_present_upstream(app):
    with app.app_context():
        db.session.query(CatalogApp).delete()
        ca = CatalogApp(upstream_app_id="app1", name="App 1", upstream_present=False)
        db.session.add(ca)
        db.session.commit()

        result = validate_before_provisioning("app1", "tier")
        assert result["valid"] is False
        assert result["reason"] == "app_missing_upstream"


def test_validate_before_provisioning_sync_error(app):
    with app.app_context():
        db.session.query(CatalogApp).delete()
        ca = CatalogApp(upstream_app_id="app1", name="App 1", upstream_present=True, sync_status="error")
        db.session.add(ca)
        db.session.commit()

        result = validate_before_provisioning("app1", "tier")
        assert result["valid"] is False
        assert result["reason"] == "app_sync_error"


def test_validate_before_provisioning_remote_failure(app):
    with app.app_context():
        db.session.query(CatalogApp).delete()
        ca = CatalogApp(
            upstream_app_id="app1",
            name="App 1",
            upstream_present=True,
            sync_status="synced",
            upstream_revision=1,
            upstream_checksum="sum",
        )
        db.session.add(ca)
        db.session.commit()

    with patch("app.admiral_client.validate_provisioning", return_value={"valid": False, "reason": "app_not_found"}):
        with app.app_context():
            result = validate_before_provisioning("app1", "tier")
            assert result["valid"] is False
            assert result["reason"] == "app_not_found"
            assert "not found in admirald" in result["message"]


def test_validate_before_provisioning_success(app):
    with app.app_context():
        db.session.query(CatalogApp).delete()
        ca = CatalogApp(
            upstream_app_id="app1",
            name="App 1",
            upstream_present=True,
            sync_status="synced",
            upstream_revision=1,
            upstream_checksum="sum",
        )
        db.session.add(ca)
        db.session.commit()

    with (
        patch(
            "app.admiral_client.validate_provisioning", return_value={"valid": True, "revision": 1, "checksum": "sum"}
        ),
        app.app_context(),
    ):
        result = validate_before_provisioning("app1", "tier")
        assert result["valid"] is True
        assert result["reason"] == "ok"


def test_validate_before_provisioning_api_error(app):
    with app.app_context():
        db.session.query(CatalogApp).delete()
        ca = CatalogApp(
            upstream_app_id="app1",
            name="App 1",
            upstream_present=True,
            sync_status="synced",
        )
        db.session.add(ca)
        db.session.commit()

    with patch("app.admiral_client.validate_provisioning", side_effect=AdmiralAPIError("API down")):
        with app.app_context():
            result = validate_before_provisioning("app1", "tier")
            assert result["valid"] is False
            assert result["reason"] == "validation_error"


def test_validate_before_provisioning_unexpected_error(app):
    with app.app_context():
        db.session.query(CatalogApp).delete()
        ca = CatalogApp(
            upstream_app_id="app1",
            name="App 1",
            upstream_present=True,
            sync_status="synced",
        )
        db.session.add(ca)
        db.session.commit()

    with patch("app.admiral_client.validate_provisioning", side_effect=Exception("Boom")), app.app_context():
        result = validate_before_provisioning("app1", "tier")
        assert result["valid"] is False
        assert result["reason"] == "internal_error"
