# SPDX-FileCopyrightText: William Moreno Reyes <williamjmorenor@gmail.com>
# SPDX-License-Identifier: Apache-2.0

import time
from collections import defaultdict


class RateLimiter:
    """Rate limiter using sliding window algorithm."""

    def __init__(self, max_attempts=5, window_seconds=60):
        self.max_attempts = max_attempts
        self.window_seconds = window_seconds
        self.attempts = defaultdict(list)

    def is_allowed(self, identifier):
        now = time.time()
        self.attempts[identifier] = [
            t for t in self.attempts[identifier] if now - t < self.window_seconds
        ]
        if len(self.attempts[identifier]) >= self.max_attempts:
            oldest = self.attempts[identifier][0]
            remaining = int(self.window_seconds - (now - oldest)) + 1
            return False, remaining
        self.attempts[identifier].append(now)
        return True, 0

    def reset(self, identifier):
        self.attempts.pop(identifier, None)
