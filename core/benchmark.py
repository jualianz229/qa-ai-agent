import json
from dataclasses import dataclass
from pathlib import Path

from core.confidence import build_historical_confidence_signal, compute_composite_confidence
from core.contradictions import analyze_cross_stage_contradictions
from core.guardrails import validate_execution_plan, validate_page_scope, validate_test_scenarios
from core.test_case_generator.planner import build_execution_plan, build_normalized_page_model, save_json_artifact
from core.site_profiles import derive_cluster_keys


@dataclass
class BenchmarkCase:
    name: str
    page_info: dict
    page_scope: dict
    test_cases: list[dict]
    base_url: str


@dataclass
class RealSiteBenchmarkTarget:
    name: str
    url: str
    expected_page_type: str = ""
    key_modules: list[str] | None = None
    critical_user_flows: list[str] | None = None
    instructions: str = ""
    use_auth: bool = False
    crawl_limit: int = 3


def run_benchmark_suite(cases: list[BenchmarkCase], output_dir: str | Path | None = None) -> dict:
    results = []
    for case in cases:
        page_model = build_normalized_page_model(case.page_info)
        scope_validation = validate_page_scope(case.page_scope, page_model, case.page_info)
        scenario_validation = validate_test_scenarios(
            case.test_cases,
            page_model,
            scope_validation.get("page_scope", case.page_scope),
            case.page_info,
        )
        plan_source_cases = scenario_validation.get("valid_cases", []) or case.test_cases
        execution_plan = build_execution_plan(plan_source_cases, page_model, case.base_url, site_profile=case.page_info.get("site_profile"))
        plan_validation = validate_execution_plan(execution_plan, page_model, case.page_info)
        contradiction_report = analyze_cross_stage_contradictions(
            page_scope=scope_validation.get("page_scope", case.page_scope),
            test_cases=plan_source_cases,
            execution_plan=plan_validation.get("valid_plan", execution_plan),
            page_model=page_model,
            page_info=case.page_info,
            scenario_validation=scenario_validation,
            execution_plan_validation=plan_validation,
        )
        grounding = _grounding_metrics(plan_validation.get("valid_plan", {}))
        heuristic_alignment = _heuristic_alignment(page_model.get("heuristic_scope", {}), case.page_scope)
        automation_counts = _automation_counts(case.test_cases)
        anti_hallu = _anti_hallucination_metrics(case.test_cases, scenario_validation, contradiction_report)
        safety = _benchmark_safety_index(
            anti_hallu_score=float(anti_hallu["score"]),
            contradiction_count=int(contradiction_report.get("summary", {}).get("contradiction_count", 0) or 0),
            conservative_plan_rate=float(grounding["conservative_plan_rate"]),
            low_alignment_plan_rate=float(grounding["low_alignment_plan_rate"]),
            false_positive_case_rate=float(anti_hallu["false_positive_case_rate"]),
            weak_grounding_case_rate=float(anti_hallu["weak_grounding_case_rate"]),
        )
        historical_signal = build_historical_confidence_signal(
            url=case.base_url,
            page_model=page_model,
            page_scope=scope_validation.get("page_scope", case.page_scope),
            site_profile=case.page_info.get("site_profile", {}),
        )
        composite_confidence = compute_composite_confidence(
            page_scope=scope_validation.get("page_scope", case.page_scope),
            page_info=case.page_info,
            page_model=page_model,
            scope_validation=scope_validation,
            scenario_validation=scenario_validation,
            execution_plan_validation=plan_validation,
            historical_signal=historical_signal,
            contradiction_analysis=contradiction_report,
        )
        confidence_breakdown = composite_confidence.get("breakdown", {})
        cluster_keys = derive_cluster_keys(page_model, case.page_scope)
        results.append(
            {
                "name": case.name,
                "scope_valid": scope_validation.get("is_valid", False),
                "scope_issue_count": len(scope_validation.get("issues", [])),
                "plan_valid": plan_validation.get("is_valid", False),
                "valid_plan_count": len(plan_validation.get("valid_plan", {}).get("plans", [])),
                "rejected_plan_count": len(plan_validation.get("rejected_plans", [])),
                "valid_scenario_count": len(scenario_validation.get("valid_cases", [])),
                "rejected_scenario_count": len(scenario_validation.get("rejected_cases", [])),
                "page_type": scope_validation.get("page_scope", {}).get("page_type", ""),
                "heuristic_page_type": page_model.get("heuristic_scope", {}).get("likely_page_type", ""),
                "heuristic_alignment": heuristic_alignment,
                "grounding_coverage": grounding["coverage"],
                "weak_grounding_items": grounding["weak_items"],
                "average_fact_coverage": grounding["average_fact_coverage"],
                "conservative_plan_rate": grounding["conservative_plan_rate"],
                "low_alignment_plan_rate": grounding["low_alignment_plan_rate"],
                "contradiction_count": contradiction_report.get("summary", {}).get("contradiction_count", 0),
                "false_positive_case_rate": anti_hallu["false_positive_case_rate"],
                "weak_grounding_case_rate": anti_hallu["weak_grounding_case_rate"],
                "anti_hallucination_score": anti_hallu["score"],
                "safety_index": safety["index"],
                "safety_status": safety["status"],
                "safety_reasons": safety["reasons"],
                "cluster_keys": cluster_keys,
                "auto_case_count": automation_counts["auto"],
                "semi_auto_case_count": automation_counts["semi-auto"],
                "manual_case_count": automation_counts["manual"],
                "confidence_score": composite_confidence.get("score", 0),
                "source_trust": confidence_breakdown.get("source_trust", 0),
                "stability": confidence_breakdown.get("stability", 0),
                "negative_evidence": confidence_breakdown.get("negative_evidence", 0),
                "concrete_target_density": _concrete_target_density(page_model),
                "component_types": [component.get("type", "") for component in page_model.get("components", [])],
            }
        )

    summary = {
        "total_cases": len(results),
        "passed_scope": sum(1 for item in results if item["scope_valid"]),
        "passed_plan": sum(1 for item in results if item["plan_valid"]),
        "average_grounding_coverage": _average([item["grounding_coverage"] for item in results]),
        "average_fact_coverage": _average([item["average_fact_coverage"] for item in results]),
        "average_conservative_plan_rate": _average([item["conservative_plan_rate"] for item in results]),
        "average_low_alignment_plan_rate": _average([item["low_alignment_plan_rate"] for item in results]),
        "average_heuristic_alignment": _average([item["heuristic_alignment"] for item in results]),
        "average_confidence": _average([item["confidence_score"] for item in results]),
        "average_source_trust": _average([item["source_trust"] for item in results]),
        "average_stability": _average([item["stability"] for item in results]),
        "average_negative_evidence": _average([item["negative_evidence"] for item in results]),
        "average_concrete_target_density": _average([item["concrete_target_density"] for item in results]),
        "average_anti_hallucination_score": _average([item["anti_hallucination_score"] for item in results]),
        "average_safety_index": _average([float(item["safety_index"]) / 100 for item in results]) * 100 if results else 0,
        "critical_safety_case_count": sum(1 for item in results if item.get("safety_status") == "critical"),
        "average_false_positive_case_rate": _average([item["false_positive_case_rate"] for item in results]),
        "average_weak_grounding_case_rate": _average([item["weak_grounding_case_rate"] for item in results]),
        "average_contradiction_count": _average([float(item["contradiction_count"]) for item in results]),
        "cluster_keys": sorted({key for item in results for key in item.get("cluster_keys", [])}),
        "total_auto_cases": sum(item["auto_case_count"] for item in results),
        "total_semi_auto_cases": sum(item["semi_auto_case_count"] for item in results),
        "total_manual_cases": sum(item["manual_case_count"] for item in results),
        "results": results,
    }
    if output_dir:
        save_json_artifact(summary, Path(output_dir) / "Benchmark_Report.json")
    return summary


def load_real_site_benchmark_suite(config_path: str | Path) -> dict:
    path = Path(config_path)
    payload = json.loads(path.read_text(encoding="utf-8"))
    targets = []
    for item in payload.get("targets", []):
        targets.append(
            RealSiteBenchmarkTarget(
                name=str(item.get("name", "")).strip() or str(item.get("url", "")).strip(),
                url=str(item.get("url", "")).strip(),
                expected_page_type=str(item.get("expected_page_type", "")).strip(),
                key_modules=list(item.get("key_modules", []) or []),
                critical_user_flows=list(item.get("critical_user_flows", []) or []),
                instructions=str(item.get("instructions", "")).strip(),
                use_auth=bool(item.get("use_auth", False)),
                crawl_limit=int(item.get("crawl_limit", 3) or 3),
            )
        )
    return {
        "suite_name": str(payload.get("suite_name", "")).strip() or path.stem,
        "targets": targets,
        "raw": payload,
    }


def run_real_site_benchmark_suite(
    config_path: str | Path,
    scanner,
    ai_engine=None,
    output_dir: str | Path | None = None,
) -> dict:
    suite = load_real_site_benchmark_suite(config_path)
    cases = []
    live_targets = []
    for target in suite["targets"]:
        project_info, page_info, _ = scanner.scan_website(
            target.url,
            use_auth=target.use_auth,
            crawl_limit=target.crawl_limit,
        )
        page_model = build_normalized_page_model(page_info)
        if ai_engine:
            page_scope = ai_engine.analyze_page_scope(
                target.url,
                page_info.get("title", "") or project_info.get("title", ""),
                page_info,
                page_model=page_model,
                custom_instruction=target.instructions,
            )
            scenarios = ai_engine.generate_test_scenarios(
                target.url,
                page_info.get("title", "") or project_info.get("title", ""),
                page_info,
                page_model=page_model,
                page_scope=page_scope,
                custom_instruction=target.instructions,
            )
        else:
            page_scope = {
                "page_type": target.expected_page_type,
                "primary_goal": "",
                "key_modules": target.key_modules or [],
                "critical_user_flows": target.critical_user_flows or [],
                "priority_areas": target.key_modules or [],
                "risks": [],
                "scope_summary": "",
                "confidence": 0.5,
            }
            scenarios = []
        expected_scope = {
            "page_type": target.expected_page_type or page_scope.get("page_type", ""),
            "primary_goal": page_scope.get("primary_goal", ""),
            "key_modules": target.key_modules or page_scope.get("key_modules", []),
            "critical_user_flows": target.critical_user_flows or page_scope.get("critical_user_flows", []),
            "priority_areas": page_scope.get("priority_areas", []),
            "risks": page_scope.get("risks", []),
            "scope_summary": page_scope.get("scope_summary", ""),
            "confidence": page_scope.get("confidence", 0.5),
        }
        cases.append(
            BenchmarkCase(
                name=target.name,
                page_info=page_info,
                page_scope=expected_scope,
                test_cases=scenarios,
                base_url=target.url,
            )
        )
        live_targets.append(
            {
                "name": target.name,
                "url": target.url,
                "expected_page_type": target.expected_page_type,
                "observed_page_type": page_scope.get("page_type", ""),
                "run_dir": project_info.get("run_dir", ""),
            }
        )

    summary = run_benchmark_suite(cases, output_dir=output_dir)
    summary["suite_name"] = suite["suite_name"]
    summary["live_targets"] = live_targets
    if output_dir:
        save_json_artifact(summary, Path(output_dir) / "Real_Site_Benchmark_Report.json")
    return summary


def _grounding_metrics(valid_plan: dict) -> dict:
    plans = list(valid_plan.get("plans", []))
    total_items = 0
    grounded_items = 0
    weak_items = []
    fact_coverages = []
    conservative_plans = 0
    low_alignment_plans = 0
    for plan in plans:
        plan_id = str(plan.get("id", "")).strip()
        if str(plan.get("planning_mode", "")).strip().lower() == "conservative":
            conservative_plans += 1
        if float(plan.get("scenario_alignment", {}).get("score", 0.0) or 0.0) < 0.4:
            low_alignment_plans += 1
        fact_coverages.append(float(plan.get("grounding_summary", {}).get("scenario_fact_coverage_score", 0.0) or 0.0))
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
    return {
        "coverage": round(coverage, 2),
        "weak_items": weak_items[:20],
        "average_fact_coverage": _average(fact_coverages),
        "conservative_plan_rate": round(conservative_plans / max(len(plans), 1), 2) if plans else 0.0,
        "low_alignment_plan_rate": round(low_alignment_plans / max(len(plans), 1), 2) if plans else 0.0,
    }


def _concrete_target_density(page_model: dict) -> float:
    field_count = len(page_model.get("field_catalog", []))
    component_count = len(page_model.get("component_catalog", []))
    section_count = len(page_model.get("section_graph", {}).get("nodes", []))
    total = field_count + component_count + section_count
    return round(total / max(1, section_count + 1), 2)


def _anti_hallucination_metrics(
    test_cases: list[dict],
    scenario_validation: dict,
    contradiction_report: dict,
) -> dict:
    total_cases = max(len(test_cases), 1)
    rejected_cases = len(scenario_validation.get("rejected_cases", []))
    false_positive_case_rate = rejected_cases / total_cases
    valid_cases = scenario_validation.get("valid_cases", [])
    weak_grounding_cases = sum(
        1
        for item in valid_cases
        if float(item.get("_grounding", {}).get("coverage_score", 0.0) or 0.0) < 0.5
    )
    weak_grounding_case_rate = weak_grounding_cases / max(len(valid_cases), 1) if valid_cases else 0.0
    contradiction_count = float(contradiction_report.get("summary", {}).get("contradiction_count", 0) or 0.0)
    contradiction_penalty = min(1.0, contradiction_count / max(total_cases, 1))
    score = 1.0 - min(
        1.0,
        (false_positive_case_rate * 0.45)
        + (weak_grounding_case_rate * 0.3)
        + (contradiction_penalty * 0.25),
    )
    return {
        "false_positive_case_rate": round(false_positive_case_rate, 2),
        "weak_grounding_case_rate": round(weak_grounding_case_rate, 2),
        "score": round(max(0.0, score), 2),
    }


def _benchmark_safety_index(
    anti_hallu_score: float,
    contradiction_count: int,
    conservative_plan_rate: float,
    low_alignment_plan_rate: float,
    false_positive_case_rate: float,
    weak_grounding_case_rate: float,
) -> dict:
    score = float(anti_hallu_score)
    score -= min(0.3, contradiction_count * 0.08)
    score -= min(0.18, conservative_plan_rate * 0.25)
    score -= min(0.2, low_alignment_plan_rate * 0.28)
    score -= min(0.2, false_positive_case_rate * 0.35)
    score -= min(0.15, weak_grounding_case_rate * 0.3)
    score = max(0.0, min(1.0, score))

    reasons = []
    if contradiction_count > 0:
        reasons.append(f"{contradiction_count} contradiction signal")
    if low_alignment_plan_rate >= 0.3:
        reasons.append("low alignment plan rate tinggi")
    if false_positive_case_rate >= 0.2:
        reasons.append("false positive scenario rate tinggi")
    if weak_grounding_case_rate >= 0.25:
        reasons.append("weak grounding case rate tinggi")
    if conservative_plan_rate >= 0.4:
        reasons.append("conservative plan rate tinggi")

    status = "safe"
    if score < 0.75 or reasons:
        status = "warning"
    if score < 0.55:
        status = "critical"
    return {
        "index": int(round(score * 100)),
        "status": status,
        "reasons": reasons[:5],
    }


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
