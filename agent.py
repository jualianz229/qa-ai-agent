"""
QA AI Agent - Page Scope Analyzer and Test Scenario Generator
1. Input URL
2. Optional Session Auth
3. Optional Instructions
4. Scan Page
5. Analyze Page Scope
6. Generate Test Scenarios
"""

import argparse
import json
import os
import sys
from pathlib import Path
from urllib.parse import urlparse

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from dotenv import load_dotenv
from rich import box
from rich.console import Console
from rich.panel import Panel
from rich.rule import Rule

from core.ai_engine import AIEngine
from core.artifacts import (
    confidence_analysis_path,
    execution_debug_path,
    execution_learning_path,
    execution_results_path,
    flaky_analysis_path,
    json_artifact_path,
    scenario_contract_validation_path,
    token_usage_path,
    contradiction_analysis_path,
    anti_hallucination_audit_path,
    drift_analysis_path,
    execution_replay_verification_path,
    policy_pack_report_path,
)

# Moved to modules.test_case_generator
from modules.test_case_generator.src.case_memory import merge_case_memory
from modules.test_case_generator.src.planner import build_execution_plan, build_normalized_page_model, save_json_artifact
from modules.test_case_generator.src.scenario_contract import validate_scenario_contract

# Moved to modules.end_to_end_automation
from modules.end_to_end_automation.src.executor import CodeGenerator
from modules.end_to_end_automation.src.flaky_bank import merge_flaky_history
from modules.end_to_end_automation.src.replay_verifier import verify_plan_execution_consistency

# Moved to modules.visual_regression_testing
from modules.visual_regression_testing.src.drift_detector import detect_run_drift

from core.confidence import build_historical_confidence_signal, compute_composite_confidence
from core.contradictions import analyze_cross_stage_contradictions
from core.guardrails import validate_execution_plan
from core.policy_pack import run_anti_hallucination_policy_pack
from core.result_analyzer import analyze_execution_results, save_execution_summary
from core.run_context import merge_recrawl_project_info
from core.scanner import Scanner
from core.safety_gates import build_execution_gate_decision
from core.self_critique import refine_execution_plan_with_self_critique
from core.site_profiles import enrich_site_profile_with_clusters, load_site_profile, merge_execution_learning

from core.config import INSTRUCTIONS_DIR, AUTH_DIR, RESULT_DIR
load_dotenv()

console = Console(force_terminal=True)
runner = Scanner()


def _load_first_matching_json(directory: Path, pattern: str) -> dict:
    if not directory.exists():
        return {}
    file_path = next(directory.glob(pattern), None)
    if not file_path:
        return {}
    try:
        return json.loads(file_path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def load_run_context(run_dir: str | Path, csv_sep: str = ",") -> dict:
    """Load project_info, page_info, page_scope, page_model, parsed_data, and validations from an existing run directory."""
    run_path = Path(run_dir)
    if not run_path.is_dir():
        return {}
    json_dir = run_path / "JSON"
    raw_scan = _load_first_matching_json(json_dir, "raw_scan_*.json")
    page_scope = _load_first_matching_json(json_dir, "Page_Scope_*.json")
    page_model = _load_first_matching_json(json_dir, "Normalized_Page_Model_*.json")
    site_profile = _load_first_matching_json(json_dir, "Site_Profile_*.json")
    scope_validation = _load_first_matching_json(json_dir, "Page_Scope_Validation_*.json")
    scenario_validation = _load_first_matching_json(json_dir, "Scenario_Validation_*.json")
    execution_plan = _load_first_matching_json(json_dir, "Execution_Plan_*.json")
    execution_plan_validation = _load_first_matching_json(json_dir, "Execution_Plan_Validation_*.json")

    safe_name = "unknown"
    timestamp = ""
    if json_dir.exists():
        for f in json_dir.glob("raw_scan_*.json"):
            stem = f.stem
            if stem.startswith("raw_scan_"):
                parts = stem[len("raw_scan_"):].rsplit("_", 2)
                if len(parts) >= 2:
                    safe_name = parts[0]
                    timestamp = "_".join(parts[1:])
                break

    url = raw_scan.get("url", "")
    domain = urlparse(url).netloc.replace("www.", "") if url else "unknown"
    title = page_scope.get("page_type") or raw_scan.get("title") or domain

    project_info = {
        "title": title,
        "domain": domain,
        "project_name": f"{domain} {str(title)[:40]}".strip(),
        "run_dir": str(run_path),
        "timestamp": timestamp,
        "safe_name": safe_name,
        "site_profile": site_profile or load_site_profile(url),
    }

    csv_path = next(run_path.glob("*.csv"), None)
    parsed_data = []
    if csv_path:
        import csv as csv_module
        with csv_path.open("r", encoding="utf-8-sig", newline="") as handle:
            parsed_data = list(csv_module.DictReader(handle))

    return {
        "project_info": project_info,
        "page_info": raw_scan,
        "page_scope": page_scope,
        "page_model": page_model,
        "parsed_data": parsed_data,
        "scenario_validation": scenario_validation,
        "scope_validation": scope_validation,
        "execution_plan": execution_plan,
        "execution_plan_validation": execution_plan_validation,
        "csv_path": csv_path,
        "csv_sep": csv_sep,
    }


def _job_step(step_id: int, message: str) -> None:
    """Emit a step line for Live Jobs UI (GitHub Actions-style). Parsed by frontend."""
    print(f"[STEP] {step_id} | {message}", flush=True)


def _resolve_run_dir(run_name_or_path: str | Path) -> Path:
    """Resolve run dir from absolute path or a folder name under Result/."""
    p = Path(run_name_or_path)
    if p.is_absolute():
        return p
    return RESULT_DIR / str(run_name_or_path)


def run_feature_case_generator(
    *,
    url: str | None,
    input_run: str | Path | None,
    instruction: str,
    csv_sep: str,
    use_auth: bool,
    crawl_limit: int,
    adaptive_recrawl: bool,
    run_name: str | None,
) -> None:
    """Case generator feature: scan/scope (if needed) -> generate scenarios (CSV) -> stop."""
    engine = AIEngine()
    engine.last_scope_validation = {}
    engine.last_scenario_validation = {}
    engine.reset_usage()

    if input_run:
        run_dir = _resolve_run_dir(input_run)
        ctx = load_run_context(run_dir, csv_sep)
        if not ctx.get("project_info") or not ctx.get("page_info"):
            console.print(f"[red]Run dir tidak valid / belum ada hasil scan: {run_dir}[/red]")
            return
        project_info = ctx["project_info"]
        page_info = ctx["page_info"]
        page_scope = ctx.get("page_scope") or {}
        page_model = ctx.get("page_model") or {}
        url = page_info.get("url", "") or (url or "")

        if not page_model:
            page_model = build_normalized_page_model(page_info)
            save_json_artifact(
                page_model,
                json_artifact_path(
                    project_info["run_dir"],
                    f"Normalized_Page_Model_{project_info['safe_name']}_{project_info['timestamp']}.json",
                ),
            )
        if not page_scope:
            page_scope = analyze_page_scope_with_retry(
                url=url,
                project_info=project_info,
                page_info=page_info,
                instruction=instruction,
                use_auth=False,
                crawl_limit=0,
                adaptive_recrawl=False,
                engine=engine,
            )
            runner.save_page_scope(page_scope, project_info)
    else:
        if not url:
            console.print("[red]Case generator butuh --url atau --input-run.[/red]")
            return
        if not url.startswith(("http://", "https://")):
            url = "https://" + url
        site_profile = load_site_profile(url)

        console.print(Rule(f"[bold]Case Generator: {url}[/bold]"))
        _job_step(1, "Scanning page")
        with console.status("[bold yellow]Scanning page...[/bold yellow]", spinner="line"):
            project_info, page_info, _ = runner.scan_website(
                url,
                use_auth,
                crawl_limit=crawl_limit,
                site_profile=site_profile,
                run_name=run_name,
            )
        _job_step(1, "done")

        _job_step(2, "Scope analysis")
        page_scope = analyze_page_scope_with_retry(
            url=url,
            project_info=project_info,
            page_info=page_info,
            instruction=instruction,
            use_auth=use_auth,
            crawl_limit=crawl_limit,
            adaptive_recrawl=adaptive_recrawl,
            engine=engine,
        )
        _log_usage(engine, "Scope Analysis")
        page_model = build_normalized_page_model(page_info)
        site_profile = enrich_site_profile_with_clusters(site_profile, page_model=page_model, page_scope=page_scope)
        page_info["site_profile"] = site_profile
        page_model["site_profile"] = site_profile
        save_json_artifact(
            page_model,
            json_artifact_path(
                project_info["run_dir"],
                f"Normalized_Page_Model_{project_info['safe_name']}_{project_info['timestamp']}.json",
            ),
        )
        save_json_artifact(
            site_profile,
            json_artifact_path(
                project_info["run_dir"],
                f"Site_Profile_{project_info['safe_name']}_{project_info['timestamp']}.json",
            ),
        )
        if engine.last_scope_validation:
            save_json_artifact(
                {
                    "issues": engine.last_scope_validation.get("issues", []),
                    "allowed_vocabulary": engine.last_scope_validation.get("allowed_vocabulary", {}),
                    "task_contract": engine.last_scope_validation.get("task_contract", {}),
                    "unsupported_surface_report": engine.last_scope_validation.get("unsupported_surface_report", {}),
                    "routing": engine.last_scope_validation.get("routing", {}),
                    "fact_pack_summary": engine.last_scope_validation.get("fact_pack_summary", {}),
                    "historical_signal": engine.last_scope_validation.get("historical_signal", {}),
                    "composite_confidence": engine.last_scope_validation.get("composite_confidence", {}),
                },
                json_artifact_path(
                    project_info["run_dir"],
                    f"Page_Scope_Validation_{project_info['safe_name']}_{project_info['timestamp']}.json",
                ),
            )

        runner.save_page_scope(page_scope, project_info)
        runner.save_crawled_pages(page_info, project_info)
        runner.save_raw_scan(page_info, project_info)
        runner.save_visual_regression_artifacts(page_info, project_info)
        _job_step(2, "done")

    _job_step(3, "Generate test scenarios")
    console.print(Rule(f"[bold]Generate Test Scenarios (CSV): {url}[/bold]"))
    with console.status("[bold green]AI generating scenarios...[/bold green]", spinner="line"):
        parsed_data = engine.generate_test_scenarios(
            url=url,
            website_title=project_info["title"],
            page_info=page_info,
            page_model=page_model,
            page_scope=page_scope,
            custom_instruction=instruction,
            csv_sep=csv_sep,
        )
    _log_usage(engine, "Scenario Generation")
    parsed_data = _coerce_non_empty_scenarios(
        parsed_data, engine, url, page_info, page_model, page_scope, instruction
    )

    scenario_contract = validate_scenario_contract(
        cases=parsed_data,
        page_scope=page_scope,
        page_model=page_model,
        page_info=page_info,
    )
    save_json_artifact(scenario_contract, scenario_contract_validation_path(project_info["run_dir"]))
    if not scenario_contract.get("is_valid", False):
        salvage_cases = list(scenario_contract.get("valid_cases", []) or [])
        if salvage_cases:
            parsed_data = salvage_cases
        else:
            parsed_data = _build_fallback_scenarios(engine, url, page_info, page_model, page_scope, instruction)
            scenario_contract = validate_scenario_contract(
                cases=parsed_data,
                page_scope=page_scope,
                page_model=page_model,
                page_info=page_info,
            )
            save_json_artifact(scenario_contract, scenario_contract_validation_path(project_info["run_dir"]))
    _job_step(3, "done")

    _job_step(4, "Save CSV and artifacts")
    saved_csv_path = runner.save_csv_scenarios(parsed_data, project_info, csv_sep)
    case_memory_info = merge_case_memory(url, parsed_data, page_scope, page_model)
    if case_memory_info:
        save_json_artifact(
            case_memory_info,
            json_artifact_path(
                project_info["run_dir"],
                f"Case_Memory_{project_info['safe_name']}_{project_info['timestamp']}.json",
            ),
        )

    console.print(f"  [green][OK][/green] CSV created: [dim]{saved_csv_path}[/dim]")
    
    _job_step(5, "Membangun Execution Plan (Robot Logic)")
    plan_data = build_execution_plan(parsed_data, page_model, url, site_profile=project_info.get("site_profile", {}))
    
    # Self-critique the plan for higher reliability
    refined_plan_data, self_critique_report = refine_execution_plan_with_self_critique(
        execution_plan=plan_data,
        page_model=page_model
    )
    
    plan_path = save_json_artifact(
        refined_plan_data,
        json_artifact_path(
            project_info["run_dir"],
            f"Execution_Plan_{project_info['safe_name']}_{project_info['timestamp']}.json",
        ),
    )
    
    validate_plan = validate_execution_plan(
        execution_plan=refined_plan_data,
        page_model=page_model,
        page_info=page_info,
    )
    save_json_artifact(validate_plan, json_artifact_path(project_info["run_dir"], f"Execution_Plan_Validation_{project_info['safe_name']}_{project_info['timestamp']}.json"))
    
    console.print(f"  [green][OK][/green] Execution plan built: [dim]{plan_path}[/dim]")
    
    _job_step(5, "done")

    usage_path = save_json_artifact(engine.usage_snapshot(), token_usage_path(Path(project_info["run_dir"])))
    console.print(f"  [green][OK][/green] Token usage saved: [dim]{usage_path}[/dim]")
    _job_step(0, "Complete job")
    _job_step(0, "done")
    console.print(f"[bold green][FINISHED] Case Generator done. Hasil: {project_info['run_dir']}[/bold green]")


def run_feature_e2e_automation(*, input_run: str | Path, executor_headless: bool, csv_sep: str) -> None:
    """E2E automation feature: generate executor script -> run it -> store results (no test generation)."""
    run_dir = _resolve_run_dir(input_run)
    ctx = load_run_context(run_dir, csv_sep)
    if not ctx.get("project_info"):
        console.print(f"[red]Run dir tidak valid: {run_dir}[/red]")
        return
    project_info = ctx["project_info"]

    execution_plan_path = next(Path(project_info["run_dir"]).glob("JSON/Execution_Plan_*.json"), None)
    if not execution_plan_path:
        console.print("[red]Execution plan tidak ditemukan (JSON/Execution_Plan_*.json).[/red]")
        console.print("[dim]Jalankan mode full atau buat execution plan dulu.[/dim]")
        return

    _job_step(1, "Generate executor script")
    engine = AIEngine()
    executor = CodeGenerator(engine)
    pom_script = executor.generate_pom_script(project_info, execution_plan_path, headless=executor_headless)
    if not pom_script:
        console.print("[red]Gagal membuat script executor.[/red]")
        _job_step(1, "fail")
        return
    _job_step(1, "done")

    console.print(f"[green][OK][/green] Script dibuat: [dim]{pom_script}[/dim]")
    _job_step(2, "Run Playwright tests")
    import subprocess
    try:
        subprocess.run([sys.executable, pom_script.name], cwd=pom_script.parent, check=True)
    except subprocess.CalledProcessError as exc:
        console.print(f"[bold red][ERR] Executor gagal: {exc}[/bold red]")
        _job_step(2, "fail")
        return
    _job_step(2, "done")

    _job_step(3, "Save execution results")
    run_path = Path(project_info["run_dir"])
    results_path = execution_results_path(run_path, create=False)
    if results_path.exists():
        csv_path = next(run_path.glob("*.csv"), None)
        if csv_path:
            runner.update_csv_with_execution_results(csv_path, results_path, csv_sep)
        summary = analyze_execution_results(results_path)
        save_execution_summary(results_path, summary)
        console.print(f"[green][OK][/green] Execution results saved: [dim]{results_path}[/dim]")
    _job_step(3, "done")
    _job_step(0, "Complete job")
    _job_step(0, "done")
    console.print(f"[bold green][FINISHED] E2E Automation done. Hasil: {project_info['run_dir']}[/bold green]")


def run_feature_visual_regression(*, url: str, use_auth: bool, run_name: str | None) -> None:
    """Visual regression feature: scan page -> save Visual_Baseline/Visual_Diff -> stop."""
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    site_profile = load_site_profile(url)
    console.print(Rule(f"[bold]Visual Regression: {url}[/bold]"))
    _job_step(1, "Capture Visual Snapshot")
    with console.status("[bold yellow]Capturing visual snapshot...[/bold yellow]", spinner="line"):
        project_info, page_info, _ = runner.scan_website(
            url, use_auth, crawl_limit=1, site_profile=site_profile, run_name=run_name
        )
    _job_step(1, "done")
    _job_step(2, "Save Baseline and Artifacts")
    runner.save_raw_scan(page_info, project_info)
    baseline_path, diff_path = runner.save_visual_regression_artifacts(page_info, project_info)
    _job_step(2, "done")
    _job_step(0, "Complete job")
    _job_step(0, "done")
    console.print(f"  [green][OK][/green] Baseline: [dim]{baseline_path}[/dim]")
    console.print(f"  [green][OK][/green] Diff: [dim]{diff_path}[/dim]")
    console.print(f"[bold green][FINISHED] Visual Regression done. Hasil: {project_info['run_dir']}[/bold green]")


INSTRUCTIONS_DIR.mkdir(parents=True, exist_ok=True)
AUTH_DIR.mkdir(parents=True, exist_ok=True)
sample_auth_file = AUTH_DIR / "sample_auth_state.json"
if not sample_auth_file.exists():
    sample_auth_file.write_text(
        '{ "cookies": [ { "name": "session_id", "value": "xxxx", "domain": ".namadomain.com", "path": "/" } ] }',
        encoding="utf-8",
    )


def _minimal_fallback_case(url: str) -> dict:
    return {
        "ID": "GEN-001",
        "Module": "General",
        "Category": "Functional",
        "Test Type": "Positive",
        "Risk Rating": "Medium",
        "Anchored Selector": "",
        "Title": "Validate primary page loads and key content is visible",
        "Precondition": "",
        "Steps to Reproduce": f"1. Open the site {url}\n2. Observe the main heading and primary controls.",
        "Expected Result": "The page loads successfully and key user-facing elements are visible without errors.",
        "Actual Result": "",
        "Severity": "Medium",
        "Priority": "P2",
        "Evidence": "",
        "Automation": "auto",
    }


def _build_fallback_scenarios(
    engine: AIEngine,
    url: str,
    page_info: dict,
    page_model: dict,
    page_scope: dict,
    instruction: str,
) -> list[dict]:
    try:
        scenario_volume = engine._derive_scenario_volume(page_model, page_scope, {}, instruction)  # noqa: SLF001
        target_count = max(12, min(80, int(scenario_volume.get("target_count", 24) or 24)))
        cases = engine._heuristic_scenarios_from_facts(  # noqa: SLF001
            url=url,
            page_info=page_info,
            page_model=page_model,
            page_scope=page_scope,
            custom_instruction=instruction,
            target_count=target_count,
        )
        if cases:
            return cases
    except Exception:
        pass
    return [_minimal_fallback_case(url)]


def _log_usage(engine, label="AI"):
    usage = engine.usage_snapshot().get("summary", {})
    tokens = usage.get("estimated_total_tokens", 0)
    console.print(f"  [dim]Token used: {tokens}[/dim]")

def _coerce_non_empty_scenarios(
    cases: list[dict] | None,
    engine: AIEngine,
    url: str,
    page_info: dict,
    page_model: dict,
    page_scope: dict,
    instruction: str,
) -> list[dict]:
    normalized = [item for item in (cases or []) if isinstance(item, dict)]
    if normalized:
        return normalized
    return _build_fallback_scenarios(engine, url, page_info, page_model, page_scope, instruction)


def process_single_url(
    url: str,
    instruction: str,
    csv_sep: str,
    use_auth: bool,
    crawl_limit: int,
    adaptive_recrawl: bool,
    execute_now: bool | None = None,
    executor_headless: bool = True,
    run_name: str | None = None,
):
    engine = AIEngine()
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    site_profile = load_site_profile(url)

    engine.last_scope_validation = {}
    engine.last_scenario_validation = {}
    engine.reset_usage()

    console.print(Rule(f"[bold]Scan & Analysis: {url}[/bold]"))
    with console.status(
        f"[bold yellow]AI checking page...[/bold yellow]",
        spinner="line",
    ):
        project_info, page_info, _ = runner.scan_website(
            url,
            use_auth,
            crawl_limit=crawl_limit,
            site_profile=site_profile,
            run_name=run_name,
        )

    try:
        page_scope = analyze_page_scope_with_retry(
            url=url,
            project_info=project_info,
            page_info=page_info,
            instruction=instruction,
            use_auth=use_auth,
            crawl_limit=crawl_limit,
            adaptive_recrawl=adaptive_recrawl,
            engine=engine,
        )
        _log_usage(engine, "Scope Analysis")
        page_model = build_normalized_page_model(page_info)
        site_profile = enrich_site_profile_with_clusters(site_profile, page_model=page_model, page_scope=page_scope)
        page_info["site_profile"] = site_profile
        page_model["site_profile"] = site_profile
        page_model_path = save_json_artifact(
            page_model,
            json_artifact_path(
                project_info["run_dir"],
                f"Normalized_Page_Model_{project_info['safe_name']}_{project_info['timestamp']}.json",
            ),
        )
        site_profile_path = save_json_artifact(
            site_profile,
            json_artifact_path(
                project_info["run_dir"],
                f"Site_Profile_{project_info['safe_name']}_{project_info['timestamp']}.json",
            ),
        )
        if engine.last_scope_validation:
            scope_validation_path = save_json_artifact(
                {
                    "issues": engine.last_scope_validation.get("issues", []),
                    "allowed_vocabulary": engine.last_scope_validation.get("allowed_vocabulary", {}),
                    "task_contract": engine.last_scope_validation.get("task_contract", {}),
                    "unsupported_surface_report": engine.last_scope_validation.get("unsupported_surface_report", {}),
                    "routing": engine.last_scope_validation.get("routing", {}),
                    "fact_pack_summary": engine.last_scope_validation.get("fact_pack_summary", {}),
                    "historical_signal": engine.last_scope_validation.get("historical_signal", {}),
                    "composite_confidence": engine.last_scope_validation.get("composite_confidence", {}),
                },
                json_artifact_path(
                    project_info["run_dir"],
                    f"Page_Scope_Validation_{project_info['safe_name']}_{project_info['timestamp']}.json",
                ),
            )
        else:
            scope_validation_path = None

        console.print(f"  [green][OK][/green] Page type: [bold]{page_scope.get('page_type', '-') }[/bold]")
        console.print(f"  [dim]Primary goal: {page_scope.get('primary_goal', '-') }[/dim]")
        console.print(f"  [dim]Confidence: {page_scope.get('confidence', 0)}[/dim]")
        if page_scope.get("key_modules"):
            console.print(f"  [dim]Key modules: {', '.join(page_scope['key_modules'][:6])}[/dim]")
        if page_scope.get("scope_summary"):
            console.print(f"  [dim]Scope summary: {page_scope['scope_summary']}[/dim]")
        if page_info.get("crawled_pages"):
            console.print(f"  [dim]Linked pages sampled: {len(page_info['crawled_pages'])}[/dim]")
            for item in page_info["crawled_pages"][:5]:
                console.print(f"  [dim]- {item.get('url', '')}[/dim]")
        scope_json_path = runner.save_page_scope(page_scope, project_info)
        crawled_json_path = runner.save_crawled_pages(page_info, project_info)
        raw_scan_json_path = runner.save_raw_scan(page_info, project_info)
        visual_baseline_json_path, visual_diff_json_path = runner.save_visual_regression_artifacts(page_info, project_info)
        console.print(f"  [green][OK][/green] Scope analysis disimpan: [dim]{scope_json_path}[/dim]")
        console.print(f"  [green][OK][/green] Daftar linked pages disimpan: [dim]{crawled_json_path}[/dim]")
        console.print(f"  [green][OK][/green] Raw scan disimpan: [dim]{raw_scan_json_path}[/dim]")
        console.print(f"  [green][OK][/green] Baseline (Visual) disimpan: [dim]{visual_baseline_json_path}[/dim]")
        console.print(f"  [green][OK][/green] Visual Diff disimpan: [dim]{visual_diff_json_path}[/dim]")
        console.print(f"  [green][OK][/green] Normalized page model disimpan: [dim]{page_model_path}[/dim]")
        console.print(f"  [green][OK][/green] Site profile disimpan: [dim]{site_profile_path}[/dim]")
        if scope_validation_path:
            console.print(f"  [green][OK][/green] Scope validation saved: [dim]{scope_validation_path}[/dim]")

        console.print(Rule(f"[bold]Generate Test Scenarios (CSV): {url}[/bold]"))
        with console.status(
            f"[bold green]AI generating scenarios...[/bold green]",
            spinner="line",
        ):
            parsed_data = engine.generate_test_scenarios(
                url=url,
                website_title=project_info["title"],
                page_info=page_info,
                page_model=page_model,
                page_scope=page_scope,
                custom_instruction=instruction,
                csv_sep=csv_sep,
            )
            _log_usage(engine, "Scenario Generation")
            parsed_data = _coerce_non_empty_scenarios(
                parsed_data,
                engine,
                url,
                page_info,
                page_model,
                page_scope,
                instruction,
            )
            scenario_contract = validate_scenario_contract(
                cases=parsed_data,
                page_scope=page_scope,
                page_model=page_model,
                page_info=page_info,
            )
            scenario_contract_path = save_json_artifact(
                scenario_contract,
                scenario_contract_validation_path(project_info["run_dir"]),
            )
            if not scenario_contract.get("is_valid", False):
                salvage_cases = list(scenario_contract.get("valid_cases", []) or [])
                if salvage_cases:
                    parsed_data = salvage_cases
                    console.print(
                        "  [yellow][i][/yellow] Scenario contract found blocking issues; "
                        "continuing with validated cases only."
                    )
                else:
                    parsed_data = _build_fallback_scenarios(
                        engine,
                        url,
                        page_info,
                        page_model,
                        page_scope,
                        instruction,
                    )
                    console.print(
                        "  [yellow][i][/yellow] Scenario contract blocked all AI cases; "
                        "using grounded fallback scenarios to keep output non-empty."
                    )
                scenario_contract = validate_scenario_contract(
                    cases=parsed_data,
                    page_scope=page_scope,
                    page_model=page_model,
                    page_info=page_info,
                )
                scenario_contract_path = save_json_artifact(
                    scenario_contract,
                    scenario_contract_validation_path(project_info["run_dir"]),
                )
            saved_csv_path = runner.save_csv_scenarios(parsed_data, project_info, csv_sep)
            console.print(f"  [green][OK][/green] CSV created: [dim]{saved_csv_path}[/dim]")
            console.print(f"  [green][OK][/green] Scenario contract validation saved: [dim]{scenario_contract_path}[/dim]")
            if engine.last_scenario_validation:
                scenario_validation_path = save_json_artifact(
                {
                    "issues": engine.last_scenario_validation.get("issues", []),
                    "valid_cases": engine.last_scenario_validation.get("valid_cases", []),
                    "rejected_cases": engine.last_scenario_validation.get("rejected_cases", []),
                    "allowed_vocabulary": engine.last_scenario_validation.get("allowed_vocabulary", {}),
                    "task_contract": engine.last_scenario_validation.get("task_contract", {}),
                    "grounding_summary": engine.last_scenario_validation.get("grounding_summary", {}),
                    "unsupported_surface_report": engine.last_scenario_validation.get("unsupported_surface_report", {}),
                    "quality_flags": engine.last_scenario_validation.get("quality_flags", {}),
                    "routing": engine.last_scenario_validation.get("routing", {}),
                    "fact_pack_summary": engine.last_scenario_validation.get("fact_pack_summary", {}),
                    "historical_signal": engine.last_scenario_validation.get("historical_signal", {}),
                    "feedback_learning_signal": engine.last_scenario_validation.get("feedback_learning_signal", {}),
                    "self_critique": engine.last_scenario_validation.get("self_critique", {}),
                    "ai_quality": engine.last_scenario_validation.get("ai_quality", {}),
                    "composite_confidence": engine.last_scenario_validation.get("composite_confidence", {}),
                },
                json_artifact_path(
                    project_info["run_dir"],
                    f"Scenario_Validation_{project_info['safe_name']}_{project_info['timestamp']}.json",
                ),
                )
                console.print(f"  [green][OK][/green] Scenario validation saved: [dim]{scenario_validation_path}[/dim]")
            case_memory_info = merge_case_memory(url, parsed_data, page_scope, page_model)
            if case_memory_info:
                case_memory_path = save_json_artifact(
                    case_memory_info,
                    json_artifact_path(
                        project_info["run_dir"],
                        f"Case_Memory_{project_info['safe_name']}_{project_info['timestamp']}.json",
                    ),
                )
                console.print(f"  [green][OK][/green] Case memory updated: [dim]{case_memory_path}[/dim]")

            execution_plan = build_execution_plan(parsed_data, page_model, page_info.get("url", url), site_profile=site_profile)
            execution_plan_validation = validate_execution_plan(execution_plan, page_model, page_info)
            validation_path = save_json_artifact(
                {
                    "issues": execution_plan_validation["issues"],
                    "rejected_plans": execution_plan_validation["rejected_plans"],
                    "valid_plan_count": len(execution_plan_validation["valid_plan"].get("plans", [])),
                },
                json_artifact_path(
                    project_info["run_dir"],
                    f"Execution_Plan_Validation_{project_info['safe_name']}_{project_info['timestamp']}.json",
                ),
            )
            execution_plan = execution_plan_validation["valid_plan"]
            execution_plan, self_critique_report = refine_execution_plan_with_self_critique(execution_plan, page_model)
            execution_plan_validation = validate_execution_plan(execution_plan, page_model, page_info)
            validation_path = save_json_artifact(
                {
                    "issues": execution_plan_validation["issues"],
                    "rejected_plans": execution_plan_validation["rejected_plans"],
                    "valid_plan_count": len(execution_plan_validation["valid_plan"].get("plans", [])),
                    "self_critique": self_critique_report,
                },
                json_artifact_path(
                    project_info["run_dir"],
                    f"Execution_Plan_Validation_{project_info['safe_name']}_{project_info['timestamp']}.json",
                ),
            )
            if not execution_plan.get("plans"):
                console.print(
                    "  [yellow][i][/yellow] All execution plans were rejected by guardrails. "
                    "CSV remains available, automation step is skipped for this run."
                )
            confidence_scope = dict(page_scope)
            if "ai_confidence" in confidence_scope:
                confidence_scope["confidence"] = confidence_scope.get("ai_confidence", confidence_scope.get("confidence", 0))
            historical_signal = build_historical_confidence_signal(
                url=url,
                page_model=page_model,
                page_scope=page_scope,
                site_profile=site_profile,
            )
            contradiction_report = analyze_cross_stage_contradictions(
                page_scope=page_scope,
                test_cases=parsed_data,
                execution_plan=execution_plan,
                page_model=page_model,
                page_info=page_info,
                scenario_validation=engine.last_scenario_validation,
                execution_plan_validation=execution_plan_validation,
            )
            composite_confidence = compute_composite_confidence(
                page_scope=confidence_scope,
                page_info=page_info,
                page_model=page_model,
                scope_validation=engine.last_scope_validation,
                scenario_validation=engine.last_scenario_validation,
                execution_plan_validation=execution_plan_validation,
                historical_signal=historical_signal,
                contradiction_analysis=contradiction_report,
            )
            page_scope["confidence"] = composite_confidence["score"]
            page_scope["confidence_breakdown"] = composite_confidence["breakdown"]
            page_scope["confidence_explanation"] = composite_confidence["explanation"]
            page_scope["confidence_class"] = composite_confidence["confidence_class"]
            runner.save_page_scope(page_scope, project_info)
            confidence_path = save_json_artifact(
                {
                    "url": url,
                    "page_type": page_scope.get("page_type", ""),
                    "confidence": composite_confidence["score"],
                    "confidence_score": int(round(composite_confidence["score"] * 100)),
                    "confidence_class": composite_confidence["confidence_class"],
                    "explanation": composite_confidence["explanation"],
                    "breakdown": composite_confidence["breakdown"],
                    "historical_signal": historical_signal,
                },
                confidence_analysis_path(project_info["run_dir"]),
            )
            execution_plan_path = save_json_artifact(
                execution_plan,
                json_artifact_path(
                    project_info["run_dir"],
                    f"Execution_Plan_{project_info['safe_name']}_{project_info['timestamp']}.json",
                ),
            )
            contradiction_path = save_json_artifact(
                contradiction_report,
                contradiction_analysis_path(project_info["run_dir"]),
            )
            anti_hallucination_audit = {
                "url": url,
                "run_name": Path(project_info["run_dir"]).name,
                "page_type": page_scope.get("page_type", ""),
                "routing": {
                    "scope": engine.last_scope_validation.get("routing", {}),
                    "scenario": engine.last_scenario_validation.get("routing", {}),
                },
                "fact_pack_summary": {
                    "scope": engine.last_scope_validation.get("fact_pack_summary", {}),
                    "scenario": engine.last_scenario_validation.get("fact_pack_summary", {}),
                },
                "guardrails": {
                    "scope_issue_count": len(engine.last_scope_validation.get("issues", [])),
                    "scenario_rejection_count": len(engine.last_scenario_validation.get("rejected_cases", [])),
                    "plan_rejection_count": len(execution_plan_validation.get("rejected_plans", [])),
                    "contradiction_count": contradiction_report.get("summary", {}).get("contradiction_count", 0),
                },
                "unsupported_surface_report": {
                    "scope": engine.last_scope_validation.get("unsupported_surface_report", {}),
                    "scenario": engine.last_scenario_validation.get("unsupported_surface_report", {}),
                },
                "confidence": {
                    "score": composite_confidence.get("score", 0),
                    "class": composite_confidence.get("confidence_class", ""),
                    "anti_hallucination": composite_confidence.get("breakdown", {}).get("anti_hallucination", 0.0),
                    "source_trust": composite_confidence.get("breakdown", {}).get("source_trust", 0.0),
                    "negative_evidence": composite_confidence.get("breakdown", {}).get("negative_evidence", 0.0),
                },
                "top_scenario_rejection_reasons": [
                    issue
                    for item in engine.last_scenario_validation.get("rejected_cases", [])[:8]
                    for issue in item.get("issues", [])[:2]
                ][:12],
                "top_contradictions": [
                    item.get("message", "")
                    for item in contradiction_report.get("issues", [])[:10]
                    if str(item.get("message", "")).strip()
                ],
                "self_critique": self_critique_report,
            }
            execution_gate = build_execution_gate_decision(
                composite_confidence=composite_confidence,
                scenario_validation=engine.last_scenario_validation,
                execution_plan_validation=execution_plan_validation,
                contradiction_report=contradiction_report,
            )
            policy_pack_report = run_anti_hallucination_policy_pack()
            anti_hallucination_audit["execution_gate"] = execution_gate
            anti_hallucination_audit["policy_pack"] = policy_pack_report
            anti_hallucination_audit_path_saved = save_json_artifact(
                anti_hallucination_audit,
                anti_hallucination_audit_path(project_info["run_dir"]),
            )
            policy_pack_path_saved = save_json_artifact(
                policy_pack_report,
                policy_pack_report_path(project_info["run_dir"]),
            )
            console.print(f"  [green][OK][/green] Execution plan created: [dim]{execution_plan_path}[/dim]")
            console.print(f"  [green][OK][/green] Plan validation saved: [dim]{validation_path}[/dim]")
            console.print(f"  [green][OK][/green] Confidence analysis saved: [dim]{confidence_path}[/dim]")
            console.print(
                f"  [green][OK][/green] Contradiction analysis saved: [dim]{contradiction_path}[/dim]"
            )
            console.print(
                f"  [green][OK][/green] Anti-hallucination audit saved: [dim]{anti_hallucination_audit_path_saved}[/dim]"
            )
            console.print(
                f"  [green][OK][/green] Policy pack report saved: [dim]{policy_pack_path_saved}[/dim]"
            )
            if contradiction_report.get("summary", {}).get("contradiction_count", 0):
                console.print(
                    "  [yellow][i][/yellow] Cross-stage contradictions: "
                    f"{contradiction_report['summary'].get('contradiction_count', 0)}"
                )
            if page_info.get("visual_render_regression", {}).get("baseline_created"):
                console.print(f"  [yellow][i][/yellow] Visual baseline created for {url}")

            console.print("  [dim][>] Generating Executive Summary...[/dim]")
            md_summary = engine.generate_executive_summary(url, project_info["title"], page_info, parsed_data)
            _log_usage(engine, "Executive Summary")
            saved_md_path = runner.save_executive_summary(md_summary, project_info)
            console.print(f"  [green][OK][/green] Executive summary created: [dim]{saved_md_path}[/dim]")
            usage_path = save_json_artifact(engine.usage_snapshot(), token_usage_path(project_info["run_dir"]))
            console.print(f"  [green][OK][/green] Token usage saved: [dim]{usage_path}[/dim]")

            executor = CodeGenerator(engine)
            pom_script = executor.generate_pom_script(project_info, execution_plan_path, headless=executor_headless)

        if pom_script:
            console.print(f"\n[cyan]  [!] Script executor successfully created at:[/cyan] [bold]{pom_script}[/bold]")
            if execute_now is None:
                console.print(
                    "\n[bold yellow]Do you want to run the executor now to collect video evidence?[/bold yellow]"
                )
                console.print("  [1] Yes, run now")
                console.print("  [2] No, save script only")
                execute_choice = console.input("  [cyan]Select [1/2] (Default: 2): [/cyan]").strip()
                should_execute = execute_choice == "1"
            else:
                should_execute = execute_now

            if should_execute:
                if execution_gate.get("blocked"):
                    console.print(
                        "[bold yellow][BLOCKED][/bold yellow] Execution blocked by anti-hallucination guard: "
                        + "; ".join(execution_gate.get("reasons", [])[:3])
                    )
                    console.print(
                        f"[dim]Set env {execution_gate.get('override_env', 'QA_AI_ALLOW_LOW_ANTI_HALLU')}=1 for manual override.[/dim]"
                    )
                    should_execute = False
                elif execution_gate.get("override_applied"):
                    console.print("[yellow][i][/yellow] Anti-hallucination guard overridden via environment variable.")

            if should_execute:
                console.print("[bold green][>] Running executor script...[/bold green]")
                import subprocess

                try:
                    subprocess.run([sys.executable, pom_script.name], cwd=pom_script.parent, check=True)
                    console.print("\n[bold green][OK] Automation execution completed.[/bold green]")
                    results_path = execution_results_path(project_info["run_dir"])
                    if results_path.exists():
                        results_payload = json.loads(results_path.read_text(encoding="utf-8"))
                        flaky_info = merge_flaky_history(
                            url,
                            results_payload,
                            page_model=page_model,
                            page_scope=page_scope,
                        )
                        if flaky_info:
                            flaky_path = save_json_artifact(flaky_info, flaky_analysis_path(project_info["run_dir"]))
                            console.print(f"[green][OK][/green] Flaky analysis saved: [dim]{flaky_path}[/dim]")
                        summary = analyze_execution_results(results_path)
                        summary_path = save_execution_summary(results_path, summary)
                        runner.update_csv_with_execution_results(saved_csv_path, results_path, csv_sep)
                        contradiction_report = analyze_cross_stage_contradictions(
                            page_scope=page_scope,
                            test_cases=parsed_data,
                            execution_plan=execution_plan,
                            page_model=page_model,
                            page_info=page_info,
                            scenario_validation=engine.last_scenario_validation,
                            execution_plan_validation=execution_plan_validation,
                            execution_results=results_payload,
                        )
                        post_execution_confidence = compute_composite_confidence(
                            page_scope=confidence_scope,
                            page_info=page_info,
                            page_model=page_model,
                            scope_validation=engine.last_scope_validation,
                            scenario_validation=engine.last_scenario_validation,
                            execution_plan_validation=execution_plan_validation,
                            execution_results=results_payload,
                            historical_signal=historical_signal,
                            contradiction_analysis=contradiction_report,
                        )
                        page_scope["confidence"] = post_execution_confidence["score"]
                        page_scope["confidence_breakdown"] = post_execution_confidence["breakdown"]
                        page_scope["confidence_explanation"] = post_execution_confidence["explanation"]
                        page_scope["confidence_class"] = post_execution_confidence["confidence_class"]
                        runner.save_page_scope(page_scope, project_info)
                        save_json_artifact(
                            {
                                "url": url,
                                "page_type": page_scope.get("page_type", ""),
                                "confidence": post_execution_confidence["score"],
                                "confidence_score": int(round(post_execution_confidence["score"] * 100)),
                                "confidence_class": post_execution_confidence["confidence_class"],
                                "explanation": post_execution_confidence["explanation"],
                                "breakdown": post_execution_confidence["breakdown"],
                                "historical_signal": historical_signal,
                                "execution_status_counts": summary.get("status_counts", {}),
                            },
                            confidence_analysis_path(project_info["run_dir"]),
                        )
                        save_json_artifact(
                            contradiction_report,
                            contradiction_analysis_path(project_info["run_dir"]),
                        )
                        replay_report = verify_plan_execution_consistency(
                            execution_plan=execution_plan,
                            execution_results=results_payload,
                            execution_debug=_load_json_if_exists(execution_debug_path(project_info["run_dir"])),
                        )
                        replay_report_path = save_json_artifact(
                            replay_report,
                            execution_replay_verification_path(project_info["run_dir"]),
                        )
                        drift_report = detect_run_drift(
                            run_dir=project_info["run_dir"],
                            url=url,
                            visual_signature=_build_visual_signature_from_context(page_info, page_scope, page_model),
                            network_summary=_network_summary_from_results(results_payload),
                        )
                        drift_report_path = save_json_artifact(
                            drift_report,
                            drift_analysis_path(project_info["run_dir"]),
                        )
                        debug_path = execution_debug_path(project_info["run_dir"])
                        learning_path = execution_learning_path(project_info["run_dir"])
                        console.print(f"[green][OK][/green] Execution summary created: [dim]{summary_path}[/dim]")
                        console.print(f"[green][OK][/green] CSV updated with execution results: [dim]{saved_csv_path}[/dim]")
                        console.print(f"[green][OK][/green] Replay verification saved: [dim]{replay_report_path}[/dim]")
                        console.print(f"[green][OK][/green] Drift analysis saved: [dim]{drift_report_path}[/dim]")
                        if replay_report.get("summary", {}).get("issue_count", 0):
                            console.print(
                                "  [yellow][i][/yellow] Replay consistency issue: "
                                f"{replay_report['summary'].get('issue_count', 0)}"
                            )
                        if drift_report.get("issues"):
                            console.print(
                                "  [yellow][i][/yellow] Drift warning: "
                                f"{len(drift_report.get('issues', []))} signal"
                            )
                        if debug_path.exists():
                            console.print(f"[green][OK][/green] Execution debug saved: [dim]{debug_path}[/dim]")
                        if learning_path.exists():
                            learned_profile_info = merge_execution_learning(
                                url,
                                json.loads(learning_path.read_text(encoding="utf-8")),
                                knowledge_context={"page_model": page_model, "page_scope": page_scope},
                            )
                            if learned_profile_info:
                                console.print(
                                    f"[green][OK][/green] Global knowledge bank updated: "
                                    f"[dim]{learned_profile_info['global_path']}[/dim]"
                                )
                                console.print(
                                    f"[green][OK][/green] Domain learning updated: "
                                    f"[dim]{learned_profile_info['domain_path']}[/dim]"
                                )
                                if learned_profile_info.get("cluster_paths"):
                                    console.print(
                                        f"[green][OK][/green] Cluster learning updated: "
                                        f"[dim]{', '.join(learned_profile_info['cluster_paths'][:3])}[/dim]"
                                    )
                except subprocess.CalledProcessError as exc:
                    console.print(f"\n[bold red][ERR] Executor failed to run: {exc}[/bold red]")
            else:
                console.print("[dim]  Execution skipped. The script can be run manually later.[/dim]")

        console.print(f"\n[bold green][FINISHED] Jobs complete. Check results in: {project_info['run_dir']}[/bold green]")

    except Exception as exc:
        import traceback

        console.print(f"\n[bold red][ERR] Error during AI/generation process for {url}:[/bold red] {exc}")
        console.print(f"[red]{traceback.format_exc()}[/red]")


def analyze_page_scope_with_retry(
    url: str,
    project_info: dict,
    page_info: dict,
    instruction: str,
    use_auth: bool,
    crawl_limit: int,
    adaptive_recrawl: bool,
    engine: AIEngine,
) -> dict:
    with console.status(
        f"[bold green]AI analyzing scope...[/bold green]",
        spinner="line",
    ):
        page_scope = engine.analyze_page_scope(
            url=url,
            website_title=project_info["title"],
            page_info=page_info,
            page_model=build_normalized_page_model(page_info),
            custom_instruction=instruction,
        )

    current_confidence = float(page_scope.get("confidence", 0) or 0)
    if not adaptive_recrawl or current_confidence >= 0.55 or crawl_limit >= 5:
        return page_scope

    save_json_artifact(
        page_scope,
        json_artifact_path(
            project_info["run_dir"],
            f"Page_Scope_Before_Recrawl_{project_info['safe_name']}_{project_info['timestamp']}.json",
        ),
    )

    expanded_limit = min(max(crawl_limit + 2, 3), 5)
    console.print(
        f"  [yellow][i][/yellow] Low confidence ({current_confidence}). Retrying scan with more linked pages ({expanded_limit})."
    )
    with console.status(
        f"[bold yellow]AI expanding context (low confidence)...[/bold yellow]",
        spinner="dots",
    ):
        refreshed_project_info, refreshed_page_info, _ = runner.scan_website(
            url,
            use_auth,
            crawl_limit=expanded_limit,
            site_profile=project_info.get("site_profile"),
            run_name=Path(project_info.get("run_dir", "")).name or None,
        )

    merge_recrawl_project_info(project_info, refreshed_project_info)
    page_info.clear()
    page_info.update(refreshed_page_info)
 
    with console.status(
        f"[bold green]AI re-analyzing page...[/bold green]",
        spinner="line",
    ):
        page_scope = engine.analyze_page_scope(
            url=url,
            website_title=project_info["title"],
            page_info=page_info,
            page_model=build_normalized_page_model(page_info),
            custom_instruction=instruction,
        )
    save_json_artifact(
        page_scope,
        json_artifact_path(
            project_info["run_dir"],
            f"Page_Scope_After_Recrawl_{project_info['safe_name']}_{project_info['timestamp']}.json",
        ),
    )
    return page_scope


def _load_json_if_exists(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _build_visual_signature_from_context(page_info: dict, page_scope: dict, page_model: dict) -> dict:
    headings = [item.get("text", "") for item in page_info.get("headings", []) if isinstance(item, dict)]
    component_types = [item.get("type", "") for item in page_model.get("component_catalog", []) if item.get("type")]
    return {
        "page_type": page_scope.get("page_type", ""),
        "heading_count": len(headings),
        "button_count": len(page_info.get("buttons", [])),
        "link_count": len(page_info.get("links", [])),
        "section_count": len(page_info.get("sections", [])),
        "component_count": len(page_model.get("component_catalog", [])),
        "component_types": sorted(dict.fromkeys(component_types)),
    }


def _network_summary_from_results(results_payload: dict) -> dict:
    endpoints: set[str] = set()
    requests = 0
    failing = 0
    for item in results_payload.get("results", []):
        summary = item.get("network_summary", {}) if isinstance(item.get("network_summary", {}), dict) else {}
        requests += int(summary.get("request_count", 0) or 0)
        failing += int(summary.get("failing_response_count", 0) or 0)
        for endpoint in summary.get("top_endpoints", [])[:8]:
            path = str(endpoint.get("path", "")).strip()
            if path:
                endpoints.add(path)
    return {
        "request_count": requests,
        "failing_response_count": failing,
        "top_endpoints": sorted(endpoints)[:10],
    }


def parse_args():
    parser = argparse.ArgumentParser(add_help=True)
    parser.add_argument("--url")
    parser.add_argument("--batch-file")
    parser.add_argument("--run-name", help="Nama run output (folder di bawah Result/) saat scan (opsional).")
    parser.add_argument(
        "--feature",
        choices=["case-generator", "e2e-automation", "visual-regression"],
        help="Jalankan per fitur saja lalu berhenti.",
    )
    parser.add_argument(
        "--input-run",
        help="Run folder existing (nama folder di bawah Result/ atau path penuh). Dipakai untuk feature yang butuh data run sebelumnya.",
    )
    parser.add_argument("--instruction", default="")
    parser.add_argument("--instruction-file")
    parser.add_argument("--csv-sep", choices=[",", ";"], default=",")
    parser.add_argument("--use-auth", action="store_true")
    parser.add_argument("--crawl-limit", type=int, choices=[1, 3, 5], default=3)
    parser.add_argument("--disable-adaptive-recrawl", action="store_true")
    parser.add_argument("--run-executor", action="store_true")
    parser.add_argument("--executor-headed", action="store_true")
    return parser.parse_args()


def _load_instruction(instruction: str, instruction_file: str | None) -> str:
    manual_instruction = instruction.strip()
    if instruction_file:
        file_instruction = Path(instruction_file).read_text(encoding="utf-8").strip()
        if manual_instruction:
            return f"{file_instruction}\n\n{manual_instruction}".strip()
        return file_instruction
    return manual_instruction

def main():
    args = parse_args()
    preview_engine = AIEngine()
    console.print(
        Panel(
            f"[bold cyan]QA Agent - Page Scope Analyzer & Test Scenario Generator[/bold cyan]\n"
            f"[dim]Active model: {preview_engine.current_model} ({preview_engine.current_rpm} RPM)[/dim]",
            border_style="cyan",
            box=box.DOUBLE_EDGE,
        )
    )

    if getattr(args, "feature", None):
        instruction = _load_instruction(args.instruction or "", args.instruction_file)
        csv_sep = ";" if args.csv_sep == ";" else ","
        use_auth = bool(args.use_auth)
        crawl_limit = int(args.crawl_limit or 3)
        adaptive_recrawl = not bool(args.disable_adaptive_recrawl)
        executor_headless = not bool(args.executor_headed)

        if args.feature == "case-generator":
            run_feature_case_generator(
                url=args.url,
                input_run=args.input_run,
                instruction=instruction,
                csv_sep=csv_sep,
                use_auth=use_auth,
                crawl_limit=crawl_limit,
                adaptive_recrawl=adaptive_recrawl,
                run_name=args.run_name,
            )
        elif args.feature == "e2e-automation":
            if not args.input_run:
                console.print("[red]Feature e2e-automation butuh --input-run.[/red]")
                return
            run_feature_e2e_automation(
                input_run=args.input_run,
                executor_headless=executor_headless,
                csv_sep=csv_sep,
            )
        else:
            if not args.url:
                console.print("[red]Feature visual-regression butuh --url.[/red]")
                return
            run_feature_visual_regression(url=args.url, use_auth=use_auth, run_name=args.run_name)

        console.print("[bold green]Proses selesai (mode --feature).[/bold green]")
        return

    if args.url or args.batch_file:
        if args.url and args.batch_file:
            raise ValueError("Use only one option: --url or --batch-file, not both at once.")
        if args.batch_file:
            batch_path = Path(args.batch_file)
            if not batch_path.exists():
                raise FileNotFoundError(f"Batch file not found: {batch_path}")
            urls_to_process = [
                line.strip()
                for line in batch_path.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
        else:
            urls_to_process = [args.url]
        if args.run_name and len(urls_to_process) != 1:
            raise ValueError("--run-name is only supported for a single URL per execution.")
        instruction = _load_instruction(args.instruction, args.instruction_file)
        csv_sep = ";" if args.csv_sep == ";" else ","
        use_auth = args.use_auth
        crawl_limit = args.crawl_limit
        adaptive_recrawl = not args.disable_adaptive_recrawl
        execute_now = args.run_executor
        executor_headless = not args.executor_headed
        console.print(
            f"[dim]Non-interactive mode enabled for "
            f"{args.url or args.batch_file}[/dim]"
        )
    else:
        console.print("\n[bold]Step 1: Input URL / File Batch (.txt)[/bold]")
        url_input = console.input("  [cyan]Enter target URL or .txt batch file: [/cyan]").strip()

        if url_input.lower() in ["", "exit", "quit", "keluar"]:
            console.print("[dim]Sampai jumpa![/dim]")
            return

        urls_to_process = []
        if url_input.endswith(".txt") and os.path.exists(url_input):
            with open(url_input, "r", encoding="utf-8") as handle:
                urls_to_process = [line.strip() for line in handle if line.strip()]
            console.print(f"[bold green]Batch mode enabled: found {len(urls_to_process)} URLs.[/bold green]")
        else:
            urls_to_process.append(url_input)

        console.print("\n[bold]Step 2: Format CSV[/bold]")
        console.print("  [1] Koma (,) - standar umum")
        console.print("  [2] Semicolon (;) - suitable for certain regional Excel formats")
        sep_choice = console.input("  [cyan]Select [1/2] (Default: 1): [/cyan]").strip()
        csv_sep = ";" if sep_choice == "2" else ","

        console.print("\n[bold]Step 3: Session Mode (Optional)[/bold]")
        console.print("  [1] Standard scan tanpa session")
        console.print("  [2] Gunakan session auth Playwright")
        auth_choice = console.input("  [cyan]Select [1/2] (Default: 1): [/cyan]").strip()
        use_auth = auth_choice == "2"

        if use_auth:
            console.print("\n[bold yellow][i] Cara memakai session auth Playwright:[/bold yellow]")
            console.print(f"  1. Simpan file session bernama [bold green]auth_state.json[/bold green] di folder [bold cyan]{AUTH_DIR}/[/bold cyan].")
            console.print(f"  2. Gunakan [dim]{AUTH_DIR}/sample_auth_state.json[/dim] sebagai template struktur file.")
            console.print("  3. This session will be used during scan and executor run.")
            console.input("  [green]Press Enter once the session file is ready...[/green]")

        console.print("\n[bold]Step 4: Additional Instructions (Optional)[/bold]")
        console.print(f"[dim]Type /profile_name.txt to load a profile from {INSTRUCTIONS_DIR}[/dim]")
        instruction = ""
        instruction_lines = []

        try:
            first_line = input("  > ").strip()
            if first_line.startswith("/"):
                profile_name = first_line.lstrip("/")
                if not profile_name.endswith(".txt"):
                    profile_name += ".txt"
                profile_path = INSTRUCTIONS_DIR / profile_name
                if profile_path.exists():
                    instruction = profile_path.read_text(encoding="utf-8")
                    console.print(f"[green][OK] Instruction profile loaded from {profile_path}[/green]")
                else:
                    console.print(f"[red][ERR] Instruction profile {profile_path} not found. Using empty instruction.[/red]")
            else:
                if first_line.lower() != "selesai" and first_line != "":
                    instruction_lines.append(first_line)
                    while True:
                        line = input("  > ")
                        if line.strip().lower() == "selesai":
                            break
                        if not line.strip() and not instruction_lines:
                            break
                        if not line.strip() and instruction_lines and not instruction_lines[-1].strip():
                            break
                        instruction_lines.append(line)
                    instruction = "\n".join(instruction_lines).strip()
        except EOFError:
            pass

        console.print("\n[bold]Step 5: Mini Crawl Depth[/bold]")
        console.print("  [1] Ringan - 1 linked page")
        console.print("  [2] Normal - 3 linked pages")
        console.print("  [3] Lebih luas - 5 linked pages")
        crawl_choice = console.input("  [cyan]Select [1/2/3] (Default: 2): [/cyan]").strip()
        crawl_limit = {"1": 1, "2": 3, "3": 5}.get(crawl_choice, 3)

        console.print("\n[bold]Step 6: Adaptive Recrawl[/bold]")
        console.print("  [1] Enabled - if confidence is low, the agent rescans with more linked pages")
        console.print("  [2] Disabled - use the initial scan result only")
        adaptive_choice = console.input("  [cyan]Select [1/2] (Default: 1): [/cyan]").strip()
        adaptive_recrawl = adaptive_choice != "2"
        execute_now = None
        executor_headless = True

    console.print("\n[bold green]=== MEMULAI SCAN, ANALISA SCOPE, DAN GENERASI TEST SCENARIOS ===[/bold green]\n")
    for url in urls_to_process:
        process_single_url(
            url,
            instruction,
            csv_sep,
            use_auth,
            crawl_limit,
            adaptive_recrawl,
            execute_now=execute_now,
            executor_headless=executor_headless,
            run_name=args.run_name if args.url else None,
        )
        console.print("")

    console.print("[bold green]Semua proses selesai.[/bold green]")


if __name__ == "__main__":
    main()
