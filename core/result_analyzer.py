import json
from pathlib import Path

from core.artifacts import execution_checkpoint_path, execution_debug_path, execution_learning_path


def analyze_execution_results(results_path: Path) -> dict:
    payload = json.loads(results_path.read_text(encoding="utf-8"))
    results = payload.get("results", [])
    run_dir = results_path.parent.parent if results_path.parent.name == "JSON" else results_path.parent
    debug_path = execution_debug_path(run_dir)
    debug_payload = json.loads(debug_path.read_text(encoding="utf-8")) if debug_path.exists() else {}
    learning_path = execution_learning_path(run_dir)
    checkpoint_path = execution_checkpoint_path(run_dir)
    learning_payload = json.loads(learning_path.read_text(encoding="utf-8")) if learning_path.exists() else {}
    checkpoint_payload = json.loads(checkpoint_path.read_text(encoding="utf-8")) if checkpoint_path.exists() else {}
    passed = [item for item in results if item.get("status") == "passed"]
    failed = [item for item in results if item.get("status") == "failed"]
    skipped = [item for item in results if item.get("status") == "skipped"]
    checkpoint_required = [item for item in results if item.get("status") == "checkpoint_required"]

    return {
        "total": len(results),
        "passed": len(passed),
        "failed": len(failed),
        "skipped": len(skipped),
        "checkpoint_required": len(checkpoint_required),
        "failed_cases": [
            {
                "id": item.get("id", ""),
                "title": item.get("title", ""),
                "error": item.get("error", ""),
            }
            for item in failed
        ],
        "debug_entries": debug_payload.get("debug_entries", []),
        "learning_entries": learning_payload.get("learning_entries", []),
        "checkpoints": checkpoint_payload.get("checkpoints", []),
    }


def save_execution_summary(results_path: Path, summary: dict) -> Path:
    run_dir = results_path.parent.parent if results_path.parent.name == "JSON" else results_path.parent
    output_path = run_dir / "Execution_Summary.md"
    lines = [
        "# Execution Summary",
        "",
        f"- Total: {summary.get('total', 0)}",
        f"- Passed: {summary.get('passed', 0)}",
        f"- Failed: {summary.get('failed', 0)}",
        f"- Skipped: {summary.get('skipped', 0)}",
        f"- Checkpoint Required: {summary.get('checkpoint_required', 0)}",
        "",
    ]
    if summary.get("failed_cases"):
        lines.append("## Failed Cases")
        lines.append("")
        for item in summary["failed_cases"]:
            lines.append(f"- {item.get('id', '-')}: {item.get('title', '-')} | {item.get('error', '')}")
    if summary.get("debug_entries"):
        lines.append("")
        lines.append("## Debug Entries")
        lines.append("")
        for item in summary["debug_entries"][:10]:
            details = item.get("details", {})
            lines.append(
                f"- {item.get('id', '-')}: {item.get('stage', '-')}"
                f" | target={details.get('target', '')}"
                f" | semantic={details.get('semantic_type', '')}"
            )
    if summary.get("checkpoints"):
        lines.append("")
        lines.append("## Checkpoints")
        lines.append("")
        for item in summary["checkpoints"][:10]:
            lines.append(f"- {item.get('id', '-')}: {item.get('type', '-')} | {item.get('reason', '')}")
    if summary.get("learning_entries"):
        lines.append("")
        lines.append("## Selector Learning")
        lines.append("")
        for item in summary["learning_entries"][:10]:
            lines.append(
                f"- {item.get('id', '-')}: status={item.get('status', '-')}"
                f" | resolved={item.get('resolved_selector', '')}"
            )
    output_path.write_text("\n".join(lines).strip() + "\n", encoding="utf-8")
    return output_path
