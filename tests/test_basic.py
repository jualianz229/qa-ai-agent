import os
import sys

# Add project root to sys.path so 'core' and other modules can be found
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

def test_imports():
    """Basic test to verify that core modules can be imported correctly."""
    try:
        from core.config import RESULT_DIR
        print(f"Import successful: core.config.RESULT_DIR = {RESULT_DIR}")
        
        from core.utils import load_json_file
        print("Import successful: core.utils.load_json_file")
        
        from core.dashboard_data import dashboard_metrics
        print("Import successful: core.dashboard_data.dashboard_metrics")
        
    except ImportError as e:
        assert False, f"Failed to import core modules: {e}"

def test_workdir():
    """Verify that the working directory is set correctly in CI."""
    cwd = os.getcwd()
    print(f"Current working directory: {cwd}")
    # In GitHub Actions, we expect to be in the repo root
    assert os.path.exists("core"), "Directory 'core' should exist in the root."
    assert os.path.exists("website"), "Directory 'website' should exist in the root."
