from __future__ import annotations


def verify_plan_execution_consistency(
    execution_plan: dict | None,
    execution_results: dict | None,
    execution_debug: dict | None = None,
) -> dict:
    execution_plan = execution_plan or {}
    execution_results = execution_results or {}
    execution_debug = execution_debug or {}

    plan_map = {
        str(item.get("id", "")).strip(): item
        for item in execution_plan.get("plans", [])
        if str(item.get("id", "")).strip()
    }
    debug_index = _debug_index_by_case(execution_debug.get("debug_entries", []))
    issues = []
    checked = 0
    severe = 0

    for result in execution_results.get("results", []):
        case_id = str(result.get("id", "")).strip()
        if not case_id:
            continue
        checked += 1
        status = str(result.get("status", "")).strip().lower()
        plan = plan_map.get(case_id, {})
        action_count = len(plan.get("pre_actions", [])) + len(plan.get("actions", []))
        assertion_count = len(plan.get("assertions", []))
        debug_stages = debug_index.get(case_id, set())
        network_summary = result.get("network_summary", {}) if isinstance(result.get("network_summary", {}), dict) else {}
        observed_requests = int(network_summary.get("request_count", 0) or 0)
        expected_map = plan.get("expected_request_map", {}) if isinstance(plan.get("expected_request_map", {}), dict) else {}
        expected_endpoints = list(expected_map.get("expected_endpoints", []) or [])

        if status == "passed" and assertion_count == 0:
            issues.append(_issue(case_id, "medium", "Test passed tanpa assertion, hasil bisa false-positive."))
        if status == "failed" and action_count == 0:
            issues.append(_issue(case_id, "high", "Test failed but has no execution action."))
        if status == "checkpoint_required" and not plan.get("checkpoints"):
            issues.append(_issue(case_id, "medium", "Checkpoint required muncul tanpa checkpoint plan."))
        if status == "passed" and ("timeout" in debug_stages or "resolution" in debug_stages):
            issues.append(_issue(case_id, "medium", "Passed status conflicts with debug timeout/resolution error."))
        if status == "passed" and expected_endpoints and observed_requests == 0:
            issues.append(_issue(case_id, "medium", "Plan ekspektasi network ada, tapi request teramati 0."))

    for item in issues:
        if item["severity"] in {"high", "critical"}:
            severe += 1
    consistency_score = 1.0 - min(1.0, (len(issues) * 0.18) + (severe * 0.12))
    return {
        "summary": {
            "checked_case_count": checked,
            "issue_count": len(issues),
            "severe_issue_count": severe,
            "consistency_score": round(max(0.0, consistency_score), 2),
            "blocking": severe > 0,
        },
        "issues": issues[:30],
    }


def _debug_index_by_case(entries: list[dict]) -> dict[str, set[str]]:
    index: dict[str, set[str]] = {}
    for item in entries:
        case_id = str(item.get("id", "")).strip()
        stage = str(item.get("stage", "")).strip().lower()
        if not case_id or not stage:
            continue
        index.setdefault(case_id, set()).add(stage)
    return index


def _issue(case_id: str, severity: str, message: str) -> dict:
    return {
        "id": case_id,
        "severity": severity,
        "message": message,
    }
