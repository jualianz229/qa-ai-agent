import json
import re
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

from core.site_profiles import derive_cluster_keys


DEFAULT_CASE_MEMORY = {
    "patterns": {},
    "updated_at": "",
}


def merge_case_memory(
    url: str,
    cases: list[dict],
    page_scope: dict | None,
    page_model: dict | None,
    memory_dir: str | Path = "site_profiles/case_memory",
) -> dict | None:
    entries = _build_case_entries(cases, page_scope or {}, page_model or {})
    if not entries:
        return None

    cluster_keys = derive_cluster_keys(page_model or {}, page_scope or {})
    paths = _candidate_paths(url, memory_dir, cluster_keys)
    updated_paths = []
    for path in paths:
        bank = _load_case_memory(path)
        _apply_entries(bank, entries)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(bank, indent=2, ensure_ascii=False), encoding="utf-8")
        updated_paths.append(str(path))

    snapshot = load_case_memory_snapshot(url, page_model=page_model, page_scope=page_scope, memory_dir=memory_dir)
    return {
        "updated_entries": len(entries),
        "paths": updated_paths,
        "summary": snapshot.get("summary", {}),
    }


def load_case_memory_snapshot(
    url: str = "",
    page_model: dict | None = None,
    page_scope: dict | None = None,
    memory_dir: str | Path = "site_profiles/case_memory",
) -> dict:
    page_model = page_model or {}
    page_scope = page_scope or {}
    cluster_keys = derive_cluster_keys(page_model, page_scope)
    aggregated = deepcopy(DEFAULT_CASE_MEMORY)
    loaded_from = []
    for path in _candidate_paths(url, memory_dir, cluster_keys):
        if path.exists():
            loaded_from.append(str(path))
            aggregated = _merge_case_banks(aggregated, json.loads(path.read_text(encoding="utf-8")))

    ranked = _rank_patterns(
        list(aggregated.get("patterns", {}).values()),
        page_model=page_model,
        page_scope=page_scope,
    )
    return {
        "patterns": ranked[:8],
        "summary": {
            "pattern_count": len(aggregated.get("patterns", {})),
            "loaded_from": loaded_from,
            "top_modules": _top_modules(aggregated.get("patterns", {})),
            "updated_at": aggregated.get("updated_at", ""),
        },
    }


def _candidate_paths(url: str, memory_dir: str | Path, cluster_keys: list[str]) -> list[Path]:
    base = Path(memory_dir)
    host = (urlparse(url).netloc or "").replace("www.", "").lower()
    paths = [base / "_global.json"]
    if host:
        paths.append(base / f"{host}.json")
    for cluster_key in cluster_keys:
        normalized = _normalize_key(cluster_key)
        if normalized:
            paths.append(base / "clusters" / f"{normalized}.json")
    return paths


def _build_case_entries(cases: list[dict], page_scope: dict, page_model: dict) -> list[dict]:
    entries = []
    page_type = _normalize_key(page_scope.get("page_type", "") or page_model.get("heuristic_scope", {}).get("likely_page_type", ""))
    for case in cases:
        title = str(case.get("Title", "")).strip()
        module = str(case.get("Module", "")).strip() or "general"
        test_type = str(case.get("Test Type", "")).strip() or "positive"
        category = str(case.get("Category", "")).strip() or "general"
        automation = str(case.get("Automation", "auto")).strip().lower() or "auto"
        step_profile = _step_profile(case.get("Steps to Reproduce", ""))
        expected_profile = _expected_profile(case.get("Expected Result", ""))
        if not title and not step_profile:
            continue
        module_key = _normalize_key(module) or "general"
        pattern_key = "::".join(part for part in [page_type or "generic_page", module_key, _normalize_key(test_type) or "positive"] if part)
        entries.append(
            {
                "pattern_key": pattern_key,
                "page_type": page_type or "generic_page",
                "module": module,
                "category": category,
                "test_type": test_type,
                "automation": automation,
                "example_title": title,
                "step_profile": step_profile,
                "expected_profile": expected_profile,
                "signature": _case_signature(case),
                "created_at": _now_iso(),
            }
        )
    return entries


def _apply_entries(bank: dict, entries: list[dict]) -> None:
    bank.setdefault("patterns", {})
    for entry in entries:
        current = bank["patterns"].get(entry["pattern_key"], {})
        merged = {
            "pattern_key": entry["pattern_key"],
            "page_type": entry.get("page_type", ""),
            "module": entry.get("module", ""),
            "category": entry.get("category", ""),
            "test_type": entry.get("test_type", ""),
            "automation_counts": dict(current.get("automation_counts", {})),
            "step_profiles": dict(current.get("step_profiles", {})),
            "expected_profiles": dict(current.get("expected_profiles", {})),
            "example_titles": _dedupe_preserve([entry.get("example_title", "")] + list(current.get("example_titles", [])))[:6],
            "signatures": _dedupe_preserve([entry.get("signature", "")] + list(current.get("signatures", [])))[:12],
            "hits": int(current.get("hits", 0) or 0) + 1,
            "last_used_at": entry.get("created_at", ""),
        }
        automation = entry.get("automation", "auto")
        merged["automation_counts"][automation] = int(merged["automation_counts"].get(automation, 0)) + 1
        step_key = " > ".join(entry.get("step_profile", [])) or "inspect"
        expected_key = " | ".join(entry.get("expected_profile", [])) or "generic"
        merged["step_profiles"][step_key] = int(merged["step_profiles"].get(step_key, 0)) + 1
        merged["expected_profiles"][expected_key] = int(merged["expected_profiles"].get(expected_key, 0)) + 1
        bank["patterns"][entry["pattern_key"]] = merged
    bank["updated_at"] = _now_iso()


def _merge_case_banks(base: dict, override: dict) -> dict:
    merged = deepcopy(DEFAULT_CASE_MEMORY)
    patterns = {}
    for bank in (base, override):
        for key, pattern in (bank.get("patterns", {}) or {}).items():
            current = patterns.get(key, {})
            next_pattern = deepcopy(current)
            next_pattern["pattern_key"] = key
            next_pattern["page_type"] = pattern.get("page_type", current.get("page_type", ""))
            next_pattern["module"] = pattern.get("module", current.get("module", ""))
            next_pattern["category"] = pattern.get("category", current.get("category", ""))
            next_pattern["test_type"] = pattern.get("test_type", current.get("test_type", ""))
            next_pattern["hits"] = int(current.get("hits", 0) or 0) + int(pattern.get("hits", 0) or 0)
            next_pattern["example_titles"] = _dedupe_preserve(list(pattern.get("example_titles", [])) + list(current.get("example_titles", [])))[:6]
            next_pattern["signatures"] = _dedupe_preserve(list(pattern.get("signatures", [])) + list(current.get("signatures", [])))[:12]
            next_pattern["automation_counts"] = _merge_counter_maps(current.get("automation_counts", {}), pattern.get("automation_counts", {}))
            next_pattern["step_profiles"] = _merge_counter_maps(current.get("step_profiles", {}), pattern.get("step_profiles", {}))
            next_pattern["expected_profiles"] = _merge_counter_maps(current.get("expected_profiles", {}), pattern.get("expected_profiles", {}))
            next_pattern["last_used_at"] = max(str(current.get("last_used_at", "")), str(pattern.get("last_used_at", "")))
            patterns[key] = next_pattern
    merged["patterns"] = patterns
    merged["updated_at"] = max(str(base.get("updated_at", "")), str(override.get("updated_at", "")))
    return merged


def _rank_patterns(patterns: list[dict], page_model: dict, page_scope: dict) -> list[dict]:
    page_type = _normalize_key(page_scope.get("page_type", "") or page_model.get("heuristic_scope", {}).get("likely_page_type", ""))
    priority_modules = {_normalize_key(item) for item in page_model.get("heuristic_scope", {}).get("priority_modules", [])[:8]}
    priority_modules.update(_normalize_key(item) for item in page_scope.get("key_modules", [])[:8])
    ranked = []
    for index, pattern in enumerate(patterns):
        score = float(pattern.get("hits", 0) or 0)
        if page_type and _normalize_key(pattern.get("page_type", "")) == page_type:
            score += 3.0
        if _normalize_key(pattern.get("module", "")) in priority_modules:
            score += 2.0
        score += 0.5 if pattern.get("automation_counts", {}).get("auto", 0) else 0.0
        ranked.append((score, index, pattern))
    ranked.sort(key=lambda item: (-item[0], item[1]))
    rows = []
    for score, _, pattern in ranked:
        rows.append(
            {
                "pattern_key": pattern.get("pattern_key", ""),
                "page_type": pattern.get("page_type", ""),
                "module": pattern.get("module", ""),
                "test_type": pattern.get("test_type", ""),
                "hits": int(pattern.get("hits", 0) or 0),
                "score": round(score, 2),
                "example_title": (pattern.get("example_titles", [""]) or [""])[0],
                "common_step_profile": _top_counter_key(pattern.get("step_profiles", {})),
                "common_expected_profile": _top_counter_key(pattern.get("expected_profiles", {})),
                "dominant_automation": _top_counter_key(pattern.get("automation_counts", {})),
            }
        )
    return rows


def _load_case_memory(path: Path) -> dict:
    bank = deepcopy(DEFAULT_CASE_MEMORY)
    if path.exists():
        bank = _merge_case_banks(bank, json.loads(path.read_text(encoding="utf-8")))
    return bank


def _step_profile(steps: object) -> list[str]:
    verbs = []
    text = str(steps or "")
    for raw_line in text.splitlines():
        line = re.sub(r"^\s*\d+\.\s*", "", raw_line).strip().lower()
        if not line:
            continue
        if line.startswith("open"):
            verbs.append("open_url")
        elif line.startswith("input"):
            verbs.append("fill")
        elif line.startswith("click"):
            verbs.append("click")
        elif line.startswith("select") or line.startswith("choose"):
            verbs.append("select")
        elif line.startswith("upload"):
            verbs.append("upload")
        elif line.startswith("hover"):
            verbs.append("hover")
        elif line.startswith("scroll"):
            verbs.append("scroll")
        elif line.startswith("wait"):
            verbs.append("wait")
        else:
            verbs.append("inspect")
    return verbs[:10]


def _expected_profile(expected: object) -> list[str]:
    text = str(expected or "").lower()
    profile = []
    for token, label in [
        ("redirect", "url_change"),
        ("display", "text_visible"),
        ("visible", "text_visible"),
        ("message", "text_visible"),
        ("200", "network_ok"),
        ("api", "network_seen"),
        ("request", "network_seen"),
        ("graphql", "graphql_ok"),
        ("allowlist", "allowlist"),
        ("same-origin", "same_origin"),
    ]:
        if token in text and label not in profile:
            profile.append(label)
    return profile[:6]


def _case_signature(case: dict) -> str:
    return "::".join(
        [
            _normalize_key(case.get("ID", "")),
            _normalize_key(case.get("Module", "")),
            _normalize_key(case.get("Title", "")),
        ]
    ).strip(":")


def _top_modules(patterns: dict) -> list[dict]:
    counts = {}
    for pattern in patterns.values():
        module = str(pattern.get("module", "")).strip()
        if not module:
            continue
        counts[module] = counts.get(module, 0) + int(pattern.get("hits", 0) or 0)
    return [
        {"module": module, "hits": hits}
        for module, hits in sorted(counts.items(), key=lambda item: (-item[1], item[0]))[:8]
    ]


def _merge_counter_maps(left: dict, right: dict) -> dict:
    merged = {str(key): int(value or 0) for key, value in (left or {}).items()}
    for key, value in (right or {}).items():
        merged[str(key)] = int(merged.get(str(key), 0)) + int(value or 0)
    return merged


def _top_counter_key(payload: dict) -> str:
    if not payload:
        return ""
    return sorted(payload.items(), key=lambda item: (-int(item[1] or 0), item[0]))[0][0]


def _normalize_key(value: object) -> str:
    text = "".join(ch.lower() if ch.isalnum() else "_" for ch in str(value or ""))
    return "_".join(part for part in text.split("_") if part)


def _dedupe_preserve(values: list[object]) -> list[str]:
    rows = []
    seen = set()
    for value in values:
        text = str(value or "").strip()
        if not text or text in seen:
            continue
        rows.append(text)
        seen.add(text)
    return rows


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")
