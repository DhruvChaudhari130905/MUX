"""Tests for room passwords and attempt limiting (mux/rooms/access.py)."""

import time

from mux.rooms.access import AttemptLimiter, hash_password, verify_password, MAX_FAILED_ATTEMPTS, LOCKOUT_SECONDS


# ---------------------------------------------------------------------------
# hash_password / verify_password
# ---------------------------------------------------------------------------

def test_password_round_trip():
    stored = hash_password("hunter2")
    assert stored.startswith("scrypt$")
    assert verify_password("hunter2", stored) is True
    assert verify_password("wrong", stored) is False


def test_password_format_has_five_parts():
    stored = hash_password("pw")
    parts = stored.split("$")
    assert len(parts) == 6  # scheme, n, r, p, salt, digest
    assert parts[0] == "scrypt"


def test_verify_malformed_returns_false():
    assert verify_password("pw", "not-a-valid-hash") is False
    assert verify_password("pw", "scrypt$bad") is False


# ---------------------------------------------------------------------------
# AttemptLimiter
# ---------------------------------------------------------------------------

def test_limiter_allows_under_max():
    lim = AttemptLimiter(max_failures=3, window=60)
    assert lim.retry_after("room", "user") == 0.0
    lim.record_failure("room", "user")
    lim.record_failure("room", "user")
    assert lim.retry_after("room", "user") == 0.0


def test_limiter_locks_at_max():
    lim = AttemptLimiter(max_failures=3, window=60)
    for _ in range(3):
        lim.record_failure("room", "user")
    wait = lim.retry_after("room", "user")
    assert wait > 0
    assert wait <= 60


def test_limiter_window_expires_clears():
    lim = AttemptLimiter(max_failures=2, window=1)
    lim.record_failure("r", "u")
    lim.record_failure("r", "u")
    assert lim.retry_after("r", "u") > 0
    # advance time past window by monkeypatching internal _recent's time source
    import mux.rooms.access as acc
    orig = acc.time.monotonic
    acc.time.monotonic = lambda: orig() + 2
    try:
        assert lim.retry_after("r", "u") == 0.0
    finally:
        acc.time.monotonic = orig


def test_limiter_reset_clears():
    lim = AttemptLimiter(max_failures=2, window=60)
    lim.record_failure("r", "u")
    lim.record_failure("r", "u")
    assert lim.retry_after("r", "u") > 0
    lim.reset("r", "u")
    assert lim.retry_after("r", "u") == 0.0


def test_limiter_per_room_and_user():
    lim = AttemptLimiter(max_failures=1, window=60)
    lim.record_failure("r1", "u1")
    assert lim.retry_after("r1", "u1") > 0
    # different room or user is unaffected
    assert lim.retry_after("r1", "u2") == 0.0
    assert lim.retry_after("r2", "u1") == 0.0
