"""Tests for token and sandbox-run budget accounting (mux/rooms/budget.py)."""

from mux.rooms.budget import BudgetConfig, BudgetState


# ---------------------------------------------------------------------------
# BudgetState properties
# ---------------------------------------------------------------------------

def test_defaults():
    s = BudgetState()
    assert s.tokens_used == 0 and s.sandbox_runs_used == 0
    assert s.token_pct == 0.0 and s.sandbox_run_pct == 0.0
    assert s.is_over_token_cap is False
    assert s.is_over_sandbox_cap is False
    assert s.is_at_warning() is False


def test_token_pct_and_cap():
    s = BudgetState(tokens_used=800_000, token_cap=1_000_000)
    assert s.token_pct == 0.8
    assert s.is_at_warning(0.8) is True   # exactly at threshold
    assert s.is_at_warning(0.9) is False
    assert s.is_over_token_cap is False


def test_over_caps():
    s = BudgetState(tokens_used=1_000_001, token_cap=1_000_000,
                    sandbox_runs_used=101, sandbox_run_cap=100)
    assert s.is_over_token_cap is True
    assert s.is_over_sandbox_cap is True
    assert s.token_pct > 1.0
    assert s.sandbox_run_pct > 1.0


def test_zero_cap_no_division():
    s = BudgetState(token_cap=0, sandbox_run_cap=0)
    assert s.token_pct == 0.0
    assert s.sandbox_run_pct == 0.0
    # zero usage with zero cap counts as at/over cap (>=)
    assert s.is_over_token_cap is True


# ---------------------------------------------------------------------------
# with_tokens
# ---------------------------------------------------------------------------

def test_with_tokens_immutable():
    s = BudgetState(tokens_used=100, token_cap=1_000_000, paused=True, paused_reason="cap")
    s2 = s.with_tokens(500)
    assert s.tokens_used == 100          # original unchanged
    assert s2.tokens_used == 600
    assert s2.paused is True
    assert s2.paused_reason == "cap"
    assert s2.token_cap == s.token_cap   # caps carried over


# ---------------------------------------------------------------------------
# BudgetConfig
# ---------------------------------------------------------------------------

def test_config_defaults():
    c = BudgetConfig()
    assert c.token_cap == 1_000_000
    assert c.sandbox_run_cap == 100
    assert c.warning_threshold == 0.8
