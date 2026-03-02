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
    facts = _derive_page_facts(page_model, page_info)

    return {
        "component_types": component_types,
        "module_labels": module_labels[:50],
        "flow_names": flow_names[:20],
        "field_semantics": field_semantics[:30],
        "field_aliases": field_aliases[:60],
        "action_types": action_types,
        "page_facts": facts,
    }


def validate_page_scope(page_scope: dict, page_model: dict | None, page_info: dict | None) -> dict:
    allowed = build_allowed_vocabulary(page_model, page_scope, page_info)
    issues = []

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

    if not sanitized["page_type"]:
        sanitized["page_type"] = _infer_page_type(allowed["page_facts"])
        issues.append("page_type was empty and replaced from local facts.")
    if not sanitized["primary_goal"]:
        sanitized["primary_goal"] = "Review the primary interactions and visible content on this page."
        issues.append("primary_goal was empty.")

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

    penalty = min(0.45, 0.08 * len(invalid_modules + invalid_flows + invalid_priorities))
    sanitized["confidence"] = round(max(0.15, sanitized["confidence"] - penalty), 2)

    return {
        "is_valid": len(invalid_modules + invalid_flows) == 0,
        "issues": issues,
        "page_scope": sanitized,
        "allowed_vocabulary": allowed,
    }


def validate_test_scenarios(
    test_cases: list[dict],
    page_model: dict | None,
    page_scope: dict | None,
    page_info: dict | None,
) -> dict:
    allowed = build_allowed_vocabulary(page_model, page_scope, page_info)
    valid_cases = []
    rejected_cases = []
    issues = []

    for case in test_cases:
        context_errors = detect_out_of_context_case(case, allowed["page_facts"])
        sanitized = dict(case)
        sanitized["Module"] = _normalize_text(sanitized.get("Module", "")) or "General"
        sanitized["Title"] = _normalize_text(sanitized.get("Title", "")) or "Untitled scenario"
        sanitized["Automation"] = str(sanitized.get("Automation", "auto")).strip().lower() or "auto"
        if context_errors:
            rejected_cases.append({"case": sanitized, "issues": context_errors})
            issues.extend(f"{sanitized.get('ID', 'UNKNOWN')}: {item}" for item in context_errors)
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
        "inspect", "assert_text_visible", "assert_control_text", "assert_url_contains"
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
            if (
                assertion_type == "assert_url_contains"
                and not facts.get("navigation", False)
                and not facts.get("listing", False)
                and not facts.get("form", False)
                and not facts.get("auth", False)
            ):
                assertion_errors.append("url assertion has weak navigation support on this page")
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


def _normalize_text(value: object) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    return text[:240]


def _coerce_confidence(value: object) -> float:
    try:
        return round(max(0.0, min(1.0, float(value))), 2)
    except (TypeError, ValueError):
        return 0.0
