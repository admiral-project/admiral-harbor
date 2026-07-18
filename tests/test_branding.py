# SPDX-FileCopyrightText: William Moreno Reyes <williamjmorenor@gmail.com>
# SPDX-License-Identifier: Apache-2.0

import io
import json
import pytest
from pathlib import Path
from werkzeug.datastructures import FileStorage

from app.branding import (
    get_tax_rates,
    set_tax_rates,
    save_portal_asset,
    save_catalog_asset,
    portal_asset_url,
    get_currency,
    get_tos_url,
    set_portal_tos_url,
    set_portal_currency,
    get_portal_branding,
    update_portal_branding,
    ensure_default_portal_settings,
    DEFAULT_TAX_RATES,
    DEFAULT_PORTAL_NAME,
    DEFAULT_PORTAL_DESCRIPTION,
    DEFAULT_CURRENCY,
    TAX_RATES_KEY,
)
from app.extensions import db
from app.models import HarborMeta


def test_get_tax_rates_defaults(app):
    with app.test_request_context():
        # Clear or mock database to ensure TAX_RATES_KEY is not set
        db.session.query(HarborMeta).filter_by(key=TAX_RATES_KEY).delete()
        db.session.commit()
        assert get_tax_rates() == DEFAULT_TAX_RATES


def test_get_tax_rates_invalid_json(app):
    with app.test_request_context():
        # Invalid JSON
        meta = HarborMeta(key=TAX_RATES_KEY, value="not-json")
        db.session.add(meta)
        db.session.commit()
        assert get_tax_rates() == DEFAULT_TAX_RATES

        # Not a dict
        meta.value = '"string-value"'
        db.session.commit()
        assert get_tax_rates() == DEFAULT_TAX_RATES


def test_get_tax_rates_conversion_robustness(app):
    with app.test_request_context():
        # Partially invalid structure
        meta = HarborMeta(key=TAX_RATES_KEY, value=json.dumps({"ni": "15", "invalid": "abc", "us": 0, "  ": 10}))
        db.session.add(meta)
        db.session.commit()
        rates = get_tax_rates()
        assert rates["NI"] == 15
        assert rates["US"] == 0
        assert "INVALID" not in rates


def test_set_tax_rates(app):
    with app.test_request_context():
        rates = {"ni": 15, "us": "5", "  ": 10}
        set_tax_rates(rates)
        expected = {"NI": 15, "US": 5}
        assert get_tax_rates() == expected


def test_save_portal_asset_empty_or_none(app):
    with app.test_request_context():
        assert save_portal_asset(None, "logo") is None
        file_empty = FileStorage(stream=io.BytesIO(b""), filename="")
        assert save_portal_asset(file_empty, "logo") is None


def test_save_portal_asset_unsupported_kind(app):
    with app.test_request_context():
        file_storage = FileStorage(stream=io.BytesIO(b"data"), filename="logo.png")
        with pytest.raises(ValueError, match="Unsupported branding asset kind"):
            save_portal_asset(file_storage, "unsupported")


def test_save_portal_asset_unsupported_format(app):
    with app.test_request_context():
        file_storage = FileStorage(stream=io.BytesIO(b"data"), filename="logo.txt")
        with pytest.raises(ValueError, match="Unsupported branding asset format"):
            save_portal_asset(file_storage, "logo")


def test_save_portal_asset_success(app):
    with app.test_request_context():
        file_storage = FileStorage(stream=io.BytesIO(b"fake-image"), filename="custom-logo.png")
        stored_name = save_portal_asset(file_storage, "logo")
        assert stored_name == "portal-logo.png"

        target_path = Path(app.config["HARBOR_UPLOAD_DIR"]) / "branding" / stored_name
        assert target_path.exists()
        assert target_path.read_bytes() == b"fake-image"


def test_save_portal_asset_no_suffix(app):
    with app.test_request_context():
        file_storage = FileStorage(stream=io.BytesIO(b"logo-data"), filename="logo_with_no_suffix")
        stored_name = save_portal_asset(file_storage, "logo")
        assert stored_name == "portal-logo.png"

        file_storage_fav = FileStorage(stream=io.BytesIO(b"favicon-data"), filename="fav_with_no_suffix")
        stored_fav = save_portal_asset(file_storage_fav, "favicon")
        assert stored_fav == "portal-favicon.ico"


def test_save_catalog_asset(app):
    with app.test_request_context():
        assert save_catalog_asset(None, "my-app") is None
        file_empty = FileStorage(stream=io.BytesIO(b""), filename="")
        assert save_catalog_asset(file_empty, "my-app") is None

        # Invalid format
        file_invalid = FileStorage(stream=io.BytesIO(b"data"), filename="img.gif")
        with pytest.raises(ValueError, match="Unsupported catalog asset format"):
            save_catalog_asset(file_invalid, "my-app")

        # Valid format
        file_valid = FileStorage(stream=io.BytesIO(b"catalog-data"), filename="img.jpg")
        stored_name = save_catalog_asset(file_valid, "my-app")
        assert stored_name == "logo.jpg"
        target = Path(app.config["HARBOR_UPLOAD_DIR"]) / "catalog" / "my-app" / "logo.jpg"
        assert target.exists()
        assert target.read_bytes() == b"catalog-data"


def test_portal_asset_url(app):
    with app.test_request_context():
        with pytest.raises(ValueError, match="Unsupported branding asset kind"):
            portal_asset_url("invalid")

        # Clear existing logo/favicon files
        db.session.query(HarborMeta).filter(HarborMeta.key.in_(["portal_logo_file", "portal_favicon_file"])).delete()
        db.session.commit()

        # Should fall back to static
        assert portal_asset_url("logo") == "/static/img/admiral-harbor.png"
        assert portal_asset_url("favicon") == "/static/img/favicon.ico"

        # Set them and check they return app asset URL
        _upsert_meta = HarborMeta(key="portal_logo_file", value="portal-logo.png")
        _upsert_fav = HarborMeta(key="portal_favicon_file", value="portal-favicon.ico")
        db.session.add(_upsert_meta)
        db.session.add(_upsert_fav)
        db.session.commit()

        assert portal_asset_url("logo") == "/branding/logo"
        assert portal_asset_url("favicon") == "/branding/favicon"


def test_currency_and_tos(app):
    with app.test_request_context():
        # Clear database currency/tos values
        db.session.query(HarborMeta).filter(HarborMeta.key.in_(["portal_currency", "portal_tos_url"])).delete()
        db.session.commit()

        # Defaults
        assert get_currency() == DEFAULT_CURRENCY
        assert get_tos_url() == ""

        # Set / Get currency
        set_portal_currency("eur")
        assert get_currency() == "EUR"

        # Set / Get TOS
        set_portal_tos_url("  https://example.com/tos  ")
        assert get_tos_url() == "https://example.com/tos"


def test_get_portal_branding_and_update(app):
    with app.test_request_context():
        branding = get_portal_branding()
        assert branding["portal_name"] == DEFAULT_PORTAL_NAME
        assert branding["portal_description"] == DEFAULT_PORTAL_DESCRIPTION

        # Update branding name and description only
        updated = update_portal_branding("New Name", "New Desc")
        assert updated["portal_name"] == "New Name"
        assert updated["portal_description"] == "New Desc"

        # Update branding files
        logo_storage = FileStorage(stream=io.BytesIO(b"new-logo"), filename="new-logo.jpg")
        fav_storage = FileStorage(stream=io.BytesIO(b"new-fav"), filename="new-fav.png")
        updated_with_files = update_portal_branding("New Name", "New Desc", logo_storage, fav_storage)
        assert updated_with_files["portal_logo_url"] == "/branding/logo"
        assert updated_with_files["portal_favicon_url"] == "/branding/favicon"


def test_ensure_default_portal_settings(app):
    with app.test_request_context():
        # Delete all metadata
        db.session.query(HarborMeta).delete()
        db.session.commit()

        ensure_default_portal_settings()

        assert HarborMeta.get("portal_name") == DEFAULT_PORTAL_NAME
        assert HarborMeta.get("portal_description") == DEFAULT_PORTAL_DESCRIPTION
        assert HarborMeta.get("portal_currency") == DEFAULT_CURRENCY
        assert get_tax_rates() == DEFAULT_TAX_RATES
