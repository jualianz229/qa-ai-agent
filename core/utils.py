from datetime import datetime
from urllib.parse import urlparse
import sys
import json
import os
import time
import functools
import logging
from pathlib import Path
from filelock import FileLock
from core.config import RESULT_DIR


def form_bool(value: str | None, default: bool = False) -> bool:
    normalized = str(value or "").strip().lower()
    if not normalized:
        return default
    return normalized in {"1", "true", "yes", "y", "on", "iya"}


def setup_logging(log_file: str | Path = "Result/app.log") -> None:
    path = Path(log_file)
    path.parent.mkdir(parents=True, exist_ok=True)
    
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
        handlers=[
            logging.FileHandler(path, encoding='utf-8'),
            logging.StreamHandler(sys.stdout)
        ]
    )

def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)


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


import re

def repair_json(raw: str) -> str:
    """Try to fix common JSON formatting issues from AI output."""
    if not raw:
        return ""
    
    # Try to extract JSON content between first and last brackets/braces
    start_bracket = raw.find('[')
    start_brace = raw.find('{')
    
    if start_bracket == -1 and start_brace == -1:
        return raw

    if start_bracket != -1 and (start_brace == -1 or start_bracket < start_brace):
        start_idx = start_bracket
        end_char = ']'
    else:
        start_idx = start_brace
        end_char = '}'
        
    end_idx = raw.rfind(end_char)
    if end_idx != -1:
        raw = raw[start_idx:end_idx+1]
        
    # Remove trailing commas before closing brackets
    raw = re.sub(r',\s*([\]}])', r'\1', raw)
    
    # Simple fix for unclosed strings at the very end (common if truncated)
    if raw.count('"') % 2 != 0 and raw.endswith(end_char):
        # This is a bit risky but can help if it ends like "key": "value... } or ]
        # We find the last open quote and try to close it before the end_char
        last_quote = raw.rfind('"')
        if last_quote != -1:
            raw = raw[:last_quote] + '"' + raw[last_quote:]
            
    # Fix for missing closing brackets if truncated
    if end_char == ']' and raw.count('[') > raw.count(']'):
        raw += ']' * (raw.count('[') - raw.count(']'))
    elif end_char == '}' and raw.count('{') > raw.count('}'):
        raw += '}' * (raw.count('{') - raw.count('}'))
        
    return raw


@functools.lru_cache(maxsize=100)
def _cached_read_json(path_str: str, modified_time: float) -> dict:
    try:
        return json.loads(Path(path_str).read_text(encoding="utf-8"))
    except Exception as exc:
        get_logger(__name__).debug("Read JSON failed for %s: %s", path_str, exc)
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
        # On Windows, os.replace can raise PermissionError if target is open elsewhere.
        for attempt in range(4):
            try:
                os.replace(tmp_path, path)
                return
            except PermissionError:
                if attempt < 3:
                    time.sleep(0.05 * (attempt + 1))
                else:
                    # Fallback: write directly to target (overwrite in place).
                    try:
                        with open(path, 'w', encoding='utf-8') as f:
                            json.dump(data, f, indent=2, ensure_ascii=False)
                    except Exception:
                        if tmp_path.exists():
                            try:
                                tmp_path.unlink()
                            except OSError:
                                pass
                        raise
                    if tmp_path.exists():
                        try:
                            tmp_path.unlink()
                        except OSError:
                            pass
                    return


def resolve_run_dir(run_name: str) -> Path:
    candidate = (RESULT_DIR / run_name).resolve()
    result_root = RESULT_DIR.resolve()
    if not candidate.is_relative_to(result_root):
        raise ValueError("Invalid run path.")
    return candidate
