"""Next-generation E2E automation helpers.

This module centralizes the logic for creating and running a Playwright-based
end-to-end automation job.  It supports either deriving cases from an existing
Test Case Generator run or accepting completely manual input (CSV text / JSON
list from an editor).  The goal is to have a single API surface that the
frontend can call regardless of where the cases originate.

Workflow overview:

1. ``create_e2e_job`` validates the payload, optionally writes a temporary run
   directory and execution plan, then queues a background worker.
2. The worker calls ``run_e2e_job`` which reuses most of the old
   ``run_automation_job`` flow: generate script, execute, collate results.
3. The frontend can download the generated script via ``download_script``.

The existing ``modules/end_to_end_automation/src/automation.py`` still
exposes the old ``create_automation_job`` wrapper for compatibility; it simply
forwards to ``create_e2e_job`` behind the scenes.
"""

from __future__ import annotations

import csv
import json
import shutil
import sys
import threading
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from core.artifacts import execution_results_path, json_artifact_path
from core.config import RESULT_DIR
from core.dashboard_data import build_run_detail
from core.jobs import append_job_log, update_job, run_logged_process
from core.result_analyzer import analyze_execution_results, save_execution_summary
from core.scanner import Scanner
from core.utils import is_automation_or_recovery_run
from modules.end_to_end_automation.src.executor import CodeGenerator


# ----------------------------------------------------------------------------
# public helpers
# ----------------------------------------------------------------------------

def create_e2e_job(
    payload: dict,
) -> dict:
    """Create and queue an end-to-end automation job.

    ``payload`` is a flexible dictionary with the following recognised keys:

    * ``source_run_name`` - name of an existing run produced by the case
      generator.  Mutually exclusive with ``cases``/``csv``/``csv_text``.
    * ``selected_case_ids`` - optional list of IDs (strings) to filter the
      source run or the provided cases.
    * ``cases`` - a list of case dictionaries provided by the editor.  Each
      entry must have an ``id`` field and may include other metadata such as
      ``title`` or ``actions``.  If this key is given we skip any source run
      lookup and use the supplied list directly.
    * ``csv`` - a path-like object or string containing CSV text.  It's parsed
      the same way the legacy automation used to read a run's ``*.csv`` file.
      ``cases`` takes precedence if both provided.
    * ``base_url`` - when cases are supplied manually there is no embedded url;
      the script generator needs a default.
    * ``executor_headed`` - whether the Playwright runner should be headed.
    * ``inject_login`` - extra login instructions to merge into the plan.

    The returned job dictionary is identical to the one produced by the old
    ``create_automation_job`` function so existing callers continue to work.
    """
    # normalize simple fields
    executor_headed = bool(payload.get("executor_headed", False))
    inject_login = str(payload.get("inject_login", "")).strip()
    selected_ids = [str(x).strip() for x in payload.get("selected_case_ids", []) or [] if str(x).strip()]

    # determine origin of cases
    cases = payload.get("cases")
    csv_text: str | None = None
    if cases is None and payload.get("csv") is not None:
        # caller may have sent raw CSV text
        csv_text = str(payload.get("csv") or "")
    source_run_name = str(payload.get("source_run_name", ""))
    if not cases and not csv_text and not source_run_name:
        raise ValueError("Either source_run_name, cases or csv must be provided.")

    job_id = uuid.uuid4().hex[:12]
    run_name = payload.get("run_name") or f"e2e_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    job_payload = {
        "mode": "e2e_automation",
        "run_name": run_name,
        "source_run_name": source_run_name,
        "selected_case_ids": selected_ids,
        "cases": cases,
        "csv_text": csv_text,
        "base_url": payload.get("base_url", ""),
        "executor_headed": executor_headed,
        "inject_login": inject_login,
    }

    # queue job in the same global jobs map the rest of the system uses
    job = {
        "id": job_id,
        "status": "queued",
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "updated_at": datetime.now().isoformat(timespec="seconds"),
        "payload": job_payload,
        "log_lines": [],
        "run_name": run_name,
        "command": ["e2e-test", run_name],
    }
    from core.jobs import jobs, jobs_lock

    with jobs_lock:
        jobs[job_id] = job
    thread = threading.Thread(target=run_e2e_job, args=(job_id, job_payload), daemon=True)
    thread.start()
    return job


def download_script(run_name: str) -> Path:
    """Return the generated Playwright script path for the given run.

    This helper is intended to be used by a Flask route; the caller should
    wrap the result in ``send_file`` and handle ``FileNotFoundError``.
    """
    script_path = RESULT_DIR / run_name / "scripts" / "pom_runner.py"
    if not script_path.exists():
        raise FileNotFoundError(run_name)
    return script_path


# ----------------------------------------------------------------------------
# internal implementation details
# ----------------------------------------------------------------------------

def run_e2e_job(job_id: str, payload: dict) -> None:
    try:
        update_job(job_id, status="running")
        append_job_log(job_id, "[STEP] 1 | prepare automation run")
        prepared = prepare_e2e_run(
            payload.get("source_run_name", ""),
            payload.get("run_name", ""),
            payload.get("selected_case_ids", []),
            payload.get("executor_headed", False),
            payload.get("inject_login", ""),
            cases=payload.get("cases"),
            csv_text=payload.get("csv_text"),
            base_url=payload.get("base_url", ""),
        )
        append_job_log(job_id, "[STEP] 1 | done")

        append_job_log(job_id, f"Prepared automation: {prepared['run_name']}")
        append_job_log(job_id, f"Selected cases: {len(prepared['selected_ids'])}")
        if prepared.get("inject_login"):
            append_job_log(job_id, "Inject login instructions attached to this automation run.")

        append_job_log(job_id, "[STEP] 2 | run playwright script")
        command = [sys.executable, prepared["script_path"].name]
        update_job(job_id, command=command, run_name=prepared["run_name"])
        exit_code = run_logged_process(job_id, command, str(prepared["script_path"].parent))
        append_job_log(job_id, "[STEP] 2 | " + ("done" if exit_code == 0 else "fail"))

        append_job_log(job_id, "[STEP] 3 | save results")
        if exit_code == 0 and prepared["results_path"].exists():
            summary = analyze_execution_results(prepared["results_path"])
            save_execution_summary(prepared["results_path"], summary)
            csv_runner = Scanner(RESULT_DIR)
            csv_runner.update_csv_with_execution_results(prepared["csv_path"], prepared["results_path"], ",")
            append_job_log(job_id, "[STEP] 3 | done")
        elif exit_code != 0:
            append_job_log(job_id, "[STEP] 3 | fail")

        status = "completed" if exit_code == 0 else "failed"
        update_job(job_id, status=status, exit_code=exit_code)
    except Exception as exc:
        append_job_log(job_id, "[STEP] 3 | fail")
        append_job_log(job_id, f"E2E job error: {exc}")
        update_job(job_id, status="failed", exit_code=1)


def _parse_csv_rows(csv_text: str) -> list[dict]:
    """Helper that parses a string of CSV into a list of dicts.

    Uses the standard csv module to correctly handle quotes, escaping
    and multiline fields.
    """
    if not csv_text:
        return []
    import io
    f = io.StringIO(csv_text.strip())
    reader = csv.DictReader(f)
    return list(reader)


def prepare_e2e_run(
    source_run_name: str,
    automation_run_name: str,
    selected_case_ids: list[str],
    executor_headed: bool,
    inject_login: str = "",
    *,
    cases: list[dict] | None = None,
    csv_text: str | None = None,
    base_url: str = "",
) -> dict:
    """Assemble the run directory, CSV, execution plan and playwright script.

    This routine supports three modes of operation:

    1. ``cases`` provided directly (list of dicts).  ``base_url`` must be
       given when calling from manual flow.
    2. ``csv_text`` provided which will be parsed to rows.
    3. ``source_run_name`` provided; the behaviour matches the old
       ``prepare_automation_run`` exactly, filtering by ``selected_case_ids``.
    """
    automation_run_dir = RESULT_DIR / automation_run_name
    automation_run_dir.mkdir(parents=True, exist_ok=True)
    (automation_run_dir / "JSON").mkdir(parents=True, exist_ok=True)
    (automation_run_dir / "Evidence" / "Video").mkdir(parents=True, exist_ok=True)

    # determine case rows
    if cases is not None:
        rows = [{**r} for r in cases]
    elif csv_text is not None:
        rows = _parse_csv_rows(csv_text)
    else:
        # legacy behaviour using existing run
        if not source_run_name:
            raise ValueError("source_run_name is required when cases/csv are not supplied.")
        source_run_dir = RESULT_DIR / source_run_name
        if not source_run_dir.exists():
            raise FileNotFoundError(source_run_name)
        # optimized: avoid slow build_run_detail for basic metadata
        raw_scan_path = next((source_run_dir / "JSON").glob("raw_scan_*.json"), None)
        if raw_scan_path:
            raw_scan = json.loads(raw_scan_path.read_text(encoding="utf-8"))
            url = raw_scan.get("url", "")
            base_url = url
        else:
            # fallback
            detail = build_run_detail(source_run_dir)
            url = str(detail.get("url", "")).strip()
            base_url = url

        if not url:
            raise ValueError("Source run does not have a valid URL yet.")

        plan_path = next((source_run_dir / "JSON").glob("Execution_Plan_*.json"), None)
        csv_path = next(source_run_dir.glob("*.csv"), None)
        if not plan_path or not csv_path:
            raise FileNotFoundError("Source execution plan or CSV not found.")
        rows = []
        with csv_path.open("r", encoding="utf-8-sig", newline="") as handle:
            rows = list(csv.DictReader(handle))

    # filter row set if ids supplied
    all_ids = [str(r.get("ID", "")).strip() for r in rows if str(r.get("ID", "")).strip()]
    if selected_case_ids:
        selected_set = set(selected_case_ids)
        filtered_rows = [r for r in rows if str(r.get("ID", "")).strip() in selected_set]
        selected_ids = [str(r.get("ID", "")).strip() for r in filtered_rows]
    else:
        filtered_rows = rows
        selected_ids = all_ids
    if not filtered_rows:
        raise ValueError("No cases available after applying filters.")

    # write automation CSV file
    automation_csv_path = automation_run_dir / f"{automation_run_name}.csv"
    if filtered_rows:
        fieldnames = list(filtered_rows[0].keys())
        with automation_csv_path.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(filtered_rows)

    # build execution plan JSON from the rows; if we came from a source run we
    # will reuse its plan and just filter it, otherwise we construct a minimal
    # plan that contains only the id/title pairs.  The generator is tolerant so
    # a minimal plan is fine for downloading code.
    if cases is not None or csv_text is not None:
        # manual generation
        plans = []
        for r in filtered_rows:
            plans.append({"id": str(r.get("ID", "")).strip(), "title": str(r.get("Title", "")).strip()})
        execution_plan = {"run_name": automation_run_name, "base_url": base_url, "plans": plans}
    else:
        # legacy branch uses the source plan file
        execution_plan = json.loads(plan_path.read_text(encoding="utf-8"))
        if selected_ids:
            execution_plan = {**execution_plan, "plans": [p for p in execution_plan.get("plans", []) if str(p.get("id", "")) in set(selected_ids)]}
        if inject_login:
            execution_plan["inject_login"] = inject_login

    filtered_plan_path = json_artifact_path(automation_run_dir, f"Execution_Plan_{automation_run_name}.json")
    filtered_plan_path.write_text(json.dumps(execution_plan, indent=2), encoding="utf-8")

    # copy over most other artifacts from source run if present
    if source_run_name:
        source_run_dir = RESULT_DIR / source_run_name
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
                "run_name": automation_run_name,
                "selected_ids": selected_ids,
                "inject_login": inject_login,
                "base_url": base_url,
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    # generate playwright script
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
        "inject_login": inject_login,
        "base_url": base_url,
    }
