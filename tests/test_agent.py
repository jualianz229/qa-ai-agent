import unittest

from core.run_context import merge_recrawl_project_info


class AgentTests(unittest.TestCase):
    def test_merge_recrawl_project_info_preserves_run_metadata(self):
        project_info = {
            "title": "Original",
            "domain": "example.com",
            "project_name": "example.com Original",
            "run_dir": "Result/example_1",
            "timestamp": "20260101_010101",
            "safe_name": "example",
        }
        refreshed = {
            "title": "Refreshed",
            "domain": "example.com",
            "project_name": "example.com Refreshed",
            "run_dir": "Result/example_2",
            "timestamp": "20260101_020202",
            "safe_name": "example_new",
        }

        merged = merge_recrawl_project_info(project_info, refreshed)

        self.assertEqual(merged["title"], "Refreshed")
        self.assertEqual(merged["project_name"], "example.com Refreshed")
        self.assertEqual(merged["run_dir"], "Result/example_1")
        self.assertEqual(merged["timestamp"], "20260101_010101")
        self.assertEqual(merged["safe_name"], "example")


if __name__ == "__main__":
    unittest.main()
