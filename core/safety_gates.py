from __future__ import annotations

import os


def build_execution_gate_decision(
    composite_confidence: dict | None = None,
    scenario_validation: dict | None = None,
    execution_plan_validation: dict | None = None,
    contradiction_report: dict | None = None,
    execution_results: dict | None = None,
) -> dict:
    composite_confidence = composite_confidence or {}
    scenario_validation = scenario_validation or {}
    execution_plan_validation = execution_plan_validation or {}
    contradiction_report = contradiction_report or {}
    execution_results = execution_results or {}

    breakdown = composite_confidence.get("breakdown", {}) if isinstance(composite_confidence, dict) else {}
    anti_hallu = float(breakdown.get("anti_hallucination", 0.0) or 0.0)
    source_trust = float(breakdown.get("source_trust", 0.0) or 0.0)
    negative_evidence = float(breakdown.get("negative_evidence", 0.0) or 0.0)
    evidence_grounding = _resolve_evidence_grounding_signal(breakdown, execution_results)
    contradictions = int(contradiction_report.get("summary", {}).get("contradiction_count", 0) or 0)
    rejected_cases = len(scenario_validation.get("rejected_cases", []))
    rejected_plans = len(execution_plan_validation.get("rejected_plans", []))

    reasons: list[str] = []
    if anti_hallu < 0.58:
        reasons.append(f"anti_hallucination score rendah ({anti_hallu:.2f} < 0.58)")
    if source_trust < 0.5 and anti_hallu < 0.66:
        reasons.append(f"source_trust rendah ({source_trust:.2f})")
    if contradictions > 0 and anti_hallu < 0.7:
        reasons.append(f"terdapat {contradictions} contradiction signal")
    if rejected_cases >= 2 and anti_hallu < 0.72:
        reasons.append(f"scenario rejection tinggi ({rejected_cases})")
    if rejected_plans >= 2 and anti_hallu < 0.72:
        reasons.append(f"plan rejection tinggi ({rejected_plans})")
    if negative_evidence < 0.45 and anti_hallu < 0.68:
        reasons.append("negative evidence signal lemah")
    if evidence_grounding < 0.52 and anti_hallu < 0.72:
        reasons.append(f"execution evidence grounding lemah ({evidence_grounding:.2f} < 0.52)")

    env_key = "QA_AI_ALLOW_LOW_ANTI_HALLU"
    override = str(os.getenv(env_key, "")).strip().lower() in {"1", "true", "yes", "on"}
    blocked = bool(reasons) and not override
    return {
        "blocked": blocked,
        "override_applied": bool(reasons) and override,
        "override_env": env_key,
        "reasons": reasons,
        "signals": {
            "anti_hallucination": round(anti_hallu, 2),
            "source_trust": round(source_trust, 2),
            "negative_evidence": round(negative_evidence, 2),
            "evidence_grounding": round(evidence_grounding, 2),
            "contradiction_count": contradictions,
            "scenario_rejection_count": rejected_cases,
            "plan_rejection_count": rejected_plans,
        },
    }


def _resolve_evidence_grounding_signal(breakdown: dict, execution_results: dict) -> float:
    if "evidence_grounding" in breakdown:
        return float(breakdown.get("evidence_grounding", 0.0) or 0.0)
    results = list(execution_results.get("results", []))
    if not results:
        return 0.65
    total = len(results)
    with_fact_ids = 0
    grounding_total = 0.0
    for item in results:
        fact_ids = item.get("fact_ids", [])
        if isinstance(fact_ids, list) and fact_ids:
            with_fact_ids += 1
        try:
            grounding_total += float(item.get("grounding_score", 0.0) or 0.0)
        except (TypeError, ValueError):
            continue
    return max(0.0, min(1.0, (with_fact_ids / max(total, 1)) * 0.5 + (grounding_total / max(total, 1)) * 0.5))
