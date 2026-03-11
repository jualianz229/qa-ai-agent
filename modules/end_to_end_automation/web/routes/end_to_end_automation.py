from flask import Blueprint, jsonify, render_template, request, url_for, send_file, abort
from io import BytesIO

from core.config import RESULT_DIR
from core.dashboard_data import build_run_detail, list_runs, sort_runs
from core.jobs import get_job, get_all_jobs, is_duplicate_recent_job
from core.utils import form_bool, is_automation_or_recovery_run, is_automation_run
from modules.end_to_end_automation.src.recovery import (
    create_recovery_job,
    create_retry_failed_job,
    create_safe_rerun_job,
    plan_recovery_batch,
    create_recovery_batch_jobs,
    recovery_preview,
    plan_safe_rerun_batch,
    create_safe_rerun_batch_jobs,
    safe_rerun_eligibility,
    build_safe_rerun_instruction,
)
from modules.end_to_end_automation.src.automation import create_automation_job  # legacy wrapper; kept for other callers
from modules.end_to_end_automation.src import e2e
from modules.end_to_end_automation.web.features.end_to_end_automation import (
    build_automation_results_context,
    build_end_to_end_automation_context,
)

bp = Blueprint("end_to_end_automation", __name__, template_folder="../templates")


# ---------------------------------------------------------------------------
# Page routes
# ---------------------------------------------------------------------------

@bp.get("/automation-testing")
def automation_testing_page():
    from website.dashboard import dashboard_metrics
    context = build_end_to_end_automation_context(
        result_dir=RESULT_DIR,
        source_run_name=str(request.args.get("source_run_name", "")).strip(),
        list_runs=list_runs,
        is_automation_or_recovery_run=is_automation_or_recovery_run,
        jobs=get_all_jobs(),
        get_job=get_job,
        dashboard_metrics=dashboard_metrics,
    )
    return render_template("end_to_end_automation.html", **context)


@bp.get("/automation-results")
def automation_results_page():
    from website.dashboard import dashboard_metrics
    sort_mode = request.args.get("sort", "latest").strip() or "latest"
    search_query = request.args.get("q", "").strip()
    try:
        page = max(1, int(request.args.get("page", "1")))
    except ValueError:
        page = 1
    per_page = 15
    context = build_automation_results_context(
        result_dir=RESULT_DIR,
        sort_mode=sort_mode,
        list_runs=list_runs,
        sort_runs=sort_runs,
        is_automation_run=is_automation_run,
        dashboard_metrics=dashboard_metrics,
        search_query=search_query,
        page=page,
        per_page=per_page,
    )
    return render_template("end_to_end_automation_results.html", **context)


# ---------------------------------------------------------------------------
# API routes
# ---------------------------------------------------------------------------

@bp.get("/api/automation/example-format")
def download_example_format_api():
    """Download a specimen CSV file for the E2E automation tool."""
    output = BytesIO()
    # standard format for automation: ID and Title are core
    content = "ID,Title,Precondition,Steps to Reproduce,Expected Result,Priority,Severity\n"
    content += "TC-001,Successful Login,The Swag Labs login page is displayed.,\"1. Open site\n2. Enter username\n3. Click Login\",User is successfully logged in.,P1,Critical\n"
    content += "TC-002,Login with Invalid Password,The login page is displayed.,\"1. Open site\n2. Enter incorrect password\n3. Click Login\",Error message is displayed.,P2,Major\n"
    output.write(content.encode('utf-8-sig'))
    output.seek(0)
    return send_file(
        output,
        mimetype="text/csv",
        as_attachment=True,
        download_name="automation_example_format.csv"
    )

@bp.post("/api/automation-jobs")
def create_automation_job_api():
    """Unified entrypoint for creating E2E automation jobs.

    The form now offers two mutually exclusive sources:

    * ``source_run_name`` + ``case_mode``/``selected_case_ids`` – pick an
      existing run and optionally filter the cases.
    * ``cases_file`` – upload a CSV or JSON file containing the cases.  When a
      file is present the run selection is ignored.

    The payload dictionary is built accordingly and then passed to the shared
    ``create_automation_job`` helper (which delegates to the new
    ``e2e.create_e2e_job``).
    """
    from website.dashboard import _resolve_run_dir

    source_run_name = request.form.get("source_run_name", "").strip()
    inject_login = request.form.get("inject_login", "").strip()
    executor_headed = form_bool(request.form.get("executor_headed"), default=False)

    # source mode: either file upload, manual JSON text, or existing run
    cases_file = request.files.get("cases_file")
    cases_json_text = request.form.get("cases_json")

    if cases_file and cases_file.filename:
        # blob mode; read and classify by extension or JSON parse
        content = cases_file.read().decode("utf-8", errors="ignore")
        try:
            parsed = json.loads(content)
            payload = {"cases": parsed}
        except Exception:
            payload = {"csv": content}
        payload.update({"executor_headed": executor_headed, "inject_login": inject_login})
    elif cases_json_text:
        # manual JSON text mode
        try:
            parsed = json.loads(cases_json_text)
            payload = {"cases": parsed}
        except Exception as exc:
            return jsonify({"ok": False, "error": f"Invalid JSON payload: {exc}"}), 400
        payload.update({"executor_headed": executor_headed, "inject_login": inject_login})
    else:
        # existing-run mode
        case_mode = request.form.get("case_mode", "all").strip().lower()
        selected_case_ids: list[str] = []
        if case_mode == "custom":
            selected_case_ids = [
                item.strip()
                for item in str(request.form.get("case_ids", "")).replace("\n", ",").split(",")
                if item.strip()
            ]
        elif case_mode == "selected":
            selected_case_ids = [item.strip() for item in request.form.getlist("selected_case_ids[]") if item.strip()]
            if not selected_case_ids:
                fallback_ids = str(request.form.get("case_ids", "")).replace("\n", ",").split(",")
                selected_case_ids = [item.strip() for item in fallback_ids if item.strip()]
            if not selected_case_ids:
                return jsonify({"ok": False, "error": "Select at least 1 scenario case for selected mode."}), 400
        elif case_mode == "failed":
            try:
                detail = build_run_detail(_resolve_run_dir(source_run_name))
            except ValueError as exc:
                return jsonify({"ok": False, "error": str(exc)}), 400
            selected_case_ids = [row["id"] for row in detail.get("case_rows", []) if row.get("status") == "failed"]
            if not selected_case_ids:
                return jsonify({"ok": False, "error": "This run has no failed cases for failed-only mode."}), 400

        payload = {
            "source_run_name": source_run_name,
            "selected_case_ids": selected_case_ids,
            "executor_headed": executor_headed,
            "inject_login": inject_login,
        }

    # duplicate detection stays the same
    is_duplicate, duplicate_error = is_duplicate_recent_job(payload, cooldown_seconds=10)
    if is_duplicate:
        return jsonify({"ok": False, "error": duplicate_error}), 409

    try:
        # direct call to the new e2e helper avoids accidentally passing the
        # entire payload dict as a positional argument (see issue with the
        # compatibility wrapper).  most callers still use the wrapper, but the
        # API route is free to work with the dict itself.
        job = e2e.create_e2e_job(payload)
    except FileNotFoundError:
        return jsonify({"ok": False, "error": "Source run not found."}), 404
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400

    response = {"ok": True, "job": job}
    try:
        response["redirect"] = url_for("run_detail", run_name=job["run_name"])
    except Exception:
        pass
    return jsonify(response)


@bp.post("/api/runs/<run_name>/retry-failed")
def retry_failed_job_api(run_name: str):
    from website.dashboard import _append_recovery_action
    executor_headed = form_bool(request.form.get("executor_headed"), default=False)
    try:
        job = create_retry_failed_job(run_name, executor_headed=executor_headed)
    except FileNotFoundError:
        return jsonify({"ok": False, "error": "Run not found."}), 404
    except ValueError as exc:
        _append_recovery_action(run_name, {"action": "retry_failed", "status": "rejected", "reason": str(exc)})
        return jsonify({"ok": False, "error": str(exc)}), 400
    _append_recovery_action(
        run_name,
        {"action": "retry_failed", "status": "queued", "job_id": job.get("id", ""), "target_run": job.get("run_name", "")},
    )
    return jsonify({"ok": True, "job": job, "redirect": url_for("run_detail", run_name=job["run_name"])})


@bp.post("/api/runs/<run_name>/safe-rerun")
def safe_rerun_job_api(run_name: str):
    from website.dashboard import _append_recovery_action
    executor_headed = form_bool(request.form.get("executor_headed"), default=False)
    try:
        job = create_safe_rerun_job(run_name, executor_headed=executor_headed)
    except FileNotFoundError:
        return jsonify({"ok": False, "error": "Run not found."}), 404
    except ValueError as exc:
        _append_recovery_action(run_name, {"action": "safe_rerun", "status": "rejected", "reason": str(exc)})
        return jsonify({"ok": False, "error": str(exc)}), 400
    _append_recovery_action(
        run_name,
        {"action": "safe_rerun", "status": "queued", "job_id": job.get("id", ""), "target_run": job.get("run_name", "")},
    )
    return jsonify({"ok": True, "job": job, "redirect": url_for("home")})


@bp.get("/api/runs/<run_name>/download-script")
def download_script_api(run_name: str):
    try:
        script_path = e2e.download_script(run_name)
    except FileNotFoundError:
        abort(404)
    return send_file(script_path, as_attachment=True, download_name=script_path.name)


@bp.post("/api/runs/<run_name>/recover")
def recover_run_api(run_name: str):
    from website.dashboard import _append_recovery_action
    executor_headed = form_bool(request.form.get("executor_headed"), default=False)
    try:
        recovery = create_recovery_job(run_name, executor_headed=executor_headed)
    except FileNotFoundError:
        return jsonify({"ok": False, "error": "Run not found."}), 404
    except ValueError as exc:
        _append_recovery_action(run_name, {"action": "auto_recover", "status": "rejected", "reason": str(exc)})
        return jsonify({"ok": False, "error": str(exc)}), 400
    _append_recovery_action(
        run_name,
        {
            "action": "auto_recover",
            "strategy": recovery.get("strategy", ""),
            "status": "queued",
            "job_id": recovery.get("job", {}).get("id", ""),
            "target_run": recovery.get("job", {}).get("run_name", ""),
        },
    )
    return jsonify(
        {
            "ok": True,
            "strategy": recovery.get("strategy", ""),
            "job": recovery.get("job", {}),
            "redirect": url_for("home"),
        }
    )


@bp.get("/api/runs/<run_name>/recover-preview")
def recover_preview_api(run_name: str):
    try:
        preview = recovery_preview(run_name)
    except FileNotFoundError:
        return jsonify({"ok": False, "error": "Run not found."}), 404
    return jsonify({"ok": True, "preview": preview})


@bp.get("/api/runs/recover-batch-preview")
def recover_batch_preview_api():
    limit = int(request.args.get("limit", "3") or 3)
    include_warning = form_bool(request.args.get("include_warning"), default=True)
    planned = plan_recovery_batch(limit=limit, include_warning=include_warning)
    eligible_count = sum(1 for item in planned if bool(item.get("eligible", False)))
    return jsonify({"ok": True, "planned": planned, "eligible_count": eligible_count})


@bp.post("/api/runs/recover-batch")
def recover_batch_api():
    from website.dashboard import _append_recovery_action
    limit = int(request.form.get("limit", "3") or 3)
    include_warning = form_bool(request.form.get("include_warning"), default=True)
    executor_headed = form_bool(request.form.get("executor_headed"), default=False)
    payload = create_recovery_batch_jobs(
        limit=limit,
        include_warning=include_warning,
        executor_headed=executor_headed,
    )
    if not payload["jobs"]:
        message = "No high-risk runs available for recovery."
        if payload["skipped"]:
            message = payload["skipped"][0]["error"] or message
        for item in payload.get("skipped", [])[:20]:
            run_name = str(item.get("run_name", "")).strip()
            if run_name:
                _append_recovery_action(
                    run_name,
                    {"action": "recover_batch", "status": "skipped", "reason": str(item.get("error", "")).strip()},
                )
        return jsonify({"ok": False, "error": message, **payload}), 400
    for item in payload.get("jobs", [])[:20]:
        strategy = str(item.get("strategy", "")).strip()
        job = item.get("job", {}) if isinstance(item.get("job", {}), dict) else {}
        source_run = str(job.get("payload", {}).get("source_run_name", "")).strip()
        if source_run:
            _append_recovery_action(
                source_run,
                {
                    "action": "recover_batch",
                    "strategy": strategy,
                    "status": "queued",
                    "job_id": job.get("id", ""),
                    "target_run": job.get("run_name", ""),
                },
            )
    return jsonify({"ok": True, **payload, "redirect": url_for("home")})


@bp.get("/api/runs/<run_name>/safe-rerun-preview")
def safe_rerun_preview_api(run_name: str):
    from datetime import datetime
    from website.dashboard import _resolve_run_dir

    try:
        run_dir = _resolve_run_dir(run_name)
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    if not run_dir.exists():
        return jsonify({"ok": False, "error": "Run not found."}), 404
    detail = build_run_detail(run_dir)
    eligibility = safe_rerun_eligibility(run_name)
    payload = {
        "run_name": run_name,
        "suggested_run_name": f"{run_name}_safe_{datetime.now().strftime('%Y%m%d_%H%M%S')}",
        "eligible": bool(eligibility.get("eligible", False)),
        "reason": str(eligibility.get("reason", "")).strip(),
        "instruction": build_safe_rerun_instruction(detail),
        "safety_index": int(detail.get("safety_index", 0) or 0),
        "safety_status": detail.get("safety_status", ""),
        "safety_recommendations": list(detail.get("safety_recommendations", []) or []),
    }
    return jsonify({"ok": True, "preview": payload})


@bp.post("/api/runs/safe-rerun-batch")
def safe_rerun_batch_api():
    from website.dashboard import _append_recovery_action
    limit = int(request.form.get("limit", "3") or 3)
    include_warning = form_bool(request.form.get("include_warning"), default=True)
    executor_headed = form_bool(request.form.get("executor_headed"), default=False)
    payload = create_safe_rerun_batch_jobs(
        limit=limit,
        include_warning=include_warning,
        executor_headed=executor_headed,
    )
    if not payload["jobs"]:
        message = "No high-risk runs available for safe rerun."
        if payload["skipped"]:
            message = payload["skipped"][0]["error"] or message
        for item in payload.get("skipped", [])[:20]:
            run_name = str(item.get("run_name", "")).strip()
            if run_name:
                _append_recovery_action(
                    run_name,
                    {"action": "safe_rerun_batch", "status": "skipped", "reason": str(item.get("error", "")).strip()},
                )
        return jsonify({"ok": False, "error": message, **payload}), 400
    for item in payload.get("jobs", [])[:20]:
        job = item.get("job", {}) if isinstance(item.get("job", {}), dict) else {}
        source_run = str(job.get("payload", {}).get("source_run_name", "")).strip()
        if source_run:
            _append_recovery_action(
                source_run,
                {
                    "action": "safe_rerun_batch",
                    "status": "queued",
                    "job_id": job.get("id", ""),
                    "target_run": job.get("run_name", ""),
                },
            )
    return jsonify({"ok": True, **payload, "redirect": url_for("home")})


@bp.get("/api/runs/safe-rerun-batch-preview")
def safe_rerun_batch_preview_api():
    limit = int(request.args.get("limit", "3") or 3)
    include_warning = form_bool(request.args.get("include_warning"), default=True)
    planned = plan_safe_rerun_batch(limit=limit, include_warning=include_warning)
    eligible_count = sum(1 for item in planned if bool(item.get("eligible", True)))
    return jsonify({"ok": True, "planned": planned, "eligible_count": eligible_count})
