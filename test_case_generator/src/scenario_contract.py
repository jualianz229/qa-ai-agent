from __future__ import annotations

from core.contradictions import analyze_cross_stage_contradictions


REQUIRED_KEYS = [
    "ID",
    "Module",
    "Category",
    "Test Type",
    "Risk Rating",
    "Anchored Selector",
    "Title",
    "Precondition",
    "Steps to Reproduce",
    "Expected Result",
    "Actual Result",
    "Severity",
    "Priority",
    "Evidence",
    "Automation",
]


def validate_scenario_contract(
    cases: list[dict] | None,
    page_scope: dict | None,
    page_model: dict | None,
    page_info: dict | None,
) -> dict:
    rows = list(cases or [])
    issues: list[dict] = []
    valid_cases: list[dict] = []
    seen_ids: set[str] = set()

    for index, case in enumerate(rows, start=1):
        if not isinstance(case, dict):
            issues.append(_issue("invalid_type", "critical", f"Case #{index} is not an object.", case_id=f"ROW-{index}"))
            continue
        case_id = str(case.get("ID", "")).strip() or f"ROW-{index}"
        schema_issues: list[dict] = []

        missing = [key for key in REQUIRED_KEYS if key not in case]
        extra = [key for key in case.keys() if not str(key).startswith("_") and key not in REQUIRED_KEYS]
        if missing:
            schema_issues.append(_issue("missing_keys", "critical", f"Missing required keys: {', '.join(missing)}", case_id=case_id))
        if extra:
            schema_issues.append(_issue("unknown_keys", "high", f"Unknown keys present: {', '.join(extra)}", case_id=case_id))

        if case_id in seen_ids:
            schema_issues.append(_issue("duplicate_id", "critical", f"Duplicate test ID '{case_id}'.", case_id=case_id))
        seen_ids.add(case_id)

        test_type = str(case.get("Test Type", "")).strip().lower()
        if test_type not in {"positive", "negative"}:
            schema_issues.append(
                _issue("invalid_test_type", "high", "Test Type must be Positive or Negative.", case_id=case_id)
            )

        steps = str(case.get("Steps to Reproduce", "")).strip()
        if not steps:
            schema_issues.append(_issue("empty_steps", "critical", "Steps to Reproduce is empty.", case_id=case_id))
        elif "open the site" not in steps.lower():
            schema_issues.append(
                _issue("missing_open_site", "medium", "Step must include 'Open the site <url>' as first action.", case_id=case_id)
            )

        title = str(case.get("Title", "")).strip()
        if not title:
            schema_issues.append(_issue("empty_title", "critical", "Title is empty.", case_id=case_id))

        expected = str(case.get("Expected Result", "")).strip()
        if not expected:
            schema_issues.append(_issue("empty_expected_result", "critical", "Expected Result is empty.", case_id=case_id))

        if schema_issues:
            issues.extend(schema_issues)
            continue
        valid_cases.append(case)

    contradiction_report = analyze_cross_stage_contradictions(
        page_scope=page_scope or {},
        test_cases=valid_cases,
        execution_plan={},
        page_model=page_model or {},
        page_info=page_info or {},
        scenario_validation={},
        execution_plan_validation={},
    )
    contradiction_issues = list(contradiction_report.get("issues", []))
    issues.extend(contradiction_issues)
    blocking_issues = [
        item
        for item in issues
        if str(item.get("severity", "")).strip().lower() in {"critical", "high"}
    ]

    return {
        "is_valid": not blocking_issues,
        "input_count": len(rows),
        "valid_count": len(valid_cases),
        "issue_count": len(issues),
        "blocking_count": len(blocking_issues),
        "issues": issues[:200],
        "blocking_issues": blocking_issues[:80],
        "contradiction_summary": contradiction_report.get("summary", {}),
        "valid_cases": valid_cases,
    }


def _issue(code: str, severity: str, message: str, case_id: str = "") -> dict:
    return {
        "code": str(code or "").strip(),
        "severity": str(severity or "").strip().lower(),
        "stage": "scenario_contract",
        "case_id": str(case_id or "").strip(),
        "message": str(message or "").strip(),
    }

