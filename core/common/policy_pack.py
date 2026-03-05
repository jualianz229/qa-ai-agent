from __future__ import annotations

from end_to_end_automation.src.replay_verifier import verify_plan_execution_consistency
from core.common.safety_gates import build_execution_gate_decision
from core.common.self_critique import refine_execution_plan_with_self_critique


def run_anti_hallucination_policy_pack() -> dict:
    checks = []

    gate = build_execution_gate_decision(
        composite_confidence={
            "breakdown": {"anti_hallucination": 0.42, "source_trust": 0.41, "negative_evidence": 0.35}
        },
        scenario_validation={"rejected_cases": [{"case": {"ID": "X"}}]},
        execution_plan_validation={"rejected_plans": [{"plan": {"id": "X"}}]},
        contradiction_report={"summary": {"contradiction_count": 1}},
    )
    checks.append({"name": "gate_blocks_low_anti_hallu", "passed": bool(gate.get("blocked", False))})

    plan = {
        "plans": [
            {
                "id": "POL-001",
                "assertions": [
                    {"type": "assert_any_text_visible", "values": ["success"], "grounding_confidence": 0.25, "grounded": False}
                ],
                "actions": [{"type": "click", "target": "Submit"}],
                "checkpoints": [],
            }
        ]
    }
    refined, critique = refine_execution_plan_with_self_critique(plan, {"page_identity": {"title": "Checkout"}})
    checks.append(
        {
            "name": "self_critique_removes_ambiguous_assertion",
            "passed": critique.get("assertions_removed", 0) > 0 and len(refined["plans"][0].get("assertions", [])) >= 1,
        }
    )

    replay = verify_plan_execution_consistency(
        execution_plan={"plans": [{"id": "POL-002", "actions": [], "pre_actions": [], "assertions": []}]},
        execution_results={"results": [{"id": "POL-002", "status": "failed"}]},
    )
    checks.append(
        {
            "name": "replay_detects_actionless_failure",
            "passed": replay.get("summary", {}).get("issue_count", 0) > 0,
        }
    )

    passed = sum(1 for item in checks if item["passed"])
    return {
        "suite": "anti_hallucination_policy_pack",
        "passed": passed,
        "total": len(checks),
        "success": passed == len(checks),
        "checks": checks,
    }
