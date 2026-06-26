# SPDX-FileCopyrightText: William Moreno Reyes <williamjmorenor@gmail.com>
# SPDX-License-Identifier: Apache-2.0

from flask import Blueprint, jsonify

from app.admiral_client import AdmiralAPIError, get_app
from app.extensions import db
from app.models import CatalogApp

bp = Blueprint("catalog", __name__, url_prefix="/api/catalog")


@bp.route("/apps")
def list_apps():
    apps = [
        app.as_dict()
        for app in db.session.query(CatalogApp)
        .filter_by(catalog_enabled=True)
        .order_by(CatalogApp.sort_order.asc(), CatalogApp.name.asc())
        .all()
    ]
    return jsonify({"apps": apps})


@bp.route("/apps/<slug>")
def app_detail(slug):
    app = db.session.query(CatalogApp).filter_by(upstream_app_id=slug, catalog_enabled=True).one_or_none()
    if app is None:
        return jsonify({"error": "app not found"}), 404
    return jsonify(app.as_dict())


@bp.route("/apps/<slug>/tiers")
def app_tiers(slug):
    try:
        app = get_app(slug)
    except AdmiralAPIError as exc:
        return jsonify({"error": str(exc)}), 502
    return jsonify(app.get("tiers", []))
