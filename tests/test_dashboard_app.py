import csv
import json
import tempfile
import unittest
from io import BytesIO
from pathlib import Path

import dashboard
from core.artifacts import human_feedback_path
from core.instruction_templates import ensure_instruction_templates


class DashboardAppTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.results_dir = Path(self.tmp.name) / "Result"
        self.results_dir.mkdir()
        self.instructions_dir = Path(self.tmp.name) / "instructions"
        ensure_instruction_templates(self.instructions_dir)
        self.original_result_dir = dashboard.RESULT_DIR
        self.original_instructions_dir = dashboard.INSTRUCTIONS_DIR
        self.original_profiles_dir = dashboard.PROFILES_DIR
        self.original_feedback_dir = dashboard.FEEDBACK_DIR
        self.original_create_job = dashboard.create_job
        self.original_create_retry_failed_job = dashboard.create_retry_failed_job
        dashboard.RESULT_DIR = self.results_dir
        dashboard.INSTRUCTIONS_DIR = self.instructions_dir
        dashboard.PROFILES_DIR = Path(self.tmp.name) / "site_profiles"
        dashboard.FEEDBACK_DIR = dashboard.PROFILES_DIR / "feedback"
        with dashboard.jobs_lock:
            dashboard.jobs.clear()

        run_dir = self.results_dir / "demo_run"
        (run_dir / "JSON").mkdir(parents=True)
        (run_dir / "Evidence" / "Video").mkdir(parents=True)
        (run_dir / "JSON" / "raw_scan_demo.json").write_text(
            json.dumps({"title": "Demo Site", "url": "https://example.com"}),
            encoding="utf-8",
        )
        (run_dir / "JSON" / "Page_Scope_demo.json").write_text(
            json.dumps({"page_type": "listing", "confidence": 0.82, "scope_summary": "Listing page"}),
            encoding="utf-8",
        )
        (run_dir / "JSON" / "Normalized_Page_Model_demo.json").write_text(
            json.dumps(
                {
                    "page_facts": {"search": True, "listing": True},
                    "heuristic_scope": {"likely_page_type": "listing"},
                }
            ),
            encoding="utf-8",
        )
        (run_dir / "JSON" / "Execution_Results.json").write_text(
            json.dumps({"results": [{"id": "TC-1", "status": "passed"}]}),
            encoding="utf-8",
        )
        (run_dir / "JSON" / "Execution_Learning.json").write_text(
            json.dumps(
                {
                    "learning_entries": [
                        {
                            "id": "TC-1",
                            "status": "passed",
                            "resolved_selector": "button:has-text('Search')",
                            "details": {"semantic_type": "search_button"},
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )
        (run_dir / "Evidence" / "Video" / "TC-1.webm").write_text("demo", encoding="utf-8")
        with (run_dir / "demo.csv").open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=["ID", "Title", "Automation", "Priority", "Severity", "Execution Status"])
            writer.writeheader()
            writer.writerow({"ID": "TC-1", "Title": "Demo", "Automation": "auto", "Priority": "P2", "Severity": "Medium", "Execution Status": "passed"})

        self.client = dashboard.app.test_client()

    def tearDown(self):
        dashboard.RESULT_DIR = self.original_result_dir
        dashboard.INSTRUCTIONS_DIR = self.original_instructions_dir
        dashboard.PROFILES_DIR = self.original_profiles_dir
        dashboard.FEEDBACK_DIR = self.original_feedback_dir
        dashboard.create_job = self.original_create_job
        dashboard.create_retry_failed_job = self.original_create_retry_failed_job
        with dashboard.jobs_lock:
            dashboard.jobs.clear()
        self.tmp.cleanup()

    def test_home_and_runs_pages_render(self):
        home_response = self.client.get("/")
        runs_response = self.client.get("/runs")

        self.assertEqual(home_response.status_code, 200)
        self.assertEqual(runs_response.status_code, 200)
        self.assertIn(b"demo_run", home_response.data)
        self.assertIn(b"Start a run", home_response.data)
        self.assertIn(b"Global memory", home_response.data)
        self.assertIn(b"listing", runs_response.data)

    def test_api_runs_and_artifact_route(self):
        runs_response = self.client.get("/api/runs")
        artifact_response = self.client.get("/artifacts/demo_run/JSON/Page_Scope_demo.json")

        try:
            self.assertEqual(runs_response.status_code, 200)
            self.assertEqual(artifact_response.status_code, 200)
            payload = runs_response.get_json()
            self.assertEqual(payload["runs"][0]["run_name"], "demo_run")
        finally:
            artifact_response.close()

    def test_template_api_lists_uploads_and_reuses_files(self):
        list_response = self.client.get("/api/templates")
        upload_response = self.client.post(
            "/api/templates",
            data={"template_file": (BytesIO(b"Focus on forms\n- validate phone number\n"), "phone_focus.txt")},
            content_type="multipart/form-data",
        )

        self.assertEqual(list_response.status_code, 200)
        self.assertEqual(upload_response.status_code, 200)
        payload = upload_response.get_json()
        self.assertTrue((self.instructions_dir / payload["template"]["name"]).exists())

        detail_response = self.client.get(f"/api/templates/{payload['template']['name']}")
        self.assertEqual(detail_response.status_code, 200)
        self.assertIn("phone_focus", detail_response.get_json()["template"]["name"])

    def test_create_job_api_accepts_template_name(self):
        def fake_create_job(payload):
            return {
                "id": "job-demo",
                "status": "queued",
                "payload": payload,
                "log_lines": [],
                "run_name": "",
                "command": [],
            }

        dashboard.create_job = fake_create_job

        response = self.client.post(
            "/api/jobs",
            data={
                "url": "https://example.com",
                "instruction": "Extra note",
                "template_name": "basic_smoke.txt",
                "csv_sep": ",",
                "crawl_limit": "3",
                "adaptive_recrawl": "on",
            },
        )

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["job"]["payload"]["template_name"], "basic_smoke.txt")
        self.assertTrue(payload["job"]["payload"]["template_path"].endswith("basic_smoke.txt"))

    def test_retry_failed_job_api_returns_job_payload(self):
        def fake_create_retry_failed_job(run_name, executor_headed=False):
            return {
                "id": "retry-demo",
                "status": "queued",
                "payload": {
                    "source_run_name": run_name,
                    "executor_headed": executor_headed,
                },
                "log_lines": [],
                "run_name": "demo_run_retry_20260303_000001",
                "command": ["retry-failed-only", run_name],
            }

        dashboard.create_retry_failed_job = fake_create_retry_failed_job

        response = self.client.post("/api/runs/demo_run/retry-failed", data={"executor_headed": "no"})

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["job"]["id"], "retry-demo")
        self.assertEqual(payload["job"]["payload"]["source_run_name"], "demo_run")

    def test_run_feedback_api_persists_feedback_and_updates_learning(self):
        response = self.client.post(
            "/api/runs/demo_run/feedback",
            data={
                "feedback_type": "selector_quality",
                "verdict": "helpful",
                "case_id": "TC-1",
                "selector": "button:has-text('Search')",
                "semantic_key": "search_button",
            },
        )

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["feedback"]["verdict"], "helpful")
        self.assertTrue(human_feedback_path(self.results_dir / "demo_run").exists())
        self.assertTrue((dashboard.FEEDBACK_DIR / "_global.json").exists())
        self.assertTrue((dashboard.PROFILES_DIR / "learned" / "_global.json").exists())


if __name__ == "__main__":
    unittest.main()
