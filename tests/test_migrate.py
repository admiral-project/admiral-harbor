# SPDX-FileCopyrightText: William Moreno Reyes <williamjmorenor@gmail.com>
# SPDX-License-Identifier: Apache-2.0

from sqlalchemy import inspect, text

import migrate as migration_command
from app.config import Config
from app.extensions import alembic, db


def test_migrate_bootstraps_empty_database(monkeypatch, tmp_path):
    class MigrationTestConfig(Config):
        SQLALCHEMY_DATABASE_URI = f"sqlite:///{tmp_path / 'harbor.db'}"

    monkeypatch.setattr(migration_command, "app", migration_command.create_app(MigrationTestConfig))

    migration_command.migrate()

    with migration_command.app.app_context():
        tables = set(inspect(db.engine).get_table_names())
        revision = db.session.execute(text("select version_num from alembic_version")).scalar_one()
        assert {"customer", "catalog_app", "harbor_admin_user", "alembic_version"} <= tables
        assert revision == alembic.script_directory.get_current_head()
