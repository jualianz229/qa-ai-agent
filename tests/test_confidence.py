import unittest

from core.confidence import compute_composite_confidence


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
                "page_fingerprint": {"sampled_page_count": 2},
                "discovered_states": [{"state_id": "tabs_overview"}],
                "interaction_probes": [{"type": "tabs"}],
            },
            page_model={
                "component_catalog": [{"type": "tabs"}],
                "field_catalog": [],
                "possible_flows": [{"name": "switch_tabs"}],
                "state_graph": {"states": [{"id": "landing"}, {"id": "tabs_overview"}]},
            },
            scope_validation={"is_valid": True, "issues": []},
            scenario_validation={"is_valid": True, "valid_cases": [{}], "rejected_cases": []},
            execution_plan_validation={"is_valid": True, "valid_plan": {"plans": [{}]}, "rejected_plans": []},
            execution_results={"results": [{"status": "passed"}]},
        )

        self.assertGreater(result["score"], 0.7)
        self.assertIn("coverage", result["breakdown"])
        self.assertIn("execution_signal", result["breakdown"])


if __name__ == "__main__":
    unittest.main()
