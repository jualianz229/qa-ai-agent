import json
import re
from datetime import datetime
from pathlib import Path


INSTRUCTIONS_DIR = Path("instructions")
TEMPLATE_NOTES_FILENAME = ".template_user_notes.json"

DEFAULT_TEMPLATES = {
    "basic_smoke.txt": """Focus on the most critical user flows first.

Priorities:
- Open app, navigate primary routes, verify no blocker
- Run 1-2 happy path checks and 1 negative path check
- Validate key feedback messages are clear and consistent
- Capture any blocker, major regression, and flaky behavior
""",
    "global_balanced.txt": """Use a balanced end-to-end quality pass across the whole app.

Cover:
- Core user journey from start to completion
- Input, interaction, and feedback quality
- Data save/load consistency across screens
- Error handling, recovery path, and state persistence
- Major UI, UX, and functional regression signals
""",
    "global_risk_first.txt": """Prioritize highest-risk validation first, then expand coverage.

Cover:
- Flows with high business impact and high failure cost
- Critical data mutation and irreversible actions
- Permissions, boundary conditions, and failure branches
- Existing known flaky areas and recent change surfaces
- Stop at first blocker, then continue risk-ranked checks
""",
    "global_exploratory.txt": """Run exploratory testing with realistic and edge user behavior.

Cover:
- Normal user paths with varied timing and action order
- Unexpected input combinations and sequence switching
- Back/forward navigation and interrupted interaction
- Ambiguous states, unclear messages, and dead-end paths
- Any behavior that indicates weak product assumptions
""",
    "global_regression.txt": """Run broad regression checks after code/config/content changes.

Cover:
- Previously stable core paths to detect regression
- Data rendering, navigation continuity, and status updates
- Cross-page consistency for labels, actions, and feedback
- Browser responsiveness and interaction reliability
- Compare current output vs expected baseline behavior
""",
    "global_data_integrity.txt": """Prioritize end-to-end data integrity in all observed flows.

Cover:
- Input accepted/rejected according to business expectations
- Stored data remains accurate after refresh and revisit
- Derived values remain consistent across related views
- No silent truncation, mutation, or stale state leaks
- Error states do not corrupt existing valid data
""",
    "global_error_resilience.txt": """Prioritize resilience when operations fail or degrade.

Cover:
- Validation error behavior and actionable guidance
- Timeout, retry, and cancel behavior from user perspective
- Partial-failure handling without breaking whole flow
- Clear fallback state for network/service instability
- Recoverability after error without forced hard refresh
""",
    "global_usability_consistency.txt": """Prioritize consistency of interaction and communication.

Cover:
- Label naming, action semantics, and visual hierarchy
- Feedback timing, loading cues, and disabled states
- Keyboard/touch interaction consistency
- Readability of copy, helper text, and error explanation
- Predictability across repeated actions and revisit flows
""",
    "global_performance_stability.txt": """Prioritize perceived performance and runtime stability.

Cover:
- Page/interactions remain responsive under typical load
- Repeated actions do not degrade responsiveness over time
- Loading states are bounded, meaningful, and non-blocking
- Heavy screens remain usable with graceful degradation
- No obvious memory, rendering, or event-loop instability
""",
    "global_accessibility_baseline.txt": """Prioritize baseline accessibility across all core flows.

Cover:
- Logical structure, reading order, and semantic clarity
- Keyboard reachability and visible focus indication
- Understandable labels, instructions, and error context
- Sufficient contrast and non-color-only status indicators
- Dynamic updates communicated in an accessible manner
""",
    "global_release_readiness.txt": """Run holistic release-readiness validation before sign-off.

Cover:
- Core business journey quality and stability
- High-risk behavior from functionality, data, and UX angles
- Defect severity triage with reproducible evidence
- Pass/fail checkpoint summary for go/no-go decision
- Clear residual risk notes and recommended follow-up
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
        if file_path.stem.endswith("_profile"):
            continue
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


def update_instruction_template(name: str, content: str, directory: str | Path = INSTRUCTIONS_DIR) -> dict:
    file_path = resolve_instruction_template(name, directory)
    file_path.write_text(str(content or "").rstrip() + "\n", encoding="utf-8")
    return load_instruction_template(file_path.name, directory)


def _template_notes_path(directory: str | Path = INSTRUCTIONS_DIR) -> Path:
    target_dir = ensure_instruction_templates(directory)
    return target_dir / TEMPLATE_NOTES_FILENAME


def load_template_user_notes(directory: str | Path = INSTRUCTIONS_DIR) -> dict:
    path = _template_notes_path(directory)
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    if not isinstance(payload, dict):
        return {}
    notes = payload.get("notes", {})
    return notes if isinstance(notes, dict) else {}


def save_template_user_note(template_name: str, instruction: str, directory: str | Path = INSTRUCTIONS_DIR) -> None:
    safe_template = str(template_name or "").strip()
    if not safe_template:
        return
    notes = load_template_user_notes(directory)
    notes[safe_template] = str(instruction or "")
    path = _template_notes_path(directory)
    payload = {
        "updated_at": datetime.now().isoformat(timespec="seconds"),
        "notes": notes,
    }
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


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
