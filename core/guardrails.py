import re


CONTEXT_RULES = {
    "form": {
        "terms": ("form", "submit", "register", "login", "sign in", "sign up", "password", "username", "email"),
        "requires": "form",
    },
    "auth": {
        "terms": ("login", "sign in", "password", "username", "otp", "2fa", "authentication"),
        "requires_any": ("form", "auth"),
    },
    "search": {
        "terms": ("search", "keyword", "find", "query"),
        "requires": "search",
    },
    "filter": {
        "terms": ("filter", "sort", "category", "refine"),
        "requires": "filter",
    },
    "pagination": {
        "terms": ("pagination", "next page", "previous page", "page number", "load more"),
        "requires": "pagination",
    },
    "table": {
        "terms": ("table", "grid", "column", "row", "standings", "ranking", "stats"),
        "requires": "table",
    },
    "upload": {
        "terms": ("upload", "file", "attachment", "resume", "document"),
        "requires": "upload",
    },
    "rich_text": {
        "terms": ("editor", "rich text", "body content", "wysiwyg"),
        "requires": "rich_text",
    },
    "iframe": {
        "terms": ("iframe", "embedded widget", "embedded form"),
        "requires": "iframe",
    },
    "consent": {
        "terms": ("cookie", "consent", "privacy choices"),
        "requires": "consent_banner",
    },
    "otp": {
        "terms": ("otp", "verification code", "one time password", "kode verifikasi"),
        "requires_any": ("auth", "otp_flow"),
    },
    "sso": {
        "terms": ("sso", "single sign-on", "continue with google", "continue with microsoft", "sign in with"),
        "requires_any": ("auth", "sso"),
    },
    "live_updates": {
        "terms": ("live", "real-time", "realtime", "refresh automatically", "updated content"),
        "requires_any": ("live_updates", "websocket", "graphql"),
    },
}


def build_task_contract(
    page_model: dict | None,
    page_scope: dict | None,
    page_info: dict | None,
    custom_instruction: str = "",
) -> dict:
    allowed = build_allowed_vocabulary(page_model, page_scope, page_info)
    page_scope = page_scope or {}
    page_facts = allowed["page_facts"]
    allowed_terms = []
    forbidden_terms = []
    supported_surfaces = []
    unsupported_surfaces = []

    for name, rule in CONTEXT_RULES.items():
        is_supported = _rule_is_supported(rule, page_facts)
        target_list = allowed_terms if is_supported else forbidden_terms
        target_list.extend([name, *rule["terms"][:6]])
        if is_supported:
            supported_surfaces.append(name)
        else:
            unsupported_surfaces.append(name)

    allowed_terms.extend(allowed.get("component_types", []))
    allowed_terms.extend(allowed.get("module_labels", []))
    allowed_terms.extend(allowed.get("flow_names", []))
    allowed_terms.extend(allowed.get("field_semantics", []))
    allowed_terms.extend(allowed.get("field_aliases", []))
    for ext_key in (
        "test_dimensions", "edge_and_error_states", "user_states_and_roles", "multi_step_flows",
        "dynamic_components", "api_contracts", "viewports", "accessibility_focus",
        "input_validation_focus", "priority_and_risk_areas",
    ):
        allowed_terms.extend(_clean_string_list(page_scope.get(ext_key, []) or []))

    instruction_contract = compile_instruction_contract(custom_instruction, page_facts)
    instruction_focus_terms = _clean_string_list(
        _extract_instruction_focus_terms(custom_instruction, page_facts)
        + instruction_contract.get("must_focus_surfaces", [])
    )
    focus_terms = _clean_string_list(
        list(page_scope.get("key_modules", []))
        + list(page_scope.get("critical_user_flows", []))
        + list(page_scope.get("priority_areas", []))
        + instruction_focus_terms
    )
    if not focus_terms:
        focus_terms = _clean_string_list(allowed.get("module_labels", [])[:8] + allowed.get("flow_names", [])[:6])

    return {
        "objective": _normalize_text(page_scope.get("primary_goal", "")) or "Ground test planning in detected page surfaces only.",
        "focus_terms": focus_terms[:16],
        "instruction_focus_terms": instruction_focus_terms[:8],
        "instruction_conflicts": instruction_contract.get("conflicts", []),
        "allowed_terms": _clean_string_list(allowed_terms + instruction_contract.get("must_focus_surfaces", []))[:120],
        "forbidden_terms": _clean_string_list(
            forbidden_terms
            + instruction_contract.get("avoid_surfaces", [])
            + instruction_contract.get("unsupported_requested_surfaces", [])
        )[:120],
        "supported_surfaces": _clean_string_list(supported_surfaces)[:20],
        "unsupported_surfaces": _clean_string_list(unsupported_surfaces)[:20],
        "source_trust_order": [
            "dom_content",
            "section_graph",
            "field_catalog",
            "state_discovery",
            "linked_pages",
            "knowledge_bank",
            "feedback",
            "ai_inference",
        ],
        "instruction_applies": bool(custom_instruction.strip()),
        "instruction_contract": instruction_contract,
    }


def build_allowed_vocabulary(page_model: dict | None, page_scope: dict | None, page_info: dict | None) -> dict:
    page_model = page_model or {}
    page_scope = page_scope or {}
    page_info = page_info or {}

    component_types = sorted(
        {
            str(component.get("type", "")).strip().lower()
            for component in page_model.get("components", [])
            if str(component.get("type", "")).strip()
        }
    )
    module_labels = sorted(
        {
            _normalize_text(value)
            for value in (
                list(page_scope.get("key_modules", []))
                + [component.get("label", "") for component in page_model.get("components", [])]
                + [entity.get("value", "") for entity in page_model.get("entities", [])]
            )
            if _normalize_text(value)
        }
    )
    flow_names = sorted(
        {
            _normalize_text(flow.get("name", ""))
            for flow in page_model.get("possible_flows", [])
            if _normalize_text(flow.get("name", ""))
        }
    )
    field_semantics = sorted(
        {
            str(field.get("semantic_type", "")).strip().lower()
            for field in page_model.get("field_catalog", [])
            if str(field.get("semantic_type", "")).strip()
        }
    )
    field_aliases = sorted(
        {
            _normalize_text(alias)
            for field in page_model.get("field_catalog", [])
            for alias in field.get("aliases", [])
            if _normalize_text(alias)
        }
    )
    action_types = sorted(
        {
            str(action.get("type", "")).strip().lower()
            for action in page_model.get("actions", [])
            if str(action.get("type", "")).strip()
        }
    )
    field_labels = sorted(
        {
            _normalize_text(value)
            for field in page_model.get("field_catalog", [])
            for value in (
                field.get("semantic_label", ""),
                field.get("label", ""),
                field.get("field_key", "").replace("_", " "),
            )
            if _normalize_text(value)
        }
    )
    component_labels = sorted(
        {
            _normalize_text(value)
            for component in page_model.get("component_catalog", [])
            for value in (
                component.get("label", ""),
                component.get("component_key", "").replace("_", " "),
                component.get("type", "").replace("_", " "),
            )
            if _normalize_text(value)
        }
    )
    section_labels = sorted(
        {
            _normalize_text(value)
            for node in page_model.get("section_graph", {}).get("nodes", [])
            for value in (
                node.get("heading", ""),
                node.get("tag", ""),
            )
            if _normalize_text(value)
        }
    )
    facts = _derive_page_facts(page_model, page_info)

    return {
        "component_types": component_types,
        "module_labels": module_labels[:50],
        "flow_names": flow_names[:20],
        "field_semantics": field_semantics[:30],
        "field_aliases": field_aliases[:60],
        "field_labels": field_labels[:80],
        "component_labels": component_labels[:80],
        "section_labels": section_labels[:80],
        "action_types": action_types,
        "page_facts": facts,
    }


def validate_page_scope(
    page_scope: dict,
    page_model: dict | None,
    page_info: dict | None,
    custom_instruction: str = "",
) -> dict:
    allowed = build_allowed_vocabulary(page_model, page_scope, page_info)
    task_contract = build_task_contract(page_model, page_scope, page_info, custom_instruction=custom_instruction)
    issues = []

    _scope_optional_array_keys = (
        "test_dimensions",
        "edge_and_error_states",
        "user_states_and_roles",
        "multi_step_flows",
        "dynamic_components",
        "api_contracts",
        "viewports",
        "accessibility_focus",
        "input_validation_focus",
        "priority_and_risk_areas",
    )
    sanitized = {
        "page_type": _normalize_text(page_scope.get("page_type", "")),
        "primary_goal": _normalize_text(page_scope.get("primary_goal", "")),
        "key_modules": _clean_string_list(page_scope.get("key_modules", [])),
        "critical_user_flows": _clean_string_list(page_scope.get("critical_user_flows", [])),
        "priority_areas": _clean_string_list(page_scope.get("priority_areas", [])),
        "risks": _clean_string_list(page_scope.get("risks", [])),
        "scope_summary": _normalize_text(page_scope.get("scope_summary", "")),
        "confidence": _coerce_confidence(page_scope.get("confidence", 0.0)),
    }
    for key in _scope_optional_array_keys:
        val = page_scope.get(key)
        if val is not None and isinstance(val, list):
            sanitized[key] = _clean_string_list(val)[:12]
        else:
            sanitized[key] = []

    if not sanitized["page_type"]:
        sanitized["page_type"] = _infer_page_type(allowed["page_facts"])
        issues.append("page_type was empty and replaced from local facts.")
    if not sanitized["primary_goal"]:
        sanitized["primary_goal"] = "Review the primary interactions and visible content on this page."
        issues.append("primary_goal was empty.")
    page_type_errors = detect_out_of_context_case({"Module": sanitized["page_type"], "Title": sanitized["page_type"]}, allowed["page_facts"])
    generic_page_type = sanitized["page_type"].lower() in {
        "page",
        "web page",
        "website page",
        "generic page",
        "landing page",
        "homepage",
        "home page",
    }
    if page_type_errors:
        sanitized["page_type"] = _infer_page_type(allowed["page_facts"])
        issues.extend(f"Unsupported page_type: {item}" for item in page_type_errors[:3])
    elif generic_page_type and sum(1 for value in allowed["page_facts"].values() if value) >= 2:
        sanitized["page_type"] = _infer_page_type(allowed["page_facts"])
        issues.append("page_type was too generic and replaced from grounded page facts.")

    invalid_modules = _find_context_mismatches(sanitized["key_modules"], allowed["page_facts"])
    invalid_flows = _find_context_mismatches(sanitized["critical_user_flows"], allowed["page_facts"])
    invalid_priorities = _find_context_mismatches(sanitized["priority_areas"], allowed["page_facts"])

    if invalid_modules:
        issues.extend(f"Unsupported module: {item}" for item in invalid_modules)
    if invalid_flows:
        issues.extend(f"Unsupported flow: {item}" for item in invalid_flows)
    if invalid_priorities:
        issues.extend(f"Unsupported priority area: {item}" for item in invalid_priorities)

    sanitized["key_modules"] = _filter_context_items(sanitized["key_modules"], allowed["page_facts"])
    sanitized["critical_user_flows"] = _filter_context_items(sanitized["critical_user_flows"], allowed["page_facts"])
    sanitized["priority_areas"] = _filter_context_items(sanitized["priority_areas"], allowed["page_facts"])

    if not sanitized["key_modules"]:
        sanitized["key_modules"] = _fallback_modules(allowed["page_facts"], allowed["component_types"])
        issues.append("key_modules fell back to grounded component list.")
    if not sanitized["critical_user_flows"]:
        sanitized["critical_user_flows"] = _fallback_flows(allowed["page_facts"], allowed["component_types"])
        issues.append("critical_user_flows fell back to grounded flows.")

    instruction_focus_terms = task_contract.get("instruction_focus_terms", [])
    if instruction_focus_terms:
        scope_text = " ".join(
            [
                sanitized["page_type"],
                sanitized["primary_goal"],
                sanitized["scope_summary"],
                *sanitized["key_modules"],
                *sanitized["critical_user_flows"],
                *sanitized["priority_areas"],
            ]
        )
        if not _find_term_hits(scope_text, instruction_focus_terms):
            sanitized["priority_areas"] = _clean_string_list(sanitized["priority_areas"] + instruction_focus_terms[:2])
            issues.append("page scope missed grounded instruction focus terms and was aligned to supported priorities.")
    instruction_conflicts = task_contract.get("instruction_conflicts", [])
    if instruction_conflicts:
        issues.extend(f"instruction conflict: {item}" for item in instruction_conflicts[:4])
    avoid_surfaces = task_contract.get("instruction_contract", {}).get("avoid_surfaces", [])
    if avoid_surfaces:
        before_modules = list(sanitized["key_modules"])
        before_flows = list(sanitized["critical_user_flows"])
        sanitized["key_modules"] = _remove_items_by_terms(sanitized["key_modules"], avoid_surfaces)
        sanitized["critical_user_flows"] = _remove_items_by_terms(sanitized["critical_user_flows"], avoid_surfaces)
        sanitized["priority_areas"] = _remove_items_by_terms(sanitized["priority_areas"], avoid_surfaces)
        if before_modules != sanitized["key_modules"] or before_flows != sanitized["critical_user_flows"]:
            issues.append("page scope removed avoided instruction surfaces.")

    penalty = min(0.45, 0.08 * len(invalid_modules + invalid_flows + invalid_priorities))
    if instruction_focus_terms and any("instruction focus" in issue for issue in issues):
        penalty = min(0.5, penalty + 0.08)
    if instruction_conflicts:
        penalty = min(0.55, penalty + 0.12)
    sanitized["confidence"] = round(max(0.15, sanitized["confidence"] - penalty), 2)

    return {
        "is_valid": len(invalid_modules + invalid_flows) == 0 and not instruction_conflicts,
        "issues": issues,
        "page_scope": sanitized,
        "allowed_vocabulary": allowed,
        "task_contract": task_contract,
        "unsupported_surface_report": {
            "unsupported_requested_surfaces": task_contract.get("instruction_contract", {}).get("unsupported_requested_surfaces", []),
            "avoid_surfaces": task_contract.get("instruction_contract", {}).get("avoid_surfaces", []),
            "instruction_conflicts": instruction_conflicts,
        },
    }


def validate_test_scenarios(
    test_cases: list[dict],
    page_model: dict | None,
    page_scope: dict | None,
    page_info: dict | None,
    custom_instruction: str = "",
) -> dict:
    allowed = build_allowed_vocabulary(page_model, page_scope, page_info)
    task_contract = build_task_contract(page_model, page_scope, page_info, custom_instruction=custom_instruction)
    valid_cases = []
    rejected_cases = []
    issues = []
    instruction_conflicts = task_contract.get("instruction_conflicts", [])
    if instruction_conflicts:
        issues.extend(f"instruction conflict: {item}" for item in instruction_conflicts[:4])
    has_concrete_catalog = bool(
        allowed.get("field_labels")
        or allowed.get("component_labels")
        or allowed.get("section_labels")
    )
    seen_exact_signatures = set()
    seen_soft_signatures = set()

    for case in test_cases:
        context_errors = detect_out_of_context_case(case, allowed["page_facts"])
        alignment = assess_case_task_alignment(case, allowed, task_contract)
        grounding = collect_case_grounding(case, page_model, page_info, allowed["page_facts"])
        sanitized = dict(case)
        sanitized["Module"] = _normalize_text(sanitized.get("Module", "")) or "General"
        sanitized["Title"] = _normalize_text(sanitized.get("Title", "")) or "Untitled scenario"
        sanitized["Automation"] = str(sanitized.get("Automation", "auto")).strip().lower() or "auto"
        sanitized["_grounding"] = grounding
        sanitized["_task_alignment"] = alignment
        case_issues = context_errors + alignment["issues"]
        contradiction_issues = detect_case_contradictions(sanitized)
        if contradiction_issues:
            case_issues.extend(contradiction_issues)
        intent_issues = validate_case_intent_to_action(sanitized, allowed.get("page_facts", {}))
        if intent_issues:
            case_issues.extend(intent_issues)

        exact_signature = _case_exact_signature(sanitized)
        soft_signature = _case_soft_signature(sanitized)
        if exact_signature in seen_exact_signatures:
            case_issues.append("duplicate scenario content detected (exact match)")
        elif soft_signature in seen_soft_signatures:
            case_issues.append("duplicate scenario content detected (near match)")
        else:
            seen_exact_signatures.add(exact_signature)
            seen_soft_signatures.add(soft_signature)

        if grounding["requires_grounding"] and not grounding["fact_ids"]:
            case_issues.append("scenario has no grounded fact references from the scanned page model")
        elif grounding["requires_grounding"] and has_concrete_catalog and grounding.get("structured_ref_count", 0) == 0:
            case_issues.append("scenario relies only on generic page-surface facts without concrete grounded controls or sections")
        elif grounding["requires_grounding"] and grounding["score"] < 0.18:
            case_issues.append("scenario grounding is too weak to trust")
        if grounding["requires_grounding"] and has_concrete_catalog and alignment.get("concrete_hits") == []:
            case_issues.append("scenario interaction wording is too generic for reliable grounding")
        instruction_contract = task_contract.get("instruction_contract", {})
        preferred_types = instruction_contract.get("only_test_types", [])
        if preferred_types:
            case_type = str(sanitized.get("Test Type", "")).strip().lower()
            if case_type not in preferred_types:
                case_issues.append(f"scenario test type '{case_type or '-'}' violates instruction-only coverage")
        if case_issues:
            rejected_cases.append({"case": sanitized, "issues": case_issues, "alignment": alignment, "grounding": grounding})
            issues.extend(f"{sanitized.get('ID', 'UNKNOWN')}: {item}" for item in case_issues)
            continue
        valid_cases.append(sanitized)

    total = len(test_cases)
    rejection_ratio = (len(rejected_cases) / total) if total else 1.0
    is_valid = bool(valid_cases) and rejection_ratio <= 0.4

    return {
        "is_valid": is_valid,
        "issues": issues,
        "valid_cases": valid_cases,
        "rejected_cases": rejected_cases,
        "allowed_vocabulary": allowed,
        "task_contract": task_contract,
        "grounding_summary": {
            "valid_grounded_cases": sum(1 for item in valid_cases if item.get("_grounding", {}).get("fact_ids")),
            "rejected_grounding_cases": sum(1 for item in rejected_cases if item.get("grounding", {}).get("fact_ids")),
            "average_fact_coverage_score": round(
                sum(float(item.get("_grounding", {}).get("coverage_score", 0.0) or 0.0) for item in valid_cases) / len(valid_cases),
                2,
            ) if valid_cases else 0.0,
            "instruction_conflict_count": len(instruction_conflicts),
        },
        "unsupported_surface_report": {
            "unsupported_requested_surfaces": task_contract.get("instruction_contract", {}).get("unsupported_requested_surfaces", []),
            "avoid_surfaces": task_contract.get("instruction_contract", {}).get("avoid_surfaces", []),
            "instruction_conflicts": instruction_conflicts,
        },
        "quality_flags": {
            "duplicate_rejection_count": sum(
                1 for item in rejected_cases if any("duplicate scenario content" in issue for issue in item.get("issues", []))
            ),
            "contradiction_rejection_count": sum(
                1 for item in rejected_cases if any("contradiction" in issue for issue in item.get("issues", []))
            ),
            "intent_rejection_count": sum(
                1 for item in rejected_cases if any("intent-to-action" in issue for issue in item.get("issues", []))
            ),
        },
    }


def validate_execution_plan(execution_plan: dict, page_model: dict | None, page_info: dict | None) -> dict:
    page_model = page_model or {}
    page_info = page_info or {}
    facts = build_allowed_vocabulary(page_model, None, page_info)["page_facts"]
    valid_plans = []
    rejected_plans = []
    issues = []
    allowed_action_types = set(page_model.get("action_ontology", {}).keys()) or {
        "open_url", "click", "fill", "select", "upload", "hover", "scroll", "dismiss", "wait_for_text",
        "inspect", "assert_text_visible", "assert_control_text", "assert_url_contains", "assert_network_seen", "assert_network_status_ok",
        "assert_graphql_ok", "assert_endpoint_allowlist", "assert_cross_origin_safe"
    }

    for plan in execution_plan.get("plans", []):
        plan_copy = dict(plan)
        action_errors = []
        assertion_errors = []
        for action in plan_copy.get("pre_actions", []) + plan_copy.get("actions", []):
            action_type = str(action.get("type", "")).strip().lower()
            if action_type not in allowed_action_types:
                action_errors.append(f"unsupported action type '{action_type}'")
                continue
            grounding_issue = _validate_grounding(action, kind="action")
            if grounding_issue:
                action_errors.append(grounding_issue)
            if action_type in {"fill", "select"} and not facts.get("form", False) and not facts.get("search", False) and not facts.get("filter", False):
                action_errors.append(f"action '{action_type}' requires field-like controls that were not detected")
            if action_type == "select" and not facts.get("filter", False) and not facts.get("form", False):
                action_errors.append("select action requires filter/form signal")
            if action_type == "upload" and not facts.get("upload", False):
                action_errors.append("upload action requires file upload control")
            if action_type == "dismiss" and not any(facts.get(key, False) for key in ("consent_banner", "drawer", "toast")):
                action_errors.append("dismiss action requires consent, drawer, or toast signal")
            if action_type == "scroll" and not any(facts.get(key, False) for key in ("listing", "content", "infinite_scroll", "carousel")):
                action_errors.append("scroll action requires listing/content/infinite-scroll signal")
            if action_type == "wait_for_text" and not any(facts.get(key, False) for key in ("live_updates", "toast", "content", "spa_shell")):
                action_errors.append("wait_for_text action has weak async/live-content support on this page")
        for assertion in plan_copy.get("assertions", []):
            assertion_type = str(assertion.get("type", "")).strip().lower()
            if assertion_type not in allowed_action_types:
                assertion_errors.append(f"unsupported assertion type '{assertion_type}'")
            grounding_issue = _validate_grounding(assertion, kind="assertion")
            if grounding_issue:
                assertion_errors.append(grounding_issue)
            if (
                assertion_type == "assert_url_contains"
                and not facts.get("navigation", False)
                and not facts.get("listing", False)
                and not facts.get("form", False)
                and not facts.get("auth", False)
            ):
                assertion_errors.append("url assertion has weak navigation support on this page")
            if assertion_type in {"assert_network_seen", "assert_network_status_ok", "assert_graphql_ok", "assert_endpoint_allowlist", "assert_cross_origin_safe"} and not any(
                facts.get(key, False) for key in ("api_surface", "graphql", "search", "form", "upload", "auth")
            ):
                assertion_errors.append("network assertion requires API/network-capable page signals")
            if assertion_type == "assert_graphql_ok" and not any(facts.get(key, False) for key in ("graphql", "api_surface")):
                assertion_errors.append("graphql assertion requires graphql/api signal")
        for checkpoint in plan_copy.get("checkpoints", []):
            checkpoint_type = str(checkpoint.get("type", "")).strip().lower()
            if checkpoint_type == "captcha" and not facts.get("captcha", False):
                action_errors.append("captcha checkpoint requires captcha signal")
            if checkpoint_type == "otp" and not any(facts.get(key, False) for key in ("otp_flow", "auth")):
                action_errors.append("otp checkpoint requires otp/auth signal")
            if checkpoint_type == "sso" and not any(facts.get(key, False) for key in ("sso", "auth")):
                action_errors.append("sso checkpoint requires sso/auth signal")
        if action_errors or assertion_errors:
            rejected_plans.append({
                "plan": plan_copy,
                "issues": action_errors + assertion_errors,
            })
            issues.extend(f"{plan_copy.get('id', 'UNKNOWN')}: {issue}" for issue in action_errors + assertion_errors)
            continue
        valid_plans.append(plan_copy)

    validated_plan = dict(execution_plan)
    validated_plan["plans"] = valid_plans
    return {
        "is_valid": bool(valid_plans),
        "issues": issues,
        "valid_plan": validated_plan,
        "rejected_plans": rejected_plans,
    }


def detect_out_of_context_case(test_case: dict, page_facts: dict) -> list[str]:
    text = " ".join(
        str(test_case.get(key, ""))
        for key in ("Module", "Title", "Steps to Reproduce", "Expected Result")
    ).lower()
    issues = []
    for name, rule in CONTEXT_RULES.items():
        if not any(term in text for term in rule["terms"]):
            continue
        requires = rule.get("requires")
        requires_any = rule.get("requires_any", ())
        if requires and not page_facts.get(requires, False):
            issues.append(f"mentions {name} but the page model has no {requires} signal")
        if requires_any and not any(page_facts.get(item, False) for item in requires_any):
            issues.append(f"mentions {name} but the page model lacks supporting auth/form signals")
    return issues


def assess_case_task_alignment(test_case: dict, allowed: dict, task_contract: dict) -> dict:
    text = " ".join(
        str(test_case.get(key, ""))
        for key in ("Module", "Category", "Title", "Precondition", "Steps to Reproduce", "Expected Result")
    )
    normalized_text = _normalize_text(text).lower()
    forbidden_hits = _find_term_hits(normalized_text, task_contract.get("forbidden_terms", []))
    allowed_hits = _find_term_hits(normalized_text, task_contract.get("allowed_terms", []))
    focus_hits = _find_term_hits(normalized_text, task_contract.get("focus_terms", []))
    field_hits = _find_term_hits(normalized_text, allowed.get("field_labels", []))
    component_hits = _find_term_hits(normalized_text, allowed.get("component_labels", []))
    section_hits = _find_term_hits(normalized_text, allowed.get("section_labels", []))
    concrete_hits = _clean_string_list(field_hits + component_hits + section_hits)
    has_concrete_targets = bool(
        allowed.get("field_labels")
        or allowed.get("component_labels")
        or allowed.get("section_labels")
    )
    interaction_terms = ("input ", "click ", "select ", "choose ", "upload ", "hover ", "scroll ", "wait ")
    has_interaction = any(term in normalized_text for term in interaction_terms)
    open_only = "open the site" in normalized_text and not has_interaction
    score = 0.0
    score += min(0.45, len(allowed_hits) * 0.11)
    score += min(0.4, len(focus_hits) * 0.18)
    score += min(0.25, len(concrete_hits) * 0.14)
    if open_only:
        score += 0.18
    score -= min(0.6, len(forbidden_hits) * 0.22)
    issues = []
    if forbidden_hits:
        issues.append(f"mentions unsupported surfaces: {', '.join(forbidden_hits[:3])}")
    instruction_contract = task_contract.get("instruction_contract", {})
    avoid_hits = _find_term_hits(normalized_text, instruction_contract.get("avoid_surfaces", []))
    if avoid_hits:
        issues.append(f"touches avoided instruction surfaces: {', '.join(avoid_hits[:3])}")
    if has_interaction and not allowed_hits and not focus_hits:
        issues.append("interaction steps are not aligned to grounded page surfaces or task focus")
    elif has_interaction and has_concrete_targets and not concrete_hits:
        issues.append("interaction steps do not mention any concrete grounded field, control, section, or component")
    elif not open_only and not allowed_hits and not focus_hits and task_contract.get("focus_terms"):
        issues.append("case has weak task alignment with the grounded page scope")
    preferred_types = instruction_contract.get("preferred_test_types", [])
    case_type = str(test_case.get("Test Type", "")).strip().lower()
    if preferred_types and case_type and case_type not in preferred_types:
        score = max(0.0, score - 0.08)
    return {
        "score": round(max(0.0, min(1.0, score)), 2),
        "allowed_hits": allowed_hits[:6],
        "focus_hits": focus_hits[:6],
        "concrete_hits": concrete_hits[:6],
        "forbidden_hits": forbidden_hits[:6],
        "avoid_hits": avoid_hits[:6],
        "issues": issues,
        "is_aligned": not issues,
    }


def collect_case_grounding(
    test_case: dict,
    page_model: dict | None,
    page_info: dict | None,
    page_facts: dict | None,
) -> dict:
    page_model = page_model or {}
    page_info = page_info or {}
    page_facts = page_facts or {}
    text = " ".join(
        str(test_case.get(key, ""))
        for key in ("Module", "Category", "Title", "Precondition", "Steps to Reproduce", "Expected Result")
    )
    normalized_text = _normalize_text(text).lower()
    refs = []

    for field in page_model.get("field_catalog", [])[:30]:
        candidates = [
            field.get("semantic_label", ""),
            field.get("label", ""),
            field.get("semantic_type", "").replace("_", " "),
            field.get("field_key", "").replace("_", " "),
            *field.get("aliases", [])[:8],
        ]
        score, matched = _best_grounding_match(normalized_text, candidates)
        if score >= 7:
            refs.append(
                {
                    "fact_id": f"field::{field.get('field_key', '')}",
                    "source_type": "field",
                    "source_key": field.get("field_key", ""),
                    "source_label": field.get("semantic_label", "") or field.get("label", ""),
                    "matched_text": matched,
                    "score": score,
                }
            )

    for component in page_model.get("component_catalog", [])[:30]:
        candidates = [
            component.get("label", ""),
            component.get("type", "").replace("_", " "),
            component.get("component_key", "").replace("_", " "),
            *component.get("aliases", [])[:8],
        ]
        score, matched = _best_grounding_match(normalized_text, candidates)
        if score >= 7:
            refs.append(
                {
                    "fact_id": f"component::{component.get('component_key', '')}",
                    "source_type": "component",
                    "source_key": component.get("component_key", ""),
                    "source_label": component.get("label", "") or component.get("type", ""),
                    "matched_text": matched,
                    "score": score,
                }
            )

    for node in page_model.get("section_graph", {}).get("nodes", [])[:24]:
        candidates = [node.get("heading", ""), node.get("tag", "")]
        score, matched = _best_grounding_match(normalized_text, candidates)
        if score >= 7:
            refs.append(
                {
                    "fact_id": f"section::{node.get('block_id', '')}",
                    "source_type": "section",
                    "source_key": node.get("block_id", ""),
                    "source_label": node.get("heading", "") or node.get("tag", ""),
                    "matched_text": matched,
                    "score": score,
                }
            )

    for state in page_info.get("discovered_states", [])[:16]:
        candidates = [state.get("label", ""), state.get("trigger_label", ""), state.get("state_id", "").replace("_", " ")]
        score, matched = _best_grounding_match(normalized_text, candidates)
        if score >= 7:
            refs.append(
                {
                    "fact_id": f"state::{state.get('state_id', '')}",
                    "source_type": "state",
                    "source_key": state.get("state_id", ""),
                    "source_label": state.get("label", ""),
                    "matched_text": matched,
                    "score": score,
                }
            )

    for endpoint in page_model.get("api_endpoints", [])[:16]:
        endpoint_text = str(endpoint or "")
        endpoint_key = _normalize_key(endpoint_text)
        score, matched = _best_grounding_match(normalized_text, [endpoint_text, endpoint_text.split("/")[-1]])
        if score >= 7:
            refs.append(
                {
                    "fact_id": f"api::{endpoint_key}",
                    "source_type": "api_endpoint",
                    "source_key": endpoint_text,
                    "source_label": endpoint_text[:160],
                    "matched_text": matched,
                    "score": score,
                }
            )

    for name, rule in CONTEXT_RULES.items():
        if not _rule_is_supported(rule, page_facts):
            continue
        hits = _find_term_hits(normalized_text, [name, *rule.get("terms", [])])
        if hits:
            refs.append(
                {
                    "fact_id": f"page_fact::{name}",
                    "source_type": "page_fact",
                    "source_key": name,
                    "source_label": name.replace("_", " "),
                    "matched_text": hits[0],
                    "score": 8,
                }
            )

    deduped = []
    seen = set()
    for ref in sorted(refs, key=lambda item: item.get("score", 0), reverse=True):
        fact_id = ref.get("fact_id", "")
        if not fact_id or fact_id in seen:
            continue
        deduped.append(ref)
        seen.add(fact_id)

    interaction_terms = ("input ", "click ", "select ", "choose ", "upload ", "hover ", "scroll ", "wait ")
    requires_grounding = any(term in normalized_text for term in interaction_terms)
    best_score = max((ref.get("score", 0) for ref in deduped), default=0)
    mentioned_surfaces = []
    for name, rule in CONTEXT_RULES.items():
        if _find_term_hits(normalized_text, [name, *rule.get("terms", [])]):
            mentioned_surfaces.append(name)
    covered_surfaces = [
        surface
        for surface in mentioned_surfaces
        if any(ref.get("fact_id") == f"page_fact::{surface}" for ref in deduped)
    ]
    coverage_score = (
        round(len(covered_surfaces) / len(set(mentioned_surfaces)), 2)
        if mentioned_surfaces
        else (1.0 if deduped or not requires_grounding else 0.0)
    )
    structured_ref_count = sum(
        1
        for ref in deduped
        if ref.get("source_type") in {"field", "component", "section", "state", "api_endpoint"}
    )
    page_fact_ref_count = sum(1 for ref in deduped if ref.get("source_type") == "page_fact")
    return {
        "fact_ids": [ref.get("fact_id", "") for ref in deduped[:12]],
        "refs": deduped[:8],
        "summary": "; ".join(
            f"{ref.get('source_type', '')}:{ref.get('source_label', '') or ref.get('source_key', '')}"
            for ref in deduped[:4]
        ),
        "score": round(min(1.0, best_score / 12), 2) if best_score else 0.0,
        "coverage_score": coverage_score,
        "mentioned_surfaces": _clean_string_list(mentioned_surfaces),
        "covered_surfaces": _clean_string_list(covered_surfaces),
        "ref_count": len(deduped),
        "structured_ref_count": structured_ref_count,
        "page_fact_ref_count": page_fact_ref_count,
        "page_fact_only": bool(deduped) and structured_ref_count == 0,
        "requires_grounding": requires_grounding,
    }


def _derive_page_facts(page_model: dict, page_info: dict) -> dict:
    fingerprint = page_info.get("page_fingerprint", {})
    component_types = {
        str(component.get("type", "")).strip().lower()
        for component in page_model.get("components", [])
        if str(component.get("type", "")).strip()
    }
    texts = " ".join(str(text) for text in page_info.get("texts", [])[:30]).lower()
    headings = " ".join(
        str(heading.get("text", "")) for heading in page_info.get("headings", [])[:20] if isinstance(heading, dict)
    ).lower()
    haystack = f"{texts} {headings}"

    return {
        "form": bool(fingerprint.get("has_form") or "form" in component_types),
        "auth": bool(fingerprint.get("has_auth_pattern") or any(term in haystack for term in ("login", "sign in", "password", "username"))),
        "search": bool(fingerprint.get("has_search") or "search" in component_types),
        "filter": bool(fingerprint.get("has_filters") or "filter" in component_types),
        "pagination": bool(fingerprint.get("has_pagination") or "pagination" in component_types),
        "table": bool(fingerprint.get("has_table") or "table" in component_types),
        "navigation": bool(fingerprint.get("has_navigation") or "navigation" in component_types),
        "listing": bool(fingerprint.get("has_listing_pattern") or "listing" in component_types),
        "content": bool(fingerprint.get("has_article_like_sections") or "content" in component_types),
        "upload": bool(fingerprint.get("has_upload") or "file_upload" in component_types),
        "rich_text": bool(fingerprint.get("has_rich_text") or "rich_text_editor" in component_types),
        "iframe": bool(fingerprint.get("has_iframe") or "iframe" in component_types),
        "shadow_dom": bool(fingerprint.get("has_shadow_dom") or "shadow_dom" in component_types),
        "consent_banner": bool(fingerprint.get("has_cookie_banner") or "consent_banner" in component_types),
        "captcha": bool(fingerprint.get("has_captcha") or "captcha" in component_types),
        "combobox": bool(fingerprint.get("has_combobox") or "combobox" in component_types),
        "datepicker": bool(fingerprint.get("has_datepicker") or "datepicker" in component_types),
        "timepicker": bool(fingerprint.get("has_timepicker") or "timepicker" in component_types),
        "toast": bool(fingerprint.get("has_toast") or "toast" in component_types),
        "drawer": bool(fingerprint.get("has_drawer") or "drawer" in component_types),
        "carousel": bool(fingerprint.get("has_carousel") or "carousel" in component_types),
        "infinite_scroll": bool(fingerprint.get("has_infinite_scroll") or "infinite_scroll" in component_types),
        "map": bool(fingerprint.get("has_map") or "map" in component_types),
        "chart": bool(fingerprint.get("has_chart") or "chart" in component_types),
        "spa_shell": bool(fingerprint.get("has_spa_shell") or "spa_shell" in component_types),
        "graphql": bool(fingerprint.get("has_graphql") or "graphql_surface" in component_types),
        "api_surface": bool(page_info.get("apis") or fingerprint.get("has_graphql") or "graphql_surface" in component_types),
        "websocket": bool(fingerprint.get("has_websocket") or "live_feed" in component_types),
        "live_updates": bool(fingerprint.get("has_live_updates") or "live_feed" in component_types),
        "otp_flow": bool(fingerprint.get("has_otp_flow") or "otp_verification" in component_types),
        "sso": bool(fingerprint.get("has_sso") or "sso_login" in component_types),
        "auth_checkpoint": bool(fingerprint.get("has_auth_checkpoint") or "otp_verification" in component_types or "captcha" in component_types),
    }


def _find_context_mismatches(items: list[str], page_facts: dict) -> list[str]:
    invalid = []
    for item in items:
        if detect_out_of_context_case({"Module": item, "Title": item}, page_facts):
            invalid.append(item)
    return invalid


def _filter_context_items(items: list[str], page_facts: dict) -> list[str]:
    return [item for item in items if not detect_out_of_context_case({"Module": item, "Title": item}, page_facts)]


def _remove_items_by_terms(items: list[str], terms: list[str]) -> list[str]:
    lowered_terms = [_normalize_text(term).lower() for term in terms if _normalize_text(term)]
    rows = []
    for item in items:
        text = _normalize_text(item).lower()
        if any(term in text for term in lowered_terms):
            continue
        rows.append(item)
    return rows


def _fallback_modules(page_facts: dict, component_types: list[str]) -> list[str]:
    modules = []
    for item in component_types:
        modules.append(item.replace("_", " ").title())
    if page_facts.get("content"):
        modules.append("Content")
    if page_facts.get("navigation"):
        modules.append("Navigation")
    if not modules:
        modules.append("General Page")
    return _clean_string_list(modules)


def _fallback_flows(page_facts: dict, component_types: list[str]) -> list[str]:
    flows = ["Open the page and verify primary content"]
    if "navigation" in component_types or page_facts.get("navigation"):
        flows.append("Navigate through primary links")
    if "search" in component_types or page_facts.get("search"):
        flows.append("Use search controls and verify the results state")
    if "filter" in component_types or page_facts.get("filter"):
        flows.append("Change filters and verify the displayed state")
    if "form" in component_types or page_facts.get("form"):
        flows.append("Input data and submit the main form")
    return _clean_string_list(flows)


def _infer_page_type(page_facts: dict) -> str:
    if page_facts.get("form") and page_facts.get("auth"):
        return "authentication page"
    if page_facts.get("listing") and page_facts.get("navigation"):
        return "hub or listing page"
    if page_facts.get("content"):
        return "content page"
    if page_facts.get("table"):
        return "data page"
    return "general page"


def _clean_string_list(items: list) -> list[str]:
    cleaned = []
    seen = set()
    for item in items:
        text = _normalize_text(item)
        if text and text.lower() not in seen:
            cleaned.append(text)
            seen.add(text.lower())
    return cleaned


def compile_instruction_contract(custom_instruction: str, page_facts: dict) -> dict:
    normalized = _normalize_text(custom_instruction).lower()
    must_focus_surfaces = []
    avoid_surfaces = []
    unsupported_requested_surfaces = []
    preferred_test_types = []
    only_test_types = []
    conflicts = []

    if not normalized:
        return {
            "must_focus_surfaces": [],
            "avoid_surfaces": [],
            "unsupported_requested_surfaces": [],
            "preferred_test_types": [],
            "only_test_types": [],
            "conflicts": [],
            "requires_edge_cases": False,
            "requires_error_validations": False,
        }

    priority_verbs = ("prioritize", "focus on", "focus", "emphasize", "target")
    request_verbs = priority_verbs + ("test", "cover", "check", "validate", "verify")
    avoid_verbs = ("avoid", "ignore", "skip", "exclude", "do not test", "don't test")
    for name, rule in CONTEXT_RULES.items():
        terms = [name.replace("_", " "), *rule.get("terms", [])]
        requested = any(term in normalized for term in terms)
        if not requested:
            continue
        if any(f"{verb} {term}" in normalized for verb in request_verbs for term in terms):
            if _rule_is_supported(rule, page_facts):
                must_focus_surfaces.append(name.replace("_", " "))
            else:
                unsupported_requested_surfaces.append(name.replace("_", " "))
        if any(f"{verb} {term}" in normalized for verb in avoid_verbs for term in terms):
            avoid_surfaces.append(name.replace("_", " "))
        elif requested and not _rule_is_supported(rule, page_facts):
            unsupported_requested_surfaces.append(name.replace("_", " "))

    if "negative" in normalized:
        preferred_test_types.append("negative")
    if "positive" in normalized:
        preferred_test_types.append("positive")
    if "only negative" in normalized:
        only_test_types.append("negative")
    if "only positive" in normalized:
        only_test_types.append("positive")

    must_focus_surfaces = _clean_string_list(must_focus_surfaces)
    avoid_surfaces = _clean_string_list(avoid_surfaces)
    unsupported_requested_surfaces = _clean_string_list(unsupported_requested_surfaces)
    preferred_test_types = _clean_string_list(preferred_test_types)
    only_test_types = _clean_string_list(only_test_types)

    overlap = sorted(set(must_focus_surfaces) & set(avoid_surfaces))
    for surface in overlap:
        conflicts.append(f"surface '{surface}' is both focused and avoided")
    if overlap:
        must_focus_surfaces = [item for item in must_focus_surfaces if item not in overlap]

    if len(set(only_test_types)) > 1:
        conflicts.append("instruction requests mutually exclusive only-test-type constraints")
        only_test_types = []

    return {
        "must_focus_surfaces": must_focus_surfaces,
        "avoid_surfaces": avoid_surfaces,
        "unsupported_requested_surfaces": unsupported_requested_surfaces,
        "preferred_test_types": preferred_test_types,
        "only_test_types": only_test_types,
        "conflicts": _clean_string_list(conflicts),
        "requires_edge_cases": any(term in normalized for term in ("edge case", "boundary", "boundary value")),
        "requires_error_validations": any(term in normalized for term in ("error validation", "validation", "invalid")),
    }


def _extract_instruction_focus_terms(custom_instruction: str, page_facts: dict) -> list[str]:
    normalized = _normalize_text(custom_instruction).lower()
    if not normalized:
        return []
    matches = []
    for name, rule in CONTEXT_RULES.items():
        if not _rule_is_supported(rule, page_facts):
            continue
        if name in normalized or any(term in normalized for term in rule["terms"]):
            matches.append(name.replace("_", " "))
    return _clean_string_list(matches)


def _rule_is_supported(rule: dict, page_facts: dict) -> bool:
    requires = rule.get("requires")
    requires_any = rule.get("requires_any", ())
    if requires:
        return bool(page_facts.get(requires, False))
    if requires_any:
        return any(page_facts.get(item, False) for item in requires_any)
    return True


def _find_term_hits(text: str, terms: list[str]) -> list[str]:
    normalized_text = _normalize_text(text).lower()
    hits = []
    for term in terms:
        normalized_term = _normalize_text(term).lower()
        if normalized_term and normalized_term not in hits and normalized_term in normalized_text:
            hits.append(normalized_term)
    return hits


def detect_case_contradictions(test_case: dict) -> list[str]:
    issues = []
    test_type = str(test_case.get("Test Type", "")).strip().lower()
    expected = _normalize_text(test_case.get("Expected Result", "")).lower()
    title = _normalize_text(test_case.get("Title", "")).lower()
    severity = str(test_case.get("Severity", "")).strip().lower()
    priority = str(test_case.get("Priority", "")).strip().lower()

    negative_markers = ("error", "invalid", "rejected", "blocked", "failed", "must not")
    success_markers = ("success", "successfully", "visible", "displayed", "saved", "created")

    if test_type == "positive" and any(marker in expected for marker in negative_markers):
        issues.append("contradiction: positive test expects error-like outcome")
    if test_type == "negative" and any(marker in expected for marker in success_markers) and not any(
        marker in expected for marker in negative_markers
    ):
        issues.append("contradiction: negative test expects success-only outcome")
    if any(token in title for token in ("security", "auth", "payment", "data loss", "session")) and severity in {"low", "trivial"}:
        issues.append("contradiction: high-risk scenario marked with too-low severity")
    if severity in {"critical", "blocker", "highest"} and priority in {"p4", "low"}:
        issues.append("contradiction: critical severity paired with low priority")
    if severity in {"low", "minor", "trivial"} and priority in {"p0", "p1", "highest"}:
        issues.append("contradiction: low severity paired with highest priority")
    return issues


def validate_case_intent_to_action(test_case: dict, page_facts: dict) -> list[str]:
    issues = []
    steps = str(test_case.get("Steps to Reproduce", "") or "")
    if not steps.strip():
        return ["intent-to-action mismatch: steps to reproduce is empty"]

    lines = [re.sub(r"^\s*\d+\.\s*", "", line).strip().lower() for line in steps.splitlines() if line.strip()]
    if len(lines) < 2:
        issues.append("intent-to-action mismatch: scenario should include actionable steps after opening site")
        return issues

    step_one = lines[0]
    if not (step_one.startswith("open the site") or step_one.startswith("open site")):
        issues.append("intent-to-action mismatch: step 1 should open the target site")

    merged = "\n".join(lines)
    action_flags = {
        "input": any(line.startswith("input ") for line in lines),
        "click": any(line.startswith("click ") for line in lines),
        "select": any(line.startswith("select ") or line.startswith("choose ") for line in lines),
        "upload": any(line.startswith("upload ") for line in lines),
        "hover": any(line.startswith("hover ") for line in lines),
        "scroll": any(line.startswith("scroll ") for line in lines),
        "wait": any(line.startswith("wait ") for line in lines),
    }
    if not any(action_flags.values()):
        issues.append("intent-to-action mismatch: no executable user action detected in steps")

    if (action_flags["input"] or action_flags["select"]) and not any(page_facts.get(key, False) for key in ("form", "search", "filter", "auth")):
        issues.append("intent-to-action mismatch: fill/select steps require form/search/filter/auth page signals")
    if action_flags["upload"] and not page_facts.get("upload", False):
        issues.append("intent-to-action mismatch: upload action requires upload control signal")
    if "otp" in merged and not any(page_facts.get(key, False) for key in ("otp_flow", "auth")):
        issues.append("intent-to-action mismatch: OTP step requires OTP/auth page signal")
    if ("sso" in merged or "single sign-on" in merged) and not any(page_facts.get(key, False) for key in ("sso", "auth")):
        issues.append("intent-to-action mismatch: SSO step requires SSO/auth page signal")
    return issues


def _case_exact_signature(test_case: dict) -> str:
    text = " ".join(
        str(test_case.get(key, "")).strip().lower()
        for key in ("Module", "Category", "Test Type", "Title", "Steps to Reproduce", "Expected Result")
    )
    text = re.sub(r"\s+", " ", text).strip()
    text = re.sub(r"\d+", "#", text)
    return text[:420]


def _case_soft_signature(test_case: dict) -> str:
    title = _normalize_text(test_case.get("Title", "")).lower()
    module = _normalize_text(test_case.get("Module", "")).lower()
    test_type = _normalize_text(test_case.get("Test Type", "")).lower()
    tokens = [token for token in re.findall(r"[a-z0-9]+", title) if len(token) > 2]
    compact = "_".join(tokens[:8])
    return f"{module}|{test_type}|{compact}"


def _best_grounding_match(text: str, candidates: list[str]) -> tuple[int, str]:
    best_score = 0
    best_match = ""
    for candidate in candidates:
        score = _grounding_match_score(text, candidate)
        if score > best_score:
            best_score = score
            best_match = _normalize_text(candidate)
    return best_score, best_match


def _grounding_match_score(text: str, candidate: str) -> int:
    haystack = _normalize_text(text).lower()
    needle = _normalize_text(candidate).lower()
    if not haystack or not needle:
        return 0
    if needle in haystack:
        return 11 if " " in needle else 8
    needle_tokens = [token for token in re.findall(r"[a-z0-9]+", needle) if len(token) > 2]
    if not needle_tokens:
        return 0
    hits = sum(1 for token in needle_tokens if token in haystack)
    if hits >= min(len(needle_tokens), 2):
        return min(10, 6 + hits)
    return 0


def _normalize_key(value: object) -> str:
    text = re.sub(r"[^a-z0-9]+", "_", str(value or "").strip().lower())
    return text.strip("_")


def _normalize_text(value: object) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    return text[:240]


def _coerce_confidence(value: object) -> float:
    try:
        return round(max(0.0, min(1.0, float(value))), 2)
    except (TypeError, ValueError):
        return 0.0


def _validate_grounding(item: dict, kind: str) -> str:
    item_type = str(item.get("type", "")).strip().lower()
    if item_type in {"open_url", "inspect"}:
        return ""
    refs = item.get("grounding_refs", []) or []
    if not refs:
        return f"{kind} '{item_type}' is not grounded to any scanned field/component"
    confidence = float(item.get("grounding_confidence", 0.0) or 0.0)
    if confidence < 0.35:
        return f"{kind} '{item_type}' has weak grounding confidence"
    if item_type in {"fill", "select", "upload"} and not _has_grounding_ref(refs, {"field", "submit_control"}):
        return f"{kind} '{item_type}' is missing field grounding"
    if item_type in {"click", "hover", "dismiss", "wait_for_text", "scroll"} and not _has_grounding_ref(
        refs, {"component", "submit_control", "heading", "button", "link", "field", "page"}
    ):
        return f"{kind} '{item_type}' is missing interaction grounding"
    if item_type.startswith("assert_") and not _has_grounding_ref(
        refs, {"component", "heading", "button", "link", "field", "page_identity", "page_fact", "state"}
    ):
        return f"{kind} '{item_type}' is missing assertion grounding"
    return ""


def _has_grounding_ref(refs: list[dict], source_types: set[str]) -> bool:
    for ref in refs:
        source_type = str(ref.get("source_type", "")).strip().lower()
        if source_type in source_types:
            return True
    return False
