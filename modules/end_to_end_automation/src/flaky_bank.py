import json
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

from core.site_profiles import derive_cluster_keys


DEFAULT_FLAKY_BANK = {
    "cases": {},
    "updated_at": "",
}


def merge_flaky_history(
    url: str,
    results_payload: dict | list[dict],
    page_model: dict | None = None,
    page_scope: dict | None = None,
    flaky_dir: str | Path = "site_profiles/flaky",
) -> dict | None:
    results = results_payload.get("results", []) if isinstance(results_payload, dict) else list(results_payload or [])
    if not results:
        return None

    cluster_keys = derive_cluster_keys(page_model or {}, page_scope or {})
    paths = _candidate_paths(url, flaky_dir, cluster_keys)
    updated_paths = []
    for path in paths:
        bank = _load_flaky_bank(path)
        _apply_results(bank, results)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(bank, indent=2, ensure_ascii=False), encoding="utf-8")
        updated_paths.append(str(path))

    snapshot = load_flaky_snapshot(url, page_model=page_model, page_scope=page_scope, flaky_dir=flaky_dir)
    return {
        "paths": updated_paths,
        "summary": snapshot.get("summary", {}),
        "flaky_cases": snapshot.get("flaky_cases", []),
    }


def load_flaky_snapshot(
    url: str = "",
    page_model: dict | None = None,
    page_scope: dict | None = None,
    flaky_dir: str | Path = "site_profiles/flaky",
) -> dict:
    aggregated = deepcopy(DEFAULT_FLAKY_BANK)
    loaded_from = []
    for path in _candidate_paths(url, flaky_dir, derive_cluster_keys(page_model or {}, page_scope or {})):
        if path.exists():
            loaded_from.append(str(path))
            aggregated = _merge_flaky_banks(aggregated, json.loads(path.read_text(encoding="utf-8")))

    flaky_cases = []
    stable_cases = []
    for case in aggregated.get("cases", {}).values():
        row = {
            "signature": case.get("signature", ""),
            "id": case.get("id", ""),
            "title": case.get("title", ""),
            "pass_count": int(case.get("pass_count", 0) or 0),
            "fail_count": int(case.get("fail_count", 0) or 0),
            "transitions": int(case.get("transitions", 0) or 0),
            "recent_statuses": [item.get("status", "") for item in case.get("history", [])[:6]],
            "flaky": bool(case.get("flaky", False)),
        }
        (flaky_cases if row["flaky"] else stable_cases).append(row)

    flaky_cases.sort(key=lambda item: (-item["transitions"], -item["fail_count"], item["signature"]))
    stable_cases.sort(key=lambda item: (-item["fail_count"], item["signature"]))
    return {
        "summary": {
            "case_count": len(aggregated.get("cases", {})),
            "flaky_count": len(flaky_cases),
            "loaded_from": loaded_from,
            "updated_at": aggregated.get("updated_at", ""),
        },
        "flaky_cases": flaky_cases[:12],
        "stable_cases": stable_cases[:12],
    }


def _candidate_paths(url: str, flaky_dir: str | Path, cluster_keys: list[str]) -> list[Path]:
    base = Path(flaky_dir)
    host = (urlparse(url).netloc or "").replace("www.", "").lower()
    paths = [base / "_global.json"]
    if host:
        paths.append(base / f"{host}.json")
    for cluster_key in cluster_keys:
        normalized = _normalize_key(cluster_key)
        if normalized:
            paths.append(base / "clusters" / f"{normalized}.json")
    return paths


def _apply_results(bank: dict, results: list[dict]) -> None:
    bank.setdefault("cases", {})
    recorded_at = _now_iso()
    for result in results:
        signature = _result_signature(result)
        if not signature:
            continue
        current = deepcopy(bank["cases"].get(signature, {}))
        history = list(current.get("history", []))
        history.insert(
            0,
            {
                "status": str(result.get("status", "")).strip().lower(),
                "recorded_at": recorded_at,
                "error": str(result.get("error", "")).strip()[:160],
            },
        )
        history = history[:12]
        statuses = [item.get("status", "") for item in history if item.get("status")]
        pass_count = sum(1 for item in statuses if item == "passed")
        fail_count = sum(1 for item in statuses if item == "failed")
        transitions = sum(1 for index in range(1, len(statuses)) if statuses[index] != statuses[index - 1])
        bank["cases"][signature] = {
            "signature": signature,
            "id": str(result.get("id", "")).strip(),
            "title": str(result.get("title", "")).strip(),
            "history": history,
            "pass_count": pass_count,
            "fail_count": fail_count,
            "transitions": transitions,
            "flaky": pass_count > 0 and fail_count > 0 and transitions >= 1,
            "last_seen_at": recorded_at,
        }
    bank["updated_at"] = recorded_at


def _merge_flaky_banks(base: dict, override: dict) -> dict:
    merged = deepcopy(DEFAULT_FLAKY_BANK)
    cases = {}
    for payload in (base, override):
        for signature, case in (payload.get("cases", {}) or {}).items():
            current = deepcopy(cases.get(signature, {}))
            history = list(case.get("history", [])) + list(current.get("history", []))
            history.sort(key=lambda item: str(item.get("recorded_at", "")), reverse=True)
            history = _dedupe_history(history)[:12]
            statuses = [item.get("status", "") for item in history if item.get("status")]
            pass_count = sum(1 for item in statuses if item == "passed")
            fail_count = sum(1 for item in statuses if item == "failed")
            transitions = sum(1 for index in range(1, len(statuses)) if statuses[index] != statuses[index - 1])
            cases[signature] = {
                "signature": signature,
                "id": case.get("id", current.get("id", "")),
                "title": case.get("title", current.get("title", "")),
                "history": history,
                "pass_count": pass_count,
                "fail_count": fail_count,
                "transitions": transitions,
                "flaky": pass_count > 0 and fail_count > 0 and transitions >= 1,
                "last_seen_at": max(str(case.get("last_seen_at", "")), str(current.get("last_seen_at", ""))),
            }
    merged["cases"] = cases
    merged["updated_at"] = max(str(base.get("updated_at", "")), str(override.get("updated_at", "")))
    return merged


def _load_flaky_bank(path: Path) -> dict:
    bank = deepcopy(DEFAULT_FLAKY_BANK)
    if path.exists():
        bank = _merge_flaky_banks(bank, json.loads(path.read_text(encoding="utf-8")))
    return bank


def _dedupe_history(history: list[dict]) -> list[dict]:
    rows = []
    seen = set()
    for item in history:
        key = (
            str(item.get("status", "")),
            str(item.get("recorded_at", "")),
            str(item.get("error", "")),
        )
        if key in seen:
            continue
        rows.append(item)
        seen.add(key)
    return rows


def _result_signature(result: dict) -> str:
    return "::".join(
        part
        for part in [
            _normalize_key(result.get("id", "")),
            _normalize_key(result.get("title", "")),
        ]
        if part
    )


def _normalize_key(value: object) -> str:
    text = "".join(ch.lower() if ch.isalnum() else "_" for ch in str(value or ""))
    return "_".join(part for part in text.split("_") if part)


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")
