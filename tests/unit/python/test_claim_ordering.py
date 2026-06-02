"""claim_issue.py — priority + created_at ordering (the new sort keys)."""

import claim_issue as C


def _issue(iid, **over):
    base = {"status": "todo", "claimed_by": None, "ticket": None, "depends_on": []}
    base.update(over)
    return iid, base


def pick(issues, tickets=None):
    tdone = C.ticket_done_map(tickets or {}, issues)
    return C.pick_next(issues, tickets, tdone, set(), {}, {}, "all")


def test_high_priority_beats_medium():
    issues = dict([
        _issue("A-1", priority="medium", created_at="2026-01-01T00:00:00Z"),
        _issue("A-2", priority="high", created_at="2026-02-01T00:00:00Z"),
    ])
    assert pick(issues) == "A-2"


def test_same_priority_fifo_by_created_at():
    issues = dict([
        _issue("B-1", priority="high", created_at="2026-03-01T00:00:00Z"),
        _issue("B-2", priority="high", created_at="2026-01-01T00:00:00Z"),
    ])
    assert pick(issues) == "B-2"   # older first


def test_issue_inherits_ticket_priority():
    tickets = {
        "T-hi": {"priority": "high", "depends_on": [], "status": "todo"},
        "T-lo": {"priority": "low", "depends_on": [], "status": "todo"},
    }
    issues = dict([
        _issue("C-1", ticket="T-lo"),
        _issue("C-2", ticket="T-hi"),
    ])
    assert pick(issues, tickets) == "C-2"


def test_issue_priority_overrides_ticket():
    tickets = {"T": {"priority": "low", "depends_on": [], "status": "todo"}}
    issues = dict([
        _issue("D-1", ticket="T"),                 # inherits low
        _issue("D-2", ticket="T", priority="high"),  # override → wins
    ])
    assert pick(issues, tickets) == "D-2"


def test_spec_only_tree_unchanged_topo_order():
    """No priority, no created_at → behaviour falls through to topo (dep order),
    i.e. no regression for existing all-spec trees."""
    issues = dict([
        _issue("E-1"),
        _issue("E-2", depends_on=["E-1"]),
    ])
    assert pick(issues) == "E-1"


def test_effective_priority_rank_defaults_medium():
    assert C.effective_priority_rank({"ticket": None}, {}) == 1   # medium
    assert C.effective_priority_rank({"priority": "high"}, {}) == 0
    assert C.effective_priority_rank({"priority": "low"}, {}) == 2


def test_created_sort_key_missing_is_inf():
    assert C.created_sort_key({}) == float("inf")
    assert C.created_sort_key({"created_at": "2026-01-01T00:00:00Z"}) < float("inf")
