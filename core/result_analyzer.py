import json
from pathlib import Path

from core.artifacts import execution_checkpoint_path, execution_debug_path, execution_learning_path, execution_network_path, flaky_analysis_path


def analyze_execution_results(results_path: Path) -> dict:
    payload = json.loads(results_path.read_text(encoding="utf-8"))
    results = payload.get("results", [])
    run_dir = results_path.parent.parent if results_path.parent.name == "JSON" else results_path.parent
    debug_path = execution_debug_path(run_dir)
    debug_payload = json.loads(debug_path.read_text(encoding="utf-8")) if debug_path.exists() else {}
    learning_path = execution_learning_path(run_dir)
    checkpoint_path = execution_checkpoint_path(run_dir)
    network_path = execution_network_path(run_dir)
    flaky_path = flaky_analysis_path(run_dir, create=False)
    learning_payload = json.loads(learning_path.read_text(encoding="utf-8")) if learning_path.exists() else {}
    checkpoint_payload = json.loads(checkpoint_path.read_text(encoding="utf-8")) if checkpoint_path.exists() else {}
    network_payload = json.loads(network_path.read_text(encoding="utf-8")) if network_path.exists() else {}
    flaky_payload = json.loads(flaky_path.read_text(encoding="utf-8")) if flaky_path.exists() else {}
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
        "network_entries": network_payload.get("network_entries", []),
        "network_summary": _summarize_network(network_payload.get("network_entries", [])),
        "flaky_analysis": flaky_payload,
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
    if summary.get("network_summary", {}).get("request_count", 0):
        lines.append("")
        lines.append("## Network Insight")
        lines.append("")
        lines.append(f"- Requests: {summary['network_summary'].get('request_count', 0)}")
        lines.append(f"- Responses: {summary['network_summary'].get('response_count', 0)}")
        lines.append(f"- Failing Responses: {summary['network_summary'].get('failing_response_count', 0)}")
        for item in summary["network_summary"].get("top_endpoints", [])[:6]:
            lines.append(f"- {item.get('path', '-')}: hits={item.get('hits', 0)}")
    if summary.get("flaky_analysis", {}).get("summary", {}).get("flaky_count", 0):
        lines.append("")
        lines.append("## Flaky Insight")
        lines.append("")
        lines.append(f"- Flaky Cases: {summary['flaky_analysis']['summary'].get('flaky_count', 0)}")
        for item in summary.get("flaky_analysis", {}).get("flaky_cases", [])[:8]:
            lines.append(
                f"- {item.get('id', '-')}: transitions={item.get('transitions', 0)}"
                f" | history={', '.join(item.get('recent_statuses', [])[:4])}"
            )
    output_path.write_text("\n".join(lines).strip() + "\n", encoding="utf-8")
    return output_path


def _summarize_network(entries: list[dict]) -> dict:
    requests = 0
    responses = 0
    failing = 0
    endpoint_hits = {}
    for entry in entries:
        summary = entry.get("summary", {})
        requests += int(summary.get("request_count", 0) or 0)
        responses += int(summary.get("response_count", 0) or 0)
        failing += int(summary.get("failing_response_count", 0) or 0)
        for endpoint in summary.get("top_endpoints", [])[:8]:
            path = str(endpoint.get("path", "")).strip()
            if not path:
                continue
            endpoint_hits[path] = endpoint_hits.get(path, 0) + int(endpoint.get("hits", 0) or 0)
    return {
        "request_count": requests,
        "response_count": responses,
        "failing_response_count": failing,
        "top_endpoints": [
            {"path": key, "hits": value}
            for key, value in sorted(endpoint_hits.items(), key=lambda item: (-item[1], item[0]))[:8]
        ],
    }
