# SPDX-FileCopyrightText: William Moreno Reyes <williamjmorenor@gmail.com>
# SPDX-License-Identifier: Apache-2.0

from flask_alembic import Alembic
from flask_login import LoginManager
from flask_sqlalchemy import SQLAlchemy

from app.secrets_manager import SecretsManager

db = SQLAlchemy()
alembic = Alembic(run_mkdir=False)
login_manager = LoginManager()
secrets: SecretsManager | None = None
