"""Visual Regression Testing – routes blueprint."""
from __future__ import annotations

from flask import Blueprint, jsonify, redirect, render_template, request, url_for

from core.config import RESULT_DIR
from core.dashboard_data import build_run_detail, list_runs, sort_runs
from core.jobs import create_job, is_duplicate_recent_job
from core.utils import form_bool, normalize_input_url, load_json_file, resolve_run_dir
from modules.visual_regression_testing.src.visual_regression import save_visual_approval
from modules.visual_regression_testing.web.features.visual_regression_testing import (
    build_vrt_changes_context,
    build_vrt_monitor_context,
    build_vrt_scan_context,
)

bp = Blueprint("visual_regression_testing", __name__, template_folder="../templates")


# ---------------------------------------------------------------------------
# Page routes
# ---------------------------------------------------------------------------

@bp.get("/visual-regression")
def visual_regression_page():
    return redirect(url_for("visual_regression_testing.vrt_scan_page"))


@bp.get("/visual-regression-testing/scan")
def vrt_scan_page():
    from core.dashboard_data import dashboard_metrics
    return render_template("visual_regression_scan.html", **build_vrt_scan_context(dashboard_metrics=dashboard_metrics))


@bp.get("/visual-regression-testing/changes")
def vrt_changes_page():
    from core.dashboard_data import dashboard_metrics
    context = build_vrt_changes_context(
        result_dir=RESULT_DIR,
        list_runs=list_runs,
        sort_runs=sort_runs,
        dashboard_metrics=dashboard_metrics,
    )
    return render_template("visual_regression_results.html", **context)


@bp.get("/visual-regression-testing/diff-monitor")
def vrt_monitor_page():
    from core.dashboard_data import dashboard_metrics
    context = build_vrt_monitor_context(
        result_dir=RESULT_DIR,
        selected_run_name=str(request.args.get("run_name", "")).strip(),
        list_runs=list_runs,
        sort_runs=sort_runs,
        dashboard_metrics=dashboard_metrics,
    )
    return render_template("visual_regression_monitor.html", **context)


# ---------------------------------------------------------------------------
# API routes
# ---------------------------------------------------------------------------

@bp.post("/api/vrt-jobs")
def create_vrt_job_api():
    try:
        url = normalize_input_url(request.form.get("url", ""))
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    if not url:
        return jsonify({"ok": False, "error": "URL is required."}), 400
    payload = {
        "mode": "vrt_scan",
        "url": url,
        "instruction": (
            "Focus on Visual Regression Testing: capture full-page visual baseline and detect changes in text, layout, typography, color, "
            "spacing, alignment, components, and responsiveness."
        ),
        "template_name": "",
        "template_path": "",
        "csv_sep": ",",
        "use_auth": form_bool(request.form.get("use_auth"), default=False),
        "adaptive_recrawl": False,
        "run_executor": False,
        "executor_headed": False,
        "run_name": "",
    }
    
    try:
        payload["crawl_limit"] = max(1, min(int(request.form.get("crawl_limit", "1") or 1), 10))
    except (ValueError, TypeError):
        payload["crawl_limit"] = 1

    is_duplicate, duplicate_error = is_duplicate_recent_job(payload, cooldown_seconds=10)
    if is_duplicate:
        return jsonify({"ok": False, "error": duplicate_error}), 409
    job = create_job(payload)
    return jsonify({"ok": True, "job": job, "redirect": url_for("visual_regression_testing.vrt_scan_page")})


@bp.get("/api/runs/<run_name>/visual-diff")
def run_visual_diff_api(run_name: str):
    from core.artifacts import visual_regression_approval_path

    try:
        run_dir = resolve_run_dir(run_name)
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    if not run_dir.exists():
        return jsonify({"ok": False, "error": "Run not found."}), 404
    detail = build_run_detail(run_dir)
    approval = load_json_file(visual_regression_approval_path(run_dir, create=False))
    payload = {
        "run_name": detail.get("run_name", run_name),
        "url": detail.get("url", ""),
        "baseline_run": detail.get("vrt_baseline_run", ""),
        "change_count": int(detail.get("vrt_change_count", 0) or 0),
        "summary": detail.get("vrt_summary", {}),
        "changed_areas": (detail.get("visual_diff", {}) if isinstance(detail.get("visual_diff", {}), dict) else {}).get("changed_areas", []),
        "visual_regression": detail.get("visual_regression", {}),
        "visual_regression_approval": approval or {},
    }
    return jsonify({"ok": True, "visual_diff": payload})


@bp.get("/api/runs/<run_name>/visual-baseline-approval")
def run_visual_baseline_approval_api(run_name: str):
    from core.artifacts import visual_regression_approval_path

    try:
        run_dir = resolve_run_dir(run_name)
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    if not run_dir.exists():
        return jsonify({"ok": False, "error": "Run not found."}), 404
    approval = load_json_file(visual_regression_approval_path(run_dir, create=False))
    return jsonify({"ok": True, "run_name": run_name, "approval": approval})


@bp.post("/api/runs/<run_name>/visual-baseline-approval")
def upsert_visual_baseline_approval_api(run_name: str):
    status = request.form.get("status", "").strip().lower() or str((request.get_json(silent=True) or {}).get("status", "")).strip().lower()
    note = request.form.get("note", "").strip() or str((request.get_json(silent=True) or {}).get("note", "")).strip()
    compare_run = request.form.get("compare_run", "").strip() or str((request.get_json(silent=True) or {}).get("compare_run", "")).strip()
    actor = request.form.get("actor", "").strip() or str((request.get_json(silent=True) or {}).get("actor", "manual")).strip()
    try:
        payload = save_visual_approval(run_name, status=status, note=note, compare_run=compare_run, actor=actor)
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    except FileNotFoundError:
        return jsonify({"ok": False, "error": "Run not found."}), 404
    return jsonify({"ok": True, "approval": payload})
