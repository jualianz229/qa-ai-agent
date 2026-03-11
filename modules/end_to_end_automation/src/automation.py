import json
import shutil
import sys
import threading
import uuid
import csv
from datetime import datetime
from pathlib import Path

from core.artifacts import execution_results_path, json_artifact_path
from core.config import RESULT_DIR
from core.dashboard_data import build_run_detail
from modules.end_to_end_automation.src.executor import CodeGenerator
# new import for refactored logic
from modules.end_to_end_automation.src import e2e
from core.jobs import append_job_log, update_job, run_logged_process
from core.result_analyzer import analyze_execution_results, save_execution_summary
from core.scanner import Scanner
from core.utils import is_automation_or_recovery_run


# Compatibility wrapper around new e2e implementation.  Existing callers
# (mainly the Flask route) continue to call ``create_automation_job``; the
# behaviour is identical although the helper lives in ``e2e.py`` now.

def create_automation_job(
    source_run_name: str,
    selected_case_ids: list[str] | None = None,
    executor_headed: bool = False,
    inject_login: str = "",
) -> dict:
    payload = {
        "source_run_name": source_run_name,
        "selected_case_ids": selected_case_ids or [],
        "executor_headed": executor_headed,
        "inject_login": inject_login,
    }
    return e2e.create_e2e_job(payload)


# the old runner is now a thin wrapper that forwards to the new
# ``run_e2e_job`` implementation.  it exists so that existing tests or
# background workers which reference ``run_automation_job`` continue to work.

def run_automation_job(job_id: str, payload: dict) -> None:
    return e2e.run_e2e_job(job_id, payload)


# prepare_automation_run has been superseded by
# ``e2e.prepare_e2e_run``.  keep a forwarding stub for compatibility.

def prepare_automation_run(
    source_run_name: str,
    automation_run_name: str,
    selected_case_ids: list[str] | None,
    executor_headed: bool,
    inject_login: str = "",
) -> dict:
    return e2e.prepare_e2e_run(
        source_run_name,
        automation_run_name,
        selected_case_ids or [],
        executor_headed,
        inject_login,
    )
    source_run_dir = RESULT_DIR / source_run_name
    automation_run_dir = RESULT_DIR / automation_run_name
    automation_run_dir.mkdir(parents=True, exist_ok=True)
    (automation_run_dir / "JSON").mkdir(parents=True, exist_ok=True)
    (automation_run_dir / "Evidence" / "Video").mkdir(parents=True, exist_ok=True)

    source_plan_path = next((source_run_dir / "JSON").glob("Execution_Plan_*.json"), None)
    source_csv_path = next(source_run_dir.glob("*.csv"), None)
    if not source_plan_path or not source_csv_path:
        raise FileNotFoundError("Source execution plan or CSV not found.")

    execution_plan = json.loads(source_plan_path.read_text(encoding="utf-8"))
    all_plan_ids = [str(item.get("id", "")).strip() for item in execution_plan.get("plans", []) if str(item.get("id", "")).strip()]
    selected_ids = [str(item or "").strip() for item in list(selected_case_ids or []) if str(item or "").strip()]
    if selected_ids:
        selected_set = set(selected_ids)
        filtered_plans = [plan for plan in execution_plan.get("plans", []) if str(plan.get("id", "")).strip() in selected_set]
    else:
        filtered_plans = list(execution_plan.get("plans", []))
        selected_ids = all_plan_ids
    if not filtered_plans:
        raise ValueError("Selected case was not found in execution plan.")

    inject_login = str(inject_login or "").strip()
    filtered_plan = {**execution_plan, "plans": filtered_plans}
    if inject_login:
        filtered_plan["inject_login"] = inject_login
    filtered_plan_path = json_artifact_path(automation_run_dir, f"Execution_Plan_{automation_run_name}.json")
    filtered_plan_path.write_text(json.dumps(filtered_plan, indent=2), encoding="utf-8")

    with source_csv_path.open("r", encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    if not rows:
        raise ValueError("Source CSV is empty.")
    fieldnames = list(rows[0].keys())
    selected_set = set(selected_ids)
    filtered_rows = [row for row in rows if str(row.get("ID", "")).strip() in selected_set]
    if not filtered_rows:
        raise ValueError("Selected case was not found in CSV.")
    traceability_prefix = f"{source_run_name}->{automation_run_name}"
    selected_case_map = []
    for row in filtered_rows:
        case_id = str(row.get("ID", "")).strip()
        if not case_id:
            continue
        selected_case_map.append(
            {
                "id": case_id,
                "title": str(row.get("Title", "")).strip(),
                "traceability_id": f"{traceability_prefix}::{case_id}",
            }
        )
    automation_csv_path = automation_run_dir / f"{automation_run_name}.csv"
    with automation_csv_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(filtered_rows)

    skip_prefixes = {
        "Execution_Plan_",
        "Execution_Results",
        "Execution_Debug",
        "Execution_Learning",
        "Execution_Checkpoints",
    }
    for artifact in (source_run_dir / "JSON").glob("*.json"):
        if any(artifact.name.startswith(prefix) for prefix in skip_prefixes):
            continue
        shutil.copy2(artifact, automation_run_dir / "JSON" / artifact.name)

    metadata_path = json_artifact_path(automation_run_dir, "Automation_Metadata.json")
    metadata_path.write_text(
        json.dumps(
            {
                "source_run_name": source_run_name,
                "automation_run_name": automation_run_name,
                "selected_ids": selected_ids,
                "selected_case_map": selected_case_map,
                "traceability_tag": traceability_prefix,
                "inject_login": inject_login,
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    script_path = CodeGenerator(None).generate_pom_script(
        {"run_dir": str(automation_run_dir)},
        filtered_plan_path,
        headless=not executor_headed,
    )
    return {
        "run_name": automation_run_name,
        "run_dir": automation_run_dir,
        "script_path": script_path,
        "csv_path": automation_csv_path,
        "results_path": execution_results_path(automation_run_dir),
        "selected_ids": selected_ids,
        "selected_case_map": selected_case_map,
        "inject_login": inject_login,
    }
