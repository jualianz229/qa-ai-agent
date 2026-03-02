import re
from datetime import datetime
from pathlib import Path


INSTRUCTIONS_DIR = Path("instructions")

DEFAULT_TEMPLATES = {
    "basic_smoke.txt": """Focus on the most critical user flows first.

Priorities:
- Main navigation and top-level entry points
- Primary CTA and key conversion path
- Form validation and error handling
- Broken links, empty states, and loading behavior
""",
    "auth_focus.txt": """Prioritize authentication and access control behavior.

Cover:
- Login happy path
- Invalid credentials and required-field validation
- Session persistence and logout behavior
- Redirect handling for protected pages
- Any OTP, SSO, or verification checkpoints
""",
    "content_focus.txt": """Prioritize content and discovery quality.

Cover:
- Headline, metadata, and section visibility
- Navigation to detail pages
- Search, filter, sort, and pagination if present
- Empty states, related content, and broken media
- Breadcrumbs and return paths
""",
}


def ensure_instruction_templates(directory: str | Path = INSTRUCTIONS_DIR) -> Path:
    target_dir = Path(directory)
    target_dir.mkdir(parents=True, exist_ok=True)
    for filename, content in DEFAULT_TEMPLATES.items():
        file_path = target_dir / filename
        if not file_path.exists():
            file_path.write_text(content.strip() + "\n", encoding="utf-8")
    return target_dir


def list_instruction_templates(directory: str | Path = INSTRUCTIONS_DIR) -> list[dict]:
    target_dir = ensure_instruction_templates(directory)
    items = []
    for file_path in sorted(target_dir.glob("*.txt")):
        content = file_path.read_text(encoding="utf-8")
        items.append(
            {
                "name": file_path.name,
                "stem": file_path.stem,
                "path": str(file_path.resolve()),
                "preview": content[:320],
                "content": content,
                "size": file_path.stat().st_size,
                "updated_at": datetime.fromtimestamp(file_path.stat().st_mtime).isoformat(timespec="seconds"),
                "is_default": file_path.name in DEFAULT_TEMPLATES,
            }
        )
    return items


def resolve_instruction_template(name: str, directory: str | Path = INSTRUCTIONS_DIR) -> Path:
    safe_name = re.sub(r"[^A-Za-z0-9._-]+", "_", str(name or "").strip())
    if not safe_name.endswith(".txt"):
        safe_name += ".txt"
    target_dir = ensure_instruction_templates(directory).resolve()
    candidate = (target_dir / safe_name).resolve()
    if candidate.parent != target_dir or not candidate.exists():
        raise FileNotFoundError(safe_name)
    return candidate


def load_instruction_template(name: str, directory: str | Path = INSTRUCTIONS_DIR) -> dict:
    file_path = resolve_instruction_template(name, directory)
    content = file_path.read_text(encoding="utf-8")
    return {
        "name": file_path.name,
        "stem": file_path.stem,
        "path": str(file_path.resolve()),
        "content": content,
        "preview": content[:320],
        "size": file_path.stat().st_size,
        "updated_at": datetime.fromtimestamp(file_path.stat().st_mtime).isoformat(timespec="seconds"),
        "is_default": file_path.name in DEFAULT_TEMPLATES,
    }


def save_uploaded_template(uploaded_file, directory: str | Path = INSTRUCTIONS_DIR) -> dict:
    target_dir = ensure_instruction_templates(directory)
    original_name = Path(getattr(uploaded_file, "filename", "") or "").name
    if not original_name.lower().endswith(".txt"):
        raise ValueError("Only .txt files are supported.")
    safe_name = re.sub(r"[^A-Za-z0-9._-]+", "_", original_name)
    base_path = target_dir / safe_name
    file_path = base_path
    if file_path.exists():
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        file_path = target_dir / f"{base_path.stem}_{stamp}.txt"
    uploaded_file.save(file_path)
    return load_instruction_template(file_path.name, target_dir)
