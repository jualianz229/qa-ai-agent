import pytest
import os
import sys
import json
from pathlib import Path

# Add project root to sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from core.utils import atomic_write_json, load_json_file
from core.jobs import create_job, update_job

@pytest.fixture(autouse=True)
def clean_jobs():
    """Ensure a clean jobs dictionary for every test."""
    from core.jobs import jobs, jobs_lock
    with jobs_lock:
        jobs.clear()
    yield

class TestCoreSystems:
    """Complex tests for Core logic to prevent regressions."""

    def test_atomic_write_and_cache_consistency(self, tmp_path):
        """Verify that atomic_write_json and cached load_json_file work together correctly."""
        test_file = tmp_path / "test_data.json"
        data1 = {"key": "value1", "version": 1}
        
        # 1. Write data
        atomic_write_json(test_file, data1)
        assert test_file.exists()
        
        # 2. Read data (should be cached now)
        read_data1 = load_json_file(test_file)
        assert read_data1 == data1
        
        # 3. Write new data
        data2 = {"key": "value2", "version": 2}
        atomic_write_json(test_file, data2)
        
        # 4. Read again (should detect mtime change and invalidate cache)
        read_data2 = load_json_file(test_file)
        assert read_data2 == data2
        assert read_data2["version"] == 2

    def test_job_persistence(self, tmp_path, monkeypatch):
        """Test creating, updating, and persisting jobs to a mock file."""
        # 1. Mock RESULT_DIR and _JOBS_FILE paths in core.jobs
        monkeypatch.setattr("core.jobs.RESULT_DIR", tmp_path)
        mock_jobs_file = tmp_path / "jobs.json"
        monkeypatch.setattr("core.jobs._JOBS_FILE", mock_jobs_file)

        # 2. Mock generate_run_name so we don't depend on complex logic
        monkeypatch.setattr("core.jobs.generate_run_name", lambda url: "test_run_123")
        
        # 3. Create Job (this will use the patched paths)
        payload = {
            "url": "https://example.com",
            "mode": "test_mode",
            "crawl_limit": 1,
            "csv_sep": ","
        }
        
        job = create_job(payload)
        job_id = job["id"]
        
        # 4. Verify disk persistence
        assert mock_jobs_file.exists(), "Jobs file should be created on disk"
        disk_content = load_json_file(mock_jobs_file)
        assert job_id in disk_content
        
        # 5. Update Job and check disk again
        update_job(job_id, status="completed")
        disk_content_v2 = load_json_file(mock_jobs_file)
        assert disk_content_v2[job_id]["status"] == "completed"

    def test_url_normalization_logic(self):
        """Test URL utility normalization behavior."""
        from core.utils import normalize_input_url
        
        # Test basic protocol addition
        assert normalize_input_url("google.com") == "https://google.com"
        
        # Test fragment removal
        normalized = normalize_input_url("https://sub.domain.com/path?query=1#frag")
        assert "#frag" not in normalized
        assert "query=1" in normalized

    def test_scanner_naming_logic(self, tmp_path):
        """Test technical naming / directory resolution logic in Scanner context using real temp paths."""
        from core.scanner import Scanner
        
        base_dir = tmp_path / "ResultsRoot"
        base_dir.mkdir()
        scanner = Scanner(reports_dir=str(base_dir))
        
        # Case 1: Standard URL
        # URL netloc "my-app.com" -> replace "www." -> "my-app.com"
        # re.sub(r"[^\w]", "_") -> "my_app_com"
        safe, ts, path = scanner._build_run_context("https://my-app.com/login")
        assert safe == "my_app_com"
        # Use str() or resolve() to ensure path comparison is clean across OS
        assert str(path.parent.resolve()) == str(base_dir.resolve())
        
        # Case 2: Custom run name
        safe2, ts2, path2 = scanner._build_run_context("https://google.com", run_name="manual_test")
        assert path2.name == "manual_test"
        assert str(path2.parent.resolve()) == str(base_dir.resolve())
