import unittest

from core.confidence import build_historical_confidence_signal, compute_composite_confidence


class ConfidenceTests(unittest.TestCase):
    def test_compute_composite_confidence_returns_weighted_score_and_breakdown(self):
        result = compute_composite_confidence(
            page_scope={"confidence": 0.9},
            page_info={
                "headings": [{"text": "Dashboard"}],
                "texts": ["Live stats"],
                "links": [{"text": "Detail", "href": "/detail"}],
                "sections": [{"heading": "Overview", "text": "Summary"}],
                "forms": [],
                "standalone_controls": [],
                "page_fingerprint": {
                    "sampled_page_count": 2,
                    "has_form": False,
                    "has_search": True,
                    "has_table": False,
                },
                "discovered_states": [{"state_id": "tabs_overview"}],
                "interaction_probes": [{"type": "tabs"}],
            },
            page_model={
                "component_catalog": [{"type": "tabs"}],
                "field_catalog": [],
                "possible_flows": [{"name": "switch_tabs"}],
                "state_graph": {"states": [{"id": "landing"}, {"id": "tabs_overview"}]},
                "page_facts": {"search": True, "table": False, "filter": False},
            },
            scope_validation={"is_valid": True, "issues": []},
            scenario_validation={"is_valid": True, "valid_cases": [{}], "rejected_cases": []},
            execution_plan_validation={"is_valid": True, "valid_plan": {"plans": [{}]}, "rejected_plans": []},
            execution_results={"results": [{"status": "passed", "network_summary": {"failing_response_count": 0, "graphql_error_count": 0}}]},
            historical_signal={
                "matching_pattern_count": 2,
                "flaky_count": 0,
                "feedback_helpful": 3,
                "feedback_misleading": 0,
                "knowledge_counts": {
                    "field_selector_count": 2,
                    "action_selector_count": 1,
                    "semantic_pattern_count": 1,
                },
            },
            contradiction_analysis={"summary": {"contradiction_count": 0}},
        )

        self.assertGreater(result["score"], 0.7)
        self.assertIn("coverage", result["breakdown"])
        self.assertIn("execution_signal", result["breakdown"])
        self.assertIn("negative_evidence", result["breakdown"])
        self.assertIn("source_trust", result["breakdown"])
        self.assertIn("real_world_calibration", result["breakdown"])
        self.assertIn("stability", result["breakdown"])
        self.assertIn("anti_hallucination", result["breakdown"])
        self.assertIn("confidence_class", result)
        self.assertTrue(result["explanation"])

    def test_compute_composite_confidence_penalizes_conflict_and_anti_hallu_signals(self):
        result = compute_composite_confidence(
            page_scope={"confidence": 0.95},
            page_info={"headings": [{"text": "Page"}], "texts": ["Search"], "page_fingerprint": {"has_search": True}},
            page_model={"component_catalog": [{"type": "search"}], "page_facts": {"search": True}, "field_catalog": [], "possible_flows": []},
            scope_validation={"is_valid": True, "issues": []},
            scenario_validation={
                "is_valid": False,
                "valid_cases": [{"_grounding": {"coverage_score": 0.2}}],
                "rejected_cases": [{"case": {"ID": "BAD-1"}}],
                "grounding_summary": {"average_fact_coverage_score": 0.2, "instruction_conflict_count": 1},
            },
            execution_plan_validation={"is_valid": False, "valid_plan": {"plans": []}, "rejected_plans": [{"plan": {"id": "BAD-1"}}]},
            contradiction_analysis={"summary": {"contradiction_count": 2}},
        )

        self.assertLess(result["score"], 0.8)
        self.assertLess(result["breakdown"]["anti_hallucination"], 0.7)

    def test_build_historical_confidence_signal_uses_memory_feedback_and_flaky_inputs(self):
        signal = build_historical_confidence_signal(
            url="https://example.com/search",
            page_model={"page_facts": {"search": True}},
            page_scope={"page_type": "search_listing"},
            site_profile={
                "human_feedback": {
                    "summary": {
                        "scope_accurate": 2,
                        "scope_missed": 1,
                        "selector_helpful": 3,
                        "selector_misleading": 1,
                    }
                }
            },
            case_memory_snapshot={"patterns": [{"pattern_key": "search_flow"}, {"pattern_key": "empty_search"}]},
            flaky_snapshot={"summary": {"flaky_count": 1}},
            knowledge_snapshot={
                "global": {"field_selector_count": 4, "action_selector_count": 2, "semantic_pattern_count": 1},
                "domain": {"field_selector_count": 1, "action_selector_count": 0, "semantic_pattern_count": 1},
            },
        )

        self.assertEqual(signal["matching_pattern_count"], 2)
        self.assertEqual(signal["flaky_count"], 1)
        self.assertEqual(signal["feedback_helpful"], 5)
        self.assertEqual(signal["feedback_misleading"], 2)
        self.assertGreater(signal["knowledge_counts"]["field_selector_count"], 0)


if __name__ == "__main__":
    unittest.main()
