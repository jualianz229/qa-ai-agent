from __future__ import annotations

from pathlib import Path
from typing import Callable


def build_end_to_end_automation_context(
    *,
    result_dir: Path,
    source_run_name: str,
    list_runs: Callable,
    is_automation_or_recovery_run: Callable,
    jobs: dict,
    get_job: Callable,
    dashboard_metrics: Callable,
) -> dict:
    all_runs = list_runs(result_dir)
    runs = [item for item in all_runs if not is_automation_or_recovery_run(item.get("run_name", ""))]
    source = str(source_run_name or "").strip()
    if source and is_automation_or_recovery_run(source):
        source = ""
    active_jobs = jobs[:8]
    return {
        "runs": runs,
        "source_run_name": source,
        "active_jobs": active_jobs,
        "metrics": dashboard_metrics(),
    }


def build_automation_results_context(
    *,
    result_dir: Path,
    sort_mode: str,
    list_runs: Callable,
    sort_runs: Callable,
    is_automation_run: Callable,
    dashboard_metrics: Callable,
    search_query: str = "",
    page: int = 1,
    per_page: int = 10,
) -> dict:
    all_runs = list_runs(result_dir)
    auto_runs = [item for item in all_runs if is_automation_run(item.get("run_name", ""))]

    if search_query:
        q = search_query.lower()
        auto_runs = [
            run for run in auto_runs
            if q in str(run.get("run_name", "")).lower() or q in str(run.get("url", "")).lower()
        ]

    runs = sort_runs(auto_runs, mode=sort_mode or "latest")

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

