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
) -> dict:
    runs = [item for item in list_runs(result_dir) if is_automation_run(item.get("run_name", ""))]
    runs = sort_runs(runs, mode=sort_mode)
    return {
        "runs": runs,
        "metrics": dashboard_metrics(),
        "sort_mode": sort_mode,
    }

