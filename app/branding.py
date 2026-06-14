# SPDX-FileCopyrightText: William Moreno Reyes <williamjmorenor@gmail.com>
# SPDX-License-Identifier: Apache-2.0

import json
from pathlib import Path

from flask import current_app, url_for
from werkzeug.utils import secure_filename

from app.extensions import db
from app.models import HarborMeta

DEFAULT_PORTAL_NAME = "Admiral Harbor"
DEFAULT_PORTAL_DESCRIPTION = "Customer portal"
DEFAULT_TAX_RATES = {"NI": 15}
ALLOWED_IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico", ".svg"}
CATALOG_IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg"}

PORTAL_NAME_KEY = "portal_name"
PORTAL_DESCRIPTION_KEY = "portal_description"
PORTAL_LOGO_FILE_KEY = "portal_logo_file"
PORTAL_FAVICON_FILE_KEY = "portal_favicon_file"
TAX_RATES_KEY = "tax_rates_json"


def _meta_value(key, default=None):
    value = HarborMeta.get(key, default)
    return default if value in (None, "") else value


def _branding_dir():
    branding_dir = Path(current_app.config["HARBOR_UPLOAD_DIR"]) / "branding"
    branding_dir.mkdir(parents=True, exist_ok=True)
    return branding_dir


def _upsert_meta(key, value):
    row = db.session.query(HarborMeta).filter_by(key=key).one_or_none()
    if row is None:
        row = HarborMeta(key=key, value=str(value))
        db.session.add(row)
    else:
        row.value = str(value)


def get_tax_rates():
    raw = HarborMeta.get(TAX_RATES_KEY)
    if not raw:
        return dict(DEFAULT_TAX_RATES)
    try:
        data = json.loads(raw)
    except (TypeError, ValueError, json.JSONDecodeError):
        return dict(DEFAULT_TAX_RATES)
    if not isinstance(data, dict):
        return dict(DEFAULT_TAX_RATES)
    rates = {}
    for country, percent in data.items():
        try:
            rates[str(country).strip().upper()] = int(percent)
        except (TypeError, ValueError):
            continue
    return rates or dict(DEFAULT_TAX_RATES)


def set_tax_rates(rates):
    normalized = {}
    for country, percent in rates.items():
        country_code = str(country).strip().upper()
        if not country_code:
            continue
        normalized[country_code] = int(percent)
    _upsert_meta(TAX_RATES_KEY, json.dumps(normalized, sort_keys=True))
    db.session.commit()
    return normalized


def save_portal_asset(file_storage, kind):
    if file_storage is None or not file_storage.filename:
        return None
    if kind not in {"logo", "favicon"}:
        raise ValueError(f"Unsupported branding asset kind: {kind}")

    filename = secure_filename(file_storage.filename)
    suffix = Path(filename).suffix.lower()
    if suffix and suffix not in ALLOWED_IMAGE_EXTENSIONS:
        raise ValueError("Unsupported branding asset format")
    if not suffix:
        suffix = ".png" if kind == "logo" else ".ico"

    stored_name = f"portal-{kind}{suffix}"
    target = _branding_dir() / stored_name
    file_storage.save(target)
    _upsert_meta(f"portal_{kind}_file", stored_name)
    db.session.commit()
    return stored_name


def _catalog_dir(slug):
    catalog_dir = Path(current_app.config["HARBOR_UPLOAD_DIR"]) / "catalog" / secure_filename(slug)
    catalog_dir.mkdir(parents=True, exist_ok=True)
    return catalog_dir


def save_catalog_asset(file_storage, slug):
    if file_storage is None or not file_storage.filename:
        return None
    filename = secure_filename(file_storage.filename)
    suffix = Path(filename).suffix.lower()
    if suffix not in CATALOG_IMAGE_EXTENSIONS:
        raise ValueError("Unsupported catalog asset format")
    stored_name = f"logo{suffix}"
    target = _catalog_dir(slug) / stored_name
    file_storage.save(target)
    return stored_name


def portal_asset_url(kind):
    if kind not in {"logo", "favicon"}:
        raise ValueError(f"Unsupported branding asset kind: {kind}")
    filename = HarborMeta.get(f"portal_{kind}_file")
    if filename:
        return url_for("main.portal_asset", kind=kind)
    return url_for("static", filename="img/favicon.ico") if kind == "favicon" else url_for("static", filename="img/admiral-harbor.png")


def get_portal_branding():
    return {
        "portal_name": _meta_value(PORTAL_NAME_KEY, current_app.config["HARBOR_PORTAL_NAME"] or DEFAULT_PORTAL_NAME),
        "portal_description": _meta_value(
            PORTAL_DESCRIPTION_KEY,
            current_app.config["HARBOR_PORTAL_DESCRIPTION"] or DEFAULT_PORTAL_DESCRIPTION,
        ),
        "portal_logo_url": portal_asset_url("logo"),
        "portal_favicon_url": portal_asset_url("favicon"),
    }


def update_portal_branding(name, description, logo_file=None, favicon_file=None):
    if name is not None:
        _upsert_meta(PORTAL_NAME_KEY, name)
    if description is not None:
        _upsert_meta(PORTAL_DESCRIPTION_KEY, description)
    if logo_file is not None and logo_file.filename:
        save_portal_asset(logo_file, "logo")
    if favicon_file is not None and favicon_file.filename:
        save_portal_asset(favicon_file, "favicon")
    db.session.commit()
    return get_portal_branding()


def ensure_default_portal_settings():
    if HarborMeta.get(PORTAL_NAME_KEY) in (None, ""):
        _upsert_meta(PORTAL_NAME_KEY, DEFAULT_PORTAL_NAME)
    if HarborMeta.get(PORTAL_DESCRIPTION_KEY) in (None, ""):
        _upsert_meta(PORTAL_DESCRIPTION_KEY, DEFAULT_PORTAL_DESCRIPTION)
    if HarborMeta.get(TAX_RATES_KEY) in (None, ""):
        _upsert_meta(TAX_RATES_KEY, json.dumps(DEFAULT_TAX_RATES, sort_keys=True))
    db.session.commit()
