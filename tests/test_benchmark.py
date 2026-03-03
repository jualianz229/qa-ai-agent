import json
import tempfile
import unittest
from pathlib import Path

from core.benchmark import (
    BenchmarkCase,
    load_real_site_benchmark_suite,
    run_benchmark_suite,
    run_real_site_benchmark_suite,
)


class BenchmarkTests(unittest.TestCase):
    def test_run_benchmark_suite_generates_summary_and_report(self):
        page_info = {
            "url": "https://example.com/news",
            "title": "News",
            "metadata": {"title": "News"},
            "headings": [{"tag": "h1", "text": "News Portal"}],
            "texts": ["Search news"],
            "buttons": ["Search"],
            "links": [],
            "forms": [],
            "images": [],
            "apis": [],
            "sections": [],
            "tables": [],
            "lists": [],
            "navigation": [],
            "page_fingerprint": {"has_search": True, "has_form": False},
            "crawled_pages": [],
            "site_profile": {},
        }
        case = BenchmarkCase(
            name="news-smoke",
            page_info=page_info,
            page_scope={
                "page_type": "listing",
                "primary_goal": "Browse and search news content.",
                "key_modules": ["search"],
                "critical_user_flows": ["use search"],
                "priority_areas": ["search"],
                "risks": ["broken search"],
                "scope_summary": "Search-driven listing page.",
                "confidence": 0.8,
            },
            test_cases=[
                {
                    "ID": "SRH-001",
                    "Title": "Use search",
                    "Module": "Search",
                    "Category": "Functional",
                    "Test Type": "Positive",
                    "Automation": "auto",
                    "Steps to Reproduce": "1. Open the site https://example.com/news",
                    "Expected Result": "Search result text should be displayed.",
                }
            ],
            base_url="https://example.com/news",
        )

        with tempfile.TemporaryDirectory() as tmp:
            summary = run_benchmark_suite([case], output_dir=tmp)
            report_path = Path(tmp) / "Benchmark_Report.json"

            self.assertEqual(summary["total_cases"], 1)
            self.assertTrue(summary["results"])
            self.assertIn("average_grounding_coverage", summary)
            self.assertIn("average_heuristic_alignment", summary)
            self.assertIn("average_confidence", summary)
            self.assertIn("average_source_trust", summary)
            self.assertIn("average_stability", summary)
            self.assertIn("average_fact_coverage", summary)
            self.assertIn("average_anti_hallucination_score", summary)
            self.assertIn("cluster_keys", summary)
            self.assertIn("grounding_coverage", summary["results"][0])
            self.assertIn("heuristic_alignment", summary["results"][0])
            self.assertIn("confidence_score", summary["results"][0])
            self.assertIn("source_trust", summary["results"][0])
            self.assertIn("stability", summary["results"][0])
            self.assertIn("false_positive_case_rate", summary["results"][0])
            self.assertIn("anti_hallucination_score", summary["results"][0])
            self.assertTrue(report_path.exists())

    def test_load_and_run_real_site_benchmark_suite(self):
        config_payload = {
            "suite_name": "local-suite",
            "targets": [
                {
                    "name": "search-surface",
                    "url": "https://example.com/search",
                    "expected_page_type": "listing",
                    "key_modules": ["search", "listing"],
                    "critical_user_flows": ["use search"],
                    "instructions": "Focus on search relevance.",
                    "use_auth": False,
                    "crawl_limit": 2,
                }
            ],
        }

        class FakeScanner:
            def scan_website(self, url, use_auth=False, crawl_limit=3):
                return (
                    {"run_dir": "Result/fake_run", "title": "Search"},
                    {
                        "url": url,
                        "title": "Search",
                        "metadata": {"title": "Search"},
                        "headings": [{"tag": "h1", "text": "Search Surface"}],
                        "texts": ["Search the catalog"],
                        "buttons": ["Search"],
                        "links": [{"text": "Next", "href": "/page/2"}],
                        "forms": [{
                            "id": "search-form",
                            "name": "searchForm",
                            "action": "/search",
                            "method": "get",
                            "submit_texts": ["Search"],
                            "context_text": "Search the catalog",
                            "container_heading": "Search Surface",
                            "container_text": "Search the catalog",
                            "dom_path": "main > form#search-form",
                            "container_hints": ["Search Surface"],
                            "fields": [
                                {
                                    "tag": "input",
                                    "type": "search",
                                    "name": "keyword",
                                    "id": "keyword",
                                    "placeholder": "Search",
                                    "aria_label": "Search",
                                    "autocomplete": "",
                                    "inputmode": "",
                                    "label": "Search",
                                    "required": False,
                                    "pattern": "",
                                    "maxlength": "",
                                    "minlength": "",
                                    "data_testid": "",
                                    "class_tokens": [],
                                    "options": [],
                                    "context_text": "Search the catalog",
                                    "semantic_text": "search keyword query",
                                    "container_hints": ["Search Surface"],
                                    "nearby_texts": ["Search the catalog"],
                                    "dom_path": "main > form#search-form > input#keyword",
                                }
                            ],
                        }],
                        "images": [],
                        "apis": ["https://example.com/api/search"],
                        "sections": [{"heading": "Search Surface", "text": "Search the catalog"}],
                        "section_graph": {"nodes": [{"block_id": "search", "tag": "section", "heading": "Search Surface", "text": "Search the catalog", "dom_path": "main > section#search", "parent_block_id": "", "link_count": 1, "button_count": 1, "field_count": 1}], "edges": []},
                        "tables": [],
                        "lists": [["One", "Two", "Three", "Four", "Five", "Six"]],
                        "navigation": [[{"text": "Home", "href": "/"}, {"text": "Search", "href": "/search"}]],
                        "page_fingerprint": {
                            "has_search": True,
                            "has_listing_pattern": True,
                            "has_form": True,
                            "has_navigation": True,
                            "button_count": 1,
                            "link_count": 1,
                            "form_count": 1,
                            "section_count": 1,
                            "table_count": 0,
                        },
                        "crawled_pages": [],
                        "site_profile": {},
                    },
                    "",
                )

        class FakeAI:
            def analyze_page_scope(self, url, title, page_info, page_model=None, custom_instruction=""):
                return {
                    "page_type": "listing",
                    "primary_goal": "Search content",
                    "key_modules": ["search", "listing"],
                    "critical_user_flows": ["use search"],
                    "priority_areas": ["search"],
                    "risks": [],
                    "scope_summary": "Search listing page",
                    "confidence": 0.88,
                }

            def generate_test_scenarios(self, url, title, page_info, page_model=None, page_scope=None, custom_instruction=""):
                return [
                    {
                        "ID": "SRH-001",
                        "Title": "Use search",
                        "Module": "Search",
                        "Category": "Functionality",
                        "Test Type": "Positive",
                        "Automation": "auto",
                        "Steps to Reproduce": "1. Open the site https://example.com/search\n2. Input 'news' into the 'Search' field.\n3. Click the 'Search' button.",
                        "Expected Result": "The search API request should return 200 without error.",
                    }
                ]

        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "suite.json"
            output_dir = Path(tmp) / "output"
            config_path.write_text(json.dumps(config_payload), encoding="utf-8")

            suite = load_real_site_benchmark_suite(config_path)
            summary = run_real_site_benchmark_suite(config_path, FakeScanner(), ai_engine=FakeAI(), output_dir=output_dir)

            self.assertEqual(suite["suite_name"], "local-suite")
            self.assertEqual(suite["targets"][0].crawl_limit, 2)
            self.assertEqual(summary["suite_name"], "local-suite")
            self.assertEqual(summary["live_targets"][0]["observed_page_type"], "listing")
            self.assertTrue((output_dir / "Real_Site_Benchmark_Report.json").exists())
            self.assertEqual(summary["total_cases"], 1)


if __name__ == "__main__":
    unittest.main()
