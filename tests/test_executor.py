import py_compile
import tempfile
import unittest
from pathlib import Path

from core.artifacts import json_artifact_path
from core.executor import CodeGenerator


class ExecutorTests(unittest.TestCase):
    def test_generate_pom_script_respects_headless_and_select_resolver(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            plan_path = json_artifact_path(run_dir, "Execution_Plan_test.json")
            plan_path.write_text('{"base_url": "https://example.com", "plans": []}', encoding="utf-8")

            generator = CodeGenerator(ai_engine=None)
            script_path = generator.generate_pom_script({"run_dir": str(run_dir)}, plan_path, headless=False)
            content = script_path.read_text(encoding="utf-8")

            self.assertIn("HEADLESS = False", content)
            self.assertIn("def _resolve_select", content)
            self.assertIn("self._resolve_select", content)
            self.assertIn('input[name="{raw}"]', content)
            self.assertIn("def _attribute_token_locators", content)
            self.assertIn('[name*="{token}" i]', content)
            self.assertIn("JSON_DIR = RUN_DIR / \"JSON\"", content)
            self.assertIn("DEBUG_FILE = JSON_DIR / \"Execution_Debug.json\"", content)
            self.assertIn("EXECUTION_PLAN_FILE = JSON_DIR / \"Execution_Plan_test.json\"", content)
            self.assertIn("LEARNING_FILE = JSON_DIR / \"Execution_Learning.json\"", content)
            self.assertIn("CHECKPOINT_FILE = JSON_DIR / \"Execution_Checkpoints.json\"", content)
            self.assertIn("class ActionResolutionError", content)
            self.assertIn("class CheckpointRequiredError", content)
            self.assertIn("assert_title_contains", content)
            self.assertIn("assert_any_text_visible", content)
            self.assertIn("debug_entries.append", content)
            self.assertIn("def _iter_contexts", content)
            self.assertIn("page.frames", content)
            self.assertIn("def _fill_control", content)
            self.assertIn("def _resolve_upload_target", content)
            self.assertIn("set_input_files", content)
            self.assertIn('[contenteditable="true"]', content)
            self.assertIn("elif action_type == \"hover\"", content)
            self.assertIn("elif action_type == \"scroll\"", content)
            self.assertIn("elif action_type == \"dismiss\"", content)
            self.assertIn("elif action_type == \"wait_for_text\"", content)
            self.assertIn("capture_runtime_state", content)
            self.assertIn("learning_entries.append", content)
            self.assertIn("checkpoint_entries.append", content)
            self.assertNotIn("PHOTO_DIR", content)
            self.assertNotIn("page.screenshot", content)
            self.assertIn("def show_step_overlay", content)
            self.assertIn("__qa_agent_step_overlay__", content)
            self.assertIn("video.save_as", content)
            self.assertIn("context.close()", content)
            self.assertIn("STEP_DELAY_MS", content)
            py_compile.compile(str(script_path), doraise=True)


if __name__ == "__main__":
    unittest.main()
