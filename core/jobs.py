import json
import os
import queue
import re
import subprocess
import sys
import threading
import uuid
import atexit
import psutil
from datetime import datetime
from urllib.parse import urlparse

from core.config import ROOT_DIR, RESULT_DIR
from core.utils import parse_iso_datetime, atomic_write_json, load_json_file, get_logger

logger = get_logger("core.jobs")

jobs_lock = threading.Lock()
_JOBS_FILE = RESULT_DIR / "jobs.json"
jobs: dict[str, dict] = load_json_file(_JOBS_FILE) if _JOBS_FILE.exists() else {}
active_processes: dict[str, subprocess.Popen] = {}
ANSI_PATTERN = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")

def _save_jobs_locked():
    atomic_write_json(_JOBS_FILE, jobs)


def get_job(job_id: str) -> dict:
    with jobs_lock:
        job = jobs.get(job_id)
        if not job:
            raise KeyError(job_id)
        return json.loads(json.dumps(job))


def get_all_jobs() -> list[dict]:
    with jobs_lock:
        items = [json.loads(json.dumps(job)) for job in jobs.values()]
    for job in items:
        status = str(job.get("status", "")).strip().lower()
        job["progress"] = 100 if status in {"completed", "failed"} else 50
    items.sort(key=lambda item: item["created_at"], reverse=True)
    return items


def append_job_log(job_id: str, line: str) -> None:
    with jobs_lock:
        if job_id not in jobs:
            return
        job = jobs[job_id]
        job["log_lines"].append(line)
        job["log_lines"] = job["log_lines"][-1500:]
        job["updated_at"] = datetime.now().isoformat(timespec="seconds")
        # Save every 50 lines or so to avoid extreme disk I/O, but to be simple we save per line
        # Alternatively, rely on update_job and create_job for metadata persistence
        _save_jobs_locked()


def update_job(job_id: str, **fields) -> None:
    with jobs_lock:
        if job_id not in jobs:
            return
        job = jobs[job_id]
        job.update(fields)
        job["updated_at"] = datetime.now().isoformat(timespec="seconds")
        _save_jobs_locked()


def strip_ansi(text: str) -> str:
    return ANSI_PATTERN.sub("", text or "")


def run_logged_process(job_id: str, command: list[str], cwd: str, timeout: int = 1800) -> int:
    process_env = os.environ.copy()
    process_env["PYTHONUNBUFFERED"] = "1"
    process_env["PYTHONIOENCODING"] = "utf-8"
    
    logger.info(f"Starting job {job_id} with command: {' '.join(command)}")
    
    process = subprocess.Popen(
        command,
        cwd=cwd,
        env=process_env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
    )

    with jobs_lock:
        active_processes[job_id] = process

    def cleanup_process():
        if process.poll() is None:
            try:
                parent = psutil.Process(process.pid)
                for child in parent.children(recursive=True):
                    child.terminate()
                parent.terminate()
                logger.warning(f"Killed orphan child processes for job {job_id}")
            except Exception:
                pass
        with jobs_lock:
            active_processes.pop(job_id, None)

    atexit.register(cleanup_process)

    line_queue: queue.Queue[str] = queue.Queue()

    def reader() -> None:
        try:
            assert process.stdout is not None
            for line in process.stdout:
                line_queue.put(strip_ansi(line.rstrip()))
        except Exception:
            pass

    reader_thread = threading.Thread(target=reader, daemon=True)
    reader_thread.start()

    start_time = datetime.now()
    exit_code = None
    
    while True:
        if process.poll() is not None:
            exit_code = process.returncode
            break
            
        if (datetime.now() - start_time).total_seconds() > timeout:
            logger.error(f"Job {job_id} timed out after {timeout}s. Terminating.")
            cleanup_process()
            exit_code = -1
            break

        try:
            line = line_queue.get(timeout=0.2)
            append_job_log(job_id, line)
        except queue.Empty:
            continue

    # Final drain
    while not line_queue.empty():
        line = line_queue.get()
        append_job_log(job_id, line)

    reader_thread.join(timeout=1)
    cleanup_process()
    atexit.unregister(cleanup_process)
    logger.info(f"Job {job_id} finished with exit code {exit_code}")
    return exit_code

def cancel_job(job_id: str) -> bool:
    with jobs_lock:
        process = active_processes.get(job_id)
        if not process:
            # Maybe it's queued but not running yet?
            if job_id in jobs and jobs[job_id].get("status") == "queued":
                jobs[job_id]["status"] = "failed"
                jobs[job_id]["log_lines"].append("[CANCELLED] Job removed from queue.")
                _save_jobs_locked()
                return True
            return False
            
    try:
        parent = psutil.Process(process.pid)
        for child in parent.children(recursive=True):
            child.kill()
        parent.kill()
        logger.info(f"Successfully cancelled job {job_id}")
        return True
    except Exception as exc:
        logger.error(f"Failed to cancel job {job_id}: {exc}")
        return False


def generate_run_name(url: str) -> str:
    parsed = urlparse(str(url or ""))
    host = (parsed.hostname or "").replace("www.", "").lower()
    if host.endswith(".com"):
        host = host[:-4]
    safe_domain = re.sub(r"[^\w]", "_", host)
    safe_domain = re.sub(r"_+", "_", safe_domain).strip("_") or "unknown"
    timestamp = datetime.now().strftime("%d%m%y_%H%M")
    return f"{safe_domain}_{timestamp}"


def job_payload_signature(payload: dict) -> str:
    mode = str(payload.get("mode", "")).strip().lower()
    url = str(payload.get("url", "")).strip().lower().rstrip("/")
    source_run = str(payload.get("source_run_name", "")).strip().lower()
    raw_case_ids = payload.get("selected_case_ids", [])
    if not isinstance(raw_case_ids, list):
        raw_case_ids = []
    case_ids = tuple(sorted(str(item).strip().lower() for item in raw_case_ids if str(item).strip()))
    case_mode = str(payload.get("case_mode", "all")).strip().lower()
    return json.dumps(
        {
            "mode": mode,
            "url": url,
            "source_run_name": source_run,
            "case_mode": case_mode,
            "selected_case_ids": case_ids,
        },
        sort_keys=True,
    )


def is_duplicate_recent_job(payload: dict, cooldown_seconds: int = 10) -> tuple[bool, str]:
    now = datetime.now()
    target_signature = job_payload_signature(payload)
    with jobs_lock:
        job_items = [json.loads(json.dumps(item)) for item in jobs.values()]
    for job in job_items:
        status = str(job.get("status", "")).strip().lower()
        if status not in {"queued", "running"}:
            continue
        created_at = parse_iso_datetime(job.get("created_at"))
        if created_at is None:
            continue
        if (now - created_at).total_seconds() > float(cooldown_seconds):
            continue
        current_payload = job.get("payload", {}) if isinstance(job.get("payload", {}), dict) else {}
        if job_payload_signature(current_payload) == target_signature:
            return True, "Duplicate job detected. Wait a few seconds before submitting the same request."
    return False, ""


def run_job(job_id: str, payload: dict) -> None:
    # This function is a placeholder and will be expanded to handle different job types.
    # For now, it only runs the basic 'agent.py' command.
    command = [
        sys.executable,
        str(ROOT_DIR / "agent.py"),
        "--url",
        payload["url"],
        "--run-name",
        payload["run_name"],
        "--csv-sep",
        payload["csv_sep"],
        "--crawl-limit",
        str(payload["crawl_limit"]),
    ]
    if payload.get("template_path"):
        command.extend(["--instruction-file", payload["template_path"]])
    if payload.get("instruction"):
        command.extend(["--instruction", payload["instruction"]])
    if payload.get("use_auth"):
        command.append("--use-auth")
    if not payload.get("adaptive_recrawl", True):
        command.append("--disable-adaptive-recrawl")
    if payload.get("run_executor"):
        command.append("--run-executor")
    if payload.get("executor_headed"):
        command.append("--executor-headed")

    update_job(job_id, status="running", command=command)
    exit_code = run_logged_process(job_id, command, str(ROOT_DIR))
    status = "completed" if exit_code == 0 else "failed"
    update_job(job_id, status=status, run_name=payload["run_name"], exit_code=exit_code)


def create_job(payload: dict) -> dict:
    job_id = uuid.uuid4().hex[:12]
    run_name = payload.get("run_name") or generate_run_name(payload.get("url", ""))
    payload = {**payload, "run_name": run_name}
    job = {
        "id": job_id,
        "status": "queued",
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "updated_at": datetime.now().isoformat(timespec="seconds"),
        "payload": payload,
        "log_lines": [],
        "run_name": run_name,
        "command": [],
    }
    with jobs_lock:
        jobs[job_id] = job
        _save_jobs_locked()
    thread = threading.Thread(target=run_job, args=(job_id, payload), daemon=True)
    thread.start()
    return job
