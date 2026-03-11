import io
import json
import os
import tempfile
from pathlib import Path

import pytest

# add project root to sys.path
import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from website.dashboard import app as flask_app
from core.config import RESULT_DIR
from core.jobs import jobs, jobs_lock


@pytest.fixture(autouse=True)
def clean_jobs():
    with jobs_lock:
        jobs.clear()
    yield


def test_automation_job_file_json(tmp_path):
    # ensure result dir is writable
    with tempfile.TemporaryDirectory() as d:
        flask_app.config['RESULT_DIR'] = d
        client = flask_app.test_client()
        sample = [{"id": "C1", "title": "Case one"}]
        data = {
            'cases_file': (io.BytesIO(json.dumps(sample).encode('utf-8')), 'cases.json'),
            'executor_headed': 'no',
        }
        resp = client.post('/api/automation-jobs', data=data, content_type='multipart/form-data')
        assert resp.status_code == 200
        payload = resp.get_json()
        assert payload.get('ok')
        assert 'job' in payload
        assert payload['job']['payload']['csv_text'] is None
        assert payload['job']['payload']['cases'] == sample


def test_automation_job_file_csv(tmp_path):
    with tempfile.TemporaryDirectory() as d:
        flask_app.config['RESULT_DIR'] = d
        client = flask_app.test_client()
        csv_content = "id,title\nX,The X case"
        data = {
            'cases_file': (io.BytesIO(csv_content.encode('utf-8')), 'cases.csv'),
            'executor_headed': 'yes',
        }
        resp = client.post('/api/automation-jobs', data=data, content_type='multipart/form-data')
        assert resp.status_code == 200
        payload = resp.get_json()
        assert payload.get('ok')
        assert payload['job']['payload']['csv_text'] == csv_content
        assert payload['job']['payload']['cases'] is None
