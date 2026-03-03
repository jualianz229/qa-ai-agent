import unittest

from core.ai_engine import AIEngine


class AIEngineNormalizationTests(unittest.TestCase):
    def test_normalize_automation_value_maps_yes_to_auto(self):
        engine = AIEngine.__new__(AIEngine)
        self.assertEqual(engine._normalize_automation_value("yes"), "auto")
        self.assertEqual(engine._normalize_automation_value("manual"), "manual")
        self.assertEqual(engine._normalize_automation_value("semi"), "semi-auto")

    def test_build_fact_pack_and_heuristic_route(self):
        engine = AIEngine.__new__(AIEngine)
        page_info = {
            "url": "https://example.com/search",
            "headings": [{"text": "Search"}],
            "discovered_states": [],
        }
        page_model = {
            "heuristic_scope": {
                "likely_page_type": "search_listing",
                "priority_modules": ["Search", "Listing"],
                "recommended_flows": ["search by keyword"],
                "confidence": 0.88,
            },
            "page_facts": {"search": True, "listing": True, "form": True},
            "section_graph": {
                "nodes": [
                    {"block_id": "search", "heading": "Search", "tag": "section", "field_count": 1, "button_count": 1, "link_count": 0},
                    {"block_id": "results", "heading": "Results", "tag": "section", "field_count": 0, "button_count": 1, "link_count": 3},
                    {"block_id": "filters", "heading": "Filters", "tag": "aside", "field_count": 2, "button_count": 1, "link_count": 0},
                ]
            },
            "field_catalog": [
                {"field_key": "search_query", "semantic_label": "Search", "semantic_type": "search_query", "required": False, "container_hints": ["Search"]},
                {"field_key": "category_filter", "semantic_label": "Category", "semantic_type": "category", "required": False, "container_hints": ["Filters"]},
                {"field_key": "sort_order", "semantic_label": "Sort", "semantic_type": "sort_order", "required": False, "container_hints": ["Filters"]},
            ],
            "component_catalog": [
                {"component_key": "search", "label": "Search", "type": "search", "aliases": ["keyword search"]},
                {"component_key": "results", "label": "Results", "type": "listing", "aliases": ["result list"]},
                {"component_key": "filters", "label": "Filters", "type": "filter", "aliases": ["refine results"]},
            ],
            "api_endpoints": ["https://example.com/api/search"],
        }

        fact_pack = engine._build_fact_pack(page_info, page_model, {})
        route = engine._build_task_route("page_scope", page_info, page_model, {}, fact_pack, "")
        heuristic_scope = engine._heuristic_scope_from_facts(page_model, page_info, fact_pack)

        self.assertGreaterEqual(fact_pack["summary"]["fact_count"], 3)
        self.assertEqual(route["mode"], "heuristic")
        self.assertEqual(heuristic_scope["page_type"], "search_listing")
        self.assertIn("Search", heuristic_scope["key_modules"])


if __name__ == "__main__":
    unittest.main()
