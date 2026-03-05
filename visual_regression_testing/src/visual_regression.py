import json
from datetime import datetime
from pathlib import Path

from core.common.artifacts import visual_regression_approval_path
from core.common.config import RESULT_DIR


def _resolve_run_dir(run_name: str) -> Path:
    candidate = (RESULT_DIR / run_name).resolve()
    result_root = RESULT_DIR.resolve()
    if not candidate.is_relative_to(result_root):
        raise ValueError("Invalid run path.")
    return candidate


def _load_json_file(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def load_visual_approval(run_name: str) -> dict:
    run_dir = _resolve_run_dir(run_name)
    if not run_dir.exists():
        raise FileNotFoundError(run_name)
    return _load_json_file(visual_regression_approval_path(run_dir, create=False))


def save_visual_approval(run_name: str, status: str, note: str = "", compare_run: str = "", actor: str = "manual") -> dict:
    run_dir = _resolve_run_dir(run_name)
    if not run_dir.exists():
        raise FileNotFoundError(run_name)
    status = str(status or "").strip().lower()
    if status not in {"approved", "rejected", "pending"}:
        raise ValueError("Invalid approval status.")
    payload = {
        "run_name": run_name,
        "status": status,
        "note": str(note or "").strip(),
        "compare_run": str(compare_run or "").strip(),
        "updated_by": str(actor or "manual").strip(),
        "updated_at": datetime.now().isoformat(timespec="seconds"),
    }
    visual_regression_approval_path(run_dir).write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return payload
