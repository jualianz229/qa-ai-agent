import tempfile
import unittest
from pathlib import Path

from core.flaky_bank import load_flaky_snapshot, merge_flaky_history


class FlakyBankTests(unittest.TestCase):
    def test_merge_flaky_history_marks_case_as_flaky_after_status_changes(self):
        page_scope = {"page_type": "search_listing"}
        page_model = {"heuristic_scope": {"likely_page_type": "search_listing"}, "page_facts": {"search": True, "listing": True}}

        with tempfile.TemporaryDirectory() as tmp:
            merge_flaky_history(
                "https://example.com/search",
                {"results": [{"id": "SRH-001", "title": "Search with valid keyword", "status": "failed", "error": "boom"}]},
                page_model=page_model,
                page_scope=page_scope,
                flaky_dir=Path(tmp),
            )
            result = merge_flaky_history(
                "https://example.com/search",
                {"results": [{"id": "SRH-001", "title": "Search with valid keyword", "status": "passed", "error": ""}]},
                page_model=page_model,
                page_scope=page_scope,
                flaky_dir=Path(tmp),
            )
            snapshot = load_flaky_snapshot(
                "https://example.com/search",
                page_model=page_model,
                page_scope=page_scope,
                flaky_dir=Path(tmp),
            )

            self.assertIsNotNone(result)
            self.assertEqual(snapshot["summary"]["flaky_count"], 1)
            self.assertEqual(snapshot["flaky_cases"][0]["id"], "SRH-001")
            self.assertIn("passed", snapshot["flaky_cases"][0]["recent_statuses"])


if __name__ == "__main__":
    unittest.main()
