from __future__ import annotations

from pathlib import Path
from typing import Callable


def build_vrt_scan_context(*, dashboard_metrics: Callable) -> dict:
    return {
        "metrics": dashboard_metrics(),
        "vrt_feature_id": "VRT-SCAN-001",
    }


def build_vrt_changes_context(
    *,
    result_dir: Path,
    list_runs: Callable,
    sort_runs: Callable,
    dashboard_metrics: Callable,
    search_query: str = "",
    page: int = 1,
    per_page: int = 10,
) -> dict:
    ranked = sort_runs(list_runs(result_dir), mode="latest")
    # Filter only runs that actually have VRT data (baseline, diff, or regression)
    vrt_runs = [
        item for item in ranked 
        if item.get("visual_baseline") or item.get("visual_diff") or item.get("visual_regression")
    ] 

    if search_query:
        q = search_query.lower()
        vrt_runs = [
            run for run in vrt_runs
            if q in str(run.get("run_name", "")).lower() or q in str(run.get("url", "")).lower()
        ]

    total_count = len(vrt_runs)
    num_pages = (total_count + per_page - 1) // per_page if total_count > 0 else 1
    page = max(1, min(page, num_pages))
    
    start_idx = (page - 1) * per_page
    end_idx = start_idx + per_page
    paginated_runs = vrt_runs[start_idx:end_idx]

    return {
        "runs": paginated_runs,
        "metrics": dashboard_metrics(),
        "vrt_feature_id": "VRT-CHANGES-001",
        "search_query": search_query,
        "page": page,
        "num_pages": num_pages,
        "total_count": total_count,
        "per_page": per_page,
    }


def build_vrt_monitor_context(
    *,
    result_dir: Path,
    selected_run_name: str,
    list_runs: Callable,
    sort_runs: Callable,
    dashboard_metrics: Callable,
) -> dict:
    ranked = sort_runs(list_runs(result_dir), mode="vrt_change")
    return {
        "runs": ranked,
        "metrics": dashboard_metrics(),
        "vrt_feature_id": "VRT-DIFF-001",
        "selected_run_name": str(selected_run_name or "").strip(),
    }

