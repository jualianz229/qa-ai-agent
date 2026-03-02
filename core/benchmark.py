from dataclasses import dataclass
from pathlib import Path

from core.confidence import compute_composite_confidence
from core.guardrails import validate_execution_plan, validate_page_scope
from core.planner import build_execution_plan, build_normalized_page_model, save_json_artifact
from core.site_profiles import derive_cluster_keys


@dataclass
class BenchmarkCase:
    name: str
    page_info: dict
    page_scope: dict
    test_cases: list[dict]
    base_url: str


def run_benchmark_suite(cases: list[BenchmarkCase], output_dir: str | Path | None = None) -> dict:
    results = []
    for case in cases:
        page_model = build_normalized_page_model(case.page_info)
        scope_validation = validate_page_scope(case.page_scope, page_model, case.page_info)
        execution_plan = build_execution_plan(case.test_cases, page_model, case.base_url, site_profile=case.page_info.get("site_profile"))
        plan_validation = validate_execution_plan(execution_plan, page_model, case.page_info)
        grounding = _grounding_metrics(plan_validation.get("valid_plan", {}))
        heuristic_alignment = _heuristic_alignment(page_model.get("heuristic_scope", {}), case.page_scope)
        automation_counts = _automation_counts(case.test_cases)
        composite_confidence = compute_composite_confidence(
            page_scope=scope_validation.get("page_scope", case.page_scope),
            page_info=case.page_info,
            page_model=page_model,
            scope_validation=scope_validation,
            execution_plan_validation=plan_validation,
        )
        cluster_keys = derive_cluster_keys(page_model, case.page_scope)
        results.append(
            {
                "name": case.name,
                "scope_valid": scope_validation.get("is_valid", False),
                "scope_issue_count": len(scope_validation.get("issues", [])),
                "plan_valid": plan_validation.get("is_valid", False),
                "valid_plan_count": len(plan_validation.get("valid_plan", {}).get("plans", [])),
                "rejected_plan_count": len(plan_validation.get("rejected_plans", [])),
                "page_type": scope_validation.get("page_scope", {}).get("page_type", ""),
                "heuristic_page_type": page_model.get("heuristic_scope", {}).get("likely_page_type", ""),
                "heuristic_alignment": heuristic_alignment,
                "grounding_coverage": grounding["coverage"],
                "weak_grounding_items": grounding["weak_items"],
                "cluster_keys": cluster_keys,
                "auto_case_count": automation_counts["auto"],
                "semi_auto_case_count": automation_counts["semi-auto"],
                "manual_case_count": automation_counts["manual"],
                "confidence_score": composite_confidence.get("score", 0),
                "component_types": [component.get("type", "") for component in page_model.get("components", [])],
            }
        )

    summary = {
        "total_cases": len(results),
        "passed_scope": sum(1 for item in results if item["scope_valid"]),
        "passed_plan": sum(1 for item in results if item["plan_valid"]),
        "average_grounding_coverage": _average([item["grounding_coverage"] for item in results]),
        "average_heuristic_alignment": _average([item["heuristic_alignment"] for item in results]),
        "average_confidence": _average([item["confidence_score"] for item in results]),
        "cluster_keys": sorted({key for item in results for key in item.get("cluster_keys", [])}),
        "total_auto_cases": sum(item["auto_case_count"] for item in results),
        "total_semi_auto_cases": sum(item["semi_auto_case_count"] for item in results),
        "total_manual_cases": sum(item["manual_case_count"] for item in results),
        "results": results,
    }
    if output_dir:
        save_json_artifact(summary, Path(output_dir) / "Benchmark_Report.json")
    return summary


def _grounding_metrics(valid_plan: dict) -> dict:
    plans = list(valid_plan.get("plans", []))
    total_items = 0
    grounded_items = 0
    weak_items = []
    for plan in plans:
        plan_id = str(plan.get("id", "")).strip()
        for bucket in ("pre_actions", "actions", "assertions"):
            for item in plan.get(bucket, []):
                total_items += 1
                refs = list(item.get("grounding_refs", []) or [])
                if refs:
                    grounded_items += 1
                    continue
                weak_items.append(
                    {
                        "id": plan_id,
                        "bucket": bucket,
                        "type": item.get("type", ""),
                        "target": item.get("target", "") or item.get("value", ""),
                    }
                )
    coverage = grounded_items / total_items if total_items else 0.0
    return {"coverage": round(coverage, 2), "weak_items": weak_items[:20]}


def _heuristic_alignment(heuristic_scope: dict, page_scope: dict) -> float:
    heuristic_scope = heuristic_scope or {}
    page_scope = page_scope or {}
    scores = []
    heuristic_page_type = _normalize(str(heuristic_scope.get("likely_page_type", "")))
    scope_page_type = _normalize(str(page_scope.get("page_type", "")))
    if heuristic_page_type and scope_page_type:
        scores.append(1.0 if heuristic_page_type == scope_page_type else 0.35)

    scores.append(
        _overlap_score(
            heuristic_scope.get("priority_modules", []),
            page_scope.get("key_modules", []),
        )
    )
    scores.append(
        _overlap_score(
            heuristic_scope.get("recommended_flows", []),
            page_scope.get("critical_user_flows", []),
        )
    )
    return round(_average(scores or [0.0]), 2)


def _automation_counts(test_cases: list[dict]) -> dict:
    counts = {"auto": 0, "semi-auto": 0, "manual": 0}
    for item in test_cases:
        key = str(item.get("Automation", "auto")).strip().lower() or "auto"
        if key not in counts:
            key = "auto"
        counts[key] += 1
    return counts


def _overlap_score(left: list[object], right: list[object]) -> float:
    left_set = {_normalize(item) for item in left if _normalize(item)}
    right_set = {_normalize(item) for item in right if _normalize(item)}
    if not left_set and not right_set:
        return 0.7
    if not left_set or not right_set:
        return 0.25
    overlap = len(left_set & right_set)
    return overlap / max(len(left_set | right_set), 1)


def _normalize(value: object) -> str:
    return " ".join(str(value or "").strip().lower().split())


def _average(values: list[float]) -> float:
    cleaned = [float(value) for value in values if value is not None]
    if not cleaned:
        return 0.0
    return round(sum(cleaned) / len(cleaned), 2)
