import csv
import json
import re
from pathlib import Path

from core.artifacts import execution_checkpoint_path, execution_debug_path, execution_learning_path, execution_results_path
from core.confidence import compute_composite_confidence
from core.feedback_bank import load_run_feedback
from core.site_profiles import load_knowledge_bank_snapshot


def list_runs(results_dir: str | Path = "Result") -> list[dict]:
    root = Path(results_dir)
    if not root.exists():
        return []
    runs = []
    for run_dir in root.iterdir():
        if run_dir.is_dir():
            runs.append(build_run_summary(run_dir))
    runs.sort(key=lambda item: item.get("modified_ts", 0), reverse=True)
    return runs


def build_run_summary(run_dir: str | Path) -> dict:
    run_path = Path(run_dir)
    json_dir = run_path / "JSON"
    raw_scan = _load_first_matching_json(json_dir, "raw_scan_*.json")
    page_scope = _load_first_matching_json(json_dir, "Page_Scope_*.json")
    page_scope_validation = _load_first_matching_json(json_dir, "Page_Scope_Validation_*.json")
    scenario_validation = _load_first_matching_json(json_dir, "Scenario_Validation_*.json")
    execution_plan_validation = _load_first_matching_json(json_dir, "Execution_Plan_Validation_*.json")
    page_model = _load_first_matching_json(json_dir, "Normalized_Page_Model_*.json")
    execution_results = _load_json_if_exists(execution_results_path(run_path, create=False))
    learning = _load_json_if_exists(execution_learning_path(run_path, create=False))
    checkpoints = _load_json_if_exists(execution_checkpoint_path(run_path, create=False))
    csv_path = next(run_path.glob("*.csv"), None)
    test_plan_summary = next(run_path.glob("Test_Plan_Summary*.md"), None)
    execution_summary = run_path / "Execution_Summary.md"
    videos = sorted((run_path / "Evidence" / "Video").glob("*.webm")) if (run_path / "Evidence" / "Video").exists() else []
    csv_rows = _read_csv_rows(csv_path) if csv_path else []

    results = execution_results.get("results", [])
    status_counts = {}
    for item in results:
        status = str(item.get("status", "")).strip() or "unknown"
        status_counts[status] = status_counts.get(status, 0) + 1

    composite_confidence = compute_composite_confidence(
        page_scope=page_scope,
        page_info=raw_scan,
        page_model=page_model,
        scope_validation=page_scope_validation,
        scenario_validation=scenario_validation,
        execution_plan_validation=execution_plan_validation,
        execution_results=execution_results,
    )
    confidence = _normalize_confidence(composite_confidence.get("score", page_scope.get("confidence", 0)))

    return {
        "run_name": run_path.name,
        "run_dir": str(run_path),
        "title": raw_scan.get("title", "") or page_scope.get("page_type", "") or run_path.name,
        "url": raw_scan.get("url", ""),
        "page_type": page_scope.get("page_type", ""),
        "confidence": confidence,
        "confidence_score": f"{int(round(confidence * 100))} / 100",
        "confidence_percent": int(round(confidence * 100)),
        "confidence_breakdown": composite_confidence.get("breakdown", {}),
        "scope_summary": page_scope.get("scope_summary", ""),
        "total_cases": len(csv_rows),
        "status_counts": status_counts,
        "video_count": len(videos),
        "csv_path": str(csv_path) if csv_path else "",
        "csv_relative_path": _relative_to_run(run_path, csv_path),
        "test_plan_summary": str(test_plan_summary) if test_plan_summary else "",
        "test_plan_summary_relative_path": _relative_to_run(run_path, test_plan_summary),
        "execution_summary": str(execution_summary) if execution_summary.exists() else "",
        "execution_summary_relative_path": _relative_to_run(run_path, execution_summary if execution_summary.exists() else None),
        "json_files": [item.name for item in sorted(json_dir.glob("*.json"))] if json_dir.exists() else [],
        "videos": [{"name": item.name, "relative_path": _relative_to_run(run_path, item)} for item in videos],
        "learning_count": len(learning.get("learning_entries", [])),
        "checkpoint_count": len(checkpoints.get("checkpoints", [])),
        "modified_ts": run_path.stat().st_mtime,
    }


def build_run_detail(run_dir: str | Path) -> dict:
    run_path = Path(run_dir)
    summary = build_run_summary(run_path)
    json_dir = run_path / "JSON"
    csv_path = next(run_path.glob("*.csv"), None)
    page_model = _load_first_matching_json(json_dir, "Normalized_Page_Model_*.json")
    execution_results = _load_json_if_exists(execution_results_path(run_path, create=False))
    execution_learning = _load_json_if_exists(execution_learning_path(run_path, create=False))
    execution_checkpoints = _load_json_if_exists(execution_checkpoint_path(run_path, create=False))
    run_feedback = load_run_feedback(run_path)
    csv_rows = _read_csv_rows(csv_path) if csv_path else []
    case_rows = _build_case_rows(csv_rows, execution_results)
    return {
        **summary,
        "csv_rows": csv_rows,
        "case_rows": case_rows,
        "filter_options": _build_filter_options(case_rows),
        "raw_scan": _load_first_matching_json(json_dir, "raw_scan_*.json"),
        "page_scope": _load_first_matching_json(json_dir, "Page_Scope_*.json"),
        "page_scope_validation": _load_first_matching_json(json_dir, "Page_Scope_Validation_*.json"),
        "scenario_validation": _load_first_matching_json(json_dir, "Scenario_Validation_*.json"),
        "page_model": page_model,
        "execution_plan": _load_first_matching_json(json_dir, "Execution_Plan_*.json"),
        "execution_plan_validation": _load_first_matching_json(json_dir, "Execution_Plan_Validation_*.json"),
        "execution_results": execution_results,
        "execution_debug": _load_json_if_exists(execution_debug_path(run_path, create=False)),
        "execution_learning": execution_learning,
        "execution_checkpoints": execution_checkpoints,
        "run_feedback": run_feedback,
        "run_feedback_summary": run_feedback.get("summary", {}),
        "execution_ran": bool(execution_results.get("results")),
        "learning_entries": execution_learning.get("learning_entries", []),
        "checkpoint_entries": execution_checkpoints.get("checkpoints", []),
        "knowledge_snapshot": load_knowledge_bank_snapshot(summary.get("url", "")),
        "text_summaries": {
            "test_plan_summary": _read_text_if_exists(summary["test_plan_summary"]),
            "execution_summary": _read_text_if_exists(summary["execution_summary"]),
        },
    }


def build_knowledge_snapshot(url: str = "", profiles_dir: str | Path = "site_profiles") -> dict:
    return load_knowledge_bank_snapshot(url, profiles_dir=profiles_dir)


def safe_run_artifact(run_name: str, relative_path: str, results_dir: str | Path = "Result") -> Path:
    run_dir = Path(results_dir) / run_name
    candidate = (run_dir / relative_path).resolve()
    if run_dir.resolve() not in candidate.parents and candidate != run_dir.resolve():
        raise ValueError("Invalid artifact path.")
    if not candidate.exists():
        raise FileNotFoundError(candidate)
    return candidate


def _load_json_if_exists(path: Path) -> dict:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _load_first_matching_json(directory: Path, pattern: str) -> dict:
    if not directory.exists():
        return {}
    file_path = next(directory.glob(pattern), None)
    if not file_path:
        return {}
    return json.loads(file_path.read_text(encoding="utf-8"))


def _read_csv_rows(csv_path: Path) -> list[dict]:
    with csv_path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def _build_case_rows(csv_rows: list[dict], execution_results: dict) -> list[dict]:
    results_by_id = {}
    for item in execution_results.get("results", []):
        case_id = str(item.get("id", "")).strip()
        if case_id:
            results_by_id[case_id] = str(item.get("status", "")).strip().lower()

    rows = []
    for index, row in enumerate(csv_rows, start=1):
        normalized = {str(key): str(value or "").strip() for key, value in row.items()}
        case_id = normalized.get("ID") or f"ROW-{index:03d}"
        status = (normalized.get("Execution Status") or results_by_id.get(case_id, "")).strip().lower()
        automation = normalized.get("Automation", "").strip().lower()
        priority = normalized.get("Priority", "").strip().upper()
        severity = normalized.get("Severity", "").strip().title()
        steps = _parse_numbered_steps(normalized.get("Steps to Reproduce", ""))
        rows.append(
            {
                "id": case_id,
                "fields": list(normalized.items()),
                "field_map": normalized,
                "status": status,
                "automation": automation,
                "priority": priority,
                "severity": severity,
                "steps": steps,
                "title": normalized.get("Title", ""),
                "module": normalized.get("Module", ""),
                "category": normalized.get("Category", ""),
                "test_type": normalized.get("Test Type", ""),
                "precondition": normalized.get("Precondition", ""),
                "expected_result": normalized.get("Expected Result", ""),
                "actual_result": normalized.get("Actual Result", ""),
                "evidence": normalized.get("Evidence", ""),
                "evidence_relative_path": _evidence_relative_path(normalized.get("Evidence", "")),
                "search_text": " ".join(value for value in normalized.values() if value).lower(),
                "status_rank": _rank_value(status, {"failed": 0, "checkpoint_required": 1, "skipped": 2, "passed": 3}),
                "automation_rank": _rank_value(automation, {"manual": 0, "semi-auto": 1, "auto": 2}),
                "priority_rank": _rank_value(priority, {"P1": 0, "P2": 1, "P3": 2, "P4": 3, "P5": 4}),
                "severity_rank": _rank_value(severity, {"Critical": 0, "High": 1, "Medium": 2, "Low": 3, "Informational": 4, "Info": 5}),
                "is_failed": status == "failed",
                "is_manual": automation == "manual",
                "is_p1p2": priority in {"P1", "P2"},
            }
        )
    return rows


def _parse_numbered_steps(value: str) -> list[str]:
    text = str(value or "").replace("\\r\\n", "\n").replace("\\n", "\n").replace("\r", "\n").strip()
    if not text:
        return []

    matches = list(re.finditer(r"(?<!\S)(\d+)\.\s+", text))
    if matches:
        parsed_steps = []
        for index, match in enumerate(matches):
            start = match.end()
            end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
            step_text = text[start:end].strip()
            step_text = re.sub(r"\s*\n\s*", " ", step_text).strip()
            if step_text:
                parsed_steps.append(step_text)
        if parsed_steps:
            return parsed_steps

    return [line.strip() for line in text.splitlines() if line.strip()]


def _build_filter_options(case_rows: list[dict]) -> dict:
    return {
        "statuses": _format_filter_options(
            {row.get("status", "") for row in case_rows},
            order_map={"failed": 0, "passed": 1, "skipped": 2, "blocked": 3, "pending": 4, "unknown": 5},
            label_map={"failed": "Failed", "passed": "Passed", "skipped": "Skipped", "blocked": "Blocked", "pending": "Pending", "unknown": "Unknown"},
        ),
        "automations": _format_filter_options(
            {row.get("automation", "") for row in case_rows},
            order_map={"auto": 0, "semi-auto": 1, "manual": 2},
            label_map={"auto": "Auto", "semi-auto": "Semi-auto", "manual": "Manual"},
        ),
        "priorities": _format_filter_options(
            {row.get("priority", "") for row in case_rows},
            order_map={"P1": 0, "P2": 1, "P3": 2, "P4": 3, "P5": 4},
        ),
        "severities": _format_filter_options(
            {row.get("severity", "") for row in case_rows},
            order_map={"Critical": 0, "High": 1, "Medium": 2, "Low": 3, "Informational": 4, "Info": 5},
        ),
    }


def _format_filter_options(values: set[str], order_map: dict[str, int] | None = None, label_map: dict[str, str] | None = None) -> list[dict]:
    cleaned = [str(value).strip() for value in values if str(value).strip()]
    order_map = order_map or {}
    label_map = label_map or {}

    def sort_key(raw_value: str) -> tuple[int, str]:
        return (order_map.get(raw_value, order_map.get(raw_value.lower(), 999)), raw_value.lower())

    options = []
    for value in sorted(cleaned, key=sort_key):
        label = label_map.get(value, label_map.get(value.lower(), value))
        options.append({"value": value.lower(), "label": label})
    return options


def _rank_value(value: str, order_map: dict[str, int]) -> int:
    return order_map.get(value, order_map.get(value.lower(), 999))


def _evidence_relative_path(value: str) -> str:
    text = str(value or "").strip()
    if not text or text.startswith("["):
        return ""
    if text.startswith("Video/"):
        return f"Evidence/{text}"
    return text


def _read_text_if_exists(path: str) -> str:
    if not path:
        return ""
    file_path = Path(path)
    if not file_path.exists():
        return ""
    return file_path.read_text(encoding="utf-8")


def _relative_to_run(run_path: Path, file_path: Path | None) -> str:
    if not file_path:
        return ""
    return str(file_path.relative_to(run_path)).replace("\\", "/")


def _normalize_confidence(value: object) -> float:
    try:
        score = float(value or 0)
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, min(1.0, score))
