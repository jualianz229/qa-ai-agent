import tempfile
import unittest
from io import BytesIO
from pathlib import Path

from werkzeug.datastructures import FileStorage

from core.instruction_templates import (
    ensure_instruction_templates,
    list_instruction_templates,
    load_instruction_template,
    resolve_instruction_template,
    save_uploaded_template,
)


class InstructionTemplateTests(unittest.TestCase):
    def test_default_templates_are_created_and_loadable(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "instructions"
            ensure_instruction_templates(target)

            items = list_instruction_templates(target)

            self.assertGreaterEqual(len(items), 3)
            loaded = load_instruction_template("basic_smoke.txt", target)
            self.assertIn("critical user flows", loaded["content"])
            self.assertEqual(resolve_instruction_template("basic_smoke", target).name, "basic_smoke.txt")

    def test_uploaded_template_is_saved_with_safe_name(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "instructions"
            uploaded = FileStorage(
                stream=BytesIO(b"Focus on profile and phone fields.\n"),
                filename="profile focus!.txt",
                name="template_file",
            )

            saved = save_uploaded_template(uploaded, target)

            self.assertTrue((target / saved["name"]).exists())
            self.assertTrue(saved["name"].endswith(".txt"))
            self.assertIn("profile_focus", saved["name"])


if __name__ == "__main__":
    unittest.main()
