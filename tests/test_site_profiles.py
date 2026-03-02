import json
import tempfile
import unittest
from pathlib import Path

from core.site_profiles import load_site_profile


class SiteProfileTests(unittest.TestCase):
    def test_load_site_profile_merges_default_and_domain_override(self):
        with tempfile.TemporaryDirectory() as tmp:
            profile_dir = Path(tmp)
            (profile_dir / "_default.json").write_text(
                json.dumps({"interaction": {"step_delay_ms": 800}, "execution": {"auto_dismiss_consent": False}}),
                encoding="utf-8",
            )
            (profile_dir / "example.com.json").write_text(
                json.dumps({"interaction": {"settle_delay_ms": 1500}, "auth": {"storage_state_candidates": ["auth/custom.json"]}}),
                encoding="utf-8",
            )

            profile = load_site_profile("https://example.com/login", profiles_dir=profile_dir)

            self.assertEqual(profile["interaction"]["step_delay_ms"], 800)
            self.assertEqual(profile["interaction"]["settle_delay_ms"], 1500)
            self.assertFalse(profile["execution"]["auto_dismiss_consent"])
            self.assertIn("auth/custom.json", profile["auth"]["storage_state_candidates"])
            self.assertEqual(profile["resolved_host"], "example.com")


if __name__ == "__main__":
    unittest.main()
