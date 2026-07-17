"""Explicit Harbor schema migration entry point for systemd/RPM deployment."""

import os

os.environ["HARBOR_MIGRATE"] = "1"

from app import create_app
from app.extensions import alembic

app = create_app()

with app.app_context():
    alembic.upgrade()
