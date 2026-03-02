import unittest

from core.guardrails import validate_execution_plan, validate_page_scope, validate_test_scenarios


class GuardrailTests(unittest.TestCase):
    def setUp(self):
        self.page_info = {
            "headings": [{"text": "News Portal"}],
            "texts": ["Browse latest stories", "Search stories by keyword"],
            "page_fingerprint": {
                "has_search": True,
                "has_filters": False,
                "has_pagination": False,
                "has_auth_pattern": False,
                "has_table": False,
                "has_form": False,
                "has_navigation": True,
                "has_article_like_sections": True,
                "has_listing_pattern": True,
            },
        }
        self.page_model = {
            "components": [
                {"type": "search"},
                {"type": "navigation"},
                {"type": "content"},
                {"type": "listing"},
            ],
            "actions": [{"type": "open_url"}],
            "entities": [{"type": "heading", "value": "News Portal"}],
            "possible_flows": [{"name": "open_page"}],
        }

    def test_validate_page_scope_filters_unsupported_modules(self):
        page_scope = {
            "page_type": "login page",
            "primary_goal": "Authenticate users",
            "key_modules": ["Login Form", "Search", "Navigation"],
            "critical_user_flows": ["Login with valid username and password", "Use search"],
            "priority_areas": ["Authentication errors", "Search"],
            "risks": ["Invalid credentials handling"],
            "scope_summary": "Users sign in and browse content.",
            "confidence": 0.84,
        }
        validated = validate_page_scope(page_scope, self.page_model, self.page_info)

        self.assertFalse(validated["is_valid"])
        self.assertIn("Search", validated["page_scope"]["key_modules"])
        self.assertNotIn("Login Form", validated["page_scope"]["key_modules"])
        self.assertLess(validated["page_scope"]["confidence"], 0.84)

    def test_validate_test_scenarios_rejects_out_of_context_cases(self):
        test_cases = [
            {
                "ID": "AUTH-001",
                "Module": "Authentication",
                "Category": "Functional",
                "Test Type": "Positive",
                "Title": "Login with valid credentials",
                "Precondition": "",
                "Steps to Reproduce": "1. Open the site https://example.com/news\n2. Input 'demo' into the 'Username' field.\n3. Input 'secret' into the 'Password' field.\n4. Click the 'Login' button.",
                "Expected Result": "The user should be redirected after login.",
                "Actual Result": "",
                "Severity": "High",
                "Priority": "P1",
                "Evidence": "",
                "Automation": "auto",
            },
            {
                "ID": "SRH-001",
                "Module": "Search",
                "Category": "Functional",
                "Test Type": "Positive",
                "Title": "Search stories by keyword",
                "Precondition": "",
                "Steps to Reproduce": "1. Open the site https://example.com/news\n2. Input 'final' into the 'Search' field.\n3. Click the 'Search' button.",
                "Expected Result": "Relevant stories should be displayed.",
                "Actual Result": "",
                "Severity": "Medium",
                "Priority": "P2",
                "Evidence": "",
                "Automation": "auto",
            },
        ]

        validated = validate_test_scenarios(test_cases, self.page_model, {"key_modules": ["Search"]}, self.page_info)

        self.assertEqual(len(validated["valid_cases"]), 1)
        self.assertEqual(validated["valid_cases"][0]["ID"], "SRH-001")
        self.assertEqual(len(validated["rejected_cases"]), 1)
        self.assertIn("AUTH-001", validated["issues"][0])

    def test_validate_execution_plan_rejects_invalid_fill_actions(self):
        execution_plan = {
            "plans": [
                {
                    "id": "AUTH-001",
                    "actions": [{"type": "select", "target": "Role", "value": "Admin"}],
                    "assertions": [],
                },
                {
                    "id": "SRH-001",
                    "actions": [{"type": "click", "target": "Search", "role": "button"}],
                    "assertions": [{"type": "assert_text_visible", "value": "Results"}],
                },
            ]
        }

        validated = validate_execution_plan(execution_plan, self.page_model, self.page_info)

        self.assertTrue(validated["is_valid"])
        self.assertEqual(len(validated["valid_plan"]["plans"]), 1)
        self.assertEqual(validated["valid_plan"]["plans"][0]["id"], "SRH-001")
        self.assertEqual(len(validated["rejected_plans"]), 1)

    def test_validate_execution_plan_allows_auth_redirect_assertion(self):
        execution_plan = {
            "plans": [
                {
                    "id": "FRM-001",
                    "actions": [{"type": "click", "target": "Login", "role": "button"}],
                    "assertions": [{"type": "assert_url_contains", "value": "inventory"}],
                }
            ]
        }
        page_info = {
            "headings": [{"text": "Login"}],
            "texts": ["Username", "Password"],
            "page_fingerprint": {
                "has_search": False,
                "has_filters": False,
                "has_pagination": False,
                "has_auth_pattern": True,
                "has_table": False,
                "has_form": True,
                "has_navigation": False,
                "has_article_like_sections": False,
                "has_listing_pattern": False,
            },
        }
        page_model = {
            "components": [{"type": "form"}],
            "actions": [{"type": "click"}],
            "entities": [],
            "possible_flows": [{"name": "submit_form"}],
            "action_ontology": {
                "click": {},
                "assert_url_contains": {},
            },
        }

        validated = validate_execution_plan(execution_plan, page_model, page_info)

        self.assertTrue(validated["is_valid"])
        self.assertEqual(len(validated["valid_plan"]["plans"]), 1)


if __name__ == "__main__":
    unittest.main()
