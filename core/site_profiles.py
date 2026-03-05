import json
from copy import deepcopy
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import urlparse

from core.feedback_bank import load_feedback_snapshot


LEARNING_DEFAULTS = {
    "enabled": True,
    "field_selectors": {},
    "action_selectors": {},
    "semantic_patterns": {},
    "selector_stats": {
        "field_selectors": {},
        "action_selectors": {},
    },
    "failure_memory": {
        "field_selectors": {},
        "action_selectors": {},
        "semantic_patterns": {},
    },
    "scoring": {
        "selector_limit_per_key": 12,
        "selector_stats_limit_per_key": 20,
        "failure_limit_per_key": 10,
        "semantic_selector_limit_per_key": 8,
        "stale_days": 45,
    },
    "updated_at": "",
}

DEFAULT_SITE_PROFILE = {
    "name": "generic",
    "link_selection": {
        "blacklist_terms": [
            "logout", "signout", "sign-out", "privacy", "terms", "policy", "cookies",
            "mailto:", "tel:", "javascript:", "#", "whatsapp", "facebook", "twitter",
            "instagram", "linkedin", "tiktok", "youtube",
        ],
        "priority_terms": [
            "detail", "view", "read", "open", "more", "next", "continue", "menu",
            "search", "filter", "sort", "page", "submit", "save", "apply", "login",
        ],
    },
    "interaction": {
        "step_delay_ms": 700,
        "settle_delay_ms": 1000,
        "final_delay_ms": 1400,
        "retry_count": 2,
    },
    "auth": {
        "storage_state_candidates": [
            "auth/auth_state.json",
            "auth/session_state.json",
        ],
        "login_terms": ["login", "sign in", "masuk", "log in"],
        "otp_terms": ["otp", "verification code", "one time password", "kode verifikasi"],
        "sso_terms": ["continue with google", "continue with microsoft", "sign in with", "single sign-on", "sso"],
        "manual_checkpoint_terms": ["captcha", "otp", "verification code", "device verification", "magic link"],
    },
    "execution": {
        "allow_semi_auto": True,
        "auto_dismiss_consent": True,
        "video_mode": "per_test",
    },
    "network": {
        "cross_origin_mode": "same-origin",
        "allowed_hosts": [],
        "graphql_error_keys": ["errors", "error", "extensions"],
    },
    "learning": deepcopy(LEARNING_DEFAULTS),
}


def deep_merge(base: dict, override: dict) -> dict:
    merged = deepcopy(base)
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = deep_merge(merged[key], value)
        else:
            merged[key] = deepcopy(value)
    return merged


def _profile_candidate_paths(url: str, profiles_dir: str | Path = "site_profiles") -> list[Path]:
    parsed = urlparse(url)
    host = (parsed.netloc or "").replace("www.", "").lower()
    safe_host = "".join(ch if ch.isalnum() or ch in {"-", "_", "."} else "_" for ch in host)
    base = Path(profiles_dir)
    return [
        base / "_default.json",
        base / "learned" / "_global.json",
        base / f"{safe_host}.json",
        base / "learned" / f"{safe_host}.json",
    ]


def load_site_profile(url: str, profiles_dir: str | Path = "site_profiles") -> dict:
    profile = deepcopy(DEFAULT_SITE_PROFILE)
    loaded_from = []
    for path in _profile_candidate_paths(url, profiles_dir):
        if path.exists():
            loaded_from.append(str(path))
            profile = _merge_profile_data(profile, json.loads(path.read_text(encoding="utf-8")))
    parsed = urlparse(url)
    profile["resolved_host"] = (parsed.netloc or "").replace("www.", "").lower()
    profile["loaded_from"] = loaded_from
    _normalize_learning_profile(profile)
    profile["human_feedback"] = load_feedback_snapshot(url)
    profile["knowledge_bank"] = build_knowledge_bank_summary(profile)
    return profile


def merge_execution_learning(
    url: str,
    learning_payload: dict,
    profiles_dir: str | Path = "site_profiles",
    knowledge_context: dict | None = None,
) -> dict | None:
    learning_entries = list(learning_payload.get("learning_entries", []))
    if not learning_entries:
        return None

    parsed = urlparse(url)
    host = (parsed.netloc or "").replace("www.", "").lower()
    if not host:
        return None

    base = Path(profiles_dir)
    learned_dir = base / "learned"
    learned_dir.mkdir(parents=True, exist_ok=True)
    global_path = learned_dir / "_global.json"
    learned_path = learned_dir / f"{host}.json"
    cluster_paths = [
        learned_dir / "clusters" / f"{cluster_key}.json"
        for cluster_key in derive_cluster_keys(
            knowledge_context.get("page_model") if knowledge_context else None,
            knowledge_context.get("page_scope") if knowledge_context else None,
        )
    ]

    global_profile = _load_learning_profile(global_path)
    domain_profile = _load_learning_profile(learned_path)
    cluster_profiles = [(path, _load_learning_profile(path)) for path in cluster_paths]

    _apply_learning_entries(global_profile, learning_entries, scope="global", host=host)
    _apply_learning_entries(domain_profile, learning_entries, scope="domain", host=host)
    for _, cluster_profile in cluster_profiles:
        _apply_learning_entries(cluster_profile, learning_entries, scope="cluster", host=host)

    global_profile["learning"]["updated_at"] = _now_iso()
    domain_profile["learning"]["updated_at"] = _now_iso()
    for _, cluster_profile in cluster_profiles:
        cluster_profile["learning"]["updated_at"] = _now_iso()
    _compress_learning_profile(global_profile)
    _compress_learning_profile(domain_profile)
    for _, cluster_profile in cluster_profiles:
        _compress_learning_profile(cluster_profile)

    global_path.write_text(json.dumps(global_profile, indent=2, ensure_ascii=False), encoding="utf-8")
    learned_path.write_text(json.dumps(domain_profile, indent=2, ensure_ascii=False), encoding="utf-8")
    for path, cluster_profile in cluster_profiles:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(cluster_profile, indent=2, ensure_ascii=False), encoding="utf-8")
    return {
        "global_path": str(global_path),
        "domain_path": str(learned_path),
        "cluster_paths": [str(path) for path in cluster_paths],
        "updated_entries": len(learning_entries),
        "global_summary": build_knowledge_bank_summary(global_profile),
        "domain_summary": build_knowledge_bank_summary(domain_profile),
    }


def build_knowledge_bank_summary(profile: dict) -> dict:
    learning = _normalize_learning_container(profile.get("learning", {}))
    field_selectors = learning.get("field_selectors", {})
    action_selectors = learning.get("action_selectors", {})
    semantic_patterns = learning.get("semantic_patterns", {})
    failure_memory = learning.get("failure_memory", {})
    return {
        "field_keys": sorted(field_selectors.keys())[:40],
        "action_keys": sorted(action_selectors.keys())[:40],
        "semantic_keys": sorted(semantic_patterns.keys())[:40],
        "field_selector_count": sum(len(value) for value in field_selectors.values()),
        "action_selector_count": sum(len(value) for value in action_selectors.values()),
        "semantic_pattern_count": len(semantic_patterns),
        "failure_count": sum(
            len(bucket)
            for bucket_map in failure_memory.values()
            for bucket in bucket_map.values()
        ),
        "updated_at": learning.get("updated_at", ""),
        "top_field_selectors": _top_ranked_selector_records(learning, "field_selectors"),
        "top_action_selectors": _top_ranked_selector_records(learning, "action_selectors"),
        "top_semantic_patterns": _top_semantic_patterns(learning),
        "top_failures": _top_failure_records(learning),
    }


def get_ranked_selector_candidates(learning: dict, selector_type: str, key: object, limit: int = 6) -> list[str]:
    normalized_key = _normalize_learning_key(key)
    if not normalized_key:
        return []
    learning = _normalize_learning_container(learning)
    selector_map = learning.get(selector_type, {})
    ordered = list(selector_map.get(normalized_key, []))
    if selector_type == "field_selectors" and not ordered:
        ordered.extend(list(learning.get("semantic_patterns", {}).get(normalized_key, {}).get("selectors", [])))
    stats = learning.get("selector_stats", {}).get(selector_type, {}).get(normalized_key, {})
    semantic_stats = learning.get("semantic_patterns", {}).get(normalized_key, {}).get("selector_stats", {})
    failures = {
        item.get("selector", ""): item
        for item in learning.get("failure_memory", {}).get(selector_type, {}).get(normalized_key, [])
    }

    candidates = []
    for selector in ordered:
        stat = _normalize_selector_stat(stats.get(selector, semantic_stats.get(selector, {})), selector)
        failure_entry = failures.get(selector, {})
        effective_score = float(stat.get("score", 0.0)) - (float(failure_entry.get("failures", 0)) * 0.35)
        if stat.get("successes", 0.0) <= 0 and effective_score < 0:
            continue
        candidates.append(
            {
                "selector": selector,
                "effective_score": effective_score,
                "successes": float(stat.get("successes", 0.0)),
                "failures": int(stat.get("failures", 0)),
                "last_used_at": str(stat.get("last_used_at", "")),
            }
        )
    candidates.sort(
        key=lambda item: (
            -item["effective_score"],
            -item["successes"],
            item["failures"],
            item["last_used_at"],
        )
    )
    return [item["selector"] for item in candidates[:limit]]


def get_failure_memory(learning: dict, selector_type: str, key: object, limit: int = 4) -> list[dict]:
    normalized_key = _normalize_learning_key(key)
    if not normalized_key:
        return []
    learning = _normalize_learning_container(learning)
    return list(learning.get("failure_memory", {}).get(selector_type, {}).get(normalized_key, []))[:limit]


def load_knowledge_bank_snapshot(url: str = "", profiles_dir: str | Path = "site_profiles") -> dict:
    base = Path(profiles_dir)
    learned_dir = base / "learned"
    global_profile = _load_learning_profile(learned_dir / "_global.json")
    snapshot = {
        "global": build_knowledge_bank_summary(global_profile),
        "global_profile_path": str(learned_dir / "_global.json"),
        "domain": {},
        "domain_profile_path": "",
        "host": "",
    }
    host = (urlparse(url).netloc or "").replace("www.", "").lower() if url else ""
    if host:
        domain_path = learned_dir / f"{host}.json"
        snapshot["host"] = host
        snapshot["domain_profile_path"] = str(domain_path)
        if domain_path.exists():
            snapshot["domain"] = build_knowledge_bank_summary(_load_learning_profile(domain_path))
    return snapshot


def enrich_site_profile_with_clusters(
    site_profile: dict,
    page_model: dict | None = None,
    page_scope: dict | None = None,
    profiles_dir: str | Path = "site_profiles",
) -> dict:
    enriched = deepcopy(site_profile or DEFAULT_SITE_PROFILE)
    cluster_keys = derive_cluster_keys(page_model, page_scope)
    if not cluster_keys:
        enriched["cluster_keys"] = []
        return enriched
    loaded_from = list(enriched.get("loaded_from", []))
    for path in _cluster_candidate_paths(cluster_keys, profiles_dir):
        if path.exists():
            loaded_from.append(str(path))
            enriched = _merge_profile_data(enriched, json.loads(path.read_text(encoding="utf-8")))
    enriched["loaded_from"] = loaded_from
    enriched["cluster_keys"] = cluster_keys
    feedback_url = ""
    if enriched.get("resolved_host"):
        feedback_url = f"https://{enriched['resolved_host']}"
    enriched["human_feedback"] = load_feedback_snapshot(
        feedback_url,
        cluster_keys=cluster_keys,
    )
    _normalize_learning_profile(enriched)
    enriched["knowledge_bank"] = build_knowledge_bank_summary(enriched)
    return enriched


def derive_cluster_keys(page_model: dict | None = None, page_scope: dict | None = None) -> list[str]:
    page_model = page_model or {}
    page_scope = page_scope or {}
    facts = page_model.get("page_facts", {})
    page_type = _normalize_learning_key(page_scope.get("page_type", "") or page_model.get("heuristic_scope", {}).get("likely_page_type", ""))
    keys = []
    if page_type:
        keys.append(f"page_type_{page_type}")
    if facts.get("auth") and facts.get("form"):
        keys.append("auth_form")
    if facts.get("search") and facts.get("listing"):
        keys.append("search_listing")
    if facts.get("content") and not facts.get("listing"):
        keys.append("content_detail")
    if facts.get("table") or (facts.get("filter") and facts.get("pagination")):
        keys.append("data_listing")
    if facts.get("upload"):
        keys.append("upload_surface")
    if facts.get("navigation") and (facts.get("spa_shell") or facts.get("live_updates")):
        keys.append("app_shell")
    if facts.get("form") and not facts.get("auth"):
        keys.append("general_form")
    if facts.get("sso"):
        keys.append("sso_surface")
    if facts.get("otp_flow"):
        keys.append("otp_flow")
    return _dedupe_preserve_order(keys)


def _cluster_candidate_paths(cluster_keys: list[str], profiles_dir: str | Path = "site_profiles") -> list[Path]:
    base = Path(profiles_dir) / "learned" / "clusters"
    return [base / f"{_normalize_learning_key(cluster_key)}.json" for cluster_key in cluster_keys if _normalize_learning_key(cluster_key)]


def _merge_profile_data(base: dict, override: dict) -> dict:
    merged = deep_merge(base, {key: value for key, value in (override or {}).items() if key != "learning"})
    merged["learning"] = _merge_learning_data(base.get("learning", {}), (override or {}).get("learning", {}))
    return merged


def _merge_learning_data(base_learning: dict, override_learning: dict) -> dict:
    base_norm = _normalize_learning_container(base_learning)
    override_norm = _normalize_learning_container(override_learning)
    merged = deep_merge(LEARNING_DEFAULTS, base_norm)

    for selector_type in ("field_selectors", "action_selectors"):
        merged[selector_type] = {}
        merged["selector_stats"][selector_type] = {}
        all_keys = set(base_norm.get(selector_type, {})) | set(override_norm.get(selector_type, {}))
        for key in all_keys:
            selectors = _dedupe_preserve_order(
                list(override_norm.get(selector_type, {}).get(key, [])) +
                list(base_norm.get(selector_type, {}).get(key, []))
            )
            stats = {}
            for selector in selectors:
                stats[selector] = _merge_selector_stat(
                    base_norm.get("selector_stats", {}).get(selector_type, {}).get(key, {}).get(selector, {}),
                    override_norm.get("selector_stats", {}).get(selector_type, {}).get(key, {}).get(selector, {}),
                    selector,
                )
            merged[selector_type][key] = selectors
            merged["selector_stats"][selector_type][key] = stats

    for failure_type in ("field_selectors", "action_selectors", "semantic_patterns"):
        merged["failure_memory"][failure_type] = {}
        all_keys = set(base_norm.get("failure_memory", {}).get(failure_type, {})) | set(override_norm.get("failure_memory", {}).get(failure_type, {}))
        for key in all_keys:
            merged["failure_memory"][failure_type][key] = _merge_failure_buckets(
                base_norm.get("failure_memory", {}).get(failure_type, {}).get(key, []),
                override_norm.get("failure_memory", {}).get(failure_type, {}).get(key, []),
            )

    merged["semantic_patterns"] = {}
    all_pattern_keys = set(base_norm.get("semantic_patterns", {})) | set(override_norm.get("semantic_patterns", {}))
    for key in all_pattern_keys:
        merged["semantic_patterns"][key] = _merge_semantic_pattern(
            base_norm.get("semantic_patterns", {}).get(key, {}),
            override_norm.get("semantic_patterns", {}).get(key, {}),
        )

    merged["updated_at"] = override_norm.get("updated_at") or base_norm.get("updated_at", "")
    _compress_learning_container(merged)
    return merged


def _load_learning_profile(path: Path) -> dict:
    profile = {"learning": deepcopy(LEARNING_DEFAULTS)}
    if path.exists():
        profile = deep_merge(profile, json.loads(path.read_text(encoding="utf-8")))
    _normalize_learning_profile(profile)
    return profile


def _apply_learning_entries(profile: dict, learning_entries: list[dict], scope: str, host: str) -> None:
    _normalize_learning_profile(profile)
    learning = profile.setdefault("learning", {})
    field_selectors = learning.setdefault("field_selectors", {})
    action_selectors = learning.setdefault("action_selectors", {})
    selector_stats = learning.setdefault("selector_stats", {"field_selectors": {}, "action_selectors": {}})
    failure_memory = learning.setdefault("failure_memory", {"field_selectors": {}, "action_selectors": {}, "semantic_patterns": {}})
    semantic_patterns = learning.setdefault("semantic_patterns", {})

    for entry in learning_entries:
        details = entry.get("details", {}) or {}
        status = str(entry.get("status", "")).strip().lower()
        error_message = str(entry.get("error", "")).strip()
        resolved_selector = str(entry.get("resolved_selector", "")).strip()
        attempted = [_selector_signature(item) for item in entry.get("attempted", []) if _selector_signature(item)]
        successful_keys = _entry_learning_keys(details)
        semantic_key = _normalize_learning_key(details.get("semantic_type") or details.get("field_key") or details.get("target"))
        success_weight = _selector_success_weight(status, bool(resolved_selector))

        if resolved_selector and successful_keys:
            if details.get("semantic_type") or details.get("field_key"):
                for key in successful_keys:
                    _record_selector_success(field_selectors, selector_stats["field_selectors"], key, resolved_selector, host, scope, success_weight, status)
            for key in successful_keys:
                _record_selector_success(action_selectors, selector_stats["action_selectors"], key, resolved_selector, host, scope, success_weight, status)

        failed_attempts = []
        for attempted_selector in attempted:
            if resolved_selector and attempted_selector == resolved_selector:
                continue
            failed_attempts.append(attempted_selector)
            for key in successful_keys:
                if details.get("semantic_type") or details.get("field_key"):
                    _record_failure(
                        failure_memory["field_selectors"],
                        selector_stats["field_selectors"],
                        key,
                        attempted_selector,
                        host,
                        scope,
                        error_message,
                    )
                _record_failure(
                    failure_memory["action_selectors"],
                    selector_stats["action_selectors"],
                    key,
                    attempted_selector,
                    host,
                    scope,
                    error_message,
                )

        if semantic_key:
            _record_semantic_pattern(
                semantic_patterns,
                failure_memory["semantic_patterns"],
                semantic_key,
                resolved_selector,
                failed_attempts,
                host,
                scope,
                success_weight,
                status,
                error_message,
            )


def _normalize_learning_profile(profile: dict) -> None:
    profile["learning"] = _normalize_learning_container(profile.get("learning", {}))


def _normalize_learning_container(learning: dict | None) -> dict:
    learning = deep_merge(LEARNING_DEFAULTS, learning or {})
    learning["field_selectors"] = _normalize_selector_map(learning.get("field_selectors", {}))
    learning["action_selectors"] = _normalize_selector_map(learning.get("action_selectors", {}))
    learning["selector_stats"] = {
        "field_selectors": _normalize_selector_stats_map(learning.get("selector_stats", {}).get("field_selectors", {})),
        "action_selectors": _normalize_selector_stats_map(learning.get("selector_stats", {}).get("action_selectors", {})),
    }
    learning["semantic_patterns"] = _normalize_semantic_patterns_map(learning.get("semantic_patterns", {}))
    learning["failure_memory"] = {
        "field_selectors": _normalize_failure_map(learning.get("failure_memory", {}).get("field_selectors", {})),
        "action_selectors": _normalize_failure_map(learning.get("failure_memory", {}).get("action_selectors", {})),
        "semantic_patterns": _normalize_failure_map(learning.get("failure_memory", {}).get("semantic_patterns", {})),
    }
    _compress_learning_container(learning)
    return learning


def _normalize_selector_map(selector_map: dict) -> dict:
    normalized = {}
    for raw_key, bucket in (selector_map or {}).items():
        key = _normalize_learning_key(raw_key)
        if not key:
            continue
        selectors = []
        for item in list(bucket or []):
            if isinstance(item, dict):
                selector = str(item.get("selector", "")).strip()
            else:
                selector = str(item or "").strip()
            if selector and selector not in selectors:
                selectors.append(selector)
        normalized[key] = selectors
    return normalized


def _normalize_selector_stats_map(stats_map: dict) -> dict:
    normalized = {}
    for raw_key, bucket in (stats_map or {}).items():
        key = _normalize_learning_key(raw_key)
        if not key:
            continue
        normalized[key] = {}
        for selector, payload in (bucket or {}).items():
            selector_text = str(selector or "").strip()
            if selector_text:
                normalized[key][selector_text] = _normalize_selector_stat(payload, selector_text)
    return normalized


def _normalize_failure_map(failure_map: dict) -> dict:
    normalized = {}
    for raw_key, bucket in (failure_map or {}).items():
        key = _normalize_learning_key(raw_key)
        if not key:
            continue
        entries = []
        for item in list(bucket or []):
            entry = _normalize_failure_entry(item)
            if entry.get("selector"):
                entries.append(entry)
        normalized[key] = _dedupe_failure_entries(entries)
    return normalized


def _normalize_semantic_patterns_map(patterns: dict) -> dict:
    normalized = {}
    for raw_key, payload in (patterns or {}).items():
        key = _normalize_learning_key(raw_key)
        if not key:
            continue
        selectors = []
        selector_stats = {}
        for item in list(payload.get("selectors", [])):
            if isinstance(item, dict):
                selector = str(item.get("selector", "")).strip()
                if selector:
                    selectors.append(selector)
                    selector_stats[selector] = _normalize_selector_stat(item, selector)
            else:
                selector = str(item or "").strip()
                if selector:
                    selectors.append(selector)
        for selector, stat in (payload.get("selector_stats", {}) or {}).items():
            selector_text = str(selector or "").strip()
            if selector_text:
                selector_stats[selector_text] = _normalize_selector_stat(stat, selector_text)
                if selector_text not in selectors:
                    selectors.append(selector_text)
        normalized[key] = {
            "hits": int(payload.get("hits", 0) or 0),
            "successes": float(payload.get("successes", payload.get("hits", 0)) or 0.0),
            "failures": int(payload.get("failures", 0) or 0),
            "selectors": _dedupe_preserve_order(selectors),
            "selector_stats": selector_stats,
            "top_failures": _dedupe_failure_entries([_normalize_failure_entry(item) for item in payload.get("top_failures", [])]),
            "scopes": _dedupe_preserve_order([str(item) for item in payload.get("scopes", []) if str(item).strip()]),
            "domains": _dedupe_preserve_order([str(item) for item in payload.get("domains", []) if str(item).strip()]),
            "score": float(payload.get("score", 0.0) or 0.0),
            "updated_at": str(payload.get("updated_at", "")),
        }
    return normalized


def _normalize_selector_stat(payload: dict, selector: str) -> dict:
    payload = payload or {}
    stat = {
        "selector": selector,
        "successes": float(payload.get("successes", 0.0) or 0.0),
        "failures": int(payload.get("failures", 0) or 0),
        "last_used_at": str(payload.get("last_used_at", payload.get("updated_at", "")) or ""),
        "last_status": str(payload.get("last_status", "")),
        "domains": _dedupe_preserve_order([str(item) for item in payload.get("domains", []) if str(item).strip()]),
        "scopes": _dedupe_preserve_order([str(item) for item in payload.get("scopes", []) if str(item).strip()]),
        "score": float(payload.get("score", 0.0) or 0.0),
    }
    stat["score"] = round(stat["score"] or _selector_score(stat), 2)
    return stat


def _normalize_failure_entry(payload: dict) -> dict:
    if isinstance(payload, str):
        payload = {"selector": payload}
    payload = payload or {}
    entry = {
        "selector": str(payload.get("selector", "")).strip(),
        "failures": int(payload.get("failures", 0) or 0),
        "last_error": str(payload.get("last_error", "")),
        "last_seen_at": str(payload.get("last_seen_at", payload.get("updated_at", "")) or ""),
        "domains": _dedupe_preserve_order([str(item) for item in payload.get("domains", []) if str(item).strip()]),
        "scopes": _dedupe_preserve_order([str(item) for item in payload.get("scopes", []) if str(item).strip()]),
        "score": float(payload.get("score", 0.0) or 0.0),
    }
    entry["score"] = round(entry["score"] or _failure_score(entry), 2)
    return entry


def _compress_learning_profile(profile: dict) -> None:
    profile["learning"] = _normalize_learning_container(profile.get("learning", {}))


def _compress_learning_container(learning: dict) -> None:
    scoring = learning.get("scoring", {})
    selector_limit = int(scoring.get("selector_limit_per_key", 12) or 12)
    stats_limit = int(scoring.get("selector_stats_limit_per_key", 20) or 20)
    failure_limit = int(scoring.get("failure_limit_per_key", 10) or 10)
    semantic_limit = int(scoring.get("semantic_selector_limit_per_key", 8) or 8)
    stale_days = int(scoring.get("stale_days", 45) or 45)

    for selector_type in ("field_selectors", "action_selectors"):
        selector_map = learning.get(selector_type, {})
        stats_map = learning.get("selector_stats", {}).get(selector_type, {})
        failure_map = learning.get("failure_memory", {}).get(selector_type, {})
        for key in list(selector_map.keys()):
            stats_bucket = stats_map.setdefault(key, {})
            selector_map[key] = _sort_selector_bucket(selector_map.get(key, []), stats_bucket, failure_map.get(key, []), selector_limit)
            stats_map[key] = _prune_selector_stats(stats_bucket, selector_map[key], stats_limit, stale_days)
            if not selector_map[key] and not stats_map[key]:
                selector_map.pop(key, None)
                stats_map.pop(key, None)
        for key in list(failure_map.keys()):
            failure_map[key] = _prune_failure_bucket(failure_map[key], failure_limit, stale_days)
            if not failure_map[key]:
                failure_map.pop(key, None)

    for key in list(learning.get("semantic_patterns", {}).keys()):
        pattern = learning["semantic_patterns"][key]
        pattern["selectors"] = _sort_selector_bucket(
            pattern.get("selectors", []),
            pattern.setdefault("selector_stats", {}),
            pattern.get("top_failures", []),
            semantic_limit,
        )
        pattern["selector_stats"] = _prune_selector_stats(pattern.get("selector_stats", {}), pattern["selectors"], stats_limit, stale_days)
        pattern["top_failures"] = _prune_failure_bucket(pattern.get("top_failures", []), failure_limit, stale_days)
        pattern["domains"] = _dedupe_preserve_order(pattern.get("domains", []))[:12]
        pattern["scopes"] = _dedupe_preserve_order(pattern.get("scopes", []))[:8]
        pattern["score"] = round(_pattern_score(pattern), 2)
        if not pattern["selectors"] and not pattern["top_failures"] and not pattern.get("hits"):
            learning["semantic_patterns"].pop(key, None)

    for key in list(learning.get("failure_memory", {}).get("semantic_patterns", {}).keys()):
        learning["failure_memory"]["semantic_patterns"][key] = _prune_failure_bucket(
            learning["failure_memory"]["semantic_patterns"][key],
            failure_limit,
            stale_days,
        )
        if not learning["failure_memory"]["semantic_patterns"][key]:
            learning["failure_memory"]["semantic_patterns"].pop(key, None)


def _record_selector_success(
    selector_map: dict,
    stats_map: dict,
    key: str,
    selector: str,
    host: str,
    scope: str,
    success_weight: float,
    status: str,
) -> None:
    if success_weight <= 0:
        return
    bucket = selector_map.setdefault(key, [])
    stats_bucket = stats_map.setdefault(key, {})
    if selector not in bucket:
        bucket.append(selector)
    stat = stats_bucket.setdefault(selector, _normalize_selector_stat({}, selector))
    stat["successes"] = round(float(stat.get("successes", 0.0)) + success_weight, 2)
    stat["last_status"] = status
    stat["last_used_at"] = _now_iso()
    _append_unique(stat["domains"], host)
    _append_unique(stat["scopes"], scope)
    stat["score"] = round(_selector_score(stat), 2)
    selector_map[key] = _sort_selector_bucket(bucket, stats_bucket, [], 999)


def _record_failure(
    failure_map: dict,
    stats_map: dict,
    key: str,
    selector: str,
    host: str,
    scope: str,
    error_message: str,
) -> None:
    if not selector:
        return
    bucket = failure_map.setdefault(key, [])
    stats_bucket = stats_map.setdefault(key, {})
    entry = next((item for item in bucket if item.get("selector") == selector), None)
    if not entry:
        entry = _normalize_failure_entry({"selector": selector})
        bucket.append(entry)
    entry["failures"] = int(entry.get("failures", 0)) + 1
    entry["last_error"] = error_message[:240]
    entry["last_seen_at"] = _now_iso()
    _append_unique(entry["domains"], host)
    _append_unique(entry["scopes"], scope)
    entry["score"] = round(_failure_score(entry), 2)

    stat = stats_bucket.setdefault(selector, _normalize_selector_stat({}, selector))
    stat["failures"] = int(stat.get("failures", 0)) + 1
    stat["last_status"] = "failed"
    stat["last_used_at"] = _now_iso()
    _append_unique(stat["domains"], host)
    _append_unique(stat["scopes"], scope)
    stat["score"] = round(_selector_score(stat), 2)


def _record_semantic_pattern(
    semantic_patterns: dict,
    failure_memory: dict,
    key: str,
    resolved_selector: str,
    failed_attempts: list[str],
    host: str,
    scope: str,
    success_weight: float,
    status: str,
    error_message: str,
) -> None:
    pattern = semantic_patterns.setdefault(
        key,
        {
            "hits": 0,
            "successes": 0.0,
            "failures": 0,
            "selectors": [],
            "selector_stats": {},
            "top_failures": [],
            "scopes": [],
            "domains": [],
            "score": 0.0,
            "updated_at": "",
        },
    )
    pattern["hits"] = int(pattern.get("hits", 0)) + 1
    pattern["updated_at"] = _now_iso()
    _append_unique(pattern["domains"], host)
    _append_unique(pattern["scopes"], scope)
    if resolved_selector and success_weight > 0:
        if resolved_selector not in pattern["selectors"]:
            pattern["selectors"].append(resolved_selector)
        stat = pattern["selector_stats"].setdefault(resolved_selector, _normalize_selector_stat({}, resolved_selector))
        stat["successes"] = round(float(stat.get("successes", 0.0)) + success_weight, 2)
        stat["last_status"] = status
        stat["last_used_at"] = _now_iso()
        _append_unique(stat["domains"], host)
        _append_unique(stat["scopes"], scope)
        stat["score"] = round(_selector_score(stat), 2)
        pattern["successes"] = round(float(pattern.get("successes", 0.0)) + success_weight, 2)
    if failed_attempts:
        pattern["failures"] = int(pattern.get("failures", 0)) + len(failed_attempts)
        for selector in failed_attempts:
            failure_entry = next((item for item in pattern["top_failures"] if item.get("selector") == selector), None)
            if not failure_entry:
                failure_entry = _normalize_failure_entry({"selector": selector})
                pattern["top_failures"].append(failure_entry)
            failure_entry["failures"] = int(failure_entry.get("failures", 0)) + 1
            failure_entry["last_error"] = error_message[:240]
            failure_entry["last_seen_at"] = _now_iso()
            _append_unique(failure_entry["domains"], host)
            _append_unique(failure_entry["scopes"], scope)
            failure_entry["score"] = round(_failure_score(failure_entry), 2)

            memory_bucket = failure_memory.setdefault(key, [])
            memory_entry = next((item for item in memory_bucket if item.get("selector") == selector), None)
            if not memory_entry:
                memory_entry = _normalize_failure_entry({"selector": selector})
                memory_bucket.append(memory_entry)
            memory_entry["failures"] = int(memory_entry.get("failures", 0)) + 1
            memory_entry["last_error"] = error_message[:240]
            memory_entry["last_seen_at"] = _now_iso()
            _append_unique(memory_entry["domains"], host)
            _append_unique(memory_entry["scopes"], scope)
            memory_entry["score"] = round(_failure_score(memory_entry), 2)
    pattern["score"] = round(_pattern_score(pattern), 2)


def _sort_selector_bucket(selectors: list[str], stats_bucket: dict, failures: list[dict], limit: int) -> list[str]:
    failure_scores = {item.get("selector", ""): int(item.get("failures", 0)) for item in failures}
    candidates = []
    for selector in _dedupe_preserve_order(selectors):
        stat = _normalize_selector_stat(stats_bucket.get(selector, {}), selector)
        effective_score = float(stat.get("score", 0.0)) - (failure_scores.get(selector, 0) * 0.35)
        candidates.append(
            (
                selector,
                effective_score,
                float(stat.get("successes", 0.0)),
                int(stat.get("failures", 0)),
                str(stat.get("last_used_at", "")),
            )
        )
    candidates.sort(key=lambda item: (-item[1], -item[2], item[3], item[4]))
    return [item[0] for item in candidates[:limit]]


def _prune_selector_stats(stats_bucket: dict, preferred_selectors: list[str], limit: int, stale_days: int) -> dict:
    now = datetime.now()
    selected = {}
    items = []
    for selector, stat in (stats_bucket or {}).items():
        normalized = _normalize_selector_stat(stat, selector)
        items.append((selector, normalized))
    items.sort(
        key=lambda item: (
            -float(item[1].get("score", 0.0)),
            -float(item[1].get("successes", 0.0)),
            int(item[1].get("failures", 0)),
            str(item[1].get("last_used_at", "")),
        )
    )
    for selector in preferred_selectors:
        stat = dict(next((item[1] for item in items if item[0] == selector), _normalize_selector_stat({}, selector)))
        selected[selector] = stat
    for selector, stat in items:
        if selector in selected:
            continue
        if len(selected) >= limit:
            break
        if float(stat.get("successes", 0.0)) <= 0 and _is_stale(stat.get("last_used_at", ""), stale_days, now):
            continue
        selected[selector] = stat
    return selected


def _prune_failure_bucket(bucket: list[dict], limit: int, stale_days: int) -> list[dict]:
    now = datetime.now()
    items = [entry for entry in (_normalize_failure_entry(item) for item in bucket or []) if entry.get("selector")]
    items.sort(
        key=lambda item: (
            -float(item.get("score", 0.0)),
            -int(item.get("failures", 0)),
            str(item.get("last_seen_at", "")),
        )
    )
    pruned = []
    for item in items:
        if len(pruned) >= limit:
            break
        if _is_stale(item.get("last_seen_at", ""), stale_days, now) and int(item.get("failures", 0)) <= 1:
            continue
        pruned.append(item)
    return pruned


def _top_ranked_selector_records(learning: dict, selector_type: str, limit: int = 8) -> list[dict]:
    selector_map = learning.get(selector_type, {})
    stats_map = learning.get("selector_stats", {}).get(selector_type, {})
    rows = []
    for key in selector_map.keys():
        for selector in get_ranked_selector_candidates(learning, selector_type, key, limit=3):
            stat = _normalize_selector_stat(stats_map.get(key, {}).get(selector, {}), selector)
            rows.append(
                {
                    "key": key,
                    "selector": selector,
                    "score": round(float(stat.get("score", 0.0)), 2),
                    "successes": round(float(stat.get("successes", 0.0)), 2),
                    "failures": int(stat.get("failures", 0)),
                    "domains": stat.get("domains", [])[:4],
                }
            )
    rows.sort(key=lambda item: (-item["score"], -item["successes"], item["failures"], item["key"]))
    return rows[:limit]


def _top_failure_records(learning: dict, limit: int = 8) -> list[dict]:
    rows = []
    for selector_type, failure_map in (learning.get("failure_memory", {}) or {}).items():
        for key, bucket in failure_map.items():
            for item in bucket[:4]:
                rows.append(
                    {
                        "bucket": selector_type,
                        "key": key,
                        "selector": item.get("selector", ""),
                        "failures": int(item.get("failures", 0)),
                        "score": round(float(item.get("score", 0.0)), 2),
                    }
                )
    rows.sort(key=lambda item: (-item["score"], -item["failures"], item["key"]))
    return rows[:limit]


def _top_semantic_patterns(learning: dict, limit: int = 8) -> list[dict]:
    rows = []
    for key, pattern in (learning.get("semantic_patterns", {}) or {}).items():
        rows.append(
            {
                "key": key,
                "score": round(float(pattern.get("score", 0.0)), 2),
                "hits": int(pattern.get("hits", 0)),
                "successes": round(float(pattern.get("successes", 0.0)), 2),
                "failures": int(pattern.get("failures", 0)),
                "selectors": list(pattern.get("selectors", []))[:3],
            }
        )
    rows.sort(key=lambda item: (-item["score"], -item["hits"], item["key"]))
    return rows[:limit]


def _entry_learning_keys(details: dict) -> list[str]:
    keys = [
        _normalize_learning_key(details.get("field_key", "")),
        _normalize_learning_key(details.get("semantic_type", "")),
        _normalize_learning_key(details.get("semantic_label", "")),
        _normalize_learning_key(details.get("target", "")),
    ]
    return _dedupe_preserve_order([key for key in keys if key])


def _selector_signature(value: object) -> str:
    text = str(value or "").strip()
    if "|" in text:
        return text.split("|", 1)[1].strip()
    return text


def _selector_success_weight(status: str, has_selector: bool) -> float:
    if not has_selector:
        return 0.0
    if status == "passed":
        return 1.0
    if status == "checkpoint_required":
        return 0.45
    if status == "failed":
        return 0.35
    return 0.2


def _selector_score(stat: dict) -> float:
    freshness_bonus = 0.0 if _is_stale(stat.get("last_used_at", ""), 30) else 0.6
    domain_bonus = min(len(stat.get("domains", [])), 4) * 0.35
    scope_bonus = min(len(stat.get("scopes", [])), 3) * 0.15
    return round((float(stat.get("successes", 0.0)) * 2.6) - (int(stat.get("failures", 0)) * 1.15) + freshness_bonus + domain_bonus + scope_bonus, 2)


def _failure_score(entry: dict) -> float:
    freshness_bonus = 0.0 if _is_stale(entry.get("last_seen_at", ""), 30) else 0.4
    domain_bonus = min(len(entry.get("domains", [])), 4) * 0.2
    return round((int(entry.get("failures", 0)) * 1.1) + freshness_bonus + domain_bonus, 2)


def _pattern_score(pattern: dict) -> float:
    return round((float(pattern.get("successes", 0.0)) * 2.1) - (int(pattern.get("failures", 0)) * 0.6) + (int(pattern.get("hits", 0)) * 0.2), 2)


def _is_stale(value: str, stale_days: int, now: datetime | None = None) -> bool:
    if not value:
        return False
    now = now or datetime.now()
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return False
    return parsed < (now - timedelta(days=stale_days))


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _append_unique(bucket: list[str], value: str) -> None:
    text = str(value or "").strip()
    if text and text not in bucket:
        bucket.append(text)


def _dedupe_preserve_order(values: list[object]) -> list[str]:
    deduped = []
    seen = set()
    for value in values:
        text = str(value or "").strip()
        if text and text not in seen:
            deduped.append(text)
            seen.add(text)
    return deduped


def _dedupe_failure_entries(entries: list[dict]) -> list[dict]:
    deduped = []
    seen = set()
    for item in entries:
        selector = str(item.get("selector", "")).strip()
        if selector and selector not in seen:
            deduped.append(item)
            seen.add(selector)
    deduped.sort(key=lambda item: (-float(item.get("score", 0.0)), -int(item.get("failures", 0)), item.get("selector", "")))
    return deduped


def _merge_selector_stat(base: dict, override: dict, selector: str) -> dict:
    base_stat = _normalize_selector_stat(base, selector)
    override_stat = _normalize_selector_stat(override, selector)
    merged = {
        "selector": selector,
        "successes": max(float(base_stat.get("successes", 0.0)), float(override_stat.get("successes", 0.0))),
        "failures": max(int(base_stat.get("failures", 0)), int(override_stat.get("failures", 0))),
        "last_used_at": max(str(base_stat.get("last_used_at", "")), str(override_stat.get("last_used_at", ""))),
        "last_status": str(override_stat.get("last_status", "") or base_stat.get("last_status", "")),
        "domains": _dedupe_preserve_order(list(override_stat.get("domains", [])) + list(base_stat.get("domains", []))),
        "scopes": _dedupe_preserve_order(list(override_stat.get("scopes", [])) + list(base_stat.get("scopes", []))),
        "score": max(float(base_stat.get("score", 0.0)), float(override_stat.get("score", 0.0))),
    }
    merged["score"] = round(merged["score"] or _selector_score(merged), 2)
    return merged


def _merge_failure_buckets(base_bucket: list[dict], override_bucket: list[dict]) -> list[dict]:
    merged = {}
    for item in list(base_bucket or []) + list(override_bucket or []):
        entry = _normalize_failure_entry(item)
        selector = entry.get("selector", "")
        if not selector:
            continue
        if selector not in merged:
            merged[selector] = entry
            continue
        current = merged[selector]
        current["failures"] = max(int(current.get("failures", 0)), int(entry.get("failures", 0)))
        current["last_error"] = str(entry.get("last_error", "") or current.get("last_error", ""))
        current["last_seen_at"] = max(str(current.get("last_seen_at", "")), str(entry.get("last_seen_at", "")))
        current["domains"] = _dedupe_preserve_order(list(entry.get("domains", [])) + list(current.get("domains", [])))
        current["scopes"] = _dedupe_preserve_order(list(entry.get("scopes", [])) + list(current.get("scopes", [])))
        current["score"] = round(max(float(current.get("score", 0.0)), float(entry.get("score", 0.0))) or _failure_score(current), 2)
    return _dedupe_failure_entries(list(merged.values()))


def _merge_semantic_pattern(base_pattern: dict, override_pattern: dict) -> dict:
    base_norm = _normalize_semantic_patterns_map({"base": base_pattern}).get("base", {})
    override_norm = _normalize_semantic_patterns_map({"override": override_pattern}).get("override", {})
    merged = {
        "hits": max(int(base_norm.get("hits", 0)), int(override_norm.get("hits", 0))),
        "successes": max(float(base_norm.get("successes", 0.0)), float(override_norm.get("successes", 0.0))),
        "failures": max(int(base_norm.get("failures", 0)), int(override_norm.get("failures", 0))),
        "selectors": _dedupe_preserve_order(list(override_norm.get("selectors", [])) + list(base_norm.get("selectors", []))),
        "selector_stats": {},
        "top_failures": _merge_failure_buckets(base_norm.get("top_failures", []), override_norm.get("top_failures", [])),
        "scopes": _dedupe_preserve_order(list(override_norm.get("scopes", [])) + list(base_norm.get("scopes", []))),
        "domains": _dedupe_preserve_order(list(override_norm.get("domains", [])) + list(base_norm.get("domains", []))),
        "score": max(float(base_norm.get("score", 0.0)), float(override_norm.get("score", 0.0))),
        "updated_at": max(str(base_norm.get("updated_at", "")), str(override_norm.get("updated_at", ""))),
    }
    for selector in merged["selectors"]:
        merged["selector_stats"][selector] = _merge_selector_stat(
            base_norm.get("selector_stats", {}).get(selector, {}),
            override_norm.get("selector_stats", {}).get(selector, {}),
            selector,
        )
    merged["score"] = round(merged["score"] or _pattern_score(merged), 2)
    return merged


def _normalize_learning_key(value: object) -> str:
    text = "".join(ch.lower() if ch.isalnum() else "_" for ch in str(value or ""))
    return "_".join(part for part in text.split("_") if part)
