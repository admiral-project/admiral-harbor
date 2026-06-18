# SPDX-FileCopyrightText: William Moreno Reyes <williamjmorenor@gmail.com>
# SPDX-License-Identifier: Apache-2.0

import time

from app.extensions import db
from app.models import RateLimit


class RateLimiter:
    """Rate limiter using PostgreSQL for multi-worker support."""

    def __init__(self, max_attempts=5, window_seconds=60):
        self.max_attempts = max_attempts
        self.window_seconds = window_seconds

    def is_allowed(self, identifier):
        now = time.time()
        cutoff = now - self.window_seconds

        entry = (
            db.session.query(RateLimit)
            .filter(RateLimit.identifier == identifier)
            .with_for_update()
            .first()
        )

        if entry is None:
            db.session.add(
                RateLimit(
                    identifier=identifier,
                    window_start=now,
                    attempts=1,
                )
            )
            db.session.commit()
            return True, 0

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
