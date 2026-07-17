# SPDX-FileCopyrightText: William Moreno Reyes <williamjmorenor@gmail.com>
# SPDX-License-Identifier: Apache-2.0

import time

from app.extensions import db
from app.models import RateLimit
from sqlalchemy import delete


def _insert_counter(identifier, now):
    """Create a counter atomically on PostgreSQL and SQLite."""
    dialect = db.engine.dialect.name
    if dialect == "postgresql":
        from sqlalchemy.dialects.postgresql import insert
    elif dialect == "sqlite":
        from sqlalchemy.dialects.sqlite import insert
    else:  # pragma: no cover - production uses PostgreSQL
        return False
    statement = insert(RateLimit).values(identifier=identifier, window_start=now, attempts=1)
    statement = statement.on_conflict_do_nothing(index_elements=["identifier"])
    result = db.session.execute(statement)
    return bool(result.rowcount)


class RateLimiter:
    """Rate limiter using PostgreSQL for multi-worker support."""

    def __init__(self, max_attempts=5, window_seconds=60):
        self.max_attempts = max_attempts
        self.window_seconds = window_seconds

    def is_allowed(self, identifier):
        now = time.time()
        cutoff = now - self.window_seconds

        entry = db.session.query(RateLimit).filter(RateLimit.identifier == identifier).with_for_update().first()

        if entry is None:
            created = _insert_counter(identifier, now)
            if not created:
                entry = db.session.query(RateLimit).filter(RateLimit.identifier == identifier).with_for_update().first()
            else:
                db.session.commit()
                return True, 0
        if entry is None:
            # A non-PostgreSQL backend without upsert support is not a valid
            # production deployment; fail closed rather than admit a request.
            db.session.rollback()
            return False, self.window_seconds

        if entry.window_start < cutoff:
            entry.window_start = now
            entry.attempts = 1
            db.session.commit()
            return True, 0

        if entry.attempts >= self.max_attempts:
            remaining = int(self.window_seconds - (now - entry.window_start)) + 1
            db.session.commit()
            return False, max(remaining, 1)

        entry.attempts += 1
        db.session.commit()
        return True, 0

    def reset(self, identifier):
        db.session.query(RateLimit).filter(RateLimit.identifier == identifier).delete()
        db.session.commit()

    def cleanup_expired(self, now=None):
        """Remove counters that can no longer affect a request."""
        now = time.time() if now is None else now
        cutoff = now - self.window_seconds
        removed = db.session.execute(delete(RateLimit).where(RateLimit.window_start < cutoff)).rowcount
        db.session.commit()
        return removed or 0
