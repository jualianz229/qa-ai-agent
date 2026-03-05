from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import urlparse

from core.common.artifacts import execution_network_path, visual_signature_path


def detect_run_drift(
    run_dir: str | Path,
    url: str,
    visual_signature: dict | None = None,
    network_summary: dict | None = None,
    limit: int = 8,
) -> dict:
    current_dir = Path(run_dir)
    results_dir = current_dir.parent
    visual_signature = visual_signature or {}
    network_summary = network_summary or {}

    current_host = _host(url)
    historical = []
    if results_dir.exists():
        for item in results_dir.iterdir():
            if not item.is_dir() or item == current_dir:
                continue
            baseline = _load_baseline(item)
            if not baseline:
                continue
            if current_host and baseline.get("host") and baseline.get("host") != current_host:
                continue
            historical.append(baseline)
    historical.sort(key=lambda x: x.get("modified_ts", 0), reverse=True)
    historical = historical[:limit]
    if not historical:
        return {
            "summary": {
                "baseline_count": 0,
                "visual_drift_score": 0.0,
                "api_drift_score": 0.0,
                "blocking": False,
            },
            "issues": [],
            "baseline_run": "",
        }

    baseline = historical[0]
    visual_drift = _visual_drift_score(visual_signature, baseline.get("visual_signature", {}))
    api_drift = _api_drift_score(network_summary, baseline.get("network_summary", {}))
    issues = []
    if visual_drift >= 0.48:
        issues.append({"severity": "medium", "message": f"Visual drift cukup tinggi ({visual_drift:.2f}) vs run baseline {baseline.get('run_name', '')}."})
    if api_drift >= 0.45:
        issues.append({"severity": "high", "message": f"API contract drift tinggi ({api_drift:.2f}) vs run baseline {baseline.get('run_name', '')}."})
    return {
        "summary": {
            "baseline_count": len(historical),
            "visual_drift_score": round(visual_drift, 2),
            "api_drift_score": round(api_drift, 2),
            "blocking": any(item.get("severity") in {"high", "critical"} for item in issues),
        },
        "issues": issues,
        "baseline_run": baseline.get("run_name", ""),
    }


def _load_baseline(run_dir: Path) -> dict:
    json_dir = run_dir / "JSON"
    raw_scan = _load_first(json_dir, "raw_scan_*.json")
    scope = _load_first(json_dir, "Page_Scope_*.json")
    signature_path = visual_signature_path(run_dir, create=False)
    if signature_path.exists():
        visual = _load_json(signature_path)
    else:
        visual = _build_visual_signature(raw_scan, scope)
    network = _summarize_network_entries(_load_json(execution_network_path(run_dir, create=False)).get("network_entries", []))
    url = str(raw_scan.get("url", ""))
    return {
        "run_name": run_dir.name,
        "host": _host(url),
        "visual_signature": visual,
        "network_summary": network,
        "modified_ts": run_dir.stat().st_mtime,
    }


def _load_first(json_dir: Path, pattern: str) -> dict:
    if not json_dir.exists():
        return {}
    file_path = next(json_dir.glob(pattern), None)
    if not file_path:
        return {}
    return _load_json(file_path)


def _load_json(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _build_visual_signature(raw_scan: dict, page_scope: dict) -> dict:
    headings = [item.get("text", "") for item in raw_scan.get("headings", []) if isinstance(item, dict)]
    return {
        "page_type": page_scope.get("page_type", ""),
        "heading_count": len(headings),
        "button_count": len(raw_scan.get("buttons", [])),
        "link_count": len(raw_scan.get("links", [])),
        "section_count": len(raw_scan.get("sections", [])),
    }


def _summarize_network_entries(entries: list[dict]) -> dict:
    requests = 0
    failing = 0
    endpoints: set[str] = set()
    for item in entries:
        summary = item.get("summary", {}) if isinstance(item.get("summary", {}), dict) else {}
        requests += int(summary.get("request_count", 0) or 0)
        failing += int(summary.get("failing_response_count", 0) or 0)
        for endpoint in summary.get("top_endpoints", [])[:10]:
            path = str(endpoint.get("path", "")).strip()
            if path:
                endpoints.add(path)
    return {
        "request_count": requests,
        "failing_response_count": failing,
        "top_endpoints": sorted(endpoints)[:10],
    }


def _visual_drift_score(current: dict, baseline: dict) -> float:
    keys = ("heading_count", "button_count", "link_count", "section_count")
    deltas = []
    for key in keys:
        left = float(current.get(key, 0) or 0)
        right = float(baseline.get(key, 0) or 0)
        deltas.append(abs(left - right) / max(right, 1.0))
    type_penalty = 0.0
    if str(current.get("page_type", "")).strip() and str(baseline.get("page_type", "")).strip():
        if str(current.get("page_type", "")).strip().lower() != str(baseline.get("page_type", "")).strip().lower():
            type_penalty = 0.25
    return min(1.0, (sum(deltas) / max(len(deltas), 1)) + type_penalty)


def _api_drift_score(current: dict, baseline: dict) -> float:
    current_set = {str(item).strip() for item in current.get("top_endpoints", []) if str(item).strip()}
    baseline_set = {str(item).strip() for item in baseline.get("top_endpoints", []) if str(item).strip()}
    overlap = len(current_set & baseline_set)
    union = len(current_set | baseline_set)
    endpoint_delta = 1.0 - (overlap / max(union, 1))
    current_fail = float(current.get("failing_response_count", 0) or 0)
    baseline_fail = float(baseline.get("failing_response_count", 0) or 0)
    fail_delta = abs(current_fail - baseline_fail) / max(baseline_fail + 1.0, 1.0)
    return min(1.0, (endpoint_delta * 0.75) + (min(1.0, fail_delta) * 0.25))


def _host(url: str) -> str:
    return (urlparse(str(url or "")).netloc or "").replace("www.", "").lower()
