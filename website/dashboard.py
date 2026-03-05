import json
import os
import queue
import re
import shutil
import subprocess
import sys
import threading
import time
import uuid
import csv
import io
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import urlparse

# Add project root to sys.path
root_path = Path(__file__).resolve().parent.parent
if str(root_path) not in sys.path:
    sys.path.insert(0, str(root_path))

from flask import Flask, abort, jsonify, make_response, redirect, render_template, request, send_file, url_for

from core.artifacts import (
    execution_results_path,
    json_artifact_path,
    recovery_actions_path,
    visual_regression_approval_path,
)
from core.config import (
    INSTRUCTIONS_DIR,
    PROFILES_DIR,
    RESULT_DIR,
    ROOT_DIR,
    FEEDBACK_DIR,
)
from core.dashboard_data import (
    build_ai_safety_audit,
    build_benchmark_snapshot,
    build_knowledge_snapshot,
    build_run_comparison,
    build_run_detail,
    build_triage_inbox,
    list_runs,
    safe_run_artifact,
    sort_runs,
    dashboard_metrics,
)
from modules.end_to_end_automation.src.executor import CodeGenerator
from core.feedback_bank import merge_human_feedback
from core.guardrails import CONTEXT_RULES, compile_instruction_contract
from core.instruction_templates import (
    ensure_instruction_templates,
    list_instruction_templates,
    load_instruction_template,
    load_template_user_notes,
    resolve_instruction_template,
    save_template_user_note,
    save_uploaded_template,
    update_instruction_template,
)
from core.jobs import get_all_jobs, get_job
from core.result_analyzer import analyze_execution_results, save_execution_summary
from core.scanner import Scanner
from core.site_profiles import derive_cluster_keys, merge_execution_learning
from core.utils import is_automation_or_recovery_run, is_automation_run
from modules.test_case_generator.web.routes.test_case_generator import bp as test_case_generator_bp
from modules.end_to_end_automation.web.routes.end_to_end_automation import bp as end_to_end_automation_bp
from modules.visual_regression_testing.web.routes.visual_regression_testing import bp as visual_regression_testing_bp


app = Flask(
    __name__,
    template_folder=str(ROOT_DIR / "website" / "templates"),
    static_folder=str(ROOT_DIR / "website" / "static"),
)
app.config["TEMPLATES_AUTO_RELOAD"] = True
app.config["SEND_FILE_MAX_AGE_DEFAULT"] = 0
app.jinja_env.auto_reload = True

ensure_instruction_templates(INSTRUCTIONS_DIR)
csv_runner = Scanner(RESULT_DIR)

app.register_blueprint(test_case_generator_bp)
app.register_blueprint(end_to_end_automation_bp)
app.register_blueprint(visual_regression_testing_bp)


@app.context_processor
def inject_sidebar_scenario_runs():
    runs = [item for item in list_runs(RESULT_DIR) if not is_automation_or_recovery_run(item.get("run_name", ""))]
    runs = sorted(runs, key=lambda item: float(item.get("modified_ts", 0) or 0), reverse=True)[:12]
    return {"sidebar_scenario_runs": runs}


def _resolve_run_dir(run_name: str) -> Path:
    candidate = (RESULT_DIR / run_name).resolve()
    result_root = RESULT_DIR.resolve()
    if not candidate.is_relative_to(result_root):
        raise ValueError("Invalid run path.")
    return candidate


def _load_json_file(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _load_visual_approval(run_name: str) -> dict:
    run_dir = _resolve_run_dir(run_name)
    if not run_dir.exists():
        raise FileNotFoundError(run_name)
    return _load_json_file(visual_regression_approval_path(run_dir, create=False))


def _save_visual_approval(run_name: str, status: str, note: str = "", compare_run: str = "", actor: str = "manual") -> dict:
    run_dir = _resolve_run_dir(run_name)
    if not run_dir.exists():
        raise FileNotFoundError(run_name)
    status = str(status or "").strip().lower()
    if status not in {"approved", "rejected", "pending"}:
        raise ValueError("Invalid approval status.")
    payload = {
        "run_name": run_name,
        "status": status,
        "note": str(note or "").strip(),
        "compare_run": str(compare_run or "").strip(),
        "updated_by": str(actor or "manual").strip(),
        "updated_at": datetime.now().isoformat(timespec="seconds"),
    }
    visual_regression_approval_path(run_dir).write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return payload


def _append_recovery_action(run_name: str, action: dict) -> None:
    run_dir = _resolve_run_dir(run_name)
    if not run_dir.exists():
        return
    path = recovery_actions_path(run_dir)
    payload = _load_json_file(path)
    entries = list(payload.get("actions", []))
    item = {
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "run_name": run_name,
        **dict(action or {}),
    }
    entries.append(item)
    entries = entries[-60:]
    path.write_text(json.dumps({"actions": entries}, indent=2), encoding="utf-8")


@app.get("/")
def home():
    runs = list_runs(RESULT_DIR)[:8]
    risk_runs = sort_runs(list_runs(RESULT_DIR), mode="safety_risk")[:6]
    active_jobs = get_all_jobs()[:8]
    knowledge_snapshot = build_knowledge_snapshot(profiles_dir=PROFILES_DIR)
    benchmark_snapshot = build_benchmark_snapshot(RESULT_DIR, limit=6)
    return render_template(
        "dashboard.html",
        runs=runs,
        active_jobs=active_jobs,
        risk_runs=risk_runs,
        metrics=dashboard_metrics(),
        knowledge_snapshot=knowledge_snapshot,
        benchmark_snapshot=benchmark_snapshot,
    )


@app.get("/runs/<run_name>")
def run_detail(run_name: str):
    try:
        run_dir = _resolve_run_dir(run_name)
    except ValueError:
        abort(404)
    if not run_dir.exists():
        abort(404)
    detail = build_run_detail(run_dir)
    return render_template("run_detail.html", run=detail)


@app.get("/compare")
def compare_runs():
    left = request.args.get("left", "").strip()
    right = request.args.get("right", "").strip()
    runs = list_runs(RESULT_DIR)
    comparison = None
    if left and right:
        comparison = build_run_comparison(left, right, RESULT_DIR)
    return render_template("compare.html", runs=runs, comparison=comparison, metrics=dashboard_metrics(), left=left, right=right)


@app.get("/benchmarks")
def benchmark_page():
    benchmark_snapshot = build_benchmark_snapshot(RESULT_DIR, limit=10)
    return render_template("benchmarks.html", benchmark=benchmark_snapshot, metrics=dashboard_metrics())


@app.get("/artifacts/<run_name>/<path:relative_path>")
def serve_artifact(run_name: str, relative_path: str):
    try:
        artifact = safe_run_artifact(run_name, relative_path, RESULT_DIR)
    except (FileNotFoundError, ValueError):
        abort(404)
    return send_file(artifact)


@app.post("/api/runs/<run_name>/delete")
def delete_run_api(run_name: str):
    try:
        run_dir = _resolve_run_dir(run_name)
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400

    if not run_dir.exists() or not run_dir.is_dir():
        return jsonify({"ok": False, "error": "Run not found."}), 404

    shutil.rmtree(run_dir)
    return jsonify({"ok": True, "deleted_run": run_name})


@app.post("/api/runs/<run_name>/feedback")
def run_feedback_api(run_name: str):
    run_dir = RESULT_DIR / run_name
    if not run_dir.exists():
        return jsonify({"ok": False, "error": "Run not found."}), 404

    detail = build_run_detail(run_dir)
    url = str(detail.get("url", "")).strip()
    if not url:
        return jsonify({"ok": False, "error": "This run does not have a valid source URL yet."}), 400

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
    return jsonify({"jobs": get_all_jobs()})


@app.get("/api/jobs/<job_id>")
def job_detail_api(job_id: str):
    try:
        job = get_job(job_id)
    except KeyError:
        abort(404)
    return jsonify({"job": job})


@app.get("/api/runs")
def runs_api():
    return jsonify({"runs": list_runs(RESULT_DIR)})


@app.get("/api/runs/<run_name>/cases")
def run_cases_api(run_name: str):
    try:
        run_dir = _resolve_run_dir(run_name)
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    if not run_dir.exists():
        return jsonify({"ok": False, "error": "Run not found."}), 404
    detail = build_run_detail(run_dir)
    cases = [
        {
            "id": str(row.get("id", "")).strip(),
            "title": str(row.get("title", "")).strip(),
            "status": str(row.get("status", "")).strip(),
            "priority": str(row.get("priority", "")).strip(),
            "automation": str(row.get("automation", "")).strip(),
            "traceability_id": f"{run_name}::{str(row.get('id', '')).strip()}",
        }
        for row in detail.get("case_rows", [])
        if str(row.get("id", "")).strip()
    ]
    return jsonify({"ok": True, "run_name": run_name, "cases": cases})


@app.post("/api/runs/<run_name>/csv-save")
def run_csv_save_api(run_name: str):
    try:
        run_dir = _resolve_run_dir(run_name)
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    if not run_dir.exists():
        return jsonify({"ok": False, "error": "Run not found."}), 404
    csv_path = next(run_dir.glob("*.csv"), None)
    if not csv_path:
        return jsonify({"ok": False, "error": "Run CSV not found."}), 404

    payload = request.get_json(silent=True) or {}
    headers_raw = list(payload.get("headers", []) or [])
    rows_raw = list(payload.get("rows", []) or [])
    headers = [str(item or "").strip() for item in headers_raw if str(item or "").strip()]
    if not headers:
        return jsonify({"ok": False, "error": "CSV headers are empty."}), 400

    unique_headers = []
    seen = set()
    for item in headers:
        if item in seen:
            continue
        seen.add(item)
        unique_headers.append(item)
    if "ID" not in unique_headers:
        return jsonify({"ok": False, "error": "ID column is required."}), 400

    normalized_rows = []
    for row in rows_raw:
        if not isinstance(row, dict):
            continue
        item = {key: str(row.get(key, "")) for key in unique_headers}
        if not str(item.get("ID", "")).strip():
            continue
        normalized_rows.append(item)

    with csv_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=unique_headers)
        writer.writeheader()
        writer.writerows(normalized_rows)

    return jsonify({"ok": True, "run_name": run_name, "saved_rows": len(normalized_rows)})


@app.get("/api/triage-inbox")
def triage_inbox_api():
    limit = int(request.args.get("limit", "12") or 12)
    snapshot = build_triage_inbox(RESULT_DIR, limit=max(1, min(limit, 50)))
    return jsonify({"ok": True, "triage": snapshot})


@app.get("/api/ai-safety-audit")
def ai_safety_audit_api():
    limit = int(request.args.get("limit", "30") or 30)
    snapshot = build_ai_safety_audit(RESULT_DIR, limit=max(1, min(limit, 100)))
    return jsonify({"ok": True, "audit": snapshot})


@app.get("/api/runs/<run_name>/recovery-history")
def recovery_history_api(run_name: str):
    try:
        actions = _get_recovery_actions(run_name, limit=30)
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    except FileNotFoundError:
        return jsonify({"ok": False, "error": "Run not found."}), 404
    return jsonify({"ok": True, "run_name": run_name, "actions": actions})


@app.get("/api/runs/<run_name>/recovery-summary")
def recovery_summary_api(run_name: str):
    try:
        run_dir = _resolve_run_dir(run_name)
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    if not run_dir.exists():
        return jsonify({"ok": False, "error": "Run not found."}), 404
    detail = build_run_detail(run_dir)
    return jsonify(
        {
            "ok": True,
            "run_name": run_name,
            "summary": detail.get("recovery_summary", {}),
            "effectiveness": detail.get("recovery_effectiveness", {}),
        }
    )


@app.get("/api/runs/<run_name>/recovery-export")
def recovery_export_api(run_name: str):
    output_format = str(request.args.get("format", "json")).strip().lower()
    limit = int(request.args.get("limit", "50") or 50)
    try:
        rows = _get_recovery_actions(run_name, limit=max(1, min(limit, 300)))
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    except FileNotFoundError:
        return jsonify({"ok": False, "error": "Run not found."}), 404
    if output_format == "csv":
        buffer = io.StringIO()
        fieldnames = ["timestamp", "action", "strategy", "status", "reason", "job_id", "target_run", "run_name"]
        writer = csv.DictWriter(buffer, fieldnames=fieldnames)
        writer.writeheader()
        for item in rows:
            writer.writerow(
                {
                    "timestamp": str(item.get("timestamp", "")),
                    "action": str(item.get("action", "")),
                    "strategy": str(item.get("strategy", "")),
                    "status": str(item.get("status", "")),
                    "reason": str(item.get("reason", "")),
                    "job_id": str(item.get("job_id", "")),
                    "target_run": str(item.get("target_run", "")),
                    "run_name": run_name,
                }
            )
        response = make_response(buffer.getvalue())
        response.headers["Content-Type"] = "text/csv; charset=utf-8"
        response.headers["Content-Disposition"] = f"attachment; filename={run_name}_recovery.csv"
        return response
    return jsonify({"ok": True, "run_name": run_name, "count": len(rows), "rows": rows})


@app.get("/api/recovery-audit/export")
def recovery_audit_export_api():
    output_format = str(request.args.get("format", "json")).strip().lower()
    rows = _collect_recovery_audit_rows(limit_per_run=60)
    if output_format == "csv":
        buffer = io.StringIO()
        fieldnames = ["run_name", "timestamp", "action", "strategy", "status", "reason", "job_id", "target_run"]
        writer = csv.DictWriter(buffer, fieldnames=fieldnames)
        writer.writeheader()
        for item in rows:
            writer.writerow({key: item.get(key, "") for key in fieldnames})
        response = make_response(buffer.getvalue())
        response.headers["Content-Type"] = "text/csv; charset=utf-8"
        response.headers["Content-Disposition"] = "attachment; filename=recovery_audit.csv"
        return response
    return jsonify({"ok": True, "count": len(rows), "rows": rows})


@app.get("/api/recovery-metrics")
def recovery_metrics_api():
    metrics = _recovery_metrics_snapshot(limit_per_run=60)
    return jsonify({"ok": True, "metrics": metrics})


@app.get("/api/runs/<run_name>/safety")
def run_safety_api(run_name: str):
    try:
        run_dir = _resolve_run_dir(run_name)
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    if not run_dir.exists():
        return jsonify({"ok": False, "error": "Run not found."}), 404
    detail = build_run_detail(run_dir)
    safe_rerun = _safe_rerun_eligibility(run_name)
    retry_failed = _retry_failed_eligibility(detail)
    if retry_failed["eligible"]:
        recovery_strategy = "retry_failed"
        recovery_reason = "There are failed cases and execution gate is not blocked."
    elif safe_rerun["eligible"]:
        recovery_strategy = "safe_rerun"
        recovery_reason = "Retry-failed is not eligible; fallback to conservative safe rerun."
    else:
        recovery_strategy = "none"
        recovery_reason = "No recovery strategy is currently eligible."
    payload = {
        "run_name": detail.get("run_name", run_name),
        "safety_index": int(detail.get("safety_index", 0) or 0),
        "safety_status": detail.get("safety_status", ""),
        "safety_reasons": list(detail.get("safety_reasons", []) or []),
        "safety_recommendations": list(detail.get("safety_recommendations", []) or []),
        "safety_trend": list(detail.get("safety_trend", []) or []),
        "execution_gate": detail.get("execution_gate", {}),
        "replay_verification": detail.get("replay_verification", {}),
        "drift_analysis": detail.get("drift_analysis", {}),
        "policy_pack_report": detail.get("policy_pack_report", {}),
        "safe_rerun": safe_rerun,
        "retry_failed": retry_failed,
        "recovery": {
            "strategy": recovery_strategy,
            "reason": recovery_reason,
        },
    }
    return jsonify({"ok": True, "safety": payload})


@app.get("/health")
def health():
    return jsonify({"ok": True, "time": time.time()})


def main() -> None:
    local_debug = os.getenv("QA_AGENT_DASHBOARD_DEBUG", "1").strip().lower() not in {"0", "false", "no"}
    app.run(
        host="127.0.0.1",
        port=5000,
        debug=local_debug,
        use_reloader=local_debug,
    )


if __name__ == "__main__":
    main()
