from __future__ import annotations

from typing import Any


def compute_composite_confidence(
    page_scope: dict | None = None,
    page_info: dict | None = None,
    page_model: dict | None = None,
    scope_validation: dict | None = None,
    scenario_validation: dict | None = None,
    execution_plan_validation: dict | None = None,
    execution_results: dict | None = None,
) -> dict:
    page_scope = page_scope or {}
    page_info = page_info or {}
    page_model = page_model or {}
    scope_validation = scope_validation or {}
    scenario_validation = scenario_validation or {}
    execution_plan_validation = execution_plan_validation or {}
    execution_results = execution_results or {}

    base_ai = _clamp(page_scope.get("confidence", 0.0))
    coverage = _scan_coverage_score(page_info, page_model)
    crawl_depth = _crawl_depth_score(page_info)
    state_discovery = _state_discovery_score(page_info, page_model)
    scope_alignment = _scope_alignment_score(scope_validation)
    scenario_alignment = _scenario_alignment_score(scenario_validation)
    plan_alignment = _plan_alignment_score(execution_plan_validation)
    execution_signal = _execution_signal_score(execution_results)

    weighted_items = [
        ("base_ai", base_ai, 0.22),
        ("coverage", coverage, 0.18),
        ("crawl_depth", crawl_depth, 0.10),
        ("state_discovery", state_discovery, 0.10),
        ("scope_alignment", scope_alignment, 0.14),
        ("scenario_alignment", scenario_alignment, 0.12),
        ("plan_alignment", plan_alignment, 0.10),
        ("execution_signal", execution_signal, 0.04),
    ]

    score = 0.0
    for _, value, weight in weighted_items:
        score += value * weight
    score = round(_clamp(score), 2)

    return {
        "score": score,
        "breakdown": {
            "base_ai": round(base_ai, 2),
            "coverage": round(coverage, 2),
            "crawl_depth": round(crawl_depth, 2),
            "state_discovery": round(state_discovery, 2),
            "scope_alignment": round(scope_alignment, 2),
            "scenario_alignment": round(scenario_alignment, 2),
            "plan_alignment": round(plan_alignment, 2),
            "execution_signal": round(execution_signal, 2),
            "weights": {name: weight for name, _, weight in weighted_items},
        },
    }


def _scan_coverage_score(page_info: dict, page_model: dict) -> float:
    fingerprint = page_info.get("page_fingerprint", {})
    components = page_model.get("component_catalog", [])
    fields = page_model.get("field_catalog", [])
    possible_flows = page_model.get("possible_flows", [])

    signals = [
        bool(page_info.get("headings")),
        bool(page_info.get("texts")),
        bool(page_info.get("links")),
        bool(page_info.get("sections")),
        bool(page_info.get("forms") or page_info.get("standalone_controls")),
        bool(components),
        bool(fields),
        bool(possible_flows),
        bool(fingerprint.get("sampled_page_count", 0) > 1),
    ]
    return sum(1 for item in signals if item) / len(signals)


def _crawl_depth_score(page_info: dict) -> float:
    fingerprint = page_info.get("page_fingerprint", {})
    sampled = int(fingerprint.get("sampled_page_count", 0) or 0)
    crawled_pages = len(page_info.get("crawled_pages", []))
    observed = max(sampled, crawled_pages + 1, 1)
    return min(1.0, observed / 4)


def _state_discovery_score(page_info: dict, page_model: dict) -> float:
    discovered_states = len(page_info.get("discovered_states", []))
    probes = len(page_info.get("interaction_probes", []))
    state_graph_states = len(page_model.get("state_graph", {}).get("states", []))
    signals = 0
    if discovered_states:
        signals += 1
    if probes:
        signals += 1
    if state_graph_states >= 3:
        signals += 1
    return signals / 3


def _scope_alignment_score(scope_validation: dict) -> float:
    if not scope_validation:
        return 0.65
    issues = len(scope_validation.get("issues", []))
    score = 1.0 - min(0.7, issues * 0.08)
    if not scope_validation.get("is_valid", False):
        score -= 0.12
    return _clamp(score)


def _scenario_alignment_score(scenario_validation: dict) -> float:
    if not scenario_validation:
        return 0.7
    valid_cases = len(scenario_validation.get("valid_cases", []))
    rejected_cases = len(scenario_validation.get("rejected_cases", []))
    total = valid_cases + rejected_cases
    if not total:
        return 0.35
    score = valid_cases / total
    if not scenario_validation.get("is_valid", False):
        score *= 0.85
    return _clamp(score)


def _plan_alignment_score(execution_plan_validation: dict) -> float:
    if not execution_plan_validation:
        return 0.7
    valid_plans = len(execution_plan_validation.get("valid_plan", {}).get("plans", []))
    rejected_plans = len(execution_plan_validation.get("rejected_plans", []))
    total = valid_plans + rejected_plans
    if not total:
        return 0.3
    score = valid_plans / total
    if not execution_plan_validation.get("is_valid", False):
        score *= 0.85
    return _clamp(score)


def _execution_signal_score(execution_results: dict) -> float:
    results = execution_results.get("results", [])
    if not results:
        return 0.75
    total = len(results)
    passed = sum(1 for item in results if item.get("status") == "passed")
    checkpoint = sum(1 for item in results if item.get("status") == "checkpoint_required")
    score = (passed + (checkpoint * 0.5)) / total
    return _clamp(score)


def _clamp(value: Any) -> float:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, min(1.0, numeric))
