import unittest

from core.ai_engine import AIEngine


class AIEngineNormalizationTests(unittest.TestCase):
    def test_normalize_automation_value_maps_yes_to_auto(self):
        engine = AIEngine.__new__(AIEngine)
        self.assertEqual(engine._normalize_automation_value("yes"), "auto")
        self.assertEqual(engine._normalize_automation_value("manual"), "manual")
        self.assertEqual(engine._normalize_automation_value("semi"), "semi-auto")


if __name__ == "__main__":
    unittest.main()
