import json
from copy import deepcopy
from pathlib import Path
from urllib.parse import urlparse


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
        base / f"{safe_host}.json",
    ]


def load_site_profile(url: str, profiles_dir: str | Path = "site_profiles") -> dict:
    profile = deepcopy(DEFAULT_SITE_PROFILE)
    loaded_from = []
    for path in _profile_candidate_paths(url, profiles_dir):
        if path.exists():
            loaded_from.append(str(path))
            profile = deep_merge(profile, json.loads(path.read_text(encoding="utf-8")))
    parsed = urlparse(url)
    profile["resolved_host"] = (parsed.netloc or "").replace("www.", "").lower()
    profile["loaded_from"] = loaded_from
    return profile
