from datetime import datetime
from urllib.parse import urlparse
import json
import os
import functools
from pathlib import Path
from filelock import FileLock
from core.config import RESULT_DIR


def form_bool(value: str | None, default: bool = False) -> bool:
    normalized = str(value or "").strip().lower()
    if not normalized:
        return default
    return normalized in {"1", "true", "yes", "y", "on", "iya"}


def normalize_input_url(value: str) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    parsed = urlparse(raw)
    if not parsed.scheme:
        raw = f"https://{raw}"
        parsed = urlparse(raw)
    if parsed.scheme not in {"http", "https"}:
        raise ValueError("URL must use http:// or https://")
    if not parsed.netloc:
        raise ValueError("URL is invalid.")
    normalized = parsed._replace(fragment="").geturl().strip()
    return normalized


def parse_iso_datetime(value: object) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text)
    except Exception:
        return None


def is_automation_or_recovery_run(run_name: str) -> bool:
    name = str(run_name or "").strip().lower()
    return any(token in name for token in ("_auto_", "_retry_", "_safe_"))


def is_automation_run(run_name: str) -> bool:
    return "_auto_" in str(run_name or "").strip().lower()


@functools.lru_cache(maxsize=100)
def _cached_read_json(path_str: str, modified_time: float) -> dict:
    try:
        return json.loads(Path(path_str).read_text(encoding="utf-8"))
    except Exception:
        return {}

def load_json_file(path: Path) -> dict:
    if not path.exists():
        return {}
    lock_path = str(path) + ".lock"
    with FileLock(lock_path, timeout=5):
        mtime = path.stat().st_mtime
        return _cached_read_json(str(path), mtime)


def atomic_write_json(filepath: Path | str, data: dict | list) -> None:
    path = Path(filepath)
    lock_path = str(path) + ".lock"
    tmp_path = path.with_suffix('.tmp')
    
    with FileLock(lock_path, timeout=5):
        with open(tmp_path, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        os.replace(tmp_path, path)


def resolve_run_dir(run_name: str) -> Path:
    candidate = (RESULT_DIR / run_name).resolve()
    result_root = RESULT_DIR.resolve()
    if not candidate.is_relative_to(result_root):
        raise ValueError("Invalid run path.")
    return candidate
