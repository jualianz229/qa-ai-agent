from __future__ import annotations

from typing import Any

from modules.test_case_generator.src.case_memory import load_case_memory_snapshot
from core.feedback_bank import load_feedback_snapshot
from modules.end_to_end_automation.src.flaky_bank import load_flaky_snapshot
from core.site_profiles import derive_cluster_keys, load_knowledge_bank_snapshot


def compute_composite_confidence(
    page_scope: dict | None = None,
    page_info: dict | None = None,
    page_model: dict | None = None,
    scope_validation: dict | None = None,
    scenario_validation: dict | None = None,
    execution_plan_validation: dict | None = None,
    execution_results: dict | None = None,
    historical_signal: dict | None = None,
    contradiction_analysis: dict | None = None,
) -> dict:
    page_scope = page_scope or {}
    page_info = page_info or {}
    page_model = page_model or {}
    scope_validation = scope_validation or {}
    scenario_validation = scenario_validation or {}
    execution_plan_validation = execution_plan_validation or {}
    execution_results = execution_results or {}
    historical_signal = historical_signal or {}
    contradiction_analysis = contradiction_analysis or {}

    base_ai = _clamp(page_scope.get("confidence", 0.0))
    coverage = _scan_coverage_score(page_info, page_model)
    crawl_depth = _crawl_depth_score(page_info)
    state_discovery = _state_discovery_score(page_info, page_model)
    scope_alignment = _scope_alignment_score(scope_validation)
    scenario_alignment = _scenario_alignment_score(scenario_validation)
    plan_alignment = _plan_alignment_score(execution_plan_validation)
    execution_signal = _execution_signal_score(execution_results)
    anti_hallucination = _anti_hallucination_score(scenario_validation, execution_plan_validation, contradiction_analysis)
    evidence_grounding = _evidence_grounding_score(execution_results, scenario_validation)
    negative_evidence_score, negative_evidence = _negative_evidence_score(page_info, page_model, execution_results)
    source_trust_score, source_trust = _source_trust_score(page_info, page_model, historical_signal)
    real_world_calibration = _real_world_calibration_score(execution_results)
    stability = _stability_score(historical_signal)

    weighted_items = [
        ("base_ai", base_ai, 0.16),
        ("coverage", coverage, 0.16),
        ("crawl_depth", crawl_depth, 0.08),
        ("state_discovery", state_discovery, 0.08),
        ("scope_alignment", scope_alignment, 0.12),
        ("scenario_alignment", scenario_alignment, 0.09),
        ("plan_alignment", plan_alignment, 0.09),
        ("anti_hallucination", anti_hallucination, 0.08),
        ("evidence_grounding", evidence_grounding, 0.07),
        ("execution_signal", execution_signal, 0.06),
        ("negative_evidence", negative_evidence_score, 0.05),
        ("source_trust", source_trust_score, 0.04),
        ("real_world_calibration", real_world_calibration, 0.03),
        ("stability", stability, 0.02),
    ]

    total_weight = sum(weight for _, _, weight in weighted_items) or 1.0
    score = round(_clamp(sum(value * weight for _, value, weight in weighted_items) / total_weight), 2)
    explanation = _build_confidence_explanation(
        weighted_items=weighted_items,
        negative_evidence=negative_evidence,
        source_trust=source_trust,
        historical_signal=historical_signal,
    )

    return {
        "score": score,
        "confidence_class": _confidence_class(score),
        "explanation": explanation,
        "breakdown": {
            "base_ai": round(base_ai, 2),
            "coverage": round(coverage, 2),
            "crawl_depth": round(crawl_depth, 2),
            "state_discovery": round(state_discovery, 2),
            "scope_alignment": round(scope_alignment, 2),
            "scenario_alignment": round(scenario_alignment, 2),
            "plan_alignment": round(plan_alignment, 2),
            "anti_hallucination": round(anti_hallucination, 2),
            "evidence_grounding": round(evidence_grounding, 2),
            "execution_signal": round(execution_signal, 2),
            "negative_evidence": round(negative_evidence_score, 2),
            "source_trust": round(source_trust_score, 2),
            "real_world_calibration": round(real_world_calibration, 2),
            "stability": round(stability, 2),
            "weights": {name: weight for name, _, weight in weighted_items},
            "negative_evidence_detail": negative_evidence,
            "source_trust_detail": source_trust,
            "historical_signal": historical_signal,
        },
    }


def build_historical_confidence_signal(
    url: str = "",
    page_model: dict | None = None,
    page_scope: dict | None = None,
    site_profile: dict | None = None,
    case_memory_snapshot: dict | None = None,
    flaky_snapshot: dict | None = None,
    feedback_snapshot: dict | None = None,
    knowledge_snapshot: dict | None = None,
) -> dict:
    page_model = page_model or {}
    page_scope = page_scope or {}
    site_profile = site_profile or {}
    cluster_keys = derive_cluster_keys(page_model, page_scope)

    case_memory_snapshot = case_memory_snapshot or load_case_memory_snapshot(url, page_model=page_model, page_scope=page_scope)
    flaky_snapshot = flaky_snapshot or load_flaky_snapshot(url, page_model=page_model, page_scope=page_scope)
    feedback_snapshot = feedback_snapshot or site_profile.get("human_feedback") or load_feedback_snapshot(url, cluster_keys=cluster_keys)
    knowledge_snapshot = knowledge_snapshot or {
        "global": site_profile.get("knowledge_bank", {}),
        "domain": load_knowledge_bank_snapshot(url).get("domain", {}) if url else {},
    }

    matching_patterns = len(case_memory_snapshot.get("patterns", []))
    flaky_count = int(flaky_snapshot.get("summary", {}).get("flaky_count", 0) or 0)
    feedback_summary = feedback_snapshot.get("summary", {}) if isinstance(feedback_snapshot, dict) else {}
    helpful = int(feedback_summary.get("selector_helpful", 0) or 0) + int(feedback_summary.get("scope_accurate", 0) or 0)
    misleading = int(feedback_summary.get("selector_misleading", 0) or 0) + int(feedback_summary.get("scope_missed", 0) or 0)
    knowledge_counts = {
        "field_selector_count": int(knowledge_snapshot.get("global", {}).get("field_selector_count", 0) or 0) + int(knowledge_snapshot.get("domain", {}).get("field_selector_count", 0) or 0),
        "action_selector_count": int(knowledge_snapshot.get("global", {}).get("action_selector_count", 0) or 0) + int(knowledge_snapshot.get("domain", {}).get("action_selector_count", 0) or 0),
        "semantic_pattern_count": int(knowledge_snapshot.get("global", {}).get("semantic_pattern_count", 0) or 0) + int(knowledge_snapshot.get("domain", {}).get("semantic_pattern_count", 0) or 0),
    }
    return {
        "matching_pattern_count": matching_patterns,
        "flaky_count": flaky_count,
        "feedback_helpful": helpful,
        "feedback_misleading": misleading,
        "knowledge_counts": knowledge_counts,
        "cluster_keys": cluster_keys,
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
        bool(page_info.get("section_graph", {}).get("nodes")),
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
    grounding_summary = scenario_validation.get("grounding_summary", {}) if isinstance(scenario_validation, dict) else {}
    coverage_score = float(grounding_summary.get("average_fact_coverage_score", 0.0) or 0.0)
    instruction_conflicts = int(grounding_summary.get("instruction_conflict_count", 0) or 0)
    score = (score * 0.7) + (coverage_score * 0.3)
    if instruction_conflicts:
        score -= min(0.25, instruction_conflicts * 0.1)
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


def _anti_hallucination_score(
    scenario_validation: dict,
    execution_plan_validation: dict,
    contradiction_analysis: dict,
) -> float:
    if not scenario_validation and not execution_plan_validation and not contradiction_analysis:
        return 0.72
    valid_cases = len(scenario_validation.get("valid_cases", [])) if isinstance(scenario_validation, dict) else 0
    rejected_cases = len(scenario_validation.get("rejected_cases", [])) if isinstance(scenario_validation, dict) else 0
    total_cases = valid_cases + rejected_cases
    false_positive_rate = (rejected_cases / total_cases) if total_cases else 0.0
    grounding_summary = scenario_validation.get("grounding_summary", {}) if isinstance(scenario_validation, dict) else {}
    fact_coverage = float(grounding_summary.get("average_fact_coverage_score", 0.0) or 0.0)
    instruction_conflicts = int(grounding_summary.get("instruction_conflict_count", 0) or 0)
    contradiction_count = int(contradiction_analysis.get("summary", {}).get("contradiction_count", 0) or 0)
    valid_plans = len(execution_plan_validation.get("valid_plan", {}).get("plans", [])) if isinstance(execution_plan_validation, dict) else 0
    rejected_plans = len(execution_plan_validation.get("rejected_plans", [])) if isinstance(execution_plan_validation, dict) else 0
    plan_total = valid_plans + rejected_plans
    rejected_plan_rate = (rejected_plans / plan_total) if plan_total else 0.0

    score = 0.92
    score -= min(0.35, false_positive_rate * 0.45)
    score -= min(0.22, rejected_plan_rate * 0.28)
    score -= min(0.18, contradiction_count * 0.06)
    score -= min(0.18, instruction_conflicts * 0.08)
    score -= min(0.2, max(0.0, 0.7 - fact_coverage) * 0.4)
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


def _evidence_grounding_score(execution_results: dict, scenario_validation: dict | None = None) -> float:
    scenario_validation = scenario_validation or {}
    results = list(execution_results.get("results", []))
    if not results:
        grounding_summary = scenario_validation.get("grounding_summary", {}) if isinstance(scenario_validation, dict) else {}
        coverage_score = float(grounding_summary.get("average_fact_coverage_score", 0.0) or 0.0)
        base = 0.45 + (coverage_score * 0.35)
        if scenario_validation and not scenario_validation.get("is_valid", False):
            base -= 0.12
        return _clamp(base)

    total = len(results)
    with_facts = 0
    grounding_total = 0.0
    grounded_outcomes = 0
    for item in results:
        fact_ids = item.get("fact_ids", [])
        if isinstance(fact_ids, list) and fact_ids:
            with_facts += 1
        grounding_total += _clamp(item.get("grounding_score", 0.0))
        if item.get("status") in {"passed", "checkpoint_required"}:
            grounded_outcomes += 1
    fact_coverage = with_facts / max(total, 1)
    grounding_strength = grounding_total / max(total, 1)
    outcome_stability = grounded_outcomes / max(total, 1)
    score = (fact_coverage * 0.45) + (grounding_strength * 0.4) + (outcome_stability * 0.15)
    return _clamp(score)


def _negative_evidence_score(page_info: dict, page_model: dict, execution_results: dict | None = None) -> tuple[float, dict]:
    execution_results = execution_results or {}
    fingerprint = page_info.get("page_fingerprint", {})
    tracked = {
        "form": ("has_form", bool(page_model.get("field_catalog"))),
        "search": ("has_search", page_model.get("page_facts", {}).get("search", False)),
        "filter": ("has_filters", page_model.get("page_facts", {}).get("filter", False)),
        "pagination": ("has_pagination", page_model.get("page_facts", {}).get("pagination", False)),
        "table": ("has_table", page_model.get("page_facts", {}).get("table", False)),
        "listing": ("has_listing_pattern", page_model.get("page_facts", {}).get("listing", False)),
        "upload": ("has_upload", page_model.get("page_facts", {}).get("upload", False)),
        "graphql": ("has_graphql", page_model.get("page_facts", {}).get("graphql", False)),
        "live_updates": ("has_live_updates", page_model.get("page_facts", {}).get("live_updates", False)),
    }
    positive = []
    negative = []
    explicit_count = 0
    for label, (fingerprint_key, model_signal) in tracked.items():
        if fingerprint_key in fingerprint:
            explicit_count += 1
            if bool(fingerprint.get(fingerprint_key)):
                positive.append(label)
            else:
                negative.append(label)
        elif model_signal:
            positive.append(label)
    results = list(execution_results.get("results", []))
    network_failures = 0
    graphql_failures = 0
    for item in results:
        summary = item.get("network_summary", {}) if isinstance(item.get("network_summary", {}), dict) else {}
        network_failures += int(summary.get("failing_response_count", 0) or 0)
        graphql_failures += int(summary.get("graphql_error_count", 0) or 0)

    dom_ambiguity = 0
    if len(page_info.get("headings", [])) < 1:
        dom_ambiguity += 1
    if len(page_info.get("texts", [])) < 3:
        dom_ambiguity += 1
    if len(page_model.get("section_graph", {}).get("nodes", [])) < 1:
        dom_ambiguity += 1

    base_score = (min(len(negative), 6) * 0.12) + (min(explicit_count, len(tracked)) / max(len(tracked), 1) * 0.28) + 0.2
    penalty = min(0.35, network_failures * 0.06) + min(0.2, graphql_failures * 0.08) + min(0.18, dom_ambiguity * 0.06)
    score = _clamp(base_score - penalty)
    return score, {
        "explicit_surface_count": explicit_count,
        "positive_surfaces": positive[:8],
        "negative_surfaces": negative[:8],
        "network_failure_count": network_failures,
        "graphql_failure_count": graphql_failures,
        "dom_ambiguity_signal": dom_ambiguity,
        "penalty_applied": round(min(0.65, penalty), 2),
    }


def _source_trust_score(page_info: dict, page_model: dict, historical_signal: dict) -> tuple[float, dict]:
    sources = {
        "dom_content": 1.0 if any(page_info.get(key) for key in ("headings", "texts", "links", "sections")) else 0.0,
        "section_graph": 0.95 if page_model.get("section_graph", {}).get("nodes") else 0.0,
        "field_catalog": 0.95 if page_model.get("field_catalog") else 0.0,
        "state_discovery": 0.85 if page_info.get("discovered_states") or page_info.get("interaction_probes") else 0.0,
        "linked_pages": 0.75 if page_info.get("crawled_pages") else 0.0,
        "knowledge_bank": 0.65 if historical_signal.get("knowledge_counts", {}).get("field_selector_count", 0) or historical_signal.get("knowledge_counts", {}).get("action_selector_count", 0) else 0.0,
        "feedback": 0.6 if historical_signal.get("feedback_helpful", 0) or historical_signal.get("feedback_misleading", 0) else 0.0,
        "ai_inference": 0.55,
    }
    weights = {
        "dom_content": 0.22,
        "section_graph": 0.18,
        "field_catalog": 0.16,
        "state_discovery": 0.12,
        "linked_pages": 0.10,
        "knowledge_bank": 0.10,
        "feedback": 0.06,
        "ai_inference": 0.06,
    }
    score = sum(sources[name] * weights[name] for name in sources)
    return _clamp(score), {
        "sources": {key: round(value, 2) for key, value in sources.items()},
        "weights": weights,
    }


def _real_world_calibration_score(execution_results: dict) -> float:
    results = list(execution_results.get("results", []))
    if not results:
        return 0.72
    total = len(results)
    passed = sum(1 for item in results if item.get("status") == "passed")
    failed = sum(1 for item in results if item.get("status") == "failed")
    checkpoints = sum(1 for item in results if item.get("status") == "checkpoint_required")
    network_failures = 0
    logical_failures = 0
    for item in results:
        summary = item.get("network_summary", {}) if isinstance(item.get("network_summary", {}), dict) else {}
        network_failures += int(summary.get("failing_response_count", 0) or 0)
        logical_failures += int(summary.get("graphql_error_count", 0) or 0)
    score = 0.45
    score += (passed / max(total, 1)) * 0.4
    score -= (failed / max(total, 1)) * 0.18
    score -= min(0.16, (network_failures + logical_failures) * 0.04)
    score -= min(0.08, checkpoints * 0.03)
    return _clamp(score)


def _stability_score(historical_signal: dict) -> float:
    matching_patterns = int(historical_signal.get("matching_pattern_count", 0) or 0)
    flaky_count = int(historical_signal.get("flaky_count", 0) or 0)
    helpful = int(historical_signal.get("feedback_helpful", 0) or 0)
    misleading = int(historical_signal.get("feedback_misleading", 0) or 0)
    score = 0.45
    score += min(0.28, matching_patterns * 0.06)
    score += min(0.12, helpful * 0.02)
    score -= min(0.25, flaky_count * 0.08)
    score -= min(0.15, misleading * 0.03)
    return _clamp(score)


def _build_confidence_explanation(
    weighted_items: list[tuple[str, float, float]],
    negative_evidence: dict,
    source_trust: dict,
    historical_signal: dict,
) -> list[str]:
    labels = {
        "base_ai": "base AI reasoning",
        "coverage": "scan coverage",
        "crawl_depth": "linked-page coverage",
        "state_discovery": "state discovery",
        "scope_alignment": "scope grounding",
        "scenario_alignment": "scenario grounding",
        "plan_alignment": "execution-plan grounding",
        "anti_hallucination": "anti-hallucination safety",
        "evidence_grounding": "execution evidence grounding",
        "execution_signal": "current execution signal",
        "negative_evidence": "negative evidence tracking",
        "source_trust": "source trust",
        "real_world_calibration": "real-world calibration",
        "stability": "multi-run stability",
    }
    ranked = sorted(weighted_items, key=lambda item: item[1] * item[2], reverse=True)
    rows = [f"+ strong {labels.get(name, name)}" for name, value, _ in ranked[:3] if value >= 0.75]
    weak = [f"- weak {labels.get(name, name)}" for name, value, _ in weighted_items if value <= 0.45]
    rows.extend(weak[:2])
    for surface in negative_evidence.get("negative_surfaces", [])[:2]:
        rows.append(f"+ explicit absence confirmed for {surface}")
    if historical_signal.get("matching_pattern_count", 0):
        rows.append(f"+ reusable case memory matched {historical_signal.get('matching_pattern_count', 0)} pattern(s)")
    if historical_signal.get("flaky_count", 0):
        rows.append(f"- flaky history detected in {historical_signal.get('flaky_count', 0)} case pattern(s)")
    if source_trust.get("sources", {}).get("knowledge_bank", 0.0) > 0:
        rows.append("+ knowledge bank contributed trusted selector history")
    deduped = []
    seen = set()
    for row in rows:
        if row not in seen:
            deduped.append(row)
            seen.add(row)
    return deduped[:6]


def _confidence_class(score: float) -> str:
    if score >= 0.85:
        return "high"
    if score >= 0.65:
        return "medium"
    return "low"


def _clamp(value: Any) -> float:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, min(1.0, numeric))
