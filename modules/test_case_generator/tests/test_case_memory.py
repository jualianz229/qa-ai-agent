import tempfile
import unittest
from pathlib import Path

from core.case_memory import load_case_memory_snapshot, merge_case_memory


class CaseMemoryTests(unittest.TestCase):
    def test_merge_case_memory_persists_patterns_and_loads_relevant_snapshot(self):
        cases = [
            {
                "ID": "SRH-001",
                "Module": "Search",
                "Category": "Functionality",
                "Test Type": "Positive",
                "Title": "Search with valid keyword",
                "Steps to Reproduce": "1. Open the site https://example.com/search\n2. Input 'news' into the 'Search' field.\n3. Click the 'Search' button.",
                "Expected Result": "Relevant results should be displayed.",
                "Automation": "auto",
            }
        ]
        page_scope = {"page_type": "search_listing", "key_modules": ["Search"]}
        page_model = {
            "heuristic_scope": {"likely_page_type": "search_listing", "priority_modules": ["Search"]},
            "page_facts": {"search": True, "listing": True},
        }

        with tempfile.TemporaryDirectory() as tmp:
            result = merge_case_memory(
                "https://example.com/search",
                cases,
                page_scope,
                page_model,
                memory_dir=Path(tmp),
            )
            snapshot = load_case_memory_snapshot(
                "https://example.com/search",
                page_model=page_model,
                page_scope=page_scope,
                memory_dir=Path(tmp),
            )

            self.assertIsNotNone(result)
            self.assertTrue(result["paths"])
            self.assertEqual(snapshot["summary"]["pattern_count"], 1)
            self.assertEqual(snapshot["patterns"][0]["module"], "Search")
            self.assertIn("fill", snapshot["patterns"][0]["common_step_profile"])


if __name__ == "__main__":
    unittest.main()
