import csv
import json
import re
from pathlib import Path
from urllib.parse import urlparse

from core.artifacts import (
    anti_hallucination_audit_path,
    confidence_analysis_path,
    contradiction_analysis_path,
    drift_analysis_path,
    execution_checkpoint_path,
    execution_debug_path,
    execution_learning_path,
    execution_network_path,
    execution_replay_verification_path,
    execution_results_path,
    policy_pack_report_path,
    recovery_actions_path,
    scenario_contract_validation_path,
    token_usage_path,
    visual_diff_path,
    visual_baseline_path,
    visual_regression_path,
    visual_regression_approval_path,
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
    anti_hallucination_audit = _load_json_if_exists(anti_hallucination_audit_path(run_path, create=False))
    execution_results = _load_json_if_exists(execution_results_path(run_path, create=False))
    contradiction_analysis = _load_json_if_exists(contradiction_analysis_path(run_path, create=False))
    replay_verification = _load_json_if_exists(execution_replay_verification_path(run_path, create=False))
    drift_analysis = _load_json_if_exists(drift_analysis_path(run_path, create=False))
    policy_pack_report = _load_json_if_exists(policy_pack_report_path(run_path, create=False))
    recovery_actions = _load_json_if_exists(recovery_actions_path(run_path, create=False))
    visual_baseline = _load_json_if_exists(visual_baseline_path(run_path, create=False))
    visual_diff = _load_json_if_exists(visual_diff_path(run_path, create=False))
    visual_regression = _load_json_if_exists(visual_regression_path(run_path, create=False))
    visual_regression_approval = _load_json_if_exists(visual_regression_approval_path(run_path, create=False))
    scenario_contract_validation = _load_json_if_exists(scenario_contract_validation_path(run_path, create=False))
    approval_status = _derive_visual_approval_status(visual_regression, visual_regression_approval)
    recovery_action_rows = list(recovery_actions.get("actions", []))
    recovery_effectiveness = _build_recovery_effectiveness(recovery_action_rows)
    recovery_summary = _build_recovery_summary(recovery_action_rows)
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
    safety = _build_operational_safety_snapshot(
        confidence_breakdown=confidence_breakdown,
        anti_hallucination_audit=anti_hallucination_audit,
        replay_verification=replay_verification,
        drift_analysis=drift_analysis,
        policy_pack_report=policy_pack_report,
    )
    safety_recommendations = _build_safety_recommendations(
        safety=safety,
        anti_hallucination_audit=anti_hallucination_audit,
        replay_verification=replay_verification,
        drift_analysis=drift_analysis,
        policy_pack_report=policy_pack_report,
        contradiction_analysis=contradiction_analysis,
        scenario_validation=scenario_validation,
        recovery_actions=recovery_action_rows,
    )
    alerts = _build_run_alerts(
        confidence_percent=int(round(confidence * 100)),
        status_counts=status_counts,
        scenario_validation=scenario_validation,
        execution_plan_validation=execution_plan_validation,
        page_scope_validation=page_scope_validation,
        historical_signal=historical_signal,
        confidence_breakdown=confidence_breakdown,
        contradiction_analysis=contradiction_analysis,
        replay_verification=replay_verification,
        drift_analysis=drift_analysis,
        anti_hallucination_audit=anti_hallucination_audit,
        policy_pack_report=policy_pack_report,
    )
    regression_signal = _build_regression_signal(
        run_path,
        raw_scan.get("url", ""),
        status_counts=status_counts,
        confidence_percent=int(round(confidence * 100)),
        safety_index=int(safety["index"]),
    )
    needs_review = _build_needs_review_signal(
        alerts=alerts,
        safety_index=int(safety["index"]),
        execution_gate=(anti_hallucination_audit or {}).get("execution_gate", {}),
        status_counts=status_counts,
        regression_signal=regression_signal,
        scenario_validation=scenario_validation,
        execution_plan_validation=execution_plan_validation,
    )
    root_cause_clusters = _build_root_cause_clusters(
        alerts=alerts,
        recovery_summary=recovery_summary,
        scenario_validation=scenario_validation,
        contradiction_analysis=contradiction_analysis,
        drift_analysis=drift_analysis,
        anti_hallucination_audit=anti_hallucination_audit,
        policy_pack_report=policy_pack_report,
    )
    recovery_priority = _build_recovery_priority(
        status_counts=status_counts,
        safety_index=int(safety["index"]),
        needs_review=needs_review,
        execution_gate=(anti_hallucination_audit or {}).get("execution_gate", {}),
        recovery_summary=recovery_summary,
        regression_signal=regression_signal,
    )
    ai_guardrail_summary = _build_ai_guardrail_summary(page_scope_validation, scenario_validation)
    generation_incomplete = not bool(csv_path and csv_path.exists())
    generation_status = "ok"
    generation_message = ""
    if generation_incomplete:
        generation_status = "failed_before_csv"
        generation_message = "Test case generation stopped before CSV was created."
        if visual_signature and not raw_scan:
            generation_message = "Scan reached visual-signature stage, then stopped before raw scan and CSV generation."
        elif raw_scan and not page_scope:
            generation_message = "Raw scan exists, but page scope and CSV were not completed."
        elif page_scope and not csv_rows:
            generation_message = "Page scope exists, but CSV generation did not finish."

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
        "anti_hallucination_audit": anti_hallucination_audit or {},
        "contradiction_analysis": contradiction_analysis or {},
        "replay_verification": replay_verification or {},
        "drift_analysis": drift_analysis or {},
        "execution_gate": (anti_hallucination_audit or {}).get("execution_gate", {}),
        "policy_pack_report": policy_pack_report or {},
        "safety_index": safety["index"],
        "safety_score": safety["score"],
        "safety_status": safety["status"],
        "safety_reasons": safety["reasons"],
        "safety_recommendations": safety_recommendations,
        "recovery_actions": recovery_action_rows[-20:],
        "recovery_action_count": len(recovery_action_rows),
        "recovery_effectiveness": recovery_effectiveness,
        "recovery_summary": recovery_summary,
        "anti_hallucination_score": int(round(float(confidence_breakdown.get("anti_hallucination", 0.0) or 0.0) * 100)),
        "negative_evidence_detail": confidence_breakdown.get("negative_evidence_detail", {}),
        "source_trust_detail": confidence_breakdown.get("source_trust_detail", {}),
        "token_usage": token_usage,
        "token_usage_summary": token_usage.get("summary", {}),
        "alerts": alerts,
        "alert_count": len(alerts),
        "regression_signal": regression_signal,
        "needs_review": needs_review,
        "root_cause_clusters": root_cause_clusters,
        "primary_root_cause": root_cause_clusters[0]["key"] if root_cause_clusters else "",
        "recovery_priority": recovery_priority,
        "visual_baseline": visual_baseline or {},
        "visual_diff": visual_diff or {},
        "vrt_change_count": int((visual_diff.get("summary", {}) if isinstance(visual_diff, dict) else {}).get("total_changed", 0) or 0),
        "vrt_has_changes": bool((visual_diff.get("summary", {}) if isinstance(visual_diff, dict) else {}).get("total_changed", 0)),
        "vrt_summary": (visual_diff.get("summary", {}) if isinstance(visual_diff, dict) else {}),
        "vrt_baseline_run": str((visual_diff or {}).get("baseline_run", "")),
        "visual_regression": visual_regression or {},
        "visual_regression_status": str((visual_regression or {}).get("status", "")),
        "visual_regression_ratio": float(((visual_regression or {}).get("comparison", {}) or {}).get("ratio", 0.0) or 0.0),
        "visual_regression_approval": visual_regression_approval or {},
        "visual_regression_approval_status": approval_status,
        "scenario_contract_validation": scenario_contract_validation or {},
        "scenario_contract_ok": bool((scenario_contract_validation or {}).get("is_valid", True)),
        "scenario_contract_blocking_count": int((scenario_contract_validation or {}).get("blocking_count", 0) or 0),
        "ai_guardrail_summary": ai_guardrail_summary,
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
        "generation_incomplete": generation_incomplete,
        "generation_status": generation_status,
        "generation_message": generation_message,
    }


def build_run_detail(run_dir: str | Path) -> dict:
    run_path = Path(run_dir)
    summary = build_run_summary(run_path)
    json_dir = run_path / "JSON"
    csv_path = next(run_path.glob("*.csv"), None)
    page_model = _load_first_matching_json(json_dir, "Normalized_Page_Model_*.json")
    execution_results = _load_json_if_exists(execution_results_path(run_path, create=False))
    contradiction_analysis = _load_json_if_exists(contradiction_analysis_path(run_path, create=False))
    replay_verification = _load_json_if_exists(execution_replay_verification_path(run_path, create=False))
    drift_analysis = _load_json_if_exists(drift_analysis_path(run_path, create=False))
    policy_pack_report = _load_json_if_exists(policy_pack_report_path(run_path, create=False))
    recovery_actions = _load_json_if_exists(recovery_actions_path(run_path, create=False))
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
        "replay_verification": replay_verification,
        "drift_analysis": drift_analysis,
        "policy_pack_report": policy_pack_report,
        "recovery_actions": list(recovery_actions.get("actions", []))[-20:],
        "recovery_summary": summary.get("recovery_summary", {}),
        "execution_debug": _load_json_if_exists(execution_debug_path(run_path, create=False)),
        "execution_network": execution_network,
        "execution_learning": execution_learning,
        "execution_checkpoints": execution_checkpoints,
        "anti_hallucination_audit": summary.get("anti_hallucination_audit", {}),
        "run_feedback": run_feedback,
        "run_feedback_summary": run_feedback.get("summary", {}),
        "execution_ran": bool(execution_results.get("results")),
        "learning_entries": execution_learning.get("learning_entries", []),
        "checkpoint_entries": execution_checkpoints.get("checkpoints", []),
        "knowledge_snapshot": load_knowledge_bank_snapshot(summary.get("url", "")),
        "confidence_trend": _build_confidence_trend(run_path.parent, summary.get("url", ""), current_run=run_path.name),
        "safety_trend": _build_safety_trend(run_path.parent, summary.get("url", ""), current_run=run_path.name),
        "guardrail_summary": _build_guardrail_summary(
            page_scope_validation,
            scenario_validation,
            execution_plan_validation,
            contradiction_analysis,
        ),
        "ai_guardrail_summary": summary.get("ai_guardrail_summary", {}),
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
            "safety_index": right.get("safety_index", 0) - left.get("safety_index", 0),
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
            "replay_issues": int(right.get("replay_verification", {}).get("summary", {}).get("issue_count", 0) or 0)
            - int(left.get("replay_verification", {}).get("summary", {}).get("issue_count", 0) or 0),
            "api_drift": round(
                float(right.get("drift_analysis", {}).get("summary", {}).get("api_drift_score", 0.0) or 0.0)
                - float(left.get("drift_analysis", {}).get("summary", {}).get("api_drift_score", 0.0) or 0.0),
                2,
            ),
            "gate_blocked": int(bool(right.get("execution_gate", {}).get("blocked", False)))
            - int(bool(left.get("execution_gate", {}).get("blocked", False))),
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
        "average_safety_index": 0,
        "blocked_execution_rate": 0,
        "average_replay_consistency": 0,
        "average_api_drift": 0,
        "policy_pack_failure_rate": 0,
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
    safety_lookup = {
        str(item.get("run_name", "")).strip(): {
            "safety_index": int(item.get("safety_index", 0) or 0),
            "safety_status": str(item.get("safety_status", "")),
            "gate_blocked": bool(item.get("execution_gate", {}).get("blocked", False)),
            "replay_consistency": float(item.get("replay_verification", {}).get("summary", {}).get("consistency_score", 1.0) or 1.0),
            "api_drift_score": float(item.get("drift_analysis", {}).get("summary", {}).get("api_drift_score", 0.0) or 0.0),
            "policy_pack_success": bool(item.get("policy_pack_report", {}).get("success", True)),
        }
        for item in runs
    }
    for item in snapshot.get("results", []):
        safety = safety_lookup.get(str(item.get("name", "")).strip(), {})
        if safety:
            item.update(safety)
    safety_indexes = [float(item.get("safety_index", 0) or 0) for item in runs]
    blocked_count = sum(1 for item in runs if bool(item.get("execution_gate", {}).get("blocked", False)))
    replay_scores = [
        float(item.get("replay_verification", {}).get("summary", {}).get("consistency_score", 1.0) or 1.0)
        for item in runs
    ]
    api_drifts = [
        float(item.get("drift_analysis", {}).get("summary", {}).get("api_drift_score", 0.0) or 0.0)
        for item in runs
    ]
    policy_failures = sum(1 for item in runs if not bool(item.get("policy_pack_report", {}).get("success", True)))

    return {
        **empty_snapshot,
        **snapshot,
        "average_safety_index": round(sum(safety_indexes) / len(safety_indexes), 2) if safety_indexes else 0,
        "blocked_execution_rate": round(blocked_count / len(runs), 2) if runs else 0,
        "average_replay_consistency": round(sum(replay_scores) / len(replay_scores), 2) if replay_scores else 0,
        "average_api_drift": round(sum(api_drifts) / len(api_drifts), 2) if api_drifts else 0,
        "policy_pack_failure_rate": round(policy_failures / len(runs), 2) if runs else 0,
        "cluster_keys": list(snapshot.get("cluster_keys", []) or []),
    }


def build_triage_inbox(results_dir: str | Path = "Result", limit: int = 12) -> dict:
    rows = list_runs(results_dir)
    candidates = [
        item
        for item in rows
        if bool(item.get("needs_review", {}).get("flag", False)) or str(item.get("safety_status", "")) in {"critical", "warning"}
    ]
    candidates.sort(
        key=lambda item: (
            -int(item.get("needs_review", {}).get("score", 0) or 0),
            -int(item.get("recovery_priority", {}).get("score", 0) or 0),
            -float(item.get("modified_ts", 0) or 0),
        )
    )
    selected = candidates[: max(1, int(limit or 1))]
    action_ready = 0
    reason_counts = {}
    items = []
    for run in selected:
        strategy = str(run.get("recovery_priority", {}).get("recommended_strategy", "monitor") or "monitor")
        if strategy != "monitor":
            action_ready += 1
        for reason in list(run.get("needs_review", {}).get("reasons", []) or [])[:4]:
            key = str(reason).strip().lower()
            if key:
                reason_counts[key] = reason_counts.get(key, 0) + 1
        items.append(
            {
                "run_name": run.get("run_name", ""),
                "url": run.get("url", ""),
                "title": run.get("title", ""),
                "safety_index": int(run.get("safety_index", 0) or 0),
                "safety_status": run.get("safety_status", ""),
                "failed": int(run.get("status_counts", {}).get("failed", 0) or 0),
                "alert_count": int(run.get("alert_count", 0) or 0),
                "needs_review": run.get("needs_review", {}),
                "regression_signal": run.get("regression_signal", {}),
                "primary_root_cause": run.get("primary_root_cause", ""),
                "recovery_priority": run.get("recovery_priority", {}),
            }
        )
    top_reasons = [
        {"reason": key, "count": value}
        for key, value in sorted(reason_counts.items(), key=lambda item: (-item[1], item[0]))[:6]
    ]
    return {
        "total_runs": len(rows),
        "candidate_count": len(candidates),
        "actionable_count": action_ready,
        "items": items,
        "top_review_reasons": top_reasons,
    }


def build_ai_safety_audit(results_dir: str | Path = "Result", limit: int = 30) -> dict:
    rows = list_runs(results_dir)[: max(1, int(limit or 1))]
    if not rows:
        return {
            "total_runs": 0,
            "needs_review_count": 0,
            "blocked_count": 0,
            "regression_count": 0,
            "hallucination_risk_count": 0,
            "status": "unknown",
            "top_root_causes": [],
            "recommended_focus": [],
        }
    needs_review_count = sum(1 for item in rows if bool(item.get("needs_review", {}).get("flag", False)))
    blocked_count = sum(1 for item in rows if bool(item.get("execution_gate", {}).get("blocked", False)))
    regression_count = sum(1 for item in rows if bool(item.get("regression_signal", {}).get("is_regression", False)))
    hallucination_risk_count = sum(1 for item in rows if int(item.get("anti_hallucination_score", 0) or 0) < 65)
    cause_counts = {}
    for item in rows:
        key = str(item.get("primary_root_cause", "")).strip()
        if key:
            cause_counts[key] = cause_counts.get(key, 0) + 1
    top_root_causes = [
        {"key": key, "count": value}
        for key, value in sorted(cause_counts.items(), key=lambda item: (-item[1], item[0]))[:6]
    ]
    unsafe_score = (
        (needs_review_count / len(rows)) * 0.4
        + (blocked_count / len(rows)) * 0.25
        + (regression_count / len(rows)) * 0.2
        + (hallucination_risk_count / len(rows)) * 0.15
    )
    if unsafe_score >= 0.55:
        status = "unsafe"
    elif unsafe_score >= 0.3:
        status = "watch"
    else:
        status = "safe"
    recommended_focus = []
    if blocked_count > 0:
        recommended_focus.append("Increase anti-hallucination evidence before rerunning.")
    if regression_count > 0:
        recommended_focus.append("Compare the latest run vs domain baseline to prevent hidden regressions.")
    if hallucination_risk_count > 0:
        recommended_focus.append("Narrow instructions and raise concrete targets to reduce hallucinated cases.")
    if not recommended_focus:
        recommended_focus.append("Pertahankan guardrail saat ini dan lanjutkan monitoring berkala.")
    return {
        "total_runs": len(rows),
        "needs_review_count": needs_review_count,
        "blocked_count": blocked_count,
        "regression_count": regression_count,
        "hallucination_risk_count": hallucination_risk_count,
        "status": status,
        "unsafe_score": round(unsafe_score, 2),
        "top_root_causes": top_root_causes,
        "recommended_focus": recommended_focus[:6],
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
    replay_verification: dict,
    drift_analysis: dict,
    anti_hallucination_audit: dict,
    policy_pack_report: dict,
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
    replay_issues = int(replay_verification.get("summary", {}).get("issue_count", 0) or 0)
    api_drift = float(drift_analysis.get("summary", {}).get("api_drift_score", 0.0) or 0.0)
    blocked = bool(anti_hallucination_audit.get("execution_gate", {}).get("blocked", False))
    policy_success = bool(policy_pack_report.get("success", True))

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
    if replay_issues > 0:
        alerts.append({"level": "warning", "title": "Replay consistency issues", "detail": f"{replay_issues} plan-vs-result consistency issue(s) detected."})
    if api_drift >= 0.45:
        alerts.append({"level": "warning", "title": "API drift warning", "detail": f"API drift score is high ({int(round(api_drift * 100))}/100)."})
    if blocked:
        alerts.append({"level": "warning", "title": "Execution blocked", "detail": "Run execution was blocked by anti-hallucination hard gate."})
    if not policy_success:
        alerts.append({"level": "warning", "title": "Policy pack failed", "detail": "Anti-hallucination policy pack has failing checks."})
    return alerts[:4]


def _build_regression_signal(
    run_path: Path,
    url: str,
    status_counts: dict,
    confidence_percent: int,
    safety_index: int,
) -> dict:
    host = _extract_host(url)
    current_failed = int(status_counts.get("failed", 0) or 0)
    if not host:
        return {
            "has_baseline": False,
            "is_regression": False,
            "reasons": [],
            "failed_delta": 0,
            "confidence_delta": 0,
            "safety_delta": 0,
        }

    siblings = [item for item in run_path.parent.iterdir() if item.is_dir() and item != run_path]
    siblings.sort(key=lambda item: item.stat().st_mtime, reverse=True)
    baseline = None
    for candidate in siblings:
        json_dir = candidate / "JSON"
        raw_scan = _load_first_matching_json(json_dir, "raw_scan_*.json")
        if _extract_host(raw_scan.get("url", "")) != host:
            continue
        page_scope = _load_first_matching_json(json_dir, "Page_Scope_*.json")
        confidence_analysis = _load_json_if_exists(confidence_analysis_path(candidate, create=False))
        anti_hallu = _load_json_if_exists(anti_hallucination_audit_path(candidate, create=False))
        replay = _load_json_if_exists(execution_replay_verification_path(candidate, create=False))
        drift = _load_json_if_exists(drift_analysis_path(candidate, create=False))
        policy = _load_json_if_exists(policy_pack_report_path(candidate, create=False))
        execution_results = _load_json_if_exists(execution_results_path(candidate, create=False))
        baseline_status_counts = {}
        for item in execution_results.get("results", []):
            key = str(item.get("status", "")).strip().lower() or "unknown"
            baseline_status_counts[key] = baseline_status_counts.get(key, 0) + 1
        baseline_confidence = int(round(_normalize_confidence(confidence_analysis.get("confidence", page_scope.get("confidence", 0))) * 100))
        safety = _build_operational_safety_snapshot(
            confidence_breakdown=(confidence_analysis.get("breakdown", {}) if isinstance(confidence_analysis, dict) else {}),
            anti_hallucination_audit=anti_hallu,
            replay_verification=replay,
            drift_analysis=drift,
            policy_pack_report=policy,
        )
        baseline = {
            "run_name": candidate.name,
            "failed": int(baseline_status_counts.get("failed", 0) or 0),
            "confidence_percent": baseline_confidence,
            "safety_index": int(safety.get("index", 0) or 0),
        }
        break

    if not baseline:
        return {
            "has_baseline": False,
            "is_regression": False,
            "reasons": [],
            "failed_delta": 0,
            "confidence_delta": 0,
            "safety_delta": 0,
        }

    failed_delta = current_failed - baseline["failed"]
    confidence_delta = confidence_percent - baseline["confidence_percent"]
    safety_delta = safety_index - baseline["safety_index"]
    reasons = []
    if failed_delta > 0:
        reasons.append(f"failed +{failed_delta} vs {baseline['run_name']}")
    if confidence_delta <= -10:
        reasons.append(f"confidence {confidence_delta} points")
    if safety_delta <= -8:
        reasons.append(f"safety {safety_delta} points")
    return {
        "has_baseline": True,
        "baseline_run_name": baseline["run_name"],
        "is_regression": bool(reasons),
        "reasons": reasons[:5],
        "failed_delta": failed_delta,
        "confidence_delta": confidence_delta,
        "safety_delta": safety_delta,
    }


def _build_needs_review_signal(
    alerts: list[dict],
    safety_index: int,
    execution_gate: dict,
    status_counts: dict,
    regression_signal: dict,
    scenario_validation: dict,
    execution_plan_validation: dict,
) -> dict:
    score = 0
    reasons = []
    failed = int(status_counts.get("failed", 0) or 0)
    if bool(execution_gate.get("blocked", False)):
        score += 40
        reasons.append("execution gate blocked")
    if safety_index <= 55:
        score += 25
        reasons.append("low safety index")
    if failed > 0:
        score += min(25, failed * 5)
        reasons.append("failed cases detected")
    if bool(regression_signal.get("is_regression", False)):
        score += 20
        reasons.append("regression vs baseline")
    scenario_rejections = len(scenario_validation.get("rejected_cases", [])) if isinstance(scenario_validation, dict) else 0
    plan_rejections = len(execution_plan_validation.get("rejected_plans", [])) if isinstance(execution_plan_validation, dict) else 0
    if scenario_rejections + plan_rejections > 0:
        score += min(15, (scenario_rejections + plan_rejections) * 3)
        reasons.append("guardrail rejections exist")
    if len(alerts) >= 2:
        score += 10
        reasons.append("multiple warning alerts")
    score = max(0, min(100, score))
    tier = "high" if score >= 70 else "medium" if score >= 45 else "low"
    return {
        "flag": score >= 45,
        "score": score,
        "tier": tier,
        "reasons": _unique_texts(reasons)[:6],
    }


def _build_root_cause_clusters(
    alerts: list[dict],
    recovery_summary: dict,
    scenario_validation: dict,
    contradiction_analysis: dict,
    drift_analysis: dict,
    anti_hallucination_audit: dict,
    policy_pack_report: dict,
) -> list[dict]:
    buckets = {}

    def push(key: str, signal: str) -> None:
        entry = buckets.setdefault(key, {"key": key, "count": 0, "signals": []})
        entry["count"] += 1
        text = str(signal or "").strip()
        if text and text not in entry["signals"]:
            entry["signals"].append(text)

    for alert in alerts:
        title = str(alert.get("title", "")).lower()
        detail = str(alert.get("detail", ""))
        if "blocked" in title or "anti-hallucination" in title:
            push("anti_hallucination", detail or title)
        if "grounding" in title:
            push("grounding_gap", detail or title)
        if "contradiction" in title:
            push("contradiction", detail or title)
        if "drift" in title:
            push("api_drift", detail or title)
        if "policy pack" in title:
            push("policy_pack", detail or title)
        if "replay" in title:
            push("replay_consistency", detail or title)
    for item in list(scenario_validation.get("rejected_cases", []) or [])[:8]:
        for issue in list(item.get("issues", []) or [])[:3]:
            push("grounding_gap", str(issue))
    contradiction_count = int(contradiction_analysis.get("summary", {}).get("contradiction_count", 0) or 0)
    if contradiction_count > 0:
        push("contradiction", f"{contradiction_count} contradiction signal(s)")
    api_drift = float(drift_analysis.get("summary", {}).get("api_drift_score", 0.0) or 0.0)
    if api_drift >= 0.45:
        push("api_drift", f"api drift {int(round(api_drift * 100))}/100")
    if bool(anti_hallucination_audit.get("execution_gate", {}).get("blocked", False)):
        push("anti_hallucination", "execution blocked by anti-hallucination gate")
    if not bool(policy_pack_report.get("success", True)):
        push("policy_pack", "anti-hallucination policy pack has failing checks")
    if int(recovery_summary.get("failed_streak", 0) or 0) >= 2:
        push("recovery_instability", "latest recovery attempts keep failing")
    rows = sorted(buckets.values(), key=lambda item: (-item["count"], item["key"]))
    for item in rows:
        item["signals"] = item["signals"][:4]
    return rows[:5]


def _build_recovery_priority(
    status_counts: dict,
    safety_index: int,
    needs_review: dict,
    execution_gate: dict,
    recovery_summary: dict,
    regression_signal: dict,
) -> dict:
    failed = int(status_counts.get("failed", 0) or 0)
    score = int(needs_review.get("score", 0) or 0)
    if failed > 0:
        score += min(20, failed * 4)
    if bool(execution_gate.get("blocked", False)):
        score += 10
    if bool(regression_signal.get("is_regression", False)):
        score += 10
    if int(recovery_summary.get("failed_streak", 0) or 0) >= 2:
        score += 8
    score = max(0, min(100, score))
    if bool(execution_gate.get("blocked", False)):
        strategy = "safe_rerun"
        reason = "Gate blocked: gunakan safe rerun konservatif."
    elif failed > 0:
        strategy = "retry_failed"
        reason = "There are failed cases and the gate is not blocked."
    elif safety_index < 70:
        strategy = "safe_rerun"
        reason = "Safety menurun, rerun konservatif direkomendasikan."
    else:
        strategy = "monitor"
        reason = "No automatic recovery is needed right now."
    tier = "urgent" if score >= 75 else "high" if score >= 55 else "normal"
    return {
        "score": score,
        "tier": tier,
        "recommended_strategy": strategy,
        "reason": reason,
    }


def _build_operational_safety_snapshot(
    confidence_breakdown: dict,
    anti_hallucination_audit: dict,
    replay_verification: dict,
    drift_analysis: dict,
    policy_pack_report: dict,
) -> dict:
    anti_hallu = float(confidence_breakdown.get("anti_hallucination", 0.0) or 0.0)
    replay_consistency = float(replay_verification.get("summary", {}).get("consistency_score", 1.0) or 1.0)
    api_drift = float(drift_analysis.get("summary", {}).get("api_drift_score", 0.0) or 0.0)
    gate_blocked = bool(anti_hallucination_audit.get("execution_gate", {}).get("blocked", False))
    policy_success = bool(policy_pack_report.get("success", True))

    score = anti_hallu
    score = (score * 0.45) + (replay_consistency * 0.3) + ((1.0 - min(1.0, api_drift)) * 0.25)
    if gate_blocked:
        score -= 0.2
    if not policy_success:
        score -= 0.15
    score = max(0.0, min(1.0, score))
    reasons = []
    if gate_blocked:
        reasons.append("execution gate blocked")
    if replay_consistency < 0.75:
        reasons.append("replay consistency rendah")
    if api_drift >= 0.45:
        reasons.append("api drift tinggi")
    if not policy_success:
        reasons.append("policy pack failed")
    status = "safe"
    if score < 0.75 or reasons:
        status = "warning"
    if gate_blocked or score < 0.55:
        status = "critical"
    return {
        "score": round(score, 2),
        "index": int(round(score * 100)),
        "status": status,
        "reasons": reasons[:5],
    }


def _build_safety_recommendations(
    safety: dict,
    anti_hallucination_audit: dict,
    replay_verification: dict,
    drift_analysis: dict,
    policy_pack_report: dict,
    contradiction_analysis: dict,
    scenario_validation: dict,
    recovery_actions: list[dict] | None = None,
) -> list[str]:
    recommendations = []
    gate = anti_hallucination_audit.get("execution_gate", {}) if isinstance(anti_hallucination_audit, dict) else {}
    replay_summary = replay_verification.get("summary", {}) if isinstance(replay_verification, dict) else {}
    drift_summary = drift_analysis.get("summary", {}) if isinstance(drift_analysis, dict) else {}
    contradiction_count = int(contradiction_analysis.get("summary", {}).get("contradiction_count", 0) or 0)
    scenario_rejections = len(scenario_validation.get("rejected_cases", [])) if isinstance(scenario_validation, dict) else 0

    if bool(gate.get("blocked", False)):
        recommendations.append("Review anti-hallucination gate reasons before retry; avoid forcing execution without evidence.")
    if int(replay_summary.get("issue_count", 0) or 0) > 0:
        recommendations.append("Inspect replay verification issues and align plan assertions with actual runtime signals.")
    if float(drift_summary.get("api_drift_score", 0.0) or 0.0) >= 0.45:
        recommendations.append("Re-scan API surface and update expected endpoint allowlist due to API drift.")
    if contradiction_count > 0:
        recommendations.append("Resolve cross-stage contradictions between scope, scenario, and execution plan.")
    if scenario_rejections >= 2:
        recommendations.append("Refine scenario prompts to reduce unsupported surfaces and weak grounding rejections.")
    if policy_pack_report and not bool(policy_pack_report.get("success", True)):
        recommendations.append("Fix failing anti-hallucination policy checks before trusting this run.")
    if float(safety.get("score", 0.0) or 0.0) < 0.55:
        recommendations.append("Run with narrower instruction scope and stronger concrete targets to recover safety score.")
    recovery_actions = list(recovery_actions or [])
    recovery_results = [
        item for item in recovery_actions
        if str(item.get("action", "")).strip().lower() in {"safe_rerun_result", "retry_failed_result"}
    ]
    recovery_results = sorted(recovery_results, key=lambda item: str(item.get("timestamp", "")), reverse=True)
    if len(recovery_results) >= 2:
        top_two = recovery_results[:2]
        if all(str(item.get("status", "")).strip().lower() in {"failed", "canceled"} for item in top_two):
            recommendations.append("Two latest recovery attempts failed; rollback to stable baseline and re-scan before next recovery.")
    limited = recommendations[:6]
    rollback_item = next((item for item in recommendations if "rollback" in item.lower()), "")
    if rollback_item and rollback_item not in limited:
        if limited:
            limited[-1] = rollback_item
        else:
            limited = [rollback_item]
    return limited


def _build_recovery_effectiveness(recovery_actions: list[dict] | None) -> dict:
    actions = list(recovery_actions or [])
    strategies = {"safe_rerun": {"success": 0, "failed": 0}, "retry_failed": {"success": 0, "failed": 0}}
    for item in actions:
        strategy = str(item.get("strategy", "")).strip().lower()
        status = str(item.get("status", "")).strip().lower()
        if strategy not in strategies:
            continue
        if status == "completed":
            strategies[strategy]["success"] += 1
        elif status in {"failed", "canceled"}:
            strategies[strategy]["failed"] += 1
    summary = {}
    for strategy, counts in strategies.items():
        total = counts["success"] + counts["failed"]
        summary[strategy] = {
            "success": counts["success"],
            "failed": counts["failed"],
            "success_rate": round(counts["success"] / total, 2) if total else 0.0,
        }
    return summary


def _build_recovery_summary(recovery_actions: list[dict] | None) -> dict:
    actions = sorted(
        list(recovery_actions or []),
        key=lambda item: str(item.get("timestamp", "")),
    )
    status_counts: dict[str, int] = {}
    failure_reasons: dict[str, int] = {}
    result_actions = [
        item
        for item in actions
        if str(item.get("action", "")).strip().lower() in {"safe_rerun_result", "retry_failed_result"}
    ]
    for item in actions:
        status = str(item.get("status", "")).strip().lower() or "unknown"
        status_counts[status] = status_counts.get(status, 0) + 1
        if status in {"failed", "canceled", "skipped"}:
            reason = str(item.get("reason", "")).strip()
            if reason:
                failure_reasons[reason] = failure_reasons.get(reason, 0) + 1

    failed_streak = 0
    for item in reversed(result_actions):
        status = str(item.get("status", "")).strip().lower()
        if status in {"failed", "canceled"}:
            failed_streak += 1
            continue
        break

    latest = actions[-1] if actions else {}
    return {
        "total_actions": len(actions),
        "result_action_count": len(result_actions),
        "failed_streak": failed_streak,
        "status_counts": status_counts,
        "latest": {
            "timestamp": str(latest.get("timestamp", "")).strip(),
            "action": str(latest.get("action", "")).strip(),
            "strategy": str(latest.get("strategy", "")).strip(),
            "status": str(latest.get("status", "")).strip(),
            "reason": str(latest.get("reason", "")).strip(),
        },
        "top_failure_reasons": [
            {"reason": key, "count": value}
            for key, value in sorted(failure_reasons.items(), key=lambda item: (-item[1], item[0]))[:5]
        ],
    }


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


def _build_safety_trend(results_dir: Path, url: str, current_run: str = "", limit: int = 8) -> list[dict]:
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
        anti_hallucination_audit = _load_json_if_exists(anti_hallucination_audit_path(run_dir, create=False))
        replay_verification = _load_json_if_exists(execution_replay_verification_path(run_dir, create=False))
        drift_analysis = _load_json_if_exists(drift_analysis_path(run_dir, create=False))
        policy_pack_report = _load_json_if_exists(policy_pack_report_path(run_dir, create=False))
        breakdown = confidence_analysis.get("breakdown", {}) if isinstance(confidence_analysis, dict) else {}
        safety = _build_operational_safety_snapshot(
            confidence_breakdown=breakdown,
            anti_hallucination_audit=anti_hallucination_audit,
            replay_verification=replay_verification,
            drift_analysis=drift_analysis,
            policy_pack_report=policy_pack_report,
        )
        rows.append(
            {
                "run_name": run_dir.name,
                "safety_index": safety["index"],
                "safety_status": safety["status"],
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


def _build_ai_guardrail_summary(scope_validation: dict, scenario_validation: dict) -> dict:
    scope_validation = scope_validation or {}
    scenario_validation = scenario_validation or {}
    scope_routing = scope_validation.get("routing", {}) if isinstance(scope_validation, dict) else {}
    scenario_routing = scenario_validation.get("routing", {}) if isinstance(scenario_validation, dict) else {}
    fact_pack_summary = scenario_validation.get("fact_pack_summary", {}) or scope_validation.get("fact_pack_summary", {})
    rejected_cases = list(scenario_validation.get("rejected_cases", [])) if isinstance(scenario_validation, dict) else []
    rejection_reasons = []
    for item in rejected_cases[:8]:
        for issue in item.get("issues", [])[:3]:
            text = str(issue or "").strip()
            if text and text not in rejection_reasons:
                rejection_reasons.append(text)
    return {
        "scope_mode": scope_routing.get("mode", ""),
        "scope_reason": scope_routing.get("reason", ""),
        "scenario_mode": scenario_routing.get("mode", ""),
        "scenario_reason": scenario_routing.get("reason", ""),
        "fact_count": int(fact_pack_summary.get("fact_count", 0) or 0),
        "negative_fact_count": int(fact_pack_summary.get("negative_fact_count", 0) or 0),
        "component_count": int(fact_pack_summary.get("component_count", 0) or 0),
        "field_count": int(fact_pack_summary.get("field_count", 0) or 0),
        "unsupported_surface_count": int(fact_pack_summary.get("unsupported_surface_count", 0) or 0),
        "rejected_case_count": len(rejected_cases),
        "top_rejection_reasons": rejection_reasons[:6],
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


def _derive_visual_approval_status(visual_regression: dict, approval_payload: dict) -> str:
    explicit = str((approval_payload or {}).get("status", "")).strip().lower()
    if explicit in {"approved", "rejected", "pending"}:
        return explicit
    visual_status = str((visual_regression or {}).get("status", "")).strip().lower()
    if visual_status in {"failed", "passed"}:
        return "pending"
    if visual_status == "baseline_created":
        return "seeded"
    return "n/a"


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
    instruction_conflicts = list(
        scenario_validation.get("unsupported_surface_report", {}).get("instruction_conflicts", [])
    ) if isinstance(scenario_validation, dict) else []
    avoid_surfaces = list(
        scenario_validation.get("unsupported_surface_report", {}).get("avoid_surfaces", [])
    ) if isinstance(scenario_validation, dict) else []
    unsupported_requested_surfaces = list(
        scenario_validation.get("unsupported_surface_report", {}).get("unsupported_requested_surfaces", [])
    ) if isinstance(scenario_validation, dict) else []
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
    rejected_case_map = {}
    for item in scenario_validation.get("rejected_cases", []):
        case = item.get("case", {}) if isinstance(item, dict) else {}
        case_id = str(case.get("ID", "")).strip()
        if case_id:
            rejected_case_map[case_id] = item
            if case_id not in scenario_map:
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
        rejected_case = rejected_case_map.get(case_id, {})
        scenario_grounding = scenario_case.get("_grounding", {}) if isinstance(scenario_case, dict) else {}
        if not scenario_grounding and isinstance(rejected_case, dict):
            scenario_grounding = rejected_case.get("grounding", {}) or {}
        task_alignment = scenario_case.get("_task_alignment", {}) if isinstance(scenario_case, dict) else {}
        if not task_alignment and isinstance(rejected_case, dict):
            task_alignment = rejected_case.get("alignment", {}) or {}
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
                "scenario_rejection_reasons": list(rejected_case.get("issues", []) or []),
                "instruction_conflicts": instruction_conflicts[:8],
                "instruction_avoid_surfaces": avoid_surfaces[:8],
                "instruction_unsupported_surfaces": unsupported_requested_surfaces[:8],
                "instruction_rejection": any(
                    "instruction" in str(item or "").lower() or "avoid" in str(item or "").lower()
                    for item in list(rejected_case.get("issues", []) or [])
                ),
                "scenario_fact_coverage_score": float(scenario_grounding.get("coverage_score", 0.0) or 0.0),
                "scenario_grounding_score": float(scenario_grounding.get("score", 0.0) or 0.0),
                "plan_average_step_fact_coverage_score": float(plan_item.get("grounding_summary", {}).get("average_step_fact_coverage_score", 0.0) or 0.0),
                "result_grounding_score": float(result_item.get("grounding_score", 0.0) or 0.0),
                "fact_refs": list(scenario_grounding.get("refs", []) or []),
                "fact_summary": str(scenario_grounding.get("summary", "")).strip(),
                "mentioned_surfaces": list(scenario_grounding.get("mentioned_surfaces", []) or []),
                "covered_surfaces": list(scenario_grounding.get("covered_surfaces", []) or []),
                "task_alignment_score": float(task_alignment.get("score", 0.0) or 0.0),
                "alignment_allowed_hits": list(task_alignment.get("allowed_hits", []) or []),
                "alignment_focus_hits": list(task_alignment.get("focus_hits", []) or []),
                "alignment_concrete_hits": list(task_alignment.get("concrete_hits", []) or []),
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


def sort_runs(runs: list[dict], mode: str = "latest") -> list[dict]:
    mode = str(mode or "latest").strip().lower()
    if mode == "safety_risk":
        return sorted(
            runs,
            key=lambda item: (
                int(item.get("safety_index", 0) or 0),
                -int(item.get("alert_count", 0) or 0),
                -float(item.get("modified_ts", 0) or 0),
            ),
        )
    if mode == "failed_high":
        return sorted(
            runs,
            key=lambda item: (
                -int(item.get("status_counts", {}).get("failed", 0) or 0),
                int(item.get("safety_index", 0) or 0),
                -float(item.get("modified_ts", 0) or 0),
            ),
        )
    if mode == "alerts_high":
        return sorted(
            runs,
            key=lambda item: (
                -int(item.get("alert_count", 0) or 0),
                int(item.get("safety_index", 0) or 0),
                -float(item.get("modified_ts", 0) or 0),
            ),
        )
    if mode == "vrt_change":
        return sorted(
            runs,
            key=lambda item: (
                -int(item.get("vrt_change_count", 0) or 0),
                -float(item.get("modified_ts", 0) or 0),
            ),
        )
    return list(runs)
