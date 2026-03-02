import json
import queue
import re
import shutil
import subprocess
import sys
import threading
import time
import uuid
import csv
from datetime import datetime
from pathlib import Path

from flask import Flask, abort, jsonify, render_template, request, send_file, url_for

from core.artifacts import execution_results_path, json_artifact_path
from core.dashboard_data import build_knowledge_snapshot, build_run_detail, list_runs, safe_run_artifact
from core.executor import CodeGenerator
from core.feedback_bank import merge_human_feedback
from core.instruction_templates import (
    ensure_instruction_templates,
    list_instruction_templates,
    load_instruction_template,
    resolve_instruction_template,
    save_uploaded_template,
)
from core.result_analyzer import analyze_execution_results, save_execution_summary
from core.scanner import Scanner
from core.site_profiles import derive_cluster_keys, merge_execution_learning


ROOT_DIR = Path(__file__).resolve().parent
RESULT_DIR = ROOT_DIR / "Result"
PROFILES_DIR = ROOT_DIR / "site_profiles"
FEEDBACK_DIR = PROFILES_DIR / "feedback"
ANSI_PATTERN = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
INSTRUCTIONS_DIR = ROOT_DIR / "instructions"

app = Flask(
    __name__,
    template_folder=str(ROOT_DIR / "web" / "templates"),
    static_folder=str(ROOT_DIR / "web" / "static"),
)
app.config["TEMPLATES_AUTO_RELOAD"] = True
app.config["SEND_FILE_MAX_AGE_DEFAULT"] = 0
app.jinja_env.auto_reload = True

jobs_lock = threading.Lock()
jobs: dict[str, dict] = {}
ensure_instruction_templates(INSTRUCTIONS_DIR)
csv_runner = Scanner(RESULT_DIR)


def strip_ansi(text: str) -> str:
    return ANSI_PATTERN.sub("", text or "")


def _form_bool(value: str | None, default: bool = False) -> bool:
    normalized = str(value or "").strip().lower()
    if not normalized:
        return default
    return normalized in {"1", "true", "yes", "y", "on", "iya"}


def create_job(payload: dict) -> dict:
    job_id = uuid.uuid4().hex[:12]
    existing_runs = {item["run_name"] for item in list_runs(RESULT_DIR)}
    job = {
        "id": job_id,
        "status": "queued",
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "updated_at": datetime.now().isoformat(timespec="seconds"),
        "payload": payload,
        "log_lines": [],
        "run_name": "",
        "command": [],
    }
    with jobs_lock:
        jobs[job_id] = job
    thread = threading.Thread(target=_run_job, args=(job_id, payload, existing_runs), daemon=True)
    thread.start()
    return job


def create_retry_failed_job(run_name: str, executor_headed: bool = False) -> dict:
    source_run_dir = RESULT_DIR / run_name
    if not source_run_dir.exists():
        raise FileNotFoundError(run_name)
    source_detail = build_run_detail(source_run_dir)
    failed_ids = [row["id"] for row in source_detail.get("case_rows", []) if row.get("status") == "failed"]
    if not source_detail.get("execution_ran"):
        raise ValueError("Run ini belum punya hasil eksekusi dari AI Executor.")
    if not failed_ids:
        raise ValueError("Tidak ada failed case yang bisa di-retry.")

    retry_run_name = f"{run_name}_retry_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    job_id = uuid.uuid4().hex[:12]
    payload = {
        "mode": "retry_failed",
        "url": source_detail.get("url", ""),
        "source_run_name": run_name,
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
    with jobs_lock:
        jobs[job_id] = job
    thread = threading.Thread(target=_run_retry_failed_job, args=(job_id, payload), daemon=True)
    thread.start()
    return job


def _run_job(job_id: str, payload: dict, existing_runs: set[str]) -> None:
    command = [
        sys.executable,
        "agent.py",
        "--url",
        payload["url"],
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

    _update_job(job_id, status="running", command=command)
    exit_code = _run_logged_process(job_id, command, ROOT_DIR)
    new_runs = [item for item in list_runs(RESULT_DIR) if item["run_name"] not in existing_runs]
    run_name = new_runs[0]["run_name"] if new_runs else ""
    status = "completed" if exit_code == 0 else "failed"
    _update_job(job_id, status=status, run_name=run_name, exit_code=exit_code)


def _run_retry_failed_job(job_id: str, payload: dict) -> None:
    try:
        _update_job(job_id, status="running")
        prepared = _prepare_retry_run(payload["source_run_name"], payload["retry_run_name"], payload.get("executor_headed", False))
        _append_job_log(job_id, f"Prepared retry run: {prepared['run_name']}")
        _append_job_log(job_id, f"Failed cases: {', '.join(prepared['failed_ids'])}")
        command = [sys.executable, prepared["script_path"].name]
        _update_job(job_id, command=command, run_name=prepared["run_name"])
        exit_code = _run_logged_process(job_id, command, prepared["script_path"].parent)
        if exit_code == 0 and prepared["results_path"].exists():
            summary = analyze_execution_results(prepared["results_path"])
            save_execution_summary(prepared["results_path"], summary)
            csv_runner.update_csv_with_execution_results(prepared["csv_path"], prepared["results_path"], ",")
        status = "completed" if exit_code == 0 else "failed"
        _update_job(job_id, status=status, exit_code=exit_code)
    except Exception as exc:
        _append_job_log(job_id, f"Retry failed only error: {exc}")
        _update_job(job_id, status="failed", exit_code=1)


def _run_logged_process(job_id: str, command: list[str], cwd: Path) -> int:
    process = subprocess.Popen(
        command,
        cwd=cwd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
    )

    line_queue: queue.Queue[str] = queue.Queue()

    def reader() -> None:
        assert process.stdout is not None
        for line in process.stdout:
            line_queue.put(strip_ansi(line.rstrip()))

    reader_thread = threading.Thread(target=reader, daemon=True)
    reader_thread.start()

    while process.poll() is None or not line_queue.empty():
        try:
            line = line_queue.get(timeout=0.2)
        except queue.Empty:
            continue
        _append_job_log(job_id, line)

    reader_thread.join(timeout=1)
    return process.wait()


def _prepare_retry_run(source_run_name: str, retry_run_name: str, executor_headed: bool) -> dict:
    source_run_dir = RESULT_DIR / source_run_name
    retry_run_dir = RESULT_DIR / retry_run_name
    retry_run_dir.mkdir(parents=True, exist_ok=True)
    (retry_run_dir / "JSON").mkdir(parents=True, exist_ok=True)
    (retry_run_dir / "Evidence" / "Video").mkdir(parents=True, exist_ok=True)

    source_detail = build_run_detail(source_run_dir)
    failed_ids = [row["id"] for row in source_detail.get("case_rows", []) if row.get("status") == "failed"]
    if not failed_ids:
        raise ValueError("Tidak ada failed case yang bisa di-retry.")

    source_plan_path = next((source_run_dir / "JSON").glob("Execution_Plan_*.json"), None)
    source_csv_path = next(source_run_dir.glob("*.csv"), None)
    if not source_plan_path or not source_csv_path:
        raise FileNotFoundError("Execution plan atau CSV sumber tidak ditemukan.")

    execution_plan = json.loads(source_plan_path.read_text(encoding="utf-8"))
    filtered_plans = [plan for plan in execution_plan.get("plans", []) if str(plan.get("id", "")).strip() in failed_ids]
    if not filtered_plans:
        raise ValueError("Execution plan untuk failed case tidak ditemukan.")

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


def _append_job_log(job_id: str, line: str) -> None:
    with jobs_lock:
        job = jobs[job_id]
        job["log_lines"].append(line)
        job["log_lines"] = job["log_lines"][-200:]
        job["updated_at"] = datetime.now().isoformat(timespec="seconds")


def _update_job(job_id: str, **fields) -> None:
    with jobs_lock:
        job = jobs[job_id]
        job.update(fields)
        job["updated_at"] = datetime.now().isoformat(timespec="seconds")


def _get_job(job_id: str) -> dict:
    with jobs_lock:
        job = jobs.get(job_id)
        if not job:
            raise KeyError(job_id)
        return json.loads(json.dumps(job))


def dashboard_metrics() -> dict:
    runs = list_runs(RESULT_DIR)
    totals = {
        "runs": len(runs),
        "cases": sum(item.get("total_cases", 0) for item in runs),
        "videos": sum(item.get("video_count", 0) for item in runs),
        "failed": sum(item.get("status_counts", {}).get("failed", 0) for item in runs),
    }
    return totals


@app.get("/")
def home():
    runs = list_runs(RESULT_DIR)[:8]
    active_jobs = [_get_job(job_id) for job_id in list(jobs.keys())[-8:]][::-1]
    templates = list_instruction_templates(INSTRUCTIONS_DIR)
    knowledge_snapshot = build_knowledge_snapshot(profiles_dir=PROFILES_DIR)
    return render_template(
        "dashboard.html",
        runs=runs,
        active_jobs=active_jobs,
        metrics=dashboard_metrics(),
        templates=templates,
        knowledge_snapshot=knowledge_snapshot,
    )


@app.get("/runs")
def runs_page():
    return render_template("runs.html", runs=list_runs(RESULT_DIR), metrics=dashboard_metrics())


@app.get("/runs/<run_name>")
def run_detail(run_name: str):
    run_dir = RESULT_DIR / run_name
    if not run_dir.exists():
        abort(404)
    detail = build_run_detail(run_dir)
    return render_template("run_detail.html", run=detail)


@app.get("/artifacts/<run_name>/<path:relative_path>")
def serve_artifact(run_name: str, relative_path: str):
    try:
        artifact = safe_run_artifact(run_name, relative_path, RESULT_DIR)
    except (FileNotFoundError, ValueError):
        abort(404)
    return send_file(artifact)


@app.post("/api/jobs")
def create_job_api():
    template_name = request.form.get("template_name", "").strip()
    template_path = ""
    if template_name:
        try:
            template_path = str(resolve_instruction_template(template_name, INSTRUCTIONS_DIR))
        except FileNotFoundError:
            return jsonify({"ok": False, "error": "Template tidak ditemukan."}), 404
    payload = {
        "url": request.form.get("url", "").strip(),
        "instruction": request.form.get("instruction", "").strip(),
        "template_name": template_name,
        "template_path": template_path,
        "csv_sep": request.form.get("csv_sep", ","),
        "crawl_limit": int(request.form.get("crawl_limit", "3") or 3),
        "use_auth": _form_bool(request.form.get("use_auth"), default=False),
        "adaptive_recrawl": _form_bool(request.form.get("adaptive_recrawl"), default=True),
        "run_executor": _form_bool(request.form.get("run_executor"), default=False),
        "executor_headed": _form_bool(request.form.get("executor_headed"), default=False),
    }
    if not payload["url"]:
        return jsonify({"ok": False, "error": "URL wajib diisi."}), 400
    job = create_job(payload)
    return jsonify({"ok": True, "job": job, "redirect": url_for("home")})


@app.post("/api/runs/<run_name>/retry-failed")
def retry_failed_job_api(run_name: str):
    executor_headed = _form_bool(request.form.get("executor_headed"), default=False)
    try:
        job = create_retry_failed_job(run_name, executor_headed=executor_headed)
    except FileNotFoundError:
        return jsonify({"ok": False, "error": "Run tidak ditemukan."}), 404
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    return jsonify({"ok": True, "job": job, "redirect": url_for("run_detail", run_name=job["run_name"])})


@app.post("/api/runs/<run_name>/feedback")
def run_feedback_api(run_name: str):
    run_dir = RESULT_DIR / run_name
    if not run_dir.exists():
        return jsonify({"ok": False, "error": "Run tidak ditemukan."}), 404

    detail = build_run_detail(run_dir)
    url = str(detail.get("url", "")).strip()
    if not url:
        return jsonify({"ok": False, "error": "Run ini belum punya URL sumber yang valid."}), 400

    feedback_payload = {
        "feedback_type": request.form.get("feedback_type", "").strip(),
        "verdict": request.form.get("verdict", "").strip(),
        "case_id": request.form.get("case_id", "").strip(),
        "selector": request.form.get("selector", "").strip(),
        "semantic_key": request.form.get("semantic_key", "").strip(),
        "page_type": detail.get("page_type", ""),
        "run_name": run_name,
        "note": request.form.get("note", "").strip(),
    }
    cluster_keys = derive_cluster_keys(detail.get("page_model", {}), detail.get("page_scope", {}))

    try:
        feedback_result = merge_human_feedback(
            url,
            feedback_payload,
            run_dir=run_dir,
            feedback_dir=FEEDBACK_DIR,
            cluster_keys=cluster_keys,
        )
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400

    learning_result = None
    if (
        feedback_payload["feedback_type"] == "selector_quality"
        and feedback_payload["selector"]
        and feedback_payload["semantic_key"]
    ):
        semantic_key = feedback_payload["semantic_key"]
        verdict = feedback_payload["verdict"].lower()
        synthetic_learning = {
            "learning_entries": [
                {
                    "id": feedback_payload["case_id"] or "feedback",
                    "status": "passed" if verdict == "helpful" else "failed",
                    "resolved_selector": feedback_payload["selector"] if verdict == "helpful" else "",
                    "attempted": [feedback_payload["selector"]],
                    "error": "" if verdict == "helpful" else "manual feedback marked selector as misleading",
                    "details": {
                        "field_key": semantic_key,
                        "semantic_type": semantic_key,
                        "semantic_label": semantic_key.replace("_", " ").title(),
                        "target": semantic_key.replace("_", " ").title(),
                    },
                }
            ]
        }
        learning_result = merge_execution_learning(
            url,
            synthetic_learning,
            profiles_dir=PROFILES_DIR,
            knowledge_context={
                "page_model": detail.get("page_model", {}),
                "page_scope": detail.get("page_scope", {}),
            },
        )

    refreshed_detail = build_run_detail(run_dir)
    return jsonify(
        {
            "ok": True,
            "feedback": feedback_result["entry"],
            "feedback_summary": refreshed_detail.get("run_feedback_summary", {}),
            "knowledge_snapshot": refreshed_detail.get("knowledge_snapshot", {}),
            "learning_sync": learning_result or {},
        }
    )


@app.get("/api/jobs")
def jobs_api():
    with jobs_lock:
        items = [json.loads(json.dumps(job)) for job in jobs.values()]
    items.sort(key=lambda item: item["created_at"], reverse=True)
    return jsonify({"jobs": items})


@app.get("/api/jobs/<job_id>")
def job_detail_api(job_id: str):
    try:
        job = _get_job(job_id)
    except KeyError:
        abort(404)
    return jsonify({"job": job})


@app.get("/api/runs")
def runs_api():
    return jsonify({"runs": list_runs(RESULT_DIR)})


@app.get("/api/templates")
def templates_api():
    return jsonify({"templates": list_instruction_templates(INSTRUCTIONS_DIR)})


@app.get("/api/templates/<template_name>")
def template_detail_api(template_name: str):
    try:
        template = load_instruction_template(template_name, INSTRUCTIONS_DIR)
    except FileNotFoundError:
        abort(404)
    return jsonify({"template": template})


@app.post("/api/templates")
def upload_template_api():
    uploaded_file = request.files.get("template_file")
    if not uploaded_file or not uploaded_file.filename:
        return jsonify({"ok": False, "error": "Pilih file .txt terlebih dulu."}), 400
    try:
        template = save_uploaded_template(uploaded_file, INSTRUCTIONS_DIR)
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    return jsonify({"ok": True, "template": template})


@app.get("/health")
def health():
    return jsonify({"ok": True, "time": time.time()})


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=False)
