import unittest
import tempfile
import json
from pathlib import Path

from flask import Flask

import modules.end_to_end_automation.web.routes.end_to_end_automation as routes
from core.config import RESULT_DIR
from modules.end_to_end_automation.src import e2e


class RouteTests(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.orig_result = RESULT_DIR
        e2e.RESULT_DIR = Path(self.tmpdir.name)

        app = Flask(__name__)
        app.register_blueprint(routes.bp)
        app.testing = True
        self.client = app.test_client()

    def tearDown(self):
        e2e.RESULT_DIR = self.orig_result
        try:
            self.tmpdir.cleanup()
        except Exception:
            pass

    def test_download_script_route(self):
        # prepare run via helper so script exists
        prepared = e2e.prepare_e2e_run("", "testrun", [], False, "", cases=[{"ID": "1", "Title": "x"}], base_url="https://example.com")
        resp = self.client.get(f"/api/runs/{prepared['run_name']}/download-script")
        self.assertEqual(resp.status_code, 200)
        # some clients may report python mime types
        self.assertIn(resp.mimetype, ("application/octet-stream", "text/x-python", "text/plain"))
        self.assertIn(b"import csv", resp.data)

    def test_create_automation_job_api_manual(self):
        # send manual JSON payload (no base_url field anymore)
        cases = [{"ID": "z", "Title": "Z"}]
        data = {"cases_json": json.dumps(cases)}
        resp = self.client.post("/api/automation-jobs", data=data)
        payload = resp.get_json()
        self.assertTrue(payload.get("ok"))
        self.assertIn("job", payload)
        run_name = payload["job"]["run_name"]
        self.assertTrue(run_name)
        # simulate prepare to verify no errors
        e2e.prepare_e2e_run("", run_name, [], False, "", cases=cases, base_url="https://example.com")
        self.assertTrue((e2e.RESULT_DIR / run_name).exists())

    def test_create_automation_job_api_file_upload(self):
        import io
        csv_content = "ID,Title\n1,One\n"
        data = {
            "cases_file": (io.BytesIO(csv_content.encode("utf-8")), "cases.csv"),
        }
        resp = self.client.post("/api/automation-jobs", data=data, content_type="multipart/form-data")
        payload = resp.get_json()
        self.assertTrue(payload.get("ok"))
        self.assertIn("job", payload)
        run_name = payload["job"]["run_name"]
        self.assertTrue(run_name)
        # verify run dir is creatable
        e2e.prepare_e2e_run("", run_name, [], False, "", csv_text=csv_content)
        self.assertTrue((e2e.RESULT_DIR / run_name).exists())


if __name__ == "__main__":
    unittest.main()
