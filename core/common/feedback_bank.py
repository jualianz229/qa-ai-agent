import json
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

from core.common.artifacts import human_feedback_path


DEFAULT_FEEDBACK_BANK = {
    "summary": {
        "scope_accurate": 0,
        "scope_missed": 0,
        "case_relevant": 0,
        "case_irrelevant": 0,
        "selector_helpful": 0,
        "selector_misleading": 0,
    },
    "entries": [],
    "updated_at": "",
}


def merge_human_feedback(
    url: str,
    feedback_payload: dict,
    run_dir: str | Path | None = None,
    feedback_dir: str | Path = "site_profiles/feedback",
    cluster_keys: list[str] | None = None,
) -> dict:
    entry = _normalize_feedback_entry(url, feedback_payload)
    if not entry:
        raise ValueError("Invalid feedback payload.")

    paths = _feedback_candidate_paths(url, feedback_dir, cluster_keys or [])
    for path in paths:
        bank = _load_feedback_bank(path)
        _append_feedback_entry(bank, entry)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(bank, indent=2, ensure_ascii=False), encoding="utf-8")

    if run_dir:
        run_path = human_feedback_path(run_dir)
        run_bank = _load_feedback_bank(run_path)
        _append_feedback_entry(run_bank, entry)
        run_path.write_text(json.dumps(run_bank, indent=2, ensure_ascii=False), encoding="utf-8")

    return {
        "entry": entry,
        "paths": [str(path) for path in paths],
        "run_path": str(human_feedback_path(run_dir)) if run_dir else "",
    }


def load_feedback_snapshot(
    url: str = "",
    feedback_dir: str | Path = "site_profiles/feedback",
    cluster_keys: list[str] | None = None,
) -> dict:
    paths = _feedback_candidate_paths(url, feedback_dir, cluster_keys or [])
    aggregated = deepcopy(DEFAULT_FEEDBACK_BANK)
    loaded_from = []
    for path in paths:
        if path.exists():
            loaded_from.append(str(path))
            bank = _load_feedback_bank(path)
            aggregated = _merge_feedback_banks(aggregated, bank)
    aggregated["loaded_from"] = loaded_from
    return aggregated


def load_run_feedback(run_dir: str | Path) -> dict:
    return _load_feedback_bank(human_feedback_path(run_dir, create=False))


def _feedback_candidate_paths(url: str, feedback_dir: str | Path, cluster_keys: list[str]) -> list[Path]:
    base = Path(feedback_dir)
    host = (urlparse(url).netloc or "").replace("www.", "").lower()
    paths = [base / "_global.json"]
    if host:
        paths.append(base / f"{host}.json")
    for cluster_key in cluster_keys:
        normalized = _normalize_cluster_key(cluster_key)
        if normalized:
            paths.append(base / "clusters" / f"{normalized}.json")
    return paths


def _normalize_feedback_entry(url: str, payload: dict) -> dict:
    feedback_type = str(payload.get("feedback_type", "")).strip().lower()
    verdict = str(payload.get("verdict", "")).strip().lower()
    if feedback_type not in {"scope_accuracy", "case_relevance", "selector_quality"}:
        return {}
    if verdict not in {
        "accurate",
        "missed",
        "relevant",
        "irrelevant",
        "helpful",
        "misleading",
    }:
        return {}
    return {
        "feedback_type": feedback_type,
        "verdict": verdict,
        "case_id": str(payload.get("case_id", "")).strip(),
        "selector": str(payload.get("selector", "")).strip(),
        "semantic_key": str(payload.get("semantic_key", "")).strip(),
        "page_type": str(payload.get("page_type", "")).strip(),
        "run_name": str(payload.get("run_name", "")).strip(),
        "note": str(payload.get("note", "")).strip()[:240],
        "url": url,
        "host": (urlparse(url).netloc or "").replace("www.", "").lower(),
        "created_at": datetime.now().isoformat(timespec="seconds"),
    }


def _load_feedback_bank(path: Path) -> dict:
    bank = deepcopy(DEFAULT_FEEDBACK_BANK)
    if path.exists():
        bank = _merge_feedback_banks(bank, json.loads(path.read_text(encoding="utf-8")))
    return bank


def _merge_feedback_banks(base: dict, override: dict) -> dict:
    merged = deepcopy(DEFAULT_FEEDBACK_BANK)
    for key, value in base.get("summary", {}).items():
        merged["summary"][key] = int(value or 0)
    for key, value in override.get("summary", {}).items():
        merged["summary"][key] = merged["summary"].get(key, 0) + int(value or 0)
    merged["entries"] = _dedupe_entries(list(base.get("entries", [])) + list(override.get("entries", [])))[:40]
    merged["updated_at"] = max(str(base.get("updated_at", "")), str(override.get("updated_at", "")))
    return merged


def _append_feedback_entry(bank: dict, entry: dict) -> None:
    verdict_key = {
        "accurate": "scope_accurate",
        "missed": "scope_missed",
        "relevant": "case_relevant",
        "irrelevant": "case_irrelevant",
        "helpful": "selector_helpful",
        "misleading": "selector_misleading",
    }.get(entry.get("verdict", ""), "")
    if verdict_key:
        bank.setdefault("summary", {}).setdefault(verdict_key, 0)
        bank["summary"][verdict_key] = int(bank["summary"].get(verdict_key, 0)) + 1
    bank.setdefault("entries", [])
    bank["entries"] = _dedupe_entries([entry] + list(bank["entries"]))[:40]
    bank["updated_at"] = entry.get("created_at", "")


def _dedupe_entries(entries: list[dict]) -> list[dict]:
    deduped = []
    seen = set()
    for entry in entries:
        key = (
            str(entry.get("feedback_type", "")),
            str(entry.get("verdict", "")),
            str(entry.get("case_id", "")),
            str(entry.get("selector", "")),
            str(entry.get("created_at", "")),
        )
        if key in seen:
            continue
        deduped.append(entry)
        seen.add(key)
    return deduped


def _normalize_cluster_key(value: object) -> str:
    text = "".join(ch.lower() if ch.isalnum() else "_" for ch in str(value or ""))
    return "_".join(part for part in text.split("_") if part)
