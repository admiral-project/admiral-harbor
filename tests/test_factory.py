# SPDX-FileCopyrightText: William Moreno Reyes <williamjmorenor@gmail.com>
# SPDX-License-Identifier: Apache-2.0

from sqlalchemy.exc import IntegrityError

from app.extensions import db
from app.models import HarborAdminUser
from app import create_app


def test_create_app():
    app = create_app()
    assert app is not None
    assert app.config["SQLALCHEMY_DATABASE_URI"]
    assert app.config["HARBOR_OVERDUE_SUSPEND_AFTER_DAYS"] == 5
    assert app.config["HARBOR_OVERDUE_DEPROVISION_AFTER_DAYS"] == 10
    assert app.config["HARBOR_OVERDUE_LAST_BACKUP_RETENTION_DAYS"] == 15


def test_ensure_default_admin_ignores_duplicate_race(app, monkeypatch):
    with app.app_context():
        calls = {"query": 0, "commit": 0}
        existing_admin = HarborAdminUser(
            username="testadmin",
            display_name="Existing Admin",
            password_hash="hash",
        )

        class FakeQuery:
            def __init__(self, phase):
                self.phase = phase

            def filter_by(self, **kwargs):
                return self

            def one_or_none(self):
                if self.phase == 1:
                    return None
                if self.phase == 3:
                    return existing_admin
                return None

            def count(self):
                return 0

        def fake_query(model):
            calls["query"] += 1
            return FakeQuery(calls["query"])

        def fake_commit():
            calls["commit"] += 1
            raise IntegrityError("INSERT", {}, Exception("duplicate key"))

        monkeypatch.setattr(db.session, "query", fake_query)
        monkeypatch.setattr(db.session, "commit", fake_commit)
        monkeypatch.setattr(db.session, "rollback", lambda: None)
        monkeypatch.setattr(db.session, "add", lambda obj: None)

        HarborAdminUser.ensure_default_admin(username="testadmin", password="secret")

        assert calls["commit"] == 1
