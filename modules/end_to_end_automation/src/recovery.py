import json
import os
import shutil
import sys
import threading
import uuid
import csv
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import urlparse

from core.artifacts import execution_results_path, json_artifact_path
from core.config import RESULT_DIR
from core.dashboard_data import build_run_detail, list_runs, sort_runs
from modules.end_to_end_automation.src.executor import CodeGenerator
from core.jobs import create_job, get_all_jobs, append_job_log, update_job, run_logged_process
from core.result_analyzer import analyze_execution_results, save_execution_summary
from core.scanner import Scanner
from core.utils import is_automation_or_recovery_run, parse_iso_datetime


def _resolve_run_dir(run_name: str) -> Path:
    candidate = (RESULT_DIR / run_name).resolve()
    result_root = RESULT_DIR.resolve()
    if not candidate.is_relative_to(result_root):
        raise ValueError("Invalid run path.")
    return candidate


def create_retry_failed_job(run_name: str, executor_headed: bool = False) -> dict:
    from website.dashboard import _append_recovery_action
    source_run_dir = RESULT_DIR / run_name
    if not source_run_dir.exists():
        raise FileNotFoundError(run_name)
    source_detail = build_run_detail(source_run_dir)
    allowed, reason = domain_recovery_limit_check(source_detail.get("url", ""), max_concurrent=2)
    if not allowed:
        raise ValueError(reason)
    gate = source_detail.get("execution_gate", {}) if isinstance(source_detail, dict) else {}
    gate_blocked = bool(gate.get("blocked", False)) if isinstance(gate, dict) else False
    gate_override = str(os.getenv("QA_AI_ALLOW_LOW_ANTI_HALLU", "")).strip().lower() in {"1", "true", "yes", "on"}
    if gate_blocked and not gate_override:
        reasons = [str(item).strip() for item in gate.get("reasons", []) if str(item).strip()]
        reason = "; ".join(reasons[:3]) if reasons else "execution gate still blocked"
        raise ValueError(
            "Retry failed is blocked by the anti-hallucination gate. "
            f"Reason: {reason}. Set QA_AI_ALLOW_LOW_ANTI_HALLU=1 for manual override."
        )
    failed_ids = [row["id"] for row in source_detail.get("case_rows", []) if row.get("status") == "failed"]
    if not source_detail.get("execution_ran"):
        raise ValueError("This run does not have AI Executor results yet.")
    if not failed_ids:
        raise ValueError("No failed cases available for retry.")

    retry_run_name = f"{run_name}_retry_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    job_id = uuid.uuid4().hex[:12]
    payload = {
        "mode": "retry_failed",
        "url": source_detail.get("url", ""),
        "source_run_name": run_name,
        "baseline_safety_index": int(source_detail.get("safety_index", 0) or 0),
        "retry_run_name": retry_run_name,
        "executor_headed": executor_headed,
        "failed_ids": failed_ids,
    }
    job = {
        "id": job_id,
        "status": "queued",
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "updated_at": datetime.now().isoformat(timespec="seconds"),
        "payload": payload,
        "log_lines": [],
        "run_name": retry_run_name,
        "command": ["retry-failed-only", run_name, retry_run_name],
    }
    
    from core.jobs import jobs, jobs_lock
    with jobs_lock:
        jobs[job_id] = job
    thread = threading.Thread(target=run_retry_failed_job, args=(job_id, payload), daemon=True)
    thread.start()
    return job


def create_safe_rerun_job(run_name: str, executor_headed: bool = False) -> dict:
    source_run_dir = RESULT_DIR / run_name
    if not source_run_dir.exists():
        raise FileNotFoundError(run_name)
    eligibility = safe_rerun_eligibility(run_name)
    if not eligibility["eligible"]:
        raise ValueError(eligibility["reason"])
    detail = build_run_detail(source_run_dir)
    url = str(detail.get("url", "")).strip()
    if not url:
        raise ValueError("This run does not have a valid source URL yet.")
    allowed, reason = domain_recovery_limit_check(url, max_concurrent=2)
    if not allowed:
        raise ValueError(reason)

    instruction = build_safe_rerun_instruction(detail)

    safe_run_name = f"{run_name}_safe_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    payload = {
        "mode": "safe_rerun",
        "source_run_name": run_name,
        "baseline_safety_index": int(detail.get("safety_index", 0) or 0),
        "url": url,
        "instruction": instruction,
        "template_name": "",
        "template_path": "",
        "csv_sep": ",",
        "crawl_limit": 5,
        "use_auth": False,
        "adaptive_recrawl": True,
        "run_executor": True,
        "executor_headed": executor_headed,
        "run_name": safe_run_name,
    }
    return create_job(payload)


def retry_failed_eligibility(detail: dict) -> dict:
    failed_count = int(detail.get("status_counts", {}).get("failed", 0) or 0)
    gate_blocked = bool(detail.get("execution_gate", {}).get("blocked", False))
    execution_ran = bool(detail.get("execution_ran", False))
    if not execution_ran:
        return {"eligible": False, "reason": "This run has no previous execution results."}
    if failed_count <= 0:
        return {"eligible": False, "reason": "No failed cases available for retry."}
    if gate_blocked:
        return {"eligible": False, "reason": "Execution gate is blocked; retry-failed is not recommended."}
    return {"eligible": True, "reason": ""}


def recovery_preview(run_name: str) -> dict:
    source_run_dir = RESULT_DIR / run_name
    if not source_run_dir.exists():
        raise FileNotFoundError(run_name)
    detail = build_run_detail(source_run_dir)
    retry = retry_failed_eligibility(detail)
    safe_rerun = safe_rerun_eligibility(run_name)
    strategy = ""
    reason = ""
    if retry["eligible"]:
        strategy = "retry_failed"
        reason = "There are failed cases and the execution gate is not blocked."
    elif safe_rerun["eligible"]:
        strategy = "safe_rerun"
        reason = "Retry-failed is not eligible; fallback to conservative safe rerun."
    else:
        strategy = "none"
        reason = "No recovery strategy is currently eligible."
    return {
        "run_name": run_name,
        "strategy": strategy,
        "reason": reason,
        "retry_failed": retry,
        "safe_rerun": safe_rerun,
        "safety_status": detail.get("safety_status", ""),
        "safety_index": int(detail.get("safety_index", 0) or 0),
        "failed_count": int(detail.get("status_counts", {}).get("failed", 0) or 0),
    }


def plan_safe_rerun_batch(limit: int = 3, include_warning: bool = True) -> list[dict]:
    limit = max(1, min(int(limit or 1), 6))
    ranked = sort_runs(list_runs(RESULT_DIR), mode="safety_risk")
    planned = []
    for item in ranked:
        status = str(item.get("safety_status", "")).strip().lower()
        if status == "critical" or (include_warning and status == "warning"):
            run_name = str(item.get("run_name", "")).strip()
            eligible, reason = True, ""
            has_recent, recent_reason = has_recent_safe_rerun(run_name, cooldown_minutes=20)
            if has_recent:
                eligible = False
                reason = recent_reason
            planned.append(
                {
                    "run_name": run_name,
                    "safety_index": int(item.get("safety_index", 0) or 0),
                    "safety_status": status,
                    "safety_reasons": list(item.get("safety_reasons", []) or [])[:3],
                    "eligible": eligible,
                    "eligibility_reason": reason,
                }
            )
        if len(planned) >= limit:
            break
    return [item for item in planned if item.get("run_name")]


def create_safe_rerun_batch_jobs(limit: int = 3, include_warning: bool = True, executor_headed: bool = False) -> dict:
    planned = plan_safe_rerun_batch(limit=limit, include_warning=include_warning)
    selected = [item["run_name"] for item in planned if bool(item.get("eligible", True))]

    jobs = []
    skipped = []
    for item in planned:
        if bool(item.get("eligible", True)):
            continue
        skipped.append(
            {
                "run_name": item.get("run_name", ""),
                "error": item.get("eligibility_reason", "") or "Safe rerun is currently not eligible.",
            }
        )
    for run_name in selected:
        if not run_name:
            continue
        try:
            jobs.append(create_safe_rerun_job(run_name, executor_headed=executor_headed))
        except Exception as exc:
            skipped.append({"run_name": run_name, "error": str(exc)})
    return {
        "selected_runs": selected,
        "planned": planned,
        "jobs": jobs,
        "skipped": skipped,
    }


def create_recovery_job(run_name: str, executor_headed: bool = False) -> dict:
    preview = recovery_preview(run_name)
    errors = []

    if preview["strategy"] == "retry_failed":
        try:
            job = create_retry_failed_job(run_name, executor_headed=executor_headed)
            return {"strategy": "retry_failed", "job": job}
        except ValueError as exc:
            errors.append(f"retry_failed: {exc}")

    if preview["strategy"] in {"safe_rerun", "retry_failed"}:
        try:
            job = create_safe_rerun_job(run_name, executor_headed=executor_headed)
            return {"strategy": "safe_rerun", "job": job}
        except ValueError as exc:
            errors.append(f"safe_rerun: {exc}")

    if errors:
        raise ValueError("Recovery failed. " + " | ".join(errors))
    raise ValueError("Recovery failed because no strategy is available.")


def plan_recovery_batch(limit: int = 3, include_warning: bool = True) -> list[dict]:
    limit = max(1, min(int(limit or 1), 6))
    ranked = sort_runs(list_runs(RESULT_DIR), mode="safety_risk")
    planned = []
    for item in ranked:
        status = str(item.get("safety_status", "")).strip().lower()
        if not (status == "critical" or (include_warning and status == "warning")):
            continue
        run_name = str(item.get("run_name", "")).strip()
        if not run_name:
            continue
        try:
            preview = recovery_preview(run_name)
            strategy = str(preview.get("strategy", "")).strip().lower()
            if strategy == "none" or not strategy:
                planned.append(
                    {
                        "run_name": run_name,
                        "safety_index": int(item.get("safety_index", 0) or 0),
                        "safety_status": status,
                        "strategy": "none",
                        "eligible": False,
                        "reason": str(preview.get("reason", "")).strip() or "No eligible recovery strategy.",
                    }
                )
            else:
                planned.append(
                    {
                        "run_name": run_name,
                        "safety_index": int(item.get("safety_index", 0) or 0),
                        "safety_status": status,
                        "strategy": strategy,
                        "eligible": True,
                        "reason": str(preview.get("reason", "")).strip(),
                    }
                )
        except Exception as exc:
            planned.append(
                {
                    "run_name": run_name,
                    "safety_index": int(item.get("safety_index", 0) or 0),
                    "safety_status": status,
                    "strategy": "none",
                    "eligible": False,
                    "reason": str(exc),
                }
            )
        if len(planned) >= limit:
            break
    return planned


def create_recovery_batch_jobs(limit: int = 3, include_warning: bool = True, executor_headed: bool = False) -> dict:
    planned = plan_recovery_batch(limit=limit, include_warning=include_warning)
    selected = [item["run_name"] for item in planned if bool(item.get("eligible", False))]
    jobs = []
    skipped = []
    for item in planned:
        if bool(item.get("eligible", False)):
            continue
        skipped.append({"run_name": item.get("run_name", ""), "error": item.get("reason", "") or "Not eligible"})
    for run_name in selected:
        try:
            recovery = create_recovery_job(run_name, executor_headed=executor_headed)
            jobs.append({"strategy": recovery.get("strategy", ""), "job": recovery.get("job", {})})
        except Exception as exc:
            skipped.append({"run_name": run_name, "error": str(exc)})
    return {
        "selected_runs": selected,
        "planned": planned,
        "jobs": jobs,
        "skipped": skipped,
    }


def has_recent_safe_rerun(source_run_name: str, cooldown_minutes: int = 20) -> tuple[bool, str]:
    source_run_name = str(source_run_name or "").strip()
    if not source_run_name:
        return False, ""
    now = datetime.now()
    window_start = now - timedelta(minutes=max(1, int(cooldown_minutes or 1)))

    job_items = get_all_jobs()
    for job in job_items:
        payload = job.get("payload", {}) if isinstance(job.get("payload", {}), dict) else {}
        if str(payload.get("mode", "")).strip().lower() != "safe_rerun":
            continue
        if str(payload.get("source_run_name", "")).strip() != source_run_name:
            continue
        status = str(job.get("status", "")).strip().lower()
        if status in {"queued", "running"}:
            return True, "Safe rerun for this run is still active. Wait until the previous job is finished."
        timestamp = parse_iso_datetime(job.get("updated_at")) or parse_iso_datetime(job.get("created_at"))
        if timestamp and timestamp >= window_start:
            return True, "Safe rerun was executed recently. Wait for cooldown before rerunning."

    prefix = f"{source_run_name}_safe_"
    if RESULT_DIR.exists():
        for run in RESULT_DIR.iterdir():
            if not run.is_dir() or not run.name.startswith(prefix):
                continue
            try:
                modified = datetime.fromtimestamp(run.stat().st_mtime)
            except Exception:
                continue
            if modified >= window_start:
                return True, "Safe rerun was created recently for this run. Wait for cooldown before rerunning."
    return False, ""


def safe_rerun_eligibility(run_name: str) -> dict:
    run_name = str(run_name or "").strip()
    if not run_name:
        return {"eligible": False, "reason": "Invalid run name."}
    run_dir = RESULT_DIR / run_name
    if not run_dir.exists():
        return {"eligible": False, "reason": "Run not found."}
    has_recent, reason = has_recent_safe_rerun(run_name, cooldown_minutes=20)
    if has_recent:
        return {"eligible": False, "reason": reason}
    return {"eligible": True, "reason": ""}


def active_recovery_jobs_for_host(host: str) -> int:
    host = str(host or "").strip().lower()
    if not host:
        return 0
    job_items = get_all_jobs()
    count = 0
    for job in job_items:
        payload = job.get("payload", {}) if isinstance(job.get("payload", {}), dict) else {}
        mode = str(payload.get("mode", "")).strip().lower()
        if mode not in {"safe_rerun", "retry_failed"}:
            continue
        status = str(job.get("status", "")).strip().lower()
        if status not in {"queued", "running"}:
            continue
        url = str(payload.get("url", "")).strip()
        job_host = (urlparse(url).netloc or "").replace("www.", "").lower()
        if job_host == host:
            count += 1
    return count


def domain_recovery_limit_check(url: str, max_concurrent: int = 2) -> tuple[bool, str]:
    env_value = str(os.getenv("QA_AI_RECOVERY_MAX_CONCURRENT", "")).strip()
    if env_value.isdigit():
        max_concurrent = max(1, int(env_value))
    host = (urlparse(str(url or "")).netloc or "").replace("www.", "").lower()
    if not host:
        return True, ""
    active = active_recovery_jobs_for_host(host)
    if active >= max_concurrent:
        return (
            False,
            f"Recovery limited: there are already {active} active recovery jobs for domain {host} (limit {max_concurrent}).",
        )
    return True, ""


def should_cancel_recovery_by_trend(payload: dict) -> tuple[bool, str]:
    mode = str(payload.get("mode", "")).strip().lower()
    if mode not in {"safe_rerun", "retry_failed"}:
        return False, ""
    source_run_name = str(payload.get("source_run_name", "")).strip()
    baseline = int(payload.get("baseline_safety_index", 0) or 0)
    if not source_run_name or baseline <= 0:
        return False, ""
    source_dir = RESULT_DIR / source_run_name
    if not source_dir.exists():
        return False, ""
    current_detail = build_run_detail(source_dir)
    current = int(current_detail.get("safety_index", 0) or 0)
    if current + 8 < baseline:
        return True, (
            f"Recovery dibatalkan: safety index sumber turun dari {baseline} ke {current}. "
            "Perform a manual review before continuing."
        )
    return False, ""


def build_safe_rerun_instruction(detail: dict) -> str:
    recommended = list(detail.get("safety_recommendations", []) or [])[:4]
    instruction_lines = [
        "Safe rerun mode: prioritize grounded and concrete UI/API targets only.",
        "Reject ambiguous assertions and unsupported surfaces.",
        "Use conservative execution assumptions and add manual checkpoint if uncertain.",
    ]
    if recommended:
        instruction_lines.append("Recovery focus:")
        instruction_lines.extend(f"- {item}" for item in recommended)
    return "\n".join(instruction_lines).strip()


def run_retry_failed_job(job_id: str, payload: dict) -> None:
    from website.dashboard import _append_recovery_action
    try:
        should_cancel, cancel_reason = should_cancel_recovery_by_trend(payload)
        if should_cancel:
            append_job_log(job_id, cancel_reason)
            update_job(job_id, status="canceled", exit_code=1)
            source_run_name = str(payload.get("source_run_name", "")).strip()
            if source_run_name:
                _append_recovery_action(
                    source_run_name,
                    {
                        "action": "retry_failed_result",
                        "strategy": "retry_failed",
                        "status": "canceled",
                        "reason": cancel_reason,
                    },
                )
            return
        update_job(job_id, status="running")
        prepared = prepare_retry_run(payload["source_run_name"], payload["retry_run_name"], payload.get("executor_headed", False))
        append_job_log(job_id, f"Prepared retry run: {prepared['run_name']}")
        append_job_log(job_id, f"Failed cases: {', '.join(prepared['failed_ids'])}")
        command = [sys.executable, prepared["script_path"].name]
        update_job(job_id, command=command, run_name=prepared["run_name"])
        exit_code = run_logged_process(job_id, command, str(prepared["script_path"].parent))
        if exit_code == 0 and prepared["results_path"].exists():
            summary = analyze_execution_results(prepared["results_path"])
            save_execution_summary(prepared["results_path"], summary)
            csv_runner = Scanner(RESULT_DIR)
            csv_runner.update_csv_with_execution_results(prepared["csv_path"], prepared["results_path"], ",")
        status = "completed" if exit_code == 0 else "failed"
        update_job(job_id, status=status, exit_code=exit_code)
        source_run_name = str(payload.get("source_run_name", "")).strip()
        if source_run_name:
            _append_recovery_action(
                source_run_name,
                {
                    "action": "retry_failed_result",
                    "strategy": "retry_failed",
                    "status": "completed" if exit_code == 0 else "failed",
                    "job_id": job_id,
                    "target_run": prepared.get("run_name", ""),
                },
            )
    except Exception as exc:
        append_job_log(job_id, f"Retry failed only error: {exc}")
        update_job(job_id, status="failed", exit_code=1)
        source_run_name = str(payload.get("source_run_name", "")).strip()
        if source_run_name:
            _append_recovery_action(
                source_run_name,
                {
                    "action": "retry_failed_result",
                    "strategy": "retry_failed",
                    "status": "failed",
                    "reason": str(exc),
                },
            )


def prepare_retry_run(source_run_name: str, retry_run_name: str, executor_headed: bool) -> dict:
    source_run_dir = RESULT_DIR / source_run_name
    retry_run_dir = RESULT_DIR / retry_run_name
    retry_run_dir.mkdir(parents=True, exist_ok=True)
    (retry_run_dir / "JSON").mkdir(parents=True, exist_ok=True)
    (retry_run_dir / "Evidence" / "Video").mkdir(parents=True, exist_ok=True)

    source_detail = build_run_detail(source_run_dir)
    failed_ids = [row["id"] for row in source_detail.get("case_rows", []) if row.get("status") == "failed"]
    if not failed_ids:
        raise ValueError("No failed cases available for retry.")

    source_plan_path = next((source_run_dir / "JSON").glob("Execution_Plan_*.json"), None)
    source_csv_path = next(source_run_dir.glob("*.csv"), None)
    if not source_plan_path or not source_csv_path:
        raise FileNotFoundError("Source execution plan or CSV not found.")

    execution_plan = json.loads(source_plan_path.read_text(encoding="utf-8"))
    filtered_plans = [plan for plan in execution_plan.get("plans", []) if str(plan.get("id", "")).strip() in failed_ids]
    if not filtered_plans:
        raise ValueError("Execution plan for failed cases not found.")

    filtered_plan = {**execution_plan, "plans": filtered_plans}
    filtered_plan_path = json_artifact_path(retry_run_dir, f"Execution_Plan_{retry_run_name}.json")
    filtered_plan_path.write_text(json.dumps(filtered_plan, indent=2), encoding="utf-8")

    retry_csv_path = retry_run_dir / f"{retry_run_name}.csv"
    with source_csv_path.open("r", encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    fieldnames = list(rows[0].keys()) if rows else []
    filtered_rows = [row for row in rows if str(row.get("ID", "")).strip() in failed_ids]
    with retry_csv_path.open("w", encoding="utf-8-sig", newline="") as handle:
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
        shutil.copy2(artifact, retry_run_dir / "JSON" / artifact.name)

    retry_meta_path = json_artifact_path(retry_run_dir, "Retry_Metadata.json")
    retry_meta_path.write_text(
        json.dumps(
            {
                "source_run_name": source_run_name,
                "retry_run_name": retry_run_name,
                "failed_ids": failed_ids,
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    script_path = CodeGenerator(None).generate_pom_script(
        {"run_dir": str(retry_run_dir)},
        filtered_plan_path,
        headless=not executor_headed,
    )

    return {
        "run_name": retry_run_name,
        "run_dir": retry_run_dir,
        "script_path": script_path,
        "csv_path": retry_csv_path,
        "results_path": execution_results_path(retry_run_dir),
        "failed_ids": failed_ids,
    }
