# SPDX-FileCopyrightText: William Moreno Reyes <williamjmorenor@gmail.com>
# SPDX-License-Identifier: Apache-2.0

from app.rate_limit import RateLimiter


def test_rate_limiter_sliding_window(monkeypatch):
    limiter = RateLimiter(max_attempts=2, window_seconds=10)
    times = iter([100.0, 101.0, 102.0, 111.0])

    monkeypatch.setattr("app.rate_limit.time.time", lambda: next(times))

    assert limiter.is_allowed("127.0.0.1") == (True, 0)
    assert limiter.is_allowed("127.0.0.1") == (True, 0)

    allowed, remaining = limiter.is_allowed("127.0.0.1")
    assert allowed is False
    assert remaining == 9

    allowed, remaining = limiter.is_allowed("127.0.0.1")
    assert allowed is True
    assert remaining == 0


def test_rate_limiter_reset_clears_identifier(monkeypatch):
    limiter = RateLimiter(max_attempts=1, window_seconds=60)
    monkeypatch.setattr("app.rate_limit.time.time", lambda: 100.0)

    assert limiter.is_allowed("10.0.0.1") == (True, 0)
    assert limiter.is_allowed("10.0.0.1")[0] is False

    limiter.reset("10.0.0.1")

    assert limiter.is_allowed("10.0.0.1") == (True, 0)
