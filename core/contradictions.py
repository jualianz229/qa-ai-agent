from __future__ import annotations

from collections import Counter

from core.guardrails import build_allowed_vocabulary, build_task_contract, detect_out_of_context_case


INTERACTIVE_ACTION_TYPES = {
    "click",
    "fill",
    "select",
    "upload",
    "hover",
    "scroll",
    "dismiss",
    "wait_for_text",
}


def analyze_cross_stage_contradictions(
    page_scope: dict | None,
    test_cases: list[dict] | None,
    execution_plan: dict | None,
    page_model: dict | None,
    page_info: dict | None,
    scenario_validation: dict | None = None,
    execution_plan_validation: dict | None = None,
    execution_results: dict | None = None,
) -> dict:
    page_scope = page_scope or {}
    test_cases = list(test_cases or [])
    execution_plan = execution_plan or {}
    page_model = page_model or {}
    page_info = page_info or {}
    scenario_validation = scenario_validation or {}
    execution_plan_validation = execution_plan_validation or {}
    execution_results = execution_results or {}

    allowed = build_allowed_vocabulary(page_model, page_scope, page_info)
    page_facts = allowed.get("page_facts", {})
    task_contract = (
        scenario_validation.get("task_contract")
        or build_task_contract(page_model, page_scope, page_info)
    )

    issues: list[dict] = []
    issues.extend(_detect_scope_contradictions(page_scope, page_facts, task_contract))

    scenario_map = {
        str(case.get("ID", "")).strip(): case
        for case in test_cases
        if str(case.get("ID", "")).strip()
    }
    issues.extend(_detect_scenario_contradictions(scenario_map, page_facts, task_contract))
    issues.extend(
        _detect_plan_contradictions(
            scenario_map,
            execution_plan,
            execution_plan_validation,
        )
    )
    issues.extend(
        _detect_result_contradictions(
            scenario_map,
            execution_plan,
            execution_results,
        )
    )

    severity_counts = Counter(issue["severity"] for issue in issues)
    stage_counts = Counter(issue["stage"] for issue in issues)
    contradiction_count = len(issues)

    return {
        "summary": {
            "contradiction_count": contradiction_count,
            "blocking": any(issue["severity"] in {"high", "critical"} for issue in issues),
            "severity_counts": dict(severity_counts),
            "stage_counts": dict(stage_counts),
            "scenario_count": len(test_cases),
            "plan_count": len(execution_plan.get("plans", [])),
            "result_count": len(execution_results.get("results", [])),
            "rejected_scenarios": len(scenario_validation.get("rejected_cases", [])),
            "rejected_plans": len(execution_plan_validation.get("rejected_plans", [])),
        },
        "issues": issues,
        "task_contract": task_contract,
        "supported_surfaces": task_contract.get("supported_surfaces", []),
        "unsupported_surfaces": task_contract.get("unsupported_surfaces", []),
        "source_trust_order": task_contract.get("source_trust_order", []),
    }


def _detect_scope_contradictions(page_scope: dict, page_facts: dict, task_contract: dict) -> list[dict]:
    scope_case = {
        "ID": "SCOPE",
        "Module": " / ".join(page_scope.get("key_modules", []) or []),
        "Title": page_scope.get("page_type", ""),
        "Steps to Reproduce": " ".join(page_scope.get("critical_user_flows", []) or []),
        "Expected Result": " ".join(page_scope.get("priority_areas", []) or []),
    }
    issues = []
    for item in detect_out_of_context_case(scope_case, page_facts):
        issues.append(
            _issue(
                code="scope_out_of_context",
                severity="high",
                stage="scope",
                message=item,
            )
        )

    unsupported_requested = task_contract.get("instruction_contract", {}).get("unsupported_requested_surfaces", [])
    for surface in unsupported_requested[:6]:
        issues.append(
            _issue(
                code="instruction_unsupported_surface",
                severity="medium",
                stage="instruction",
                message=f"instruction requested unsupported surface '{surface}'",
            )
        )
    return issues


def _detect_scenario_contradictions(
    scenario_map: dict[str, dict],
    page_facts: dict,
    task_contract: dict,
) -> list[dict]:
    issues = []
    instruction_contract = task_contract.get("instruction_contract", {})
    only_test_types = set(instruction_contract.get("only_test_types", []))
    avoid_surfaces = {str(item).lower() for item in instruction_contract.get("avoid_surfaces", [])}

    for case_id, case in scenario_map.items():
        for item in detect_out_of_context_case(case, page_facts):
            issues.append(
                _issue(
                    code="scenario_out_of_context",
                    severity="high",
                    stage="scenario",
                    case_id=case_id,
                    message=item,
                )
            )

        grounding = case.get("_grounding", {}) or {}
        alignment = case.get("_task_alignment", {}) or {}
        fact_ids = list(grounding.get("fact_ids", []) or [])
        score = float(grounding.get("score", 0.0) or 0.0)
        text = _case_text(case).lower()

        if _has_interaction(text) and not fact_ids:
            issues.append(
                _issue(
                    code="scenario_missing_grounding",
                    severity="high",
                    stage="scenario",
                    case_id=case_id,
                    message="interactive scenario has no fact-id grounding",
                )
            )
        elif _has_interaction(text) and score < 0.18:
            issues.append(
                _issue(
                    code="scenario_weak_grounding",
                    severity="medium",
                    stage="scenario",
                    case_id=case_id,
                    message=f"interactive scenario has weak grounding score {score:.2f}",
                )
            )

        if alignment.get("issues"):
            issues.append(
                _issue(
                    code="scenario_task_misalignment",
                    severity="medium",
                    stage="scenario",
                    case_id=case_id,
                    message="; ".join(str(item) for item in alignment.get("issues", [])[:3]),
                    details={"alignment_score": alignment.get("score", 0.0)},
                )
            )

        if only_test_types:
            case_type = str(case.get("Test Type", "")).strip().lower()
            if case_type and case_type not in only_test_types:
                issues.append(
                    _issue(
                        code="scenario_instruction_type_conflict",
                        severity="medium",
                        stage="scenario",
                        case_id=case_id,
                        message=f"scenario test type '{case_type}' violates instruction-only coverage",
                    )
                )

        if avoid_surfaces:
            avoid_hits = [surface for surface in avoid_surfaces if surface and surface in text]
            if avoid_hits:
                issues.append(
                    _issue(
                        code="scenario_avoided_surface",
                        severity="medium",
                        stage="scenario",
                        case_id=case_id,
                        message=f"scenario touches avoided surfaces: {', '.join(sorted(set(avoid_hits))[:3])}",
                    )
                )
    return issues


def _detect_plan_contradictions(
    scenario_map: dict[str, dict],
    execution_plan: dict,
    execution_plan_validation: dict,
) -> list[dict]:
    issues = []
    plan_map = {
        str(plan.get("id", "")).strip(): plan
        for plan in execution_plan.get("plans", [])
        if str(plan.get("id", "")).strip()
    }
    rejected_map = {
        str(item.get("plan", {}).get("id", "")).strip(): item
        for item in execution_plan_validation.get("rejected_plans", [])
        if str(item.get("plan", {}).get("id", "")).strip()
    }

    for case_id, case in scenario_map.items():
        plan = plan_map.get(case_id)
        if not plan:
            message = "scenario has no surviving execution plan"
            details = {}
            if case_id in rejected_map:
                rejected_issues = rejected_map[case_id].get("issues", [])
                if rejected_issues:
                    message = f"{message}: {'; '.join(str(item) for item in rejected_issues[:3])}"
                    details["rejected_issues"] = rejected_issues
            issues.append(
                _issue(
                    code="plan_missing_for_scenario",
                    severity="high",
                    stage="plan",
                    case_id=case_id,
                    message=message,
                    details=details,
                )
            )
            continue

        scenario_fact_ids = set(case.get("_grounding", {}).get("fact_ids", []) or [])
        plan_fact_ids = set(plan.get("scenario_grounding", {}).get("fact_ids", []) or [])
        if scenario_fact_ids and not plan_fact_ids:
            issues.append(
                _issue(
                    code="plan_missing_scenario_grounding",
                    severity="high",
                    stage="plan",
                    case_id=case_id,
                    message="execution plan dropped scenario fact-id grounding",
                )
            )
        elif scenario_fact_ids and not scenario_fact_ids.issubset(plan_fact_ids):
            issues.append(
                _issue(
                    code="plan_partial_grounding",
                    severity="medium",
                    stage="plan",
                    case_id=case_id,
                    message="execution plan grounding is missing some scenario fact ids",
                    details={
                        "scenario_fact_ids": sorted(scenario_fact_ids),
                        "plan_fact_ids": sorted(plan_fact_ids),
                    },
                )
            )

        interactive_actions = [
            action
            for action in plan.get("pre_actions", []) + plan.get("actions", [])
            if str(action.get("type", "")).strip().lower() in INTERACTIVE_ACTION_TYPES
        ]
        if interactive_actions and all(not item.get("grounding_refs") for item in interactive_actions):
            issues.append(
                _issue(
                    code="plan_actions_without_grounding_refs",
                    severity="medium",
                    stage="plan",
                    case_id=case_id,
                    message="interactive execution actions have no direct grounding refs",
                )
            )

    for plan_id in sorted(set(plan_map) - set(scenario_map)):
        issues.append(
            _issue(
                code="orphan_execution_plan",
                severity="high",
                stage="plan",
                case_id=plan_id,
                message="execution plan exists without a source scenario",
            )
        )
    return issues


def _detect_result_contradictions(
    scenario_map: dict[str, dict],
    execution_plan: dict,
    execution_results: dict,
) -> list[dict]:
    issues = []
    plan_map = {
        str(plan.get("id", "")).strip(): plan
        for plan in execution_plan.get("plans", [])
        if str(plan.get("id", "")).strip()
    }
    for item in execution_results.get("results", []):
        case_id = str(item.get("id", "")).strip()
        if not case_id:
            continue
        plan = plan_map.get(case_id)
        scenario = scenario_map.get(case_id)
        if not plan:
            issues.append(
                _issue(
                    code="orphan_execution_result",
                    severity="high",
                    stage="result",
                    case_id=case_id,
                    message="execution result exists without a matching plan",
                )
            )
            continue

        plan_fact_ids = set(plan.get("scenario_grounding", {}).get("fact_ids", []) or [])
        result_fact_ids = set(item.get("fact_ids", []) or [])
        status = str(item.get("status", "")).strip().lower()
        grounding_score = float(item.get("grounding_score", 0.0) or 0.0)
        if plan_fact_ids and not result_fact_ids:
            issues.append(
                _issue(
                    code="result_missing_fact_ids",
                    severity="medium",
                    stage="result",
                    case_id=case_id,
                    message="execution result lost fact-id grounding from the plan",
                )
            )
        if status == "passed" and not plan_fact_ids and grounding_score < 0.18:
            issues.append(
                _issue(
                    code="result_passed_without_grounding",
                    severity="medium",
                    stage="result",
                    case_id=case_id,
                    message="case passed even though planning had no reliable grounding",
                )
            )
        if status == "failed" and scenario and float(scenario.get("_grounding", {}).get("score", 0.0) or 0.0) >= 0.45:
            issues.append(
                _issue(
                    code="result_failed_despite_strong_grounding",
                    severity="low",
                    stage="result",
                    case_id=case_id,
                    message="strongly grounded case still failed at execution; likely executor/state gap",
                )
            )
    return issues


def _issue(
    code: str,
    severity: str,
    stage: str,
    message: str,
    case_id: str = "",
    details: dict | None = None,
) -> dict:
    return {
        "code": code,
        "severity": severity,
        "stage": stage,
        "case_id": case_id,
        "message": message,
        "details": details or {},
    }


def _case_text(case: dict) -> str:
    return " ".join(
        str(case.get(key, ""))
        for key in ("Module", "Category", "Title", "Precondition", "Steps to Reproduce", "Expected Result")
    )


def _has_interaction(text: str) -> bool:
    lowered = str(text or "").lower()
    return any(
        token in lowered
        for token in ("input ", "click ", "select ", "choose ", "upload ", "hover ", "scroll ", "wait ")
    )
