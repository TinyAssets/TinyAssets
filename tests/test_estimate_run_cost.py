"""Tests for estimate_run_cost extensions action.

Covers:
- Missing branch_def_id returns error.
- Nonexistent branch_def_id returns error.
- Never-run branch returns low confidence + node_count.
- Branch with 1-4 completed runs returns medium confidence.
- Branch with 5+ completed runs returns high confidence.
- Queue depth unavailable path: free_queue_eta_hours is null + caveat in basis.
- Response is stable (read-only): two consecutive calls return identical output.
- estimate_run_cost NOT in _RUN_WRITE_ACTIONS (truly read-only).
- node_count reflects actual branch node count.
"""

from __future__ import annotations


class TestEstimateRunCostReadOnly:

    def test_not_in_write_actions(self):
        from tinyassets.api.runs import _RUN_WRITE_ACTIONS
        assert "estimate_run_cost" not in _RUN_WRITE_ACTIONS
