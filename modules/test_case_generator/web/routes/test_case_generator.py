"""Test Case Generator – routes blueprint."""
from __future__ import annotations

import json
from io import BytesIO
from pathlib import Path

from flask import Blueprint, abort, jsonify, redirect, render_template, request, url_for

from core.config import INSTRUCTIONS_DIR, RESULT_DIR
from core.dashboard_data import list_runs, sort_runs
from core.guardrails import CONTEXT_RULES, compile_instruction_contract
from core.instruction_templates import (
    list_instruction_templates,
    load_instruction_template,
    load_template_user_notes,
    resolve_instruction_template,
    save_template_user_note,
    save_uploaded_template,
    update_instruction_template,
)
from core.jobs import create_job, get_all_jobs, get_job, is_duplicate_recent_job
from core.utils import form_bool, normalize_input_url, is_automation_or_recovery_run
from modules.test_case_generator.web.features.test_case_generator import (
    build_all_test_cases_context,
    build_test_case_generator_context,
    build_scenario_results_context,
)

bp = Blueprint("test_case_generator", __name__, template_folder="../templates")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _permissive_page_facts() -> dict:
    facts = {}
    for rule in CONTEXT_RULES.values():
        requires = rule.get("requires")
        requires_any = rule.get("requires_any", ())
        if requires:
            facts[requires] = True
        for item in requires_any:
            facts[item] = True
    return facts


def _build_instruction_precheck(template_name: str, instruction: str, template_content_override: str = "") -> dict:
    template_name = str(template_name or "").strip()
    instruction = str(instruction or "").strip()
    template = None
    template_content = ""
    if template_name:
        template = load_instruction_template(template_name, INSTRUCTIONS_DIR)
        template_content = str(template.get("content", "")).strip()
    if template_name and str(template_content_override).strip() != "":
        template_content = str(template_content_override).strip()

    combined_parts = [part for part in (template_content, instruction) if part]
    combined_instruction = "\n\n".join(combined_parts).strip()
    contract = compile_instruction_contract(combined_instruction, _permissive_page_facts())
    return {
        "template_name": template_name,
        "template_used": bool(template_name),
        "combined_instruction": combined_instruction,
        "contract": contract,
        "is_valid": not contract.get("conflicts"),
    }


def _parse_scenario_job_request():
    template_name = request.form.get("template_name", "").strip()
    template_path = ""
    template_content = request.form.get("template_content", "")
    instruction = request.form.get("custom_template", "").strip()
    if template_name:
        try:
            resolved_template = resolve_instruction_template(template_name, INSTRUCTIONS_DIR)
            template_path = str(resolved_template)
        except FileNotFoundError:
            return None, None, (jsonify({"ok": False, "error": "Template not found."}), 404)
        current_content = resolved_template.read_text(encoding="utf-8")
        if current_content.strip() != str(template_content).strip():
            update_instruction_template(template_name, template_content, INSTRUCTIONS_DIR)
        save_template_user_note(template_name, instruction, INSTRUCTIONS_DIR)

    precheck = _build_instruction_precheck(
        template_name,
        instruction,
        template_content_override=template_content,
    )
    try:
        normalized_url = normalize_input_url(request.form.get("url", ""))
    except ValueError as exc:
        return None, None, (jsonify({"ok": False, "error": str(exc)}), 400)

    payload_base = {
        "url": normalized_url,
        "instruction": instruction,
        "template_name": template_name,
        "template_path": template_path,
        "csv_sep": request.form.get("csv_sep", ","),
        "crawl_limit": int(request.form.get("crawl_limit", "3") or 3),
        "use_auth": False,
        "adaptive_recrawl": form_bool(request.form.get("adaptive_recrawl"), default=True),
        "run_name": "",
    }
    if not payload_base["url"]:
        return None, None, (jsonify({"ok": False, "error": "URL is required."}), 400)
    return payload_base, precheck, None


# ---------------------------------------------------------------------------
# Page routes
# ---------------------------------------------------------------------------

@bp.get("/scenario-testing")
def scenario_testing_page():
    from core.dashboard_data import dashboard_metrics
    context = build_test_case_generator_context(
        instructions_dir=INSTRUCTIONS_DIR,
        list_instruction_templates=list_instruction_templates,
        jobs=get_all_jobs(),
        get_job=get_job,
        dashboard_metrics=dashboard_metrics,
    )
    return render_template("scenario_testing.html", **context)


@bp.get("/runs")
def runs_page():
    from core.dashboard_data import dashboard_metrics
    sort_mode = request.args.get("sort", "latest").strip()
    context = build_all_test_cases_context(
        result_dir=RESULT_DIR,
        sort_mode=sort_mode,
        list_runs=list_runs,
        sort_runs=sort_runs,
        dashboard_metrics=dashboard_metrics,
    )
    return render_template("runs.html", **context)


@bp.get("/scenario-results")
def scenario_results_page():
    from core.dashboard_data import dashboard_metrics
    sort_mode = request.args.get("sort", "latest").strip()
    search_query = request.args.get("q", "").strip()
    try:
        page = int(request.args.get("page", 1))
    except ValueError:
        page = 1
        
    context = build_scenario_results_context(
        result_dir=RESULT_DIR,
        sort_mode=sort_mode,
        list_runs=list_runs,
        sort_runs=sort_runs,
        is_automation_or_recovery_run=is_automation_or_recovery_run,
        dashboard_metrics=dashboard_metrics,
        search_query=search_query,
        page=page,
    )
    return render_template("scenario_results.html", **context)


# ---------------------------------------------------------------------------
# API routes
# ---------------------------------------------------------------------------

@bp.post("/api/jobs")
def create_job_api():
    payload_base, precheck, error = _parse_scenario_job_request()
    if error:
        return error
    if not precheck["is_valid"]:
        return jsonify(
            {
                "ok": False,
                "error": "Instruction conflict. Fix it before launching the job.",
                "precheck": precheck,
            }
        ), 400
    payload = {
        **payload_base,
        "run_executor": form_bool(request.form.get("run_executor"), default=False),
        "executor_headed": form_bool(request.form.get("executor_headed"), default=False),
    }

    try:
        payload["crawl_limit"] = max(1, min(int(payload.get("crawl_limit", 3)), 10))
    except ValueError:
        return jsonify({"ok": False, "error": "Invalid crawl limit. Must be an integer between 1 and 10."}), 400
    
    if len(str(payload.get("instruction", ""))) > 2000:
        return jsonify({"ok": False, "error": "Custom instructions cannot exceed 2000 characters."}), 400

    is_duplicate, duplicate_error = is_duplicate_recent_job(payload, cooldown_seconds=10)
    if is_duplicate:
        return jsonify({"ok": False, "error": duplicate_error}), 409
    job = create_job(payload)
    return jsonify({"ok": True, "job": job, "redirect": url_for("home")})


@bp.post("/api/scenario-jobs")
def create_scenario_job_api():
    payload_base, precheck, error = _parse_scenario_job_request()
    if error:
        return error
    if not precheck["is_valid"]:
        return jsonify({"ok": False, "error": "Instruction conflict. Fix it before generating scenario.", "precheck": precheck}), 400
    payload = {
        **payload_base,
        "run_executor": False,
        "executor_headed": False,
        "feature": "case-generator",
    }

    try:
        payload["crawl_limit"] = max(1, min(int(payload.get("crawl_limit", 3)), 10))
    except ValueError:
        return jsonify({"ok": False, "error": "Invalid crawl limit. Must be an integer between 1 and 10."}), 400
    
    if len(str(payload.get("instruction", ""))) > 2000:
        return jsonify({"ok": False, "error": "Custom instructions cannot exceed 2000 characters."}), 400

    is_duplicate, duplicate_error = is_duplicate_recent_job(payload, cooldown_seconds=10)
    if is_duplicate:
        return jsonify({"ok": False, "error": duplicate_error}), 409
    job = create_job(payload)
    return jsonify(
        {
            "ok": True,
            "job": job,
            "redirect": url_for("test_case_generator.runs_page"),
            "next_automation_url": url_for("end_to_end_automation.automation_testing_page", source_run_name=job.get("run_name", "")),
        }
    )


@bp.post("/api/instruction-precheck")
def instruction_precheck_api():
    template_name = request.form.get("template_name", "").strip()
    instruction = request.form.get("custom_template", "")
    template_content = request.form.get("template_content", "")
    try:
        precheck = _build_instruction_precheck(template_name, instruction, template_content_override=template_content)
    except FileNotFoundError:
        return jsonify({"ok": False, "error": "Template not found."}), 404
    return jsonify({"ok": True, "precheck": precheck})


@bp.get("/api/templates")
def templates_api():
    return jsonify({"templates": list_instruction_templates(INSTRUCTIONS_DIR)})


@bp.get("/api/templates/<template_name>")
def template_detail_api(template_name: str):
    try:
        template = load_instruction_template(template_name, INSTRUCTIONS_DIR)
    except FileNotFoundError:
        abort(404)
    notes = load_template_user_notes(INSTRUCTIONS_DIR)
    template["last_user_instruction"] = str(notes.get(template.get("name", ""), ""))
    return jsonify({"template": template})


@bp.post("/api/templates")
def upload_template_api():
    uploaded_file = request.files.get("template_file")
    if not uploaded_file or not uploaded_file.filename:
        return jsonify({"ok": False, "error": "Select a .txt file first."}), 400
    try:
        template = save_uploaded_template(uploaded_file, INSTRUCTIONS_DIR)
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    return jsonify({"ok": True, "template": template})
