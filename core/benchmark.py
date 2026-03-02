from dataclasses import dataclass
from pathlib import Path

from core.guardrails import validate_execution_plan, validate_page_scope
from core.planner import build_execution_plan, build_normalized_page_model, save_json_artifact


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
        results.append(
            {
                "name": case.name,
                "scope_valid": scope_validation.get("is_valid", False),
                "scope_issue_count": len(scope_validation.get("issues", [])),
                "plan_valid": plan_validation.get("is_valid", False),
                "valid_plan_count": len(plan_validation.get("valid_plan", {}).get("plans", [])),
                "rejected_plan_count": len(plan_validation.get("rejected_plans", [])),
                "page_type": scope_validation.get("page_scope", {}).get("page_type", ""),
                "component_types": [component.get("type", "") for component in page_model.get("components", [])],
            }
        )

    summary = {
        "total_cases": len(results),
        "passed_scope": sum(1 for item in results if item["scope_valid"]),
        "passed_plan": sum(1 for item in results if item["plan_valid"]),
        "results": results,
    }
    if output_dir:
        save_json_artifact(summary, Path(output_dir) / "Benchmark_Report.json")
    return summary
