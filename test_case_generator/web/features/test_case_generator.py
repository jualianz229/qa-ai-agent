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

