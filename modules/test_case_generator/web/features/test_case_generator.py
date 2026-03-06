from __future__ import annotations

from pathlib import Path
from typing import Callable


def build_test_case_generator_context(
    *,
    instructions_dir: Path,
    list_instruction_templates: Callable,
    jobs: dict,
    get_job: Callable,
    dashboard_metrics: Callable,
) -> dict:
    templates = list_instruction_templates(instructions_dir)
    active_jobs = jobs[:8]
    return {
        "templates": templates,
        "active_jobs": active_jobs,
        "metrics": dashboard_metrics(),
    }


def build_all_test_cases_context(
    *,
    result_dir: Path,
    sort_mode: str,
    list_runs: Callable,
    sort_runs: Callable,
    dashboard_metrics: Callable,
) -> dict:
    runs = sort_runs(list_runs(result_dir), mode=sort_mode)
    return {
        "runs": runs,
        "metrics": dashboard_metrics(),
        "sort_mode": sort_mode,
    }


def build_scenario_results_context(
    *,
    result_dir: Path,
    sort_mode: str,
    list_runs: Callable,
    sort_runs: Callable,
    is_automation_or_recovery_run: Callable,
    dashboard_metrics: Callable,
    search_query: str = "",
    page: int = 1,
    per_page: int = 10,
) -> dict:
    all_runs = list_runs(result_dir)
    scenario_runs = [run for run in all_runs if not is_automation_or_recovery_run(run.get("run_name", ""))]

    if search_query:
        q = search_query.lower()
        scenario_runs = [
            run for run in scenario_runs
            if q in str(run.get("run_name", "")).lower() or q in str(run.get("url", "")).lower()
        ]

    runs = sort_runs(scenario_runs, mode=sort_mode or "latest")
    
    total_count = len(runs)
    num_pages = (total_count + per_page - 1) // per_page if total_count > 0 else 1
    page = max(1, min(page, num_pages))
    
    start_idx = (page - 1) * per_page
    end_idx = start_idx + per_page
    paginated_runs = runs[start_idx:end_idx]

    return {
        "runs": paginated_runs,
        "metrics": dashboard_metrics(),
        "sort_mode": sort_mode,
        "search_query": search_query,
        "page": page,
        "num_pages": num_pages,
        "total_count": total_count,
        "per_page": per_page,
    }

