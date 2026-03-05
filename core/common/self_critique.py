from __future__ import annotations

import copy


GENERIC_ASSERT_TOKENS = {
    "success",
    "saved",
    "submitted",
    "error",
    "invalid",
    "required",
    "results",
    "result",
}


def refine_execution_plan_with_self_critique(execution_plan: dict, page_model: dict) -> tuple[dict, dict]:
    refined = copy.deepcopy(execution_plan or {})
    total_assertions = 0
    removed_assertions = 0
    plan_reports = []
    page_title = str(page_model.get("page_identity", {}).get("title", "")).strip()

    for plan in refined.get("plans", []):
        assertions = list(plan.get("assertions", []))
        kept = []
        removed = []
        for item in assertions:
            total_assertions += 1
            if _is_ambiguous_assertion(item):
                removed.append(
                    {
                        "type": item.get("type", ""),
                        "target": item.get("value", "") or item.get("target", ""),
                        "reason": "assertion ambigu atau grounding lemah",
                    }
                )
                removed_assertions += 1
                continue
            kept.append(item)
        if not kept and page_title:
            kept.append(
                {
                    "type": "assert_title_contains",
                    "value": page_title,
                    "source_text": "self-critique fallback",
                    "grounded": True,
                    "grounding_confidence": 0.65,
                }
            )
        if removed:
            checkpoints = list(plan.get("checkpoints", []))
            checkpoints.append(
                {
                    "type": "manual_review",
                    "mode": "manual",
                    "reason": "Self-critique mendeteksi assertion ambigu, butuh verifikasi manual.",
                }
            )
            plan["checkpoints"] = checkpoints[:3]
        plan["assertions"] = kept
        if removed:
            plan_reports.append(
                {
                    "id": plan.get("id", ""),
                    "removed_assertions": removed[:8],
                    "remaining_assertion_count": len(kept),
                }
            )

    report = {
        "enabled": True,
        "plans_reviewed": len(refined.get("plans", [])),
        "assertions_total": total_assertions,
        "assertions_removed": removed_assertions,
        "assertions_removed_rate": round(removed_assertions / max(total_assertions, 1), 2) if total_assertions else 0.0,
        "issues": plan_reports,
    }
    return refined, report


def _is_ambiguous_assertion(assertion: dict) -> bool:
    assertion_type = str(assertion.get("type", "")).strip().lower()
    confidence = float(assertion.get("grounding_confidence", 0.0) or 0.0)
    grounded = bool(assertion.get("grounded", False))
    value_blob = " ".join(
        [
            str(assertion.get("value", "")),
            " ".join(assertion.get("values", []) if isinstance(assertion.get("values", []), list) else []),
            str(assertion.get("source_text", "")),
        ]
    ).lower()
    generic_hits = sum(1 for token in GENERIC_ASSERT_TOKENS if token in value_blob)

    if assertion_type in {"assert_any_text_visible", "assert_text_visible"} and generic_hits >= 1 and confidence < 0.6:
        return True
    if not grounded and confidence < 0.35 and assertion_type in {
        "assert_text_visible",
        "assert_any_text_visible",
        "assert_url_contains",
        "assert_control_text",
    }:
        return True
    return False
