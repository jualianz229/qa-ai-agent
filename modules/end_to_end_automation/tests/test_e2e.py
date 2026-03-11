import tempfile
import unittest
import json
from pathlib import Path

from core.config import RESULT_DIR
from modules.end_to_end_automation.src import e2e


class E2ETests(unittest.TestCase):
    def setUp(self):
        # point RESULT_DIR at a temporary folder to avoid polluting workspace
        self.tmpdir = tempfile.TemporaryDirectory()
        self.original_result_dir = RESULT_DIR
        # monkeypatch the constant (simple override)
        e2e.RESULT_DIR = Path(self.tmpdir.name)

    def tearDown(self):
        e2e.RESULT_DIR = self.original_result_dir
        self.tmpdir.cleanup()

    def test_parse_csv_rows_empty(self):
        self.assertEqual(e2e._parse_csv_rows(""), [])

    def test_parse_csv_rows_basic(self):
        csv = "ID,Title\n1,First\n2,Second\n"
        rows = e2e._parse_csv_rows(csv)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["ID"], "1")
        self.assertEqual(rows[1]["Title"], "Second")

    def test_create_job_with_manual_cases(self):
        cases = [{"ID": "a", "Title": "A"}, {"ID": "b", "Title": "B"}]
        payload = {"cases": cases, "run_name": "foo"}
        job = e2e.create_e2e_job(payload)
        self.assertIn("id", job)
        self.assertEqual(job["run_name"], "foo")

        # run directory should exist and contain script after job completes (synchronous?)
        # since job runs in background thread we can't easily wait; instead call prepare directly
        prepared = e2e.prepare_e2e_run("", "foo", [], False, "", cases=cases, base_url="https://x")
        self.assertTrue(prepared["script_path"].exists())
        # the script should be a python file
        self.assertTrue(prepared["script_path"].suffix, ".py")

    def test_download_script_raises_notfound(self):
        with self.assertRaises(FileNotFoundError):
            e2e.download_script("nonexistent")


if __name__ == "__main__":
    unittest.main()
