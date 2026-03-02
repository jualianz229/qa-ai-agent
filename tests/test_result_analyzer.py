import json
import tempfile
import unittest
from pathlib import Path

from core.artifacts import (
    execution_checkpoint_path,
    execution_debug_path,
    execution_learning_path,
    execution_results_path,
)
from core.result_analyzer import analyze_execution_results, save_execution_summary


class ResultAnalyzerTests(unittest.TestCase):
    def test_analyze_execution_results_counts_statuses(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            results_path = execution_results_path(run_dir)
            debug_path = execution_debug_path(run_dir)
            learning_path = execution_learning_path(run_dir)
            checkpoint_path = execution_checkpoint_path(run_dir)
            results_path.write_text(
                json.dumps(
                    {
                        "results": [
                            {"id": "A", "title": "One", "status": "passed", "error": ""},
                            {"id": "B", "title": "Two", "status": "failed", "error": "boom"},
                            {"id": "C", "title": "Three", "status": "skipped", "error": ""},
                            {"id": "D", "title": "Four", "status": "checkpoint_required", "error": "otp"},
                        ]
                    }
                ),
                encoding="utf-8",
            )
            debug_path.write_text(
                json.dumps(
                    {
                        "debug_entries": [
                            {
                                "id": "B",
                                "stage": "resolution",
                                "details": {"target": "phone number", "semantic_type": "phone_number"},
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            learning_path.write_text(
                json.dumps({"learning_entries": [{"id": "B", "status": "failed", "resolved_selector": "input[name*=phone]"}]}),
                encoding="utf-8",
            )
            checkpoint_path.write_text(
                json.dumps({"checkpoints": [{"id": "D", "type": "otp", "reason": "OTP needed"}]}),
                encoding="utf-8",
            )

            summary = analyze_execution_results(results_path)
            summary_path = save_execution_summary(results_path, summary)
            summary_text = summary_path.read_text(encoding="utf-8")

            self.assertEqual(summary["total"], 4)
            self.assertEqual(summary["passed"], 1)
            self.assertEqual(summary["failed"], 1)
            self.assertEqual(summary["skipped"], 1)
            self.assertEqual(summary["checkpoint_required"], 1)
            self.assertEqual(len(summary["debug_entries"]), 1)
            self.assertEqual(len(summary["learning_entries"]), 1)
            self.assertEqual(len(summary["checkpoints"]), 1)
            self.assertTrue(summary_path.exists())
            self.assertEqual(summary_path.parent, run_dir)
            self.assertIn("## Debug Entries", summary_text)
            self.assertIn("## Checkpoints", summary_text)
            self.assertIn("## Selector Learning", summary_text)
            self.assertIn("target=phone number", summary_text)


if __name__ == "__main__":
    unittest.main()
