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
) -> dict:
    ranked = sort_runs(list_runs(result_dir), mode="vrt_change")
    changed_runs = [item for item in ranked if int(item.get("vrt_change_count", 0) or 0) > 0]
    focus_runs = changed_runs[:18] if changed_runs else ranked[:18]
    return {
        "runs": ranked,
        "focus_runs": focus_runs,
        "metrics": dashboard_metrics(),
        "vrt_feature_id": "VRT-CHANGES-001",
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

