import unittest

from core.contradictions import analyze_cross_stage_contradictions


class ContradictionTests(unittest.TestCase):
    def setUp(self):
        self.page_info = {
            "headings": [{"text": "News Portal"}],
            "texts": ["Search stories", "Browse latest updates"],
            "page_fingerprint": {
                "has_search": True,
                "has_filters": False,
                "has_pagination": False,
                "has_auth_pattern": False,
                "has_table": False,
                "has_form": False,
                "has_navigation": True,
                "has_article_like_sections": True,
                "has_listing_pattern": True,
            },
            "discovered_states": [],
        }
        self.page_model = {
            "components": [
                {"type": "search"},
                {"type": "navigation"},
                {"type": "listing"},
            ],
            "actions": [{"type": "open_url"}, {"type": "click"}],
            "entities": [{"type": "heading", "value": "News Portal"}],
            "possible_flows": [{"name": "open_page"}, {"name": "search"}],
            "field_catalog": [],
            "component_catalog": [{"component_key": "search", "type": "search", "label": "Search"}],
            "section_graph": {"nodes": []},
            "page_facts": {
                "search": True,
                "navigation": True,
                "listing": True,
                "form": False,
                "auth": False,
            },
        }
        self.page_scope = {
            "page_type": "listing page",
            "primary_goal": "Browse and search stories",
            "key_modules": ["Search", "Navigation"],
            "critical_user_flows": ["Search stories by keyword"],
            "priority_areas": ["Search relevance"],
            "scope_summary": "Users search and browse stories.",
        }

    def test_detects_cross_stage_contradictions_for_ungrounded_auth_flow(self):
        test_cases = [
            {
                "ID": "AUTH-001",
                "Module": "Authentication",
                "Category": "Functional",
                "Test Type": "Positive",
                "Title": "Login with valid credentials",
                "Precondition": "",
                "Steps to Reproduce": "1. Open the site https://example.com/news\n2. Input 'demo' into the Username field.\n3. Click the Login button.",
                "Expected Result": "The user is redirected after login.",
                "_grounding": {"fact_ids": [], "score": 0.0},
                "_task_alignment": {"score": 0.0, "issues": ["mentions unsupported surfaces: login"]},
            }
        ]
        execution_plan = {
            "plans": [
                {
                    "id": "AUTH-001",
                    "target_url": "https://example.com/news",
                    "pre_actions": [],
                    "actions": [
                        {"type": "fill", "target": "Username", "grounding_refs": []},
                        {"type": "click", "target": "Login", "grounding_refs": []},
                    ],
                    "assertions": [],
                    "scenario_grounding": {},
                }
            ]
        }
        execution_results = {
            "results": [
                {
                    "id": "AUTH-001",
                    "status": "passed",
                    "fact_ids": [],
                    "grounding_score": 0.0,
                }
            ]
        }

        report = analyze_cross_stage_contradictions(
            page_scope=self.page_scope,
            test_cases=test_cases,
            execution_plan=execution_plan,
            page_model=self.page_model,
            page_info=self.page_info,
            execution_results=execution_results,
        )

        codes = {item["code"] for item in report["issues"]}
        self.assertIn("scenario_out_of_context", codes)
        self.assertIn("scenario_missing_grounding", codes)
        self.assertIn("plan_actions_without_grounding_refs", codes)
        self.assertIn("result_passed_without_grounding", codes)
        self.assertTrue(report["summary"]["blocking"])

    def test_keeps_clean_report_for_grounded_search_flow(self):
        test_cases = [
            {
                "ID": "SRH-001",
                "Module": "Search",
                "Category": "Functionality",
                "Test Type": "Positive",
                "Title": "Search stories by keyword",
                "Precondition": "",
                "Steps to Reproduce": "1. Open the site https://example.com/news\n2. Input 'final' into the Search field.\n3. Click the Search button.",
                "Expected Result": "Relevant stories are displayed.",
                "_grounding": {"fact_ids": ["component::search"], "score": 0.74},
                "_task_alignment": {"score": 0.91, "issues": []},
            }
        ]
        execution_plan = {
            "plans": [
                {
                    "id": "SRH-001",
                    "target_url": "https://example.com/news",
                    "pre_actions": [],
                    "actions": [
                        {"type": "fill", "target": "Search", "grounding_refs": [{"source_type": "component", "source_key": "search"}]},
                        {"type": "click", "target": "Search", "grounding_refs": [{"source_type": "component", "source_key": "search"}]},
                    ],
                    "assertions": [],
                    "scenario_grounding": {"fact_ids": ["component::search"], "score": 0.74},
                }
            ]
        }
        execution_results = {
            "results": [
                {
                    "id": "SRH-001",
                    "status": "passed",
                    "fact_ids": ["component::search"],
                    "grounding_score": 0.74,
                }
            ]
        }

        report = analyze_cross_stage_contradictions(
            page_scope=self.page_scope,
            test_cases=test_cases,
            execution_plan=execution_plan,
            page_model=self.page_model,
            page_info=self.page_info,
            execution_results=execution_results,
        )

        self.assertEqual(report["summary"]["contradiction_count"], 0)
        self.assertFalse(report["summary"]["blocking"])


if __name__ == "__main__":
    unittest.main()
