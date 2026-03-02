import json
import tempfile
import unittest
from pathlib import Path

from core.site_profiles import get_failure_memory, get_ranked_selector_candidates, load_knowledge_bank_snapshot, load_site_profile, merge_execution_learning


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

    def test_merge_execution_learning_persists_learned_selectors(self):
        with tempfile.TemporaryDirectory() as tmp:
            profile_dir = Path(tmp)
            learned_path = merge_execution_learning(
                "https://example.com/login",
                {
                    "learning_entries": [
                        {
                            "resolved_selector": "input[name='user-name']",
                            "details": {
                                "field_key": "username",
                                "semantic_type": "username",
                                "semantic_label": "Username",
                                "target": "Username",
                            },
                        }
                    ]
                },
                profiles_dir=profile_dir,
            )

            self.assertIsNotNone(learned_path)
            self.assertTrue(learned_path["global_path"].endswith("_global.json"))
            profile = load_site_profile("https://example.com/login", profiles_dir=profile_dir)
            self.assertIn("input[name='user-name']", profile["learning"]["field_selectors"]["username"])
            self.assertIn("input[name='user-name']", profile["learning"]["action_selectors"]["username"])
            self.assertIn("username", profile["knowledge_bank"]["field_keys"])

    def test_global_knowledge_bank_is_reused_across_different_hosts(self):
        with tempfile.TemporaryDirectory() as tmp:
            profile_dir = Path(tmp)
            merge_execution_learning(
                "https://example.com/login",
                {
                    "learning_entries": [
                        {
                            "resolved_selector": "input[name='phone-number']",
                            "details": {
                                "field_key": "phone_number",
                                "semantic_type": "phone_number",
                                "semantic_label": "Phone Number",
                                "target": "Phone Number",
                            },
                        }
                    ]
                },
                profiles_dir=profile_dir,
            )

            other_profile = load_site_profile("https://another-site.org/contact", profiles_dir=profile_dir)

            self.assertIn("input[name='phone-number']", other_profile["learning"]["field_selectors"]["phone_number"])

    def test_failure_memory_and_scoring_are_recorded(self):
        with tempfile.TemporaryDirectory() as tmp:
            profile_dir = Path(tmp)
            merge_execution_learning(
                "https://example.com/login",
                {
                    "learning_entries": [
                        {
                            "status": "passed",
                            "resolved_selector": "input[name='user-name']",
                            "attempted": ["page|input[name='user']"],
                            "details": {
                                "field_key": "username",
                                "semantic_type": "username",
                                "semantic_label": "Username",
                                "target": "Username",
                            },
                        },
                        {
                            "status": "failed",
                            "resolved_selector": "",
                            "attempted": ["page|input[name='email']", "page|input[name='user']"],
                            "error": "unable to resolve",
                            "details": {
                                "field_key": "username",
                                "semantic_type": "username",
                                "semantic_label": "Username",
                                "target": "Username",
                            },
                        },
                    ]
                },
                profiles_dir=profile_dir,
            )

            profile = load_site_profile("https://example.com/login", profiles_dir=profile_dir)
            learning = profile["learning"]
            ranked = get_ranked_selector_candidates(learning, "field_selectors", "username", limit=3)
            failures = get_failure_memory(learning, "field_selectors", "username", limit=5)

            self.assertEqual(ranked[0], "input[name='user-name']")
            self.assertTrue(any(item["selector"] == "input[name='email']" for item in failures))
            self.assertIn("username", learning["selector_stats"]["field_selectors"])

    def test_knowledge_snapshot_exposes_global_and_domain_views(self):
        with tempfile.TemporaryDirectory() as tmp:
            profile_dir = Path(tmp)
            merge_execution_learning(
                "https://example.com/login",
                {
                    "learning_entries": [
                        {
                            "status": "passed",
                            "resolved_selector": "button:has-text('Login')",
                            "details": {
                                "target": "Login",
                                "semantic_type": "login_button",
                            },
                        }
                    ]
                },
                profiles_dir=profile_dir,
            )

            snapshot = load_knowledge_bank_snapshot("https://example.com/login", profiles_dir=profile_dir)

            self.assertEqual(snapshot["host"], "example.com")
            self.assertGreaterEqual(snapshot["global"]["action_selector_count"], 1)
            self.assertGreaterEqual(snapshot["domain"]["action_selector_count"], 1)


if __name__ == "__main__":
    unittest.main()
