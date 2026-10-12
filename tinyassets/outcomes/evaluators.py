"""Real-world outcome evaluator adapters.

Each class satisfies the `tinyassets.evaluation.Evaluator` protocol via structural
subtyping — no inheritance required. All implement:
    evaluate(state: dict[str, Any]) -> EvalResult

State keys consumed per evaluator:
  PublishedPaperEvaluator: state["doi"]          → str or None
  MergedPREvaluator:       state["pr_url"]        → str or None
  DeployedAppEvaluator:    state["app_url"]       → str or None

Network-backed evaluators are probe-free by default. To avoid real network
calls in tests, callers inject a `prober` callable: `prober(url: str) -> bool`.
When no prober is injected, `_unverified_prober` is used so the result is
explicitly unverified instead of silently treated as a checked failure.
"""

from __future__ import annotations

from typing import Any, Callable

from tinyassets.evaluation import EvalResult

# Default prober: never fires real network requests; override at call-site.
_ProbeResult = bool | None
_ProberFn = Callable[[str], _ProbeResult]
_ACCEPTED_DECISIONS = frozenset(
    {
        "accept",
        "accepted",
        "accepted_with_revisions",
        "accepted_with_minor_revisions",
        "accepted_with_major_revisions",
        "conditional_accept",
        "conditionally_accepted",
    }
)


def _unverified_prober(url: str) -> None:  # noqa: ARG001
    """Default prober: network-free and explicitly unverified."""
    return None


def _verification_status(probe_result: _ProbeResult) -> str:
    return "unverified" if probe_result is None else "verified"


def _is_accepted_decision(decision: Any) -> bool:
    return str(decision).strip().lower().replace(" ", "_").replace("-", "_") in _ACCEPTED_DECISIONS


class PublishedPaperEvaluator:
    """Checks whether a DOI resolves to a published paper.

    state keys:
      doi (str | None): DOI identifier, e.g. "10.1234/example".
    """

    label = "published_paper"

    def __init__(self, prober: _ProberFn = _unverified_prober) -> None:
        self._prober = prober

    def evaluate(self, state: dict[str, Any]) -> EvalResult:
        doi = state.get("doi")
        if not doi:
            return EvalResult(
                score=-1.0,
                verdict="skip",
                kind="custom",
                label=self.label,
                details={"reason": "no doi in state"},
            )
        doi_url = f"https://doi.org/{doi}"
        probe_result = self._prober(doi_url)
        resolved = None if probe_result is None else bool(probe_result)
        return EvalResult(
            score=1.0 if resolved else 0.0,
            verdict="pass" if resolved else "fail",
            kind="custom",
            label=self.label,
            details={
                "doi": doi,
                "doi_url": doi_url,
                "resolved": resolved,
                "verification_status": _verification_status(probe_result),
            },
        )


class MergedPREvaluator:
    """Checks whether a GitHub PR URL reports a merged state.

    state keys:
      pr_url (str | None): GitHub PR URL, e.g. "https://github.com/org/repo/pull/42".
    """

    label = "merged_pr"

    def __init__(self, prober: _ProberFn = _unverified_prober) -> None:
        self._prober = prober

    def evaluate(self, state: dict[str, Any]) -> EvalResult:
        pr_url = state.get("pr_url")
        if not pr_url:
            return EvalResult(
                score=-1.0,
                verdict="skip",
                kind="custom",
                label=self.label,
                details={"reason": "no pr_url in state"},
            )
        probe_result = self._prober(pr_url)
        merged = None if probe_result is None else bool(probe_result)
        return EvalResult(
            score=1.0 if merged else 0.0,
            verdict="pass" if merged else "fail",
            kind="custom",
            label=self.label,
            details={
                "pr_url": pr_url,
                "merged": merged,
                "verification_status": _verification_status(probe_result),
            },
        )


class DeployedAppEvaluator:
    """Checks whether an application URL is live (HTTP 2xx).

    state keys:
      app_url (str | None): URL to probe, e.g. "https://myapp.example.com".
    """

    label = "deployed_app"

    def __init__(self, prober: _ProberFn = _unverified_prober) -> None:
        self._prober = prober

    def evaluate(self, state: dict[str, Any]) -> EvalResult:
        app_url = state.get("app_url")
        if not app_url:
            return EvalResult(
                score=-1.0,
                verdict="skip",
                kind="custom",
                label=self.label,
                details={"reason": "no app_url in state"},
            )
        probe_result = self._prober(app_url)
        live = None if probe_result is None else bool(probe_result)
        return EvalResult(
            score=1.0 if live else 0.0,
            verdict="pass" if live else "fail",
            kind="custom",
            label=self.label,
            details={
                "app_url": app_url,
                "live": live,
                "verification_status": _verification_status(probe_result),
            },
        )


class PeerReviewAcceptedEvaluator:
    """Records peer-review acceptance for an academic paper.

    state keys (required):
      venue (str):         Journal or conference venue name.
      decision (str):      Acceptance decision, e.g. "accepted", "accepted_with_revisions".
      decision_date (str): ISO-8601 date string, e.g. "2026-04-01".
    state keys (optional):
      reviewers (int):     Anonymized reviewer count.
    """

    label = "peer_review_accepted"

    def evaluate(self, state: dict[str, Any]) -> EvalResult:
        missing = [k for k in ("venue", "decision", "decision_date") if not state.get(k)]
        if missing:
            return EvalResult(
                score=0.0,
                verdict="fail",
                kind="custom",
                label=self.label,
                details={"reason": "missing required fields", "missing": missing},
            )
        accepted = _is_accepted_decision(state["decision"])
        details: dict[str, Any] = {
            "venue": state["venue"],
            "decision": state["decision"],
            "decision_date": state["decision_date"],
        }
        if state.get("reviewers") is not None:
            details["reviewers"] = state["reviewers"]
        return EvalResult(
            score=1.0 if accepted else 0.0,
            verdict="pass" if accepted else "fail",
            kind="custom",
            label=self.label,
            details=details,
        )


class ConferenceAcceptedEvaluator:
    """Records conference-talk acceptance.

    state keys (required):
      conference_name (str): Conference name, e.g. "NeurIPS 2026".
      talk_date (str):       ISO-8601 date of the talk.
      decision (str):        Structured acceptance decision, e.g. "accepted".
      accepted_at (str):     ISO-8601 date of the acceptance notification.
    state keys (optional):
      recording_url (str):   URL to the talk recording once available.
    """

    label = "conference_accepted"

    def evaluate(self, state: dict[str, Any]) -> EvalResult:
        missing = [
            k
            for k in ("conference_name", "decision", "talk_date", "accepted_at")
            if not state.get(k)
        ]
        if missing:
            return EvalResult(
                score=0.0,
                verdict="fail",
                kind="custom",
                label=self.label,
                details={"reason": "missing required fields", "missing": missing},
            )
        accepted = _is_accepted_decision(state["decision"])
        details: dict[str, Any] = {
            "conference_name": state["conference_name"],
            "decision": state["decision"],
            "talk_date": state["talk_date"],
            "accepted_at": state["accepted_at"],
        }
        if state.get("recording_url"):
            details["recording_url"] = state["recording_url"]
        return EvalResult(
            score=1.0 if accepted else 0.0,
            verdict="pass" if accepted else "fail",
            kind="custom",
            label=self.label,
            details=details,
        )


def _load_hyperparameter_backends() -> tuple[Any, Any, str]:
    """Lazy-import scientific deps for HyperparameterImportanceEvaluator.

    Returns (RandomForestRegressor_or_None, spearmanr_or_None, error_message).
    Empty error_message means both loaded; otherwise both backends are None
    and error_message names the install hint.
    """
    try:
        from scipy.stats import spearmanr  # type: ignore
        from sklearn.ensemble import RandomForestRegressor  # type: ignore
    except ImportError as exc:
        return (
            None,
            None,
            f"scikit-learn / scipy not installed ({exc}); pip install tinyassets[scientific]",
        )
    return RandomForestRegressor, spearmanr, ""


class HyperparameterImportanceEvaluator:
    """Ranks hyperparameter importance across sweep run results.

    Implements the W&B-Sweeps parity gap surfaced by the Priya competitor
    trial (2026-04-24). See `openspec/specs/evaluation-outcomes-and-attribution/spec.md`.

    state keys (required):
      run_results (list[dict]): each entry shaped like {params: dict, metric: float}
                                or {params: dict, <target_metric>: float}.
    state keys (optional):
      target_metric (str): metric key inside each run entry. Default "metric".
      method (str):        "rf" (default) or "correlation".
      top_n (int):         how many top params to surface. Default 5.
      min_runs (int):      below this, returns verdict="skip". Default 10.

    Output: EvalResult.score is the importance of the #1 param (high = sweep
    was informative). details["importance_ranking"] is the full ordered list.

    Categorical params are ordinal-encoded by sorted-unique-value index — see
    design-note Q1; one-hot is phase 2.
    """

    label = "hyperparameter_importance"

    def evaluate(self, state: dict[str, Any]) -> EvalResult:
        runs = state.get("run_results") or []
        if not runs:
            return EvalResult(
                score=-1.0,
                verdict="skip",
                kind="custom",
                label=self.label,
                details={"reason": "no run_results in state"},
            )

        min_runs = int(state.get("min_runs", 10))
        if len(runs) < min_runs:
            return EvalResult(
                score=-1.0,
                verdict="skip",
                kind="custom",
                label=self.label,
                details={
                    "reason": "n_runs below min_runs",
                    "n_runs": len(runs),
                    "min_runs": min_runs,
                },
            )

        method = state.get("method", "rf")
        top_n = int(state.get("top_n", 5))
        metric_key = state.get("target_metric", "metric")

        rf_cls, spearmanr_fn, err = _load_hyperparameter_backends()
        if err:
            return EvalResult(
                score=-1.0,
                verdict="skip",
                kind="custom",
                label=self.label,
                details={"reason": err},
            )

        param_names = list(runs[0].get("params", {}).keys())
        if not param_names:
            return EvalResult(
                score=-1.0,
                verdict="skip",
                kind="custom",
                label=self.label,
                details={"reason": "no params in first run"},
            )

        # Ordinal encoding for categoricals — see design note Q1; one-hot is phase 2.
        encoders: dict[str, dict[Any, int]] = {}
        for p in param_names:
            values = [r.get("params", {}).get(p) for r in runs]
            if any(not isinstance(v, (int, float)) for v in values):
                uniq = sorted({str(v) for v in values})
                encoders[p] = {v: i for i, v in enumerate(uniq)}

        def _encode(p: str, v: Any) -> float:
            if p in encoders:
                return float(encoders[p][str(v)])
            return float(v)

        X = [[_encode(p, r.get("params", {}).get(p)) for p in param_names] for r in runs]
        y = [float(r.get(metric_key, r.get("metric", 0.0))) for r in runs]

        if method == "correlation":
            scores = []
            for i in range(len(param_names)):
                col = [row[i] for row in X]
                stat = spearmanr_fn(col, y).statistic
                scores.append(0.0 if stat != stat else abs(float(stat)))  # NaN guard
            method_used = "correlation"
        else:
            rf = rf_cls(n_estimators=100, random_state=42)
            rf.fit(X, y)
            scores = [float(s) for s in rf.feature_importances_]
            method_used = "rf"

        ranked = sorted(zip(param_names, scores), key=lambda t: -t[1])
        ranking = [
            {"param": p, "importance": s, "rank": i + 1}
            for i, (p, s) in enumerate(ranked[:top_n])
        ]

        top_score = ranking[0]["importance"] if ranking else 0.0
        return EvalResult(
            score=float(top_score),
            verdict="pass",
            kind="custom",
            label=self.label,
            details={
                "importance_ranking": ranking,
                "method_used": method_used,
                "n_runs_analyzed": len(runs),
            },
        )


class MentionedInPublicationEvaluator:
    """Records a citation or mention in a publication.

    state keys (required):
      publication (str):    Publication name, e.g. "Nature News", "TechCrunch".
      mention_url (str):    URL to the article containing the mention.
      mention_date (str):   ISO-8601 date of the mention.
    state keys (optional):
      context_snippet (str): Short excerpt showing the mention in context.
    """

    label = "mentioned_in_publication"

    def evaluate(self, state: dict[str, Any]) -> EvalResult:
        missing = [k for k in ("publication", "mention_url", "mention_date") if not state.get(k)]
        if missing:
            return EvalResult(
                score=0.0,
                verdict="fail",
                kind="custom",
                label=self.label,
                details={"reason": "missing required fields", "missing": missing},
            )
        details: dict[str, Any] = {
            "publication": state["publication"],
            "mention_url": state["mention_url"],
            "mention_date": state["mention_date"],
        }
        if state.get("context_snippet"):
            details["context_snippet"] = state["context_snippet"]
        return EvalResult(
            score=1.0,
            verdict="pass",
            kind="custom",
            label=self.label,
            details=details,
        )
