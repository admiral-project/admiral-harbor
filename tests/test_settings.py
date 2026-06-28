from app.settings import (
    get_setting,
    set_setting,
    get_all_settings,
    set_smtp_from,
    get_smtp_from,
    set_external_url,
    get_external_url,
    set_max_backup_upload_bytes,
    get_max_backup_upload_bytes,
    overdue_policy_dict,
)
from app.models import HarborMeta
from app.extensions import db


def test_get_setting_default(app):
    """Test get_setting returns default when not in DB."""
    with app.app_context():
        # Clear DB for the key
        db.session.query(HarborMeta).filter_by(key="harbor_smtp_from").delete()
        db.session.commit()

        assert get_smtp_from() == "noreply@example.com"
        assert get_setting("non_existent", "fallback") == "fallback"


def test_set_and_get_setting(app):
    """Test set_setting persists to DB and get_setting retrieves it."""
    with app.app_context():
        set_smtp_from("test@example.com")
        assert get_smtp_from() == "test@example.com"

        # Verify it's in DB
        meta = HarborMeta.query.filter_by(key="harbor_smtp_from").first()
        assert meta is not None
        assert meta.value == "test@example.com"


def test_set_setting_empty(app):
    """Test set_setting ignores empty or None values."""
    with app.app_context():
        set_setting("test_key", "original")
        assert get_setting("test_key") == "original"

        set_setting("test_key", "")
        assert get_setting("test_key") == "original"

        set_setting("test_key", None)
        assert get_setting("test_key") == "original"

        set_setting("test_key", "  ")
        assert get_setting("test_key") == "original"


def test_get_all_settings(app):
    """Test get_all_settings returns all expected keys."""
    with app.app_context():
        settings = get_all_settings()
        assert "harbor_smtp_from" in settings
        assert "harbor_external_url" in settings
        assert "harbor_max_backup_upload_bytes" in settings


def test_external_url_get_set(app):
    with app.app_context():
        set_external_url("https://harbor.test")
        assert get_external_url() == "https://harbor.test"


def test_max_backup_upload_bytes_get_set(app):
    with app.app_context():
        set_max_backup_upload_bytes(1024)
        assert get_max_backup_upload_bytes() == 1024


def test_overdue_policy_dict(app):
    with app.app_context():
        policy = overdue_policy_dict()
        assert "policy_version" in policy
        assert "grace_before_suspend_days" in policy
        assert "additional_grace_before_deprovision_days" in policy
        assert "last_backup_retention_days" in policy
        assert policy["requires_acceptance_at_signup"] is True
