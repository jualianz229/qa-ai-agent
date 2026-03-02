import csv
import json
import tempfile
import unittest
from pathlib import Path

from core.dashboard_data import build_knowledge_snapshot, build_run_detail, build_run_summary, list_runs, safe_run_artifact


class DashboardDataTests(unittest.TestCase):
    def test_build_run_summary_collects_status_and_videos(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "example_run"
            learned_dir = Path(tmp) / "site_profiles" / "learned"
            (run_dir / "JSON").mkdir(parents=True)
            (run_dir / "Evidence" / "Video").mkdir(parents=True)
            learned_dir.mkdir(parents=True)
            (run_dir / "JSON" / "raw_scan_example.json").write_text(
                json.dumps({"title": "Example", "url": "https://example.com"}),
                encoding="utf-8",
            )
            (run_dir / "JSON" / "Page_Scope_example.json").write_text(
                json.dumps({"page_type": "form", "confidence": 0.75, "scope_summary": "Form page"}),
                encoding="utf-8",
            )
            (run_dir / "JSON" / "Execution_Results.json").write_text(
                json.dumps({"results": [{"id": "TC-1", "status": "passed"}, {"id": "TC-2", "status": "failed"}]}),
                encoding="utf-8",
            )
            (run_dir / "JSON" / "Execution_Learning.json").write_text(
                json.dumps({"learning_entries": [{"id": "TC-2"}]}),
                encoding="utf-8",
            )
            (run_dir / "JSON" / "Execution_Checkpoints.json").write_text(
                json.dumps({"checkpoints": [{"id": "TC-3"}]}),
                encoding="utf-8",
            )
            (learned_dir / "_global.json").write_text(
                json.dumps(
                    {
                        "learning": {
                            "field_selectors": {"username": ["input[name='user-name']"]},
                            "selector_stats": {
                                "field_selectors": {
                                    "username": {
                                        "input[name='user-name']": {"successes": 2, "score": 5.2}
                                    }
                                }
                            },
                        }
                    }
                ),
                encoding="utf-8",
            )
            (learned_dir / "example.com.json").write_text(
                json.dumps(
                    {
                        "learning": {
                            "action_selectors": {"login": ["button:has-text('Login')"]},
                            "selector_stats": {
                                "action_selectors": {
                                    "login": {
                                        "button:has-text('Login')": {"successes": 1, "score": 3.4}
                                    }
                                }
                            },
                        }
                    }
                ),
                encoding="utf-8",
            )
            (run_dir / "Evidence" / "Video" / "TC-1.webm").write_text("demo", encoding="utf-8")
            csv_path = run_dir / "example.csv"
            with csv_path.open("w", encoding="utf-8-sig", newline="") as handle:
                writer = csv.DictWriter(
                    handle,
                    fieldnames=["ID", "Title", "Steps to Reproduce", "Automation", "Priority", "Severity", "Execution Status"],
                )
                writer.writeheader()
                writer.writerow(
                    {
                        "ID": "TC-1",
                        "Title": "Demo",
                        "Steps to Reproduce": "1. Open the site https://example.com 2. Click Sign in 3. Verify dashboard",
                        "Automation": "auto",
                        "Priority": "P1",
                        "Severity": "High",
                        "Execution Status": "passed",
                    }
                )

            summary = build_run_summary(run_dir)
            detail = build_run_detail(run_dir)
            snapshot = build_knowledge_snapshot("https://example.com/login", profiles_dir=Path(tmp) / "site_profiles")

            self.assertEqual(summary["title"], "Example")
            self.assertEqual(summary["page_type"], "form")
            self.assertEqual(summary["video_count"], 1)
            self.assertEqual(summary["status_counts"]["passed"], 1)
            self.assertEqual(summary["status_counts"]["failed"], 1)
            self.assertEqual(detail["csv_rows"][0]["ID"], "TC-1")
            self.assertEqual(detail["case_rows"][0]["status"], "passed")
            self.assertEqual(detail["case_rows"][0]["automation"], "auto")
            self.assertTrue(detail["case_rows"][0]["is_p1p2"])
            self.assertEqual(
                detail["case_rows"][0]["steps"],
                ["Open the site https://example.com", "Click Sign in", "Verify dashboard"],
            )
            self.assertEqual(detail["filter_options"]["priorities"][0]["value"], "p1")
            self.assertEqual(detail["filter_options"]["severities"][0]["value"], "high")
            self.assertIn("knowledge_snapshot", detail)
            self.assertGreaterEqual(snapshot["global"]["field_selector_count"], 1)

    def test_list_runs_and_safe_artifact(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run_dir = root / "run_one"
            (run_dir / "JSON").mkdir(parents=True)
            file_path = run_dir / "JSON" / "sample.json"
            file_path.write_text("{}", encoding="utf-8")

            runs = list_runs(root)
            artifact = safe_run_artifact("run_one", "JSON/sample.json", root)

            self.assertEqual(len(runs), 1)
            self.assertEqual(runs[0]["run_name"], "run_one")
            self.assertEqual(artifact, file_path.resolve())


if __name__ == "__main__":
    unittest.main()
