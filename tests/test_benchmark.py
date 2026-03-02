import tempfile
import unittest
from pathlib import Path

from core.benchmark import BenchmarkCase, run_benchmark_suite


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
            self.assertIn("cluster_keys", summary)
            self.assertIn("grounding_coverage", summary["results"][0])
            self.assertIn("heuristic_alignment", summary["results"][0])
            self.assertIn("confidence_score", summary["results"][0])
            self.assertTrue(report_path.exists())


if __name__ == "__main__":
    unittest.main()
