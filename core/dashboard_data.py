import csv
import json
import re
from pathlib import Path
from urllib.parse import urlparse

from core.artifacts import (
    confidence_analysis_path,
    contradiction_analysis_path,
    execution_checkpoint_path,
    execution_debug_path,
    execution_learning_path,
    execution_network_path,
    execution_results_path,
    token_usage_path,
    visual_signature_path,
)
from core.confidence import build_historical_confidence_signal, compute_composite_confidence
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
    confidence_analysis = _load_json_if_exists(confidence_analysis_path(run_path, create=False))
    execution_results = _load_json_if_exists(execution_results_path(run_path, create=False))
    contradiction_analysis = _load_json_if_exists(contradiction_analysis_path(run_path, create=False))
    execution_network = _load_json_if_exists(execution_network_path(run_path, create=False))
    token_usage = _load_json_if_exists(token_usage_path(run_path, create=False))
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

    if confidence_analysis:
        composite_confidence = {
            "score": float(confidence_analysis.get("confidence", 0) or 0),
            "confidence_class": confidence_analysis.get("confidence_class", "medium"),
            "explanation": confidence_analysis.get("explanation", []),
            "breakdown": confidence_analysis.get("breakdown", {}),
        }
        historical_signal = confidence_analysis.get("historical_signal", {}) or build_historical_confidence_signal(
            url=raw_scan.get("url", ""),
            page_model=page_model,
            page_scope=page_scope,
        )
    else:
        confidence_scope = dict(page_scope or {})
        if "ai_confidence" in confidence_scope:
            confidence_scope["confidence"] = confidence_scope.get("ai_confidence", confidence_scope.get("confidence", 0))
        historical_signal = build_historical_confidence_signal(
            url=raw_scan.get("url", ""),
            page_model=page_model,
            page_scope=page_scope,
        )
        composite_confidence = compute_composite_confidence(
            page_scope=confidence_scope,
            page_info=raw_scan,
            page_model=page_model,
            scope_validation=page_scope_validation,
            scenario_validation=scenario_validation,
            execution_plan_validation=execution_plan_validation,
            execution_results=execution_results,
            historical_signal=historical_signal,
        )
    confidence = _normalize_confidence(composite_confidence.get("score", page_scope.get("confidence", 0)))
    confidence_breakdown = composite_confidence.get("breakdown", {})
    visual_signature = _load_or_build_visual_signature(run_path, raw_scan, page_scope, page_model)
    alerts = _build_run_alerts(
        confidence_percent=int(round(confidence * 100)),
        status_counts=status_counts,
        scenario_validation=scenario_validation,
        execution_plan_validation=execution_plan_validation,
        page_scope_validation=page_scope_validation,
        historical_signal=historical_signal,
        confidence_breakdown=confidence_breakdown,
        contradiction_analysis=contradiction_analysis,
    )

    return {
        "run_name": run_path.name,
        "run_dir": str(run_path),
        "title": raw_scan.get("title", "") or page_scope.get("page_type", "") or run_path.name,
        "url": raw_scan.get("url", ""),
        "page_type": page_scope.get("page_type", ""),
        "confidence": confidence,
        "confidence_score": f"{int(round(confidence * 100))} / 100",
        "confidence_percent": int(round(confidence * 100)),
        "confidence_breakdown": confidence_breakdown,
        "confidence_explanation": composite_confidence.get("explanation", []),
        "confidence_class": composite_confidence.get("confidence_class", "medium"),
        "historical_signal": historical_signal,
        "confidence_analysis": confidence_analysis or {},
        "contradiction_analysis": contradiction_analysis or {},
        "anti_hallucination_score": int(round(float(confidence_breakdown.get("anti_hallucination", 0.0) or 0.0) * 100)),
        "negative_evidence_detail": confidence_breakdown.get("negative_evidence_detail", {}),
        "source_trust_detail": confidence_breakdown.get("source_trust_detail", {}),
        "token_usage": token_usage,
        "token_usage_summary": token_usage.get("summary", {}),
        "alerts": alerts,
        "alert_count": len(alerts),
        "scope_summary": page_scope.get("scope_summary", ""),
        "total_cases": len(csv_rows),
        "status_counts": status_counts,
        "video_count": len(videos),
        "network_summary": _summarize_network_entries(execution_network.get("network_entries", [])),
        "visual_signature": visual_signature,
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
    contradiction_analysis = _load_json_if_exists(contradiction_analysis_path(run_path, create=False))
    execution_network = _load_json_if_exists(execution_network_path(run_path, create=False))
    execution_learning = _load_json_if_exists(execution_learning_path(run_path, create=False))
    execution_checkpoints = _load_json_if_exists(execution_checkpoint_path(run_path, create=False))
    run_feedback = load_run_feedback(run_path)
    csv_rows = _read_csv_rows(csv_path) if csv_path else []
    page_scope_validation = _load_first_matching_json(json_dir, "Page_Scope_Validation_*.json")
    scenario_validation = _load_first_matching_json(json_dir, "Scenario_Validation_*.json")
    execution_plan = _load_first_matching_json(json_dir, "Execution_Plan_*.json")
    execution_plan_validation = _load_first_matching_json(json_dir, "Execution_Plan_Validation_*.json")
    case_rows = _build_case_rows(
        csv_rows,
        execution_results,
        scenario_validation=scenario_validation,
        execution_plan=execution_plan,
        contradiction_analysis=contradiction_analysis,
    )
    return {
        **summary,
        "csv_rows": csv_rows,
        "case_rows": case_rows,
        "filter_options": _build_filter_options(case_rows),
        "raw_scan": _load_first_matching_json(json_dir, "raw_scan_*.json"),
        "page_scope": _load_first_matching_json(json_dir, "Page_Scope_*.json"),
        "page_scope_validation": page_scope_validation,
        "scenario_validation": scenario_validation,
        "page_model": page_model,
        "execution_plan": execution_plan,
        "execution_plan_validation": execution_plan_validation,
        "execution_results": execution_results,
        "contradiction_analysis": contradiction_analysis,
        "execution_debug": _load_json_if_exists(execution_debug_path(run_path, create=False)),
        "execution_network": execution_network,
        "execution_learning": execution_learning,
        "execution_checkpoints": execution_checkpoints,
        "run_feedback": run_feedback,
        "run_feedback_summary": run_feedback.get("summary", {}),
        "execution_ran": bool(execution_results.get("results")),
        "learning_entries": execution_learning.get("learning_entries", []),
        "checkpoint_entries": execution_checkpoints.get("checkpoints", []),
        "knowledge_snapshot": load_knowledge_bank_snapshot(summary.get("url", "")),
        "confidence_trend": _build_confidence_trend(run_path.parent, summary.get("url", ""), current_run=run_path.name),
        "guardrail_summary": _build_guardrail_summary(
            page_scope_validation,
            scenario_validation,
            execution_plan_validation,
            contradiction_analysis,
        ),
        "knowledge_heatmap": _build_knowledge_heatmap(load_knowledge_bank_snapshot(summary.get("url", ""))),
        "visual_signature": summary.get("visual_signature", {}),
        "text_summaries": {
            "test_plan_summary": _read_text_if_exists(summary["test_plan_summary"]),
            "execution_summary": _read_text_if_exists(summary["execution_summary"]),
        },
    }


def build_knowledge_snapshot(url: str = "", profiles_dir: str | Path = "site_profiles") -> dict:
    return load_knowledge_bank_snapshot(url, profiles_dir=profiles_dir)


def build_run_comparison(left_run: str | Path, right_run: str | Path, results_dir: str | Path = "Result") -> dict:
    left_path = Path(left_run)
    right_path = Path(right_run)
    if not left_path.exists():
        left_path = Path(results_dir) / str(left_run)
    if not right_path.exists():
        right_path = Path(results_dir) / str(right_run)
    left = build_run_detail(left_path)
    right = build_run_detail(right_path)
    left_cases = {row["id"]: row for row in left.get("case_rows", [])}
    right_cases = {row["id"]: row for row in right.get("case_rows", [])}
    shared_ids = sorted(set(left_cases) & set(right_cases))

    changed_cases = []
    for case_id in shared_ids:
        before = left_cases[case_id]
        after = right_cases[case_id]
        if before.get("status") != after.get("status") or before.get("automation") != after.get("automation"):
            changed_cases.append(
                {
                    "id": case_id,
                    "title": after.get("title") or before.get("title"),
                    "before_status": before.get("status", ""),
                    "after_status": after.get("status", ""),
                    "before_automation": before.get("automation", ""),
                    "after_automation": after.get("automation", ""),
                }
            )

    left_visual = left.get("visual_signature", {})
    right_visual = right.get("visual_signature", {})
    return {
        "left": left,
        "right": right,
        "delta": {
            "confidence": right.get("confidence_percent", 0) - left.get("confidence_percent", 0),
            "passed": (right.get("status_counts", {}).get("passed", 0) - left.get("status_counts", {}).get("passed", 0)),
            "failed": (right.get("status_counts", {}).get("failed", 0) - left.get("status_counts", {}).get("failed", 0)),
            "cases": right.get("total_cases", 0) - left.get("total_cases", 0),
        },
        "confidence_diff": {
            "source_trust": round(
                float(right.get("confidence_breakdown", {}).get("source_trust", 0.0) or 0.0)
                - float(left.get("confidence_breakdown", {}).get("source_trust", 0.0) or 0.0),
                2,
            ),
            "stability": round(
                float(right.get("confidence_breakdown", {}).get("stability", 0.0) or 0.0)
                - float(left.get("confidence_breakdown", {}).get("stability", 0.0) or 0.0),
                2,
            ),
            "real_world": round(
                float(right.get("confidence_breakdown", {}).get("real_world_calibration", 0.0) or 0.0)
                - float(left.get("confidence_breakdown", {}).get("real_world_calibration", 0.0) or 0.0),
                2,
            ),
            "negative_evidence": round(
                float(right.get("confidence_breakdown", {}).get("negative_evidence", 0.0) or 0.0)
                - float(left.get("confidence_breakdown", {}).get("negative_evidence", 0.0) or 0.0),
                2,
            ),
            "anti_hallucination": round(
                float(right.get("confidence_breakdown", {}).get("anti_hallucination", 0.0) or 0.0)
                - float(left.get("confidence_breakdown", {}).get("anti_hallucination", 0.0) or 0.0),
                2,
            ),
        },
        "anti_hallu_delta": {
            "score": right.get("anti_hallucination_score", 0) - left.get("anti_hallucination_score", 0),
            "contradictions": int(right.get("contradiction_analysis", {}).get("summary", {}).get("contradiction_count", 0) or 0)
            - int(left.get("contradiction_analysis", {}).get("summary", {}).get("contradiction_count", 0) or 0),
        },
        "new_case_ids": sorted(set(right_cases) - set(left_cases)),
        "missing_case_ids": sorted(set(left_cases) - set(right_cases)),
        "changed_cases": changed_cases[:30],
        "visual_diff": {
            "heading_delta": right_visual.get("heading_count", 0) - left_visual.get("heading_count", 0),
            "button_delta": right_visual.get("button_count", 0) - left_visual.get("button_count", 0),
            "link_delta": right_visual.get("link_count", 0) - left_visual.get("link_count", 0),
            "component_delta": right_visual.get("component_count", 0) - left_visual.get("component_count", 0),
            "state_delta": right_visual.get("discovered_state_count", 0) - left_visual.get("discovered_state_count", 0),
            "changed_component_types": sorted(
                set(right_visual.get("component_types", [])) ^ set(left_visual.get("component_types", []))
            )[:12],
        },
    }


def build_benchmark_snapshot(results_dir: str | Path = "Result", limit: int = 8) -> dict:
    from core.benchmark import BenchmarkCase, run_benchmark_suite

    empty_snapshot = {
        "total_cases": 0,
        "results": [],
        "average_grounding_coverage": 0,
        "average_heuristic_alignment": 0,
        "average_confidence": 0,
        "average_source_trust": 0,
        "average_stability": 0,
        "cluster_keys": [],
    }
    runs = list_runs(results_dir)[:limit]
    cases = []
    for run in runs:
        run_dir = Path(run["run_dir"])
        detail = build_run_detail(run_dir)
        if not detail.get("raw_scan") or not detail.get("page_scope") or not detail.get("csv_rows"):
            continue
        cases.append(
            BenchmarkCase(
                name=detail["run_name"],
                page_info=detail["raw_scan"],
                page_scope=detail["page_scope"],
                test_cases=detail["csv_rows"],
                base_url=detail.get("url", ""),
            )
        )
    if not cases:
        return empty_snapshot
    snapshot = run_benchmark_suite(cases)
    return {
        **empty_snapshot,
        **snapshot,
        "cluster_keys": list(snapshot.get("cluster_keys", []) or []),
    }


def _build_run_alerts(
    confidence_percent: int,
    status_counts: dict,
    scenario_validation: dict,
    execution_plan_validation: dict,
    page_scope_validation: dict,
    historical_signal: dict,
    confidence_breakdown: dict,
    contradiction_analysis: dict,
) -> list[dict]:
    alerts = []
    failed = int(status_counts.get("failed", 0) or 0)
    rejected_cases = len(scenario_validation.get("rejected_cases", []))
    rejected_plans = len(execution_plan_validation.get("rejected_plans", []))
    scope_issues = len(page_scope_validation.get("issues", []))
    flaky_count = int(historical_signal.get("flaky_count", 0) or 0)
    source_trust = float(confidence_breakdown.get("source_trust", 0.0) or 0.0)
    anti_hallucination = float(confidence_breakdown.get("anti_hallucination", 0.0) or 0.0)
    contradiction_count = int(contradiction_analysis.get("summary", {}).get("contradiction_count", 0) or 0)

    if confidence_percent >= 85 and failed > 0:
        alerts.append({"level": "warning", "title": "Overconfidence detected", "detail": f"Confidence {confidence_percent}/100 but {failed} case(s) failed."})
    if confidence_percent >= 80 and (rejected_cases or rejected_plans or scope_issues):
        alerts.append({"level": "warning", "title": "Grounding mismatch", "detail": f"High confidence with {rejected_cases + rejected_plans + scope_issues} guardrail rejection/issue signal(s)."})
    if confidence_percent >= 80 and flaky_count > 0:
        alerts.append({"level": "warning", "title": "Unstable history", "detail": f"High confidence while {flaky_count} flaky pattern(s) exist in memory."})
    if confidence_percent >= 80 and source_trust < 0.55:
        alerts.append({"level": "warning", "title": "Low source trust", "detail": "Confidence is high but source trust is still weak."})
    if confidence_percent >= 80 and anti_hallucination < 0.65:
        alerts.append({"level": "warning", "title": "Weak anti-hallucination safety", "detail": f"Confidence is high but anti-hallucination score is only {int(round(anti_hallucination * 100))}/100."})
    if contradiction_count > 0:
        alerts.append({"level": "warning", "title": "Cross-stage contradictions", "detail": f"{contradiction_count} contradiction signal(s) detected between scope, scenario, plan, and result."})
    return alerts[:4]


def _build_confidence_trend(results_dir: Path, url: str, current_run: str = "", limit: int = 8) -> list[dict]:
    host = _extract_host(url)
    if not host or not results_dir.exists():
        return []
    rows = []
    for run_dir in results_dir.iterdir():
        if not run_dir.is_dir():
            continue
        json_dir = run_dir / "JSON"
        raw_scan = _load_first_matching_json(json_dir, "raw_scan_*.json")
        if _extract_host(raw_scan.get("url", "")) != host:
            continue
        confidence_analysis = _load_json_if_exists(confidence_analysis_path(run_dir, create=False))
        page_scope = _load_first_matching_json(json_dir, "Page_Scope_*.json")
        score = confidence_analysis.get("confidence", page_scope.get("confidence", 0))
        rows.append(
            {
                "run_name": run_dir.name,
                "confidence_percent": int(round(_normalize_confidence(score) * 100)),
                "current": run_dir.name == current_run,
                "modified_ts": run_dir.stat().st_mtime,
            }
        )
    rows.sort(key=lambda item: item["modified_ts"])
    return rows[-limit:]


def _build_guardrail_summary(scope_validation: dict, scenario_validation: dict, execution_plan_validation: dict, contradiction_analysis: dict | None = None) -> dict:
    contradiction_analysis = contradiction_analysis or {}
    unsupported_scope = scope_validation.get("unsupported_surface_report", {}) if isinstance(scope_validation, dict) else {}
    unsupported_scenario = scenario_validation.get("unsupported_surface_report", {}) if isinstance(scenario_validation, dict) else {}
    return {
        "scope_issues": list(scope_validation.get("issues", []))[:8],
        "scenario_rejections": list(scenario_validation.get("rejected_cases", []))[:6],
        "plan_rejections": list(execution_plan_validation.get("rejected_plans", []))[:6],
        "scope_issue_count": len(scope_validation.get("issues", [])),
        "scenario_rejection_count": len(scenario_validation.get("rejected_cases", [])),
        "plan_rejection_count": len(execution_plan_validation.get("rejected_plans", [])),
        "unsupported_requested_surfaces": _unique_texts(
            list(unsupported_scope.get("unsupported_requested_surfaces", []))
            + list(unsupported_scenario.get("unsupported_requested_surfaces", []))
        )[:8],
        "avoid_surfaces": _unique_texts(
            list(unsupported_scope.get("avoid_surfaces", []))
            + list(unsupported_scenario.get("avoid_surfaces", []))
        )[:8],
        "instruction_conflicts": _unique_texts(
            list(unsupported_scope.get("instruction_conflicts", []))
            + list(unsupported_scenario.get("instruction_conflicts", []))
        )[:8],
        "contradictions": list(contradiction_analysis.get("issues", []))[:8],
        "contradiction_count": int(contradiction_analysis.get("summary", {}).get("contradiction_count", 0) or 0),
    }


def _build_knowledge_heatmap(snapshot: dict) -> list[dict]:
    rows = []
    for bucket_name, bucket in (("domain", snapshot.get("domain", {})), ("global", snapshot.get("global", {}))):
        for item in bucket.get("top_field_selectors", [])[:4]:
            rows.append({"bucket": bucket_name, "label": item.get("key", ""), "score": float(item.get("score", 0.0) or 0.0), "kind": "selector"})
        for item in bucket.get("top_semantic_patterns", [])[:4]:
            rows.append({"bucket": bucket_name, "label": item.get("key", ""), "score": float(item.get("score", 0.0) or 0.0), "kind": "pattern"})
        for item in bucket.get("top_failures", [])[:4]:
            rows.append({"bucket": bucket_name, "label": item.get("key", ""), "score": float(item.get("score", 0.0) or 0.0), "kind": "failure"})
    rows.sort(key=lambda item: (-item["score"], item["bucket"], item["label"]))
    top = rows[:10]
    if not top:
        return []
    max_score = max(item["score"] for item in top) or 1.0
    for item in top:
        item["intensity"] = max(0.15, min(1.0, item["score"] / max_score))
    return top


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


def _build_case_rows(
    csv_rows: list[dict],
    execution_results: dict,
    scenario_validation: dict | None = None,
    execution_plan: dict | None = None,
    contradiction_analysis: dict | None = None,
) -> list[dict]:
    scenario_validation = scenario_validation or {}
    execution_plan = execution_plan or {}
    contradiction_analysis = contradiction_analysis or {}
    results_by_id = {}
    result_meta = {}
    for item in execution_results.get("results", []):
        case_id = str(item.get("id", "")).strip()
        if case_id:
            results_by_id[case_id] = str(item.get("status", "")).strip().lower()
            result_meta[case_id] = item

    scenario_map = {}
    for item in scenario_validation.get("valid_cases", []):
        case_id = str(item.get("ID", "")).strip()
        if case_id:
            scenario_map[case_id] = item
    for item in scenario_validation.get("rejected_cases", []):
        case = item.get("case", {}) if isinstance(item, dict) else {}
        case_id = str(case.get("ID", "")).strip()
        if case_id and case_id not in scenario_map:
            scenario_map[case_id] = case

    plan_map = {
        str(item.get("id", "")).strip(): item
        for item in execution_plan.get("plans", [])
        if str(item.get("id", "")).strip()
    }
    contradiction_map = {}
    for item in contradiction_analysis.get("issues", []):
        case_id = str(item.get("case_id", "")).strip()
        if case_id:
            contradiction_map.setdefault(case_id, []).append(item)

    rows = []
    for index, row in enumerate(csv_rows, start=1):
        normalized = {str(key): str(value or "").strip() for key, value in row.items()}
        case_id = normalized.get("ID") or f"ROW-{index:03d}"
        status = (normalized.get("Execution Status") or results_by_id.get(case_id, "")).strip().lower()
        automation = normalized.get("Automation", "").strip().lower()
        priority = normalized.get("Priority", "").strip().upper()
        severity = normalized.get("Severity", "").strip().title()
        steps = _parse_numbered_steps(normalized.get("Steps to Reproduce", ""))
        scenario_case = scenario_map.get(case_id, {})
        scenario_grounding = scenario_case.get("_grounding", {}) if isinstance(scenario_case, dict) else {}
        plan_item = plan_map.get(case_id, {})
        plan_grounding = plan_item.get("scenario_grounding", {}) if isinstance(plan_item, dict) else {}
        result_item = result_meta.get(case_id, {})
        contradictions = contradiction_map.get(case_id, [])
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
                "scenario_fact_ids": list(scenario_grounding.get("fact_ids", []) or []),
                "plan_fact_ids": list(plan_grounding.get("fact_ids", []) or []),
                "result_fact_ids": list(result_item.get("fact_ids", []) or []),
                "scenario_fact_coverage_score": float(scenario_grounding.get("coverage_score", 0.0) or 0.0),
                "plan_average_step_fact_coverage_score": float(plan_item.get("grounding_summary", {}).get("average_step_fact_coverage_score", 0.0) or 0.0),
                "result_grounding_score": float(result_item.get("grounding_score", 0.0) or 0.0),
                "fact_refs": list(scenario_grounding.get("refs", []) or []),
                "fact_summary": str(scenario_grounding.get("summary", "")).strip(),
                "mentioned_surfaces": list(scenario_grounding.get("mentioned_surfaces", []) or []),
                "covered_surfaces": list(scenario_grounding.get("covered_surfaces", []) or []),
                "contradictions": contradictions,
                "contradiction_count": len(contradictions),
                "contradiction_messages": [str(item.get("message", "")).strip() for item in contradictions if str(item.get("message", "")).strip()],
            }
        )
    return rows


def _unique_texts(values: list[str]) -> list[str]:
    rows = []
    seen = set()
    for value in values:
        text = str(value or "").strip()
        lowered = text.lower()
        if text and lowered not in seen:
            rows.append(text)
            seen.add(lowered)
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


def _extract_host(value: str) -> str:
    return (urlparse(str(value or "")).netloc or "").replace("www.", "").lower()


def _load_or_build_visual_signature(run_path: Path, raw_scan: dict, page_scope: dict, page_model: dict) -> dict:
    signature_path = visual_signature_path(run_path, create=False)
    if signature_path.exists():
        return _load_json_if_exists(signature_path)
    signature = _build_visual_signature(raw_scan, page_scope, page_model)
    if signature:
        visual_signature_path(run_path).write_text(json.dumps(signature, indent=2), encoding="utf-8")
    return signature


def _build_visual_signature(raw_scan: dict, page_scope: dict, page_model: dict) -> dict:
    headings = [item.get("text", "") for item in raw_scan.get("headings", []) if isinstance(item, dict)]
    component_types = [component.get("type", "") for component in page_model.get("component_catalog", []) if component.get("type")]
    return {
        "page_type": page_scope.get("page_type", ""),
        "heading_count": len(headings),
        "heading_samples": headings[:6],
        "button_count": len(raw_scan.get("buttons", [])),
        "link_count": len(raw_scan.get("links", [])),
        "section_count": len(raw_scan.get("sections", [])),
        "component_count": len(page_model.get("component_catalog", [])),
        "component_types": sorted(dict.fromkeys(component_types)),
        "discovered_state_count": len(raw_scan.get("discovered_states", [])),
        "sampled_page_count": int(raw_scan.get("page_fingerprint", {}).get("sampled_page_count", 0) or 0),
    }


def _summarize_network_entries(entries: list[dict]) -> dict:
    requests = 0
    responses = 0
    failing = 0
    endpoints = {}
    for item in entries:
        summary = item.get("summary", {})
        requests += int(summary.get("request_count", 0) or 0)
        responses += int(summary.get("response_count", 0) or 0)
        failing += int(summary.get("failing_response_count", 0) or 0)
        for endpoint in summary.get("top_endpoints", [])[:8]:
            path = str(endpoint.get("path", "")).strip()
            if path:
                endpoints[path] = endpoints.get(path, 0) + int(endpoint.get("hits", 0) or 0)
    return {
        "request_count": requests,
        "response_count": responses,
        "failing_response_count": failing,
        "top_endpoints": [
            {"path": key, "hits": value}
            for key, value in sorted(endpoints.items(), key=lambda item: (-item[1], item[0]))[:8]
        ],
    }
