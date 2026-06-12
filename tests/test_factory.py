# SPDX-FileCopyrightText: William Moreno Reyes <williamjmorenor@gmail.com>
# SPDX-License-Identifier: Apache-2.0

from app import create_app


def test_create_app():
    app = create_app()
    assert app is not None
    assert app.config["SQLALCHEMY_DATABASE_URI"]
    assert app.config["HARBOR_OVERDUE_SUSPEND_AFTER_DAYS"] == 5
    assert app.config["HARBOR_OVERDUE_DEPROVISION_AFTER_DAYS"] == 10
    assert app.config["HARBOR_OVERDUE_LAST_BACKUP_RETENTION_DAYS"] == 15
