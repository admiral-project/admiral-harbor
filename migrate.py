"""Explicit Harbor schema migration entry point for systemd/RPM deployment."""

import os

os.environ["HARBOR_MIGRATE"] = "1"

from app import create_app  # noqa: E402
from app.extensions import alembic, db  # noqa: E402
from sqlalchemy import inspect  # noqa: E402

app = create_app()


def migrate() -> None:
    with app.app_context():
        if not inspect(db.engine).get_table_names():
            # The historical base revision predates Alembic-managed schema
            # creation. Bootstrap only a completely empty database, then mark
            # it at the current model revision. Existing databases must always
            # follow the explicit revision chain below.
            db.create_all()
            alembic.stamp()
            return

        alembic.upgrade()


if __name__ == "__main__":
    migrate()
