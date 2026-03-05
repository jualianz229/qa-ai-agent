import pytest
import os
import sys
import importlib.util
from pathlib import Path

# Add project root to sys.path
ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
sys.path.insert(0, ROOT_DIR)

class TestCodeIntegrity:
    """Tests to ensure the codebase itself is healthy and has no syntax/import errors."""

    def test_flask_app_initialization(self):
        """
        Verify that the Flask app can be initialized without crashing.
        This catches:
        - Circular imports
        - Missing blueprints
        - Syntax errors in routes
        - Configuration errors
        """
        from website.dashboard import app
        app.config['TESTING'] = True
        with app.test_client() as client:
            # Try to load the homepage to ensure templates and core routes are fine
            response = client.get('/')
            assert response.status_code in [200, 302]

    def test_module_import_integrity(self):
        """
        Dynamically try to import all core modules to ensure no hidden ImportErrors.
        """
        core_dir = Path(ROOT_DIR) / "core"
        py_files = list(core_dir.glob("*.py"))
        
        for py_file in py_files:
            if py_file.name == "__init__.py":
                continue
            
            module_name = f"core.{py_file.stem}"
            try:
                importlib.import_module(module_name)
            except Exception as e:
                pytest.fail(f"Module {module_name} failed to import: {e}")

    def test_critical_blueprints_registration(self):
        """Ensure all modular blueprints are registered in the Flask app."""
        from website.dashboard import app
        registered_blueprints = app.blueprints.keys()
        
        expected_bps = [
            'test_case_generator',
            'end_to_end_automation',
            'visual_regression_testing'
        ]
        
        for bp in expected_bps:
            assert bp in registered_blueprints, f"Blueprint '{bp}' is missing from Flask app!"

def test_requirements_consistency():
    """Check if all requirements in requirements.txt are actually installable (basic check)."""
    req_path = Path(ROOT_DIR) / "requirements.txt"
    assert req_path.exists(), "requirements.txt is missing!"
    content = req_path.read_text()
    assert "Flask" in content
    assert "playwright" in content
    assert "psutil" in content
    assert "filelock" in content
