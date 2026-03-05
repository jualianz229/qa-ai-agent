import unittest

from core.planner import build_execution_plan, build_normalized_page_model


class PlannerTests(unittest.TestCase):
    def test_build_normalized_page_model_detects_components(self):
        page_info = {
            "url": "https://example.com/news",
            "title": "News",
            "metadata": {"title": "News"},
            "headings": [{"tag": "h1", "text": "News Portal"}],
            "texts": ["Search latest news", "Filter by category"],
            "buttons": ["Search", "Filter"],
            "links": [{"text": "Read More", "href": "/news/1"}],
            "forms": [{
                "id": "search-form",
                "name": "searchForm",
                "action": "/search",
                "method": "get",
                "submit_texts": ["Search"],
                "context_text": "Search latest news",
                "fields": [
                    {
                        "tag": "input",
                        "type": "search",
                        "name": "keyword",
                        "id": "news-keyword",
                        "placeholder": "Search news",
                        "aria_label": "Search",
                        "autocomplete": "",
                        "inputmode": "",
                        "label": "Search",
                        "required": False,
                        "pattern": "",
                        "maxlength": "",
                        "minlength": "",
                        "data_testid": "search-input",
                        "class_tokens": ["search-field"],
                        "options": [],
                        "context_text": "Search latest news",
                        "semantic_text": "Search keyword search news",
                    }
                ],
            }],
            "images": [],
            "apis": [],
            "sections": [{"heading": "Top Story", "text": "Long content block"}],
            "section_graph": {
                "nodes": [
                    {
                        "block_id": "hero",
                        "tag": "section",
                        "heading": "Top Story",
                        "text": "Long content block",
                        "dom_path": "main > section#hero",
                        "parent_block_id": "",
                        "link_count": 1,
                        "button_count": 1,
                        "field_count": 1,
                    }
                ],
                "edges": [],
            },
            "tables": [],
            "lists": [["Item 1", "Item 2", "Item 3", "Item 4", "Item 5", "Item 6"]],
            "navigation": [[{"text": "Home", "href": "/"}, {"text": "News", "href": "/news"}]],
            "page_fingerprint": {
                "has_search": True,
                "has_filters": True,
                "has_pagination": False,
                "has_auth_pattern": False,
                "has_table": False,
                "has_form": True,
                "has_navigation": True,
                "has_article_like_sections": True,
                "has_listing_pattern": True,
                "button_count": 2,
                "link_count": 1,
                "form_count": 1,
                "section_count": 1,
                "table_count": 0,
            },
            "crawled_pages": [],
        }
        model = build_normalized_page_model(page_info)
        component_types = {component["type"] for component in model["components"]}
        self.assertIn("search", component_types)
        self.assertIn("filter", component_types)
        self.assertIn("form", component_types)
        self.assertIn("navigation", component_types)
        self.assertIn("state_graph", model)
        self.assertIn("session_model", model)
        self.assertIn("runtime_observer", model)
        self.assertIn("page_facts", model)
        self.assertIn("section_graph", model)
        self.assertEqual(model["section_graph"]["nodes"][0]["block_id"], "hero")
        self.assertGreaterEqual(len(model["state_graph"]["states"]), 2)
        self.assertEqual(model["field_catalog"][0]["semantic_type"], "search_query")
        self.assertIn("search", [alias.lower() for alias in model["field_catalog"][0]["aliases"]])
        self.assertTrue(model["field_catalog"][0]["container_hints"])
        self.assertTrue(model["field_catalog"][0]["learned_path_hints"] is not None)
        self.assertIn("component_catalog", model)

    def test_build_execution_plan_generates_actions_and_assertions(self):
        page_info = {
            "url": "https://example.com/contact",
            "title": "Contact",
            "metadata": {"title": "Contact"},
            "headings": [{"tag": "h1", "text": "Contact Us"}],
            "texts": ["Share your phone number"],
            "buttons": ["Submit"],
            "links": [],
            "forms": [{
                "id": "contact-form",
                "name": "contactForm",
                "action": "/submit",
                "method": "post",
                "submit_texts": ["Submit"],
                "context_text": "Contact form",
                "container_heading": "Contact form",
                "container_text": "Share your phone number",
                "dom_path": "main > section#contact > form#contact-form",
                "container_hints": ["Contact form", "Share your phone number"],
                "fields": [
                    {
                        "tag": "input",
                        "type": "tel",
                        "name": "contact_phone",
                        "id": "contact-phone",
                        "placeholder": "Phone number",
                        "aria_label": "Phone Number",
                        "autocomplete": "tel",
                        "inputmode": "tel",
                        "label": "Phone Number",
                        "required": True,
                        "pattern": "",
                        "maxlength": "",
                        "minlength": "",
                        "data_testid": "phone-input",
                        "class_tokens": ["phone-field"],
                        "options": [],
                        "context_text": "Phone Number",
                        "semantic_text": "Phone Number contact phone tel",
                        "container_hints": ["Contact form", "Phone Number"],
                        "nearby_texts": ["Share your phone number"],
                        "dom_path": "main > section#contact > form#contact-form > input#contact-phone",
                    }
                ],
            }],
            "images": [],
            "apis": [
                "https://example.com/api/contact/submit",
                "https://example.com/graphql",
            ],
            "sections": [],
            "tables": [],
            "lists": [],
            "navigation": [],
            "page_fingerprint": {
                "has_search": False,
                "has_filters": False,
                "has_pagination": False,
                "has_auth_pattern": False,
                "has_table": False,
                "has_form": True,
                "has_navigation": False,
                "has_article_like_sections": False,
                "has_listing_pattern": False,
                "button_count": 1,
                "link_count": 0,
                "form_count": 1,
                "section_count": 0,
                "table_count": 0,
            },
            "crawled_pages": [],
        }
        model = build_normalized_page_model(page_info)
        cases = [
            {
                "ID": "NAV-001",
                "Title": "Submit phone number",
                "Automation": "auto",
                "Steps to Reproduce": "1. Open the site https://example.com/contact\n2. Input '08123' into the 'phone number' field.\n3. Click the 'Submit' button.",
                "Expected Result": "The form should display the text 'Success'. The contact API request should return 200 without error, stay same-origin, and use the approved endpoint allowlist. GraphQL should not return errors.",
                "_grounding": {
                    "fact_ids": ["field::phone_number", "component::form"],
                    "score": 0.83,
                    "summary": "field:Phone Number; component:Form",
                },
                "_task_alignment": {
                    "score": 0.88,
                    "focus_hits": ["form", "phone number"],
                },
            }
        ]
        plan = build_execution_plan(
            cases,
            model,
            "https://example.com/contact",
            site_profile={"network": {"allowed_hosts": ["cdn.example.com"], "cross_origin_mode": "same-origin"}},
        )
        self.assertEqual(plan["plans"][0]["target_url"], "https://example.com/contact")
        action_types = [action["type"] for action in plan["plans"][0]["actions"]]
        assertion_types = [assertion["type"] for assertion in plan["plans"][0]["assertions"]]
        self.assertIn("click", action_types)
        self.assertIn("fill", action_types)
        self.assertIn("assert_text_visible", assertion_types)
        self.assertIn("assert_network_seen", assertion_types)
        self.assertIn("assert_network_status_ok", assertion_types)
        self.assertIn("assert_graphql_ok", assertion_types)
        self.assertIn("assert_endpoint_allowlist", assertion_types)
        self.assertIn("assert_cross_origin_safe", assertion_types)
        self.assertIn("state_targets", plan["plans"][0])
        self.assertIn("session_strategy", plan["plans"][0])
        self.assertIn("orchestration", plan["plans"][0])
        self.assertIn("interaction_hints", plan["plans"][0])
        self.assertIn("network_policy", plan)
        self.assertIn("expected_request_map", plan["plans"][0])
        self.assertEqual(plan["network_policy"]["base_host"], "example.com")
        self.assertIn("https://example.com/api/contact/submit", plan["plans"][0]["expected_request_map"]["expected_endpoints"])
        fill_action = next(action for action in plan["plans"][0]["actions"] if action["type"] == "fill")
        self.assertEqual(fill_action["semantic_type"], "phone_number")
        self.assertTrue(fill_action["selector_candidates"])
        self.assertTrue(fill_action["container_hints"])
        self.assertIn("form_key", fill_action["grounding_refs"][0])
        self.assertTrue(fill_action["evidence_refs"])
        self.assertTrue(fill_action["evidence_summary"])
        self.assertIn("evidence_trace", plan["plans"][0])
        self.assertGreaterEqual(plan["plans"][0]["evidence_trace"]["grounded_action_count"], 1)
        self.assertEqual(plan["plans"][0]["scenario_grounding"]["fact_ids"], ["field::phone_number", "component::form"])
        self.assertEqual(plan["plans"][0]["scenario_alignment"]["score"], 0.88)
        self.assertIn("scenario_fact_coverage_score", plan["plans"][0]["grounding_summary"])
        self.assertGreaterEqual(plan["plans"][0]["grounding_summary"]["scenario_fact_coverage_score"], 0.0)
        self.assertIn("average_step_fact_coverage_score", plan["plans"][0]["grounding_summary"])
        self.assertGreaterEqual(fill_action["fact_coverage_score"], 0.5)

    def test_build_execution_plan_supports_component_flows_and_richer_assertions(self):
        page_info = {
            "url": "https://example.com/dashboard",
            "title": "Dashboard",
            "metadata": {"title": "Dashboard"},
            "headings": [{"tag": "h1", "text": "Dashboard Overview"}],
            "texts": ["Switch between tabs to inspect analytics"],
            "buttons": [],
            "links": [],
            "forms": [],
            "images": [],
            "apis": [],
            "sections": [],
            "tables": [],
            "lists": [],
            "navigation": [],
            "visual_components": [
                {"type": "tabs", "label": "Overview", "items": ["Overview", "Stats"]},
                {"type": "modal", "label": "Filters", "items": ["Apply", "Close"]},
            ],
            "page_fingerprint": {
                "has_search": False,
                "has_filters": False,
                "has_pagination": False,
                "has_auth_pattern": False,
                "has_table": False,
                "has_form": False,
                "has_navigation": False,
                "has_article_like_sections": False,
                "has_listing_pattern": False,
                "button_count": 0,
                "link_count": 0,
                "form_count": 0,
                "section_count": 0,
                "table_count": 0,
            },
            "crawled_pages": [],
        }
        model = build_normalized_page_model(page_info)
        cases = [
            {
                "ID": "CMP-001",
                "Title": "Open overview tab",
                "Automation": "auto",
                "Steps to Reproduce": "1. Open the site https://example.com/dashboard\n2. Click the 'Overview' tab.",
                "Expected Result": "The page title should contain 'Dashboard'.",
            },
            {
                "ID": "CMP-002",
                "Title": "Show modal feedback",
                "Automation": "auto",
                "Steps to Reproduce": "1. Open the site https://example.com/dashboard",
                "Expected Result": "The modal or dialog should be visible.",
            },
        ]

        plan = build_execution_plan(cases, model, "https://example.com/dashboard")

        component_types = {component["type"] for component in model["component_catalog"]}
        flow_names = {flow["name"] for flow in model["possible_flows"]}
        self.assertIn("tabs", component_types)
        self.assertIn("modal", component_types)
        self.assertIn("switch_tabs", flow_names)
        self.assertIn("assert_title_contains", [item["type"] for item in plan["plans"][0]["assertions"]])
        self.assertIn("assert_any_text_visible", [item["type"] for item in plan["plans"][1]["assertions"]])

    def test_build_execution_plan_parses_unquoted_field_and_button_steps(self):
        page_info = {
            "url": "https://example.com/login",
            "title": "Login",
            "metadata": {"title": "Login"},
            "headings": [{"tag": "h1", "text": "Login"}],
            "texts": ["Username", "Password"],
            "buttons": ["Login"],
            "links": [],
            "forms": [{
                "id": "login-form",
                "name": "loginForm",
                "action": "/login",
                "method": "post",
                "submit_texts": ["Login"],
                "context_text": "Login form",
                "fields": [
                    {
                        "tag": "input",
                        "type": "text",
                        "name": "user-name",
                        "id": "user-name",
                        "placeholder": "Username",
                        "aria_label": "Username",
                        "autocomplete": "username",
                        "inputmode": "",
                        "label": "Username",
                        "required": True,
                        "pattern": "",
                        "maxlength": "",
                        "minlength": "",
                        "data_testid": "",
                        "class_tokens": [],
                        "options": [],
                        "context_text": "Username",
                        "semantic_text": "Username user-name username",
                    },
                    {
                        "tag": "input",
                        "type": "password",
                        "name": "password",
                        "id": "password",
                        "placeholder": "Password",
                        "aria_label": "Password",
                        "autocomplete": "current-password",
                        "inputmode": "",
                        "label": "Password",
                        "required": True,
                        "pattern": "",
                        "maxlength": "",
                        "minlength": "",
                        "data_testid": "",
                        "class_tokens": [],
                        "options": [],
                        "context_text": "Password",
                        "semantic_text": "Password current-password",
                    },
                ],
            }],
            "images": [],
            "apis": [],
            "sections": [],
            "tables": [],
            "lists": [],
            "navigation": [],
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
                "button_count": 1,
                "link_count": 0,
                "form_count": 1,
                "section_count": 0,
                "table_count": 0,
            },
            "crawled_pages": [],
        }
        model = build_normalized_page_model(page_info)
        cases = [
            {
                "ID": "FRM-001",
                "Title": "Successful Login",
                "Automation": "auto",
                "Steps to Reproduce": "1. Open the site https://example.com/login\n2. Input 'standard_user' into the Username field.\n3. Input 'secret_sauce' into the Password field.\n4. Click the Login button.",
                "Expected Result": "The user is redirected to the inventory page.",
            }
        ]

        plan = build_execution_plan(cases, model, "https://example.com/login")
        action_types = [action["type"] for action in plan["plans"][0]["actions"]]
        action_targets = [action.get("target", "") for action in plan["plans"][0]["actions"]]

        self.assertEqual(action_types, ["fill", "fill", "click"])
        self.assertEqual(plan["version"], 3)
        self.assertIn("Username", action_targets)
        self.assertIn("Password", action_targets)
        self.assertIn("Login", action_targets)
        self.assertTrue(all(action.get("grounded") for action in plan["plans"][0]["actions"]))
        self.assertGreater(plan["plans"][0]["grounding_summary"]["coverage"], 0.9)

    def test_build_execution_plan_supports_modern_standalone_controls(self):
        page_info = {
            "url": "https://example.com/editor",
            "title": "Editor",
            "metadata": {"title": "Editor"},
            "headings": [{"tag": "h1", "text": "Create Article"}],
            "texts": ["Upload cover image", "Write the article body"],
            "buttons": ["Publish"],
            "links": [],
            "forms": [],
            "standalone_controls": [
                {
                    "tag": "input",
                    "type": "file",
                    "role": "",
                    "widget": "upload",
                    "name": "cover_image",
                    "id": "cover-image",
                    "placeholder": "",
                    "aria_label": "Cover Image",
                    "autocomplete": "",
                    "list_id": "",
                    "label": "Cover Image",
                    "required": False,
                    "pattern": "",
                    "maxlength": "",
                    "minlength": "",
                    "data_testid": "cover-upload",
                    "class_tokens": ["upload-field"],
                    "contenteditable": False,
                    "accept": ".png,.jpg",
                    "multiple": False,
                    "options": [],
                    "context_text": "Upload cover image",
                    "semantic_text": "upload cover image",
                },
                {
                    "tag": "div",
                    "type": "contenteditable",
                    "role": "textbox",
                    "widget": "rich_text",
                    "name": "body_editor",
                    "id": "body-editor",
                    "placeholder": "Write here",
                    "aria_label": "Article Body",
                    "autocomplete": "",
                    "list_id": "",
                    "label": "Article Body",
                    "required": True,
                    "pattern": "",
                    "maxlength": "",
                    "minlength": "",
                    "data_testid": "body-editor",
                    "class_tokens": ["ql-editor"],
                    "contenteditable": True,
                    "accept": "",
                    "multiple": False,
                    "options": [],
                    "context_text": "Article body editor",
                    "semantic_text": "article body editor rich text",
                },
            ],
            "images": [],
            "apis": [],
            "sections": [],
            "tables": [],
            "lists": [],
            "navigation": [],
            "visual_components": [
                {"type": "file_upload", "label": "Cover Image"},
                {"type": "rich_text_editor", "label": "Article Body"},
                {"type": "drawer", "label": "Menu"},
            ],
            "page_fingerprint": {
                "has_search": False,
                "has_filters": False,
                "has_pagination": False,
                "has_auth_pattern": False,
                "has_table": False,
                "has_form": False,
                "has_standalone_controls": True,
                "has_navigation": False,
                "has_article_like_sections": False,
                "has_listing_pattern": False,
                "has_upload": True,
                "has_rich_text": True,
                "has_drawer": True,
                "button_count": 1,
                "link_count": 0,
                "form_count": 0,
                "standalone_control_count": 2,
                "section_count": 0,
                "table_count": 0,
            },
            "crawled_pages": [],
        }
        model = build_normalized_page_model(page_info)
        cases = [
            {
                "ID": "EDT-001",
                "Title": "Upload cover image and write body",
                "Automation": "auto",
                "Steps to Reproduce": "1. Open the site https://example.com/editor\n2. Upload 'fixtures/cover.png' into the 'Cover Image' field.\n3. Input 'Hello world' into the 'Article Body' field.\n4. Click the 'Publish' button.",
                "Expected Result": "A success notification should be displayed.",
            }
        ]

        plan = build_execution_plan(cases, model, "https://example.com/editor")
        action_types = [action["type"] for action in plan["plans"][0]["actions"]]
        input_kinds = [action.get("input_kind", "") for action in plan["plans"][0]["actions"]]
        component_types = {component["type"] for component in model["component_catalog"]}

        self.assertIn("upload", action_types)
        self.assertIn("fill", action_types)
        self.assertIn("rich_text", input_kinds)
        self.assertIn("file_upload", component_types)
        self.assertIn("rich_text_editor", component_types)
        self.assertIn("open_close_drawer", {flow["name"] for flow in model["possible_flows"]})

    def test_build_execution_plan_adds_checkpoints_and_pre_actions_for_auth_surface(self):
        page_info = {
            "url": "https://example.com/login",
            "title": "Login",
            "metadata": {"title": "Login"},
            "headings": [{"tag": "h1", "text": "Login"}],
            "texts": ["Enter OTP after login", "Continue with Google"],
            "buttons": ["Continue with Google", "Accept cookies"],
            "links": [],
            "forms": [{
                "id": "login-form",
                "name": "loginForm",
                "action": "/login",
                "method": "post",
                "submit_texts": ["Login"],
                "context_text": "Login form",
                "fields": [
                    {
                        "tag": "input",
                        "type": "text",
                        "name": "otp_code",
                        "id": "otp-code",
                        "placeholder": "OTP",
                        "aria_label": "OTP",
                        "autocomplete": "one-time-code",
                        "inputmode": "",
                        "label": "OTP",
                        "required": True,
                        "pattern": "",
                        "maxlength": "",
                        "minlength": "",
                        "data_testid": "",
                        "class_tokens": [],
                        "options": [],
                        "context_text": "OTP code",
                        "semantic_text": "otp verification code",
                    }
                ],
            }],
            "images": [],
            "apis": [],
            "sections": [],
            "tables": [],
            "lists": [],
            "navigation": [],
            "visual_components": [
                {"type": "consent_banner", "label": "Accept cookies", "items": ["Accept cookies"]},
                {"type": "sso_login", "label": "Continue with Google", "items": ["Continue with Google"]},
                {"type": "otp_verification", "label": "OTP", "items": ["OTP"]},
            ],
            "runtime_signals": {"websocket_count": 1},
            "page_fingerprint": {
                "has_form": True,
                "has_auth_pattern": True,
                "has_cookie_banner": True,
                "has_otp_flow": True,
                "has_sso": True,
                "has_auth_checkpoint": True,
                "has_live_updates": True,
            },
            "crawled_pages": [],
            "site_profile": {"execution": {"auto_dismiss_consent": True}},
        }
        model = build_normalized_page_model(page_info)
        cases = [
            {
                "ID": "AUTH-001",
                "Title": "Verify login with OTP",
                "Automation": "auto",
                "Steps to Reproduce": "1. Open the site https://example.com/login\n2. Click the 'Continue with Google' button.",
                "Expected Result": "The user is redirected for verification.",
            }
        ]

        plan = build_execution_plan(cases, model, "https://example.com/login", site_profile=page_info["site_profile"])
        plan_item = plan["plans"][0]

        self.assertTrue(plan_item["pre_actions"])
        self.assertTrue(plan_item["checkpoints"])
        self.assertEqual(plan_item["orchestration"]["mode"], "semi-auto")

    def test_build_normalized_page_model_preserves_discovered_states(self):
        page_info = {
            "url": "https://example.com/catalog",
            "title": "Catalog",
            "metadata": {"title": "Catalog"},
            "headings": [{"tag": "h1", "text": "Catalog"}],
            "texts": ["Browse products"],
            "buttons": [],
            "links": [],
            "forms": [],
            "images": [],
            "apis": [],
            "sections": [],
            "tables": [],
            "lists": [],
            "navigation": [],
            "page_fingerprint": {"has_listing_pattern": True},
            "discovered_states": [
                {
                    "state_id": "tabs_details",
                    "label": "After tabs interaction: Details",
                    "trigger_action": "click",
                    "trigger_label": "Details",
                }
            ],
            "interaction_probes": [{"type": "tabs", "label": "Details", "changed": True}],
            "crawled_pages": [],
        }

        model = build_normalized_page_model(page_info)

        state_ids = {state["id"] for state in model["state_graph"]["states"]}
        flow_names = {flow["name"] for flow in model["possible_flows"]}

        self.assertIn("tabs_details", state_ids)
        self.assertIn("tabs_details", flow_names)
        self.assertEqual(model["runtime_observer"]["stateful_probe_count"], 1)

    def test_build_execution_plan_uses_conservative_mode_for_weak_grounding(self):
        page_info = {
            "url": "https://example.com/content",
            "title": "Content",
            "metadata": {"title": "Content"},
            "headings": [{"tag": "h1", "text": "Content"}],
            "texts": ["Read article"],
            "buttons": ["Read more"],
            "links": [{"text": "Read more", "href": "/article"}],
            "forms": [],
            "images": [],
            "apis": [],
            "sections": [{"heading": "Story", "text": "Read article details"}],
            "tables": [],
            "lists": [],
            "navigation": [],
            "page_fingerprint": {
                "has_search": False,
                "has_filters": False,
                "has_pagination": False,
                "has_auth_pattern": False,
                "has_table": False,
                "has_form": False,
                "has_navigation": False,
                "has_article_like_sections": True,
                "has_listing_pattern": False,
            },
            "crawled_pages": [],
        }
        model = build_normalized_page_model(page_info)
        cases = [
            {
                "ID": "CNT-001",
                "Title": "Review content",
                "Module": "Content",
                "Category": "Functional",
                "Test Type": "Positive",
                "Automation": "auto",
                "Steps to Reproduce": "1. Open the site https://example.com/content\n2. Click the 'Read more' button.",
                "Expected Result": "The content should be visible.",
                "_grounding": {"fact_ids": [], "score": 0.2, "coverage_score": 0.2, "ref_count": 0},
                "_task_alignment": {"score": 0.2},
            }
        ]

        plan = build_execution_plan(cases, model, "https://example.com/content")
        plan_item = plan["plans"][0]

        self.assertEqual(plan_item["planning_mode"], "conservative")
        self.assertTrue(plan_item["orchestration"]["conservative"])
        self.assertTrue(plan_item["checkpoints"])
        self.assertLessEqual(len(plan_item["actions"]), 2)
        self.assertEqual(plan_item["grounding_summary"]["planning_mode"], "conservative")


if __name__ == "__main__":
    unittest.main()
