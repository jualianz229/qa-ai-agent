import csv
import inspect
import json
import tempfile
import unittest
from pathlib import Path

from bs4 import BeautifulSoup
from PIL import Image

from core.artifacts import visual_regression_path
from core.scanner import Scanner


class ScannerTests(unittest.TestCase):
    def setUp(self):
        self.scanner = Scanner(reports_dir="Result")

    def test_select_internal_candidates_prefers_internal_links(self):
        root_info = {
            "navigation": [[
                {"text": "News", "href": "/news"},
                {"text": "About", "href": "/about"},
            ]],
            "links": [
                {"text": "Read More", "href": "/news/1"},
                {"text": "External", "href": "https://outside.com/page"},
                {"text": "Image", "href": "/hero.jpg"},
            ],
        }
        candidates = self.scanner._select_internal_candidates(
            "https://example.com",
            root_info,
            3,
            {"link_selection": {"blacklist_terms": ["about"], "priority_terms": ["detail", "read"]}},
        )
        urls = [item["url"] for item in candidates]
        self.assertIn("https://example.com/news", urls)
        self.assertIn("https://example.com/news/1", urls)
        self.assertFalse(any("outside.com" in candidate for candidate in urls))
        self.assertTrue(all(item.get("reasons") for item in candidates))

    def test_combine_fingerprints_merges_boolean_and_counts(self):
        combined = self.scanner._combine_fingerprints([
            {"has_search": True, "has_filters": False, "button_count": 1, "link_count": 3, "form_count": 0, "section_count": 1, "table_count": 0},
            {"has_search": False, "has_filters": True, "button_count": 4, "link_count": 2, "form_count": 1, "section_count": 2, "table_count": 1},
        ])
        self.assertTrue(combined["has_search"])
        self.assertTrue(combined["has_filters"])
        self.assertEqual(combined["button_count"], 4)
        self.assertEqual(combined["sampled_page_count"], 2)

    def test_update_csv_with_execution_results_updates_status_and_evidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            csv_path = Path(tmp) / "cases.csv"
            results_path = Path(tmp) / "Execution_Results.json"

            with csv_path.open("w", encoding="utf-8-sig", newline="") as handle:
                writer = csv.DictWriter(
                    handle,
                    fieldnames=["ID", "Actual Result", "Evidence", "Automation"],
                    delimiter=",",
                )
                writer.writeheader()
                writer.writerow({"ID": "TC-1", "Actual Result": "", "Evidence": "", "Automation": "auto"})

            results_path.write_text(
                json.dumps({"results": [{"id": "TC-1", "status": "passed", "error": ""}]}),
                encoding="utf-8",
            )

            self.scanner.update_csv_with_execution_results(csv_path, results_path, ",")

            content = csv_path.read_text(encoding="utf-8-sig")
            self.assertIn("Execution Status", content)
            self.assertIn("Executed successfully.", content)
            self.assertIn("Video/TC-1.webm", content)

    def test_update_csv_with_execution_results_handles_checkpoint_required(self):
        with tempfile.TemporaryDirectory() as tmp:
            csv_path = Path(tmp) / "cases.csv"
            results_path = Path(tmp) / "Execution_Results.json"

            with csv_path.open("w", encoding="utf-8-sig", newline="") as handle:
                writer = csv.DictWriter(
                    handle,
                    fieldnames=["ID", "Actual Result", "Evidence", "Automation"],
                    delimiter=",",
                )
                writer.writeheader()
                writer.writerow({"ID": "TC-2", "Actual Result": "", "Evidence": "", "Automation": "semi-auto"})

            results_path.write_text(
                json.dumps({"results": [{"id": "TC-2", "status": "checkpoint_required", "error": "OTP needed"}]}),
                encoding="utf-8",
            )

            self.scanner.update_csv_with_execution_results(csv_path, results_path, ",")

            content = csv_path.read_text(encoding="utf-8-sig")
            self.assertIn("OTP needed", content)
            self.assertIn("Video/TC-2.webm", content)

    def test_json_artifacts_are_saved_under_json_folder(self):
        with tempfile.TemporaryDirectory() as tmp:
            project_info = {
                "run_dir": tmp,
                "safe_name": "example",
                "timestamp": "20260302_120000",
            }

            scope_path = self.scanner.save_page_scope({"page_type": "form"}, project_info)
            crawled_path = self.scanner.save_crawled_pages({"url": "https://example.com", "crawled_pages": []}, project_info)
            raw_path = self.scanner.save_raw_scan({"url": "https://example.com"}, project_info)

            self.assertEqual(scope_path.parent.name, "JSON")
            self.assertEqual(crawled_path.parent.name, "JSON")
            self.assertEqual(raw_path.parent.name, "JSON")

    def test_save_visual_regression_artifacts_under_json_folder(self):
        with tempfile.TemporaryDirectory() as tmp:
            project_info = {"run_dir": tmp, "safe_name": "example", "timestamp": "20260302_120000"}
            page_info = {
                "url": "https://example.com",
                "vrt_testing": {
                    "baseline_run": "example_20260301_120000",
                    "has_baseline": True,
                    "has_changes": True,
                    "current_snapshot": {"element_count": 10, "elements": []},
                    "snapshot_summary": {"element_count": 10},
                    "diff_summary": {"total_changed": 3, "text_change_count": 1},
                    "changed_areas": [{"area": "main > h1", "change_type": "changed"}],
                },
                "visual_regression": {"status": "baseline_created", "has_baseline": False},
            }
            snapshot_path, diff_path = self.scanner.save_visual_regression_artifacts(page_info, project_info)
            self.assertEqual(snapshot_path.parent.name, "JSON")
            self.assertEqual(diff_path.parent.name, "JSON")
            snapshot_payload = json.loads(snapshot_path.read_text(encoding="utf-8"))
            diff_payload = json.loads(diff_path.read_text(encoding="utf-8"))
            visual_payload = json.loads(visual_regression_path(tmp, create=False).read_text(encoding="utf-8"))
            self.assertEqual(snapshot_payload["summary"]["element_count"], 10)
            self.assertEqual(diff_payload["summary"]["total_changed"], 3)
            self.assertEqual(visual_payload["status"], "baseline_created")

    def test_compare_visual_snapshots_reports_text_and_style_changes(self):
        baseline = {
            "elements": [
                {
                    "key": "k1",
                    "path": "main > h1:nth-of-type(1)",
                    "tag": "h1",
                    "text": "Welcome",
                    "rect": {"x": 10, "y": 10, "width": 200, "height": 40},
                    "style": {"font_size": "32px", "padding_top": "8px"},
                }
            ]
        }
        current = {
            "elements": [
                {
                    "key": "k1",
                    "path": "main > h1:nth-of-type(1)",
                    "tag": "h1",
                    "text": "Welcome back",
                    "rect": {"x": 10, "y": 10, "width": 220, "height": 40},
                    "style": {"font_size": "30px", "padding_top": "12px"},
                },
                {
                    "key": "k2",
                    "path": "main > p:nth-of-type(1)",
                    "tag": "p",
                    "text": "New paragraph",
                    "rect": {"x": 10, "y": 60, "width": 220, "height": 20},
                    "style": {"font_size": "16px", "padding_top": "0px"},
                },
            ]
        }
        diff = self.scanner._compare_visual_snapshots(current, baseline)
        self.assertGreaterEqual(diff["summary"]["total_changed"], 2)
        self.assertGreaterEqual(diff["summary"]["text_change_count"], 1)
        self.assertGreaterEqual(diff["summary"]["style_change_count"], 1)
        self.assertGreaterEqual(diff["summary"]["layout_change_count"], 1)

    def test_compare_visual_images_reports_pixel_diff(self):
        with tempfile.TemporaryDirectory() as tmp:
            current = Path(tmp) / "current.png"
            baseline = Path(tmp) / "baseline.png"
            diff_out = Path(tmp) / "diff.png"
            Image.new("RGBA", (40, 40), (255, 255, 255, 255)).save(baseline)
            img = Image.new("RGBA", (40, 40), (255, 255, 255, 255))
            img.putpixel((10, 10), (255, 0, 0, 255))
            img.save(current)
            result = self.scanner._compare_visual_images(current, baseline, diff_out, threshold_ratio=0.0)
            self.assertTrue(result["has_difference"])
            self.assertGreater(result["changed_pixels"], 0)
            self.assertTrue(diff_out.exists())

    def test_normalize_csv_text_compacts_numbered_steps_and_uses_single_quotes(self):
        raw = '1. Open the site https://example.com\n\n2. Input "demo" into the "user-name" field.\n\n3. Click the "Login" button.'
        normalized = self.scanner._normalize_csv_text(raw, numbered=True)
        self.assertEqual(
            normalized,
            "1. Open the site https://example.com\n2. Input 'demo' into the 'user-name' field.\n3. Click the 'Login' button."
        )

    def test_normalize_csv_text_removes_windows_double_breaks(self):
        raw = "1. Open the site https://example.com\r\r\n2. Input 'demo' into the Username field.\r\r\n3. Click the Login button."
        normalized = self.scanner._normalize_csv_text(raw, numbered=True)
        self.assertEqual(
            normalized,
            "1. Open the site https://example.com\n2. Input 'demo' into the Username field.\n3. Click the Login button."
        )

    def test_extract_form_info_collects_rich_field_metadata(self):
        html = """
        <section id="contact-panel">
          <h2>Contact Support</h2>
          <form id="contact-form" method="post">
            <label for="contact-phone">Phone Number</label>
            <input id="contact-phone" name="username-username" type="tel" placeholder="Input phone number" autocomplete="tel" aria-label="Phone Number" required />
            <button type="submit">Send</button>
          </form>
        </section>
        """
        soup = BeautifulSoup(html, "html.parser")
        form = soup.find("form")

        form_info = self.scanner._extract_form_info(form, soup)

        self.assertEqual(form_info["submit_texts"][0], "Send")
        self.assertEqual(form_info["fields"][0]["label"], "Phone Number")
        self.assertEqual(form_info["fields"][0]["autocomplete"], "tel")
        self.assertEqual(form_info["fields"][0]["name"], "username-username")
        self.assertTrue(form_info["fields"][0]["required"])
        self.assertFalse(form_info["fields"][0]["contenteditable"])
        self.assertEqual(form_info["container_heading"], "Contact Support")
        self.assertTrue(form_info["fields"][0]["container_hints"])
        self.assertIn("section", form_info["fields"][0]["dom_path"])

    def test_extract_visual_components_detects_tabs_and_breadcrumb(self):
        html = """
        <nav aria-label="breadcrumb"><a href="/">Home</a><a href="/news">News</a></nav>
        <div role="tablist"><button role="tab">Overview</button><button role="tab">Stats</button></div>
        """
        soup = BeautifulSoup(html, "html.parser")

        components = self.scanner._extract_visual_components(soup)
        types = {component["type"] for component in components}

        self.assertIn("breadcrumb", types)
        self.assertIn("tabs", types)

    def test_extract_visual_components_detects_navigation_menu_dialog_and_listbox(self):
        html = """
        <nav aria-label="Primary Navigation">
          <a href="/home">Home</a>
          <button aria-haspopup="menu">Open Menu</button>
        </nav>
        <div role="dialog" aria-label="Quick view"><button>Close</button></div>
        <button role="combobox" aria-haspopup="listbox" aria-label="Category"></button>
        """
        soup = BeautifulSoup(html, "html.parser")

        components = self.scanner._extract_visual_components(soup)
        component_types = {component["type"] for component in components}
        labels = " | ".join(component.get("label", "") for component in components)

        self.assertIn("navigation_menu", component_types)
        self.assertIn("dialog", component_types)
        self.assertIn("listbox", component_types)
        self.assertIn("Primary Navigation", labels)

    def test_extract_modern_components_and_standalone_controls(self):
        html = """
        <div role="combobox" aria-label="Category"></div>
        <input type="file" id="resume-upload" aria-label="Resume Upload" />
        <div contenteditable="true" class="ql-editor" aria-label="Article Body"></div>
        <div class="toast" role="status">Saved successfully</div>
        <aside class="drawer">Open navigation</aside>
        <div class="swiper">Slide 1</div>
        <div id="cookie-banner"><button>Accept cookies</button></div>
        <iframe title="Payment widget" src="https://widget.example.com"></iframe>
        <input autocomplete="one-time-code" name="otp-code" />
        <button>Continue with Google</button>
        <div aria-live="polite">Live score update</div>
        """
        soup = BeautifulSoup(html, "html.parser")

        components = self.scanner._extract_visual_components(soup)
        controls = self.scanner._extract_standalone_controls(soup)
        fingerprint = self.scanner._build_page_fingerprint(
            soup,
            {"texts": [], "buttons": [], "links": [], "forms": [], "standalone_controls": controls, "navigation": [], "sections": [], "lists": [], "tables": []},
            {
                "has_toast": True,
                "has_drawer": True,
                "has_carousel": True,
                "has_iframe": True,
                "has_cookie_banner": True,
                "has_otp_flow": True,
                "has_sso": True,
                "has_live_updates": True,
            },
        )

        component_types = {component["type"] for component in components}
        control_widgets = {control["widget"] for control in controls}

        self.assertIn("combobox", component_types)
        self.assertIn("file_upload", component_types)
        self.assertIn("rich_text_editor", component_types)
        self.assertIn("toast", component_types)
        self.assertIn("drawer", component_types)
        self.assertIn("carousel", component_types)
        self.assertIn("consent_banner", component_types)
        self.assertIn("iframe", component_types)
        self.assertIn("otp_verification", component_types)
        self.assertIn("sso_login", component_types)
        self.assertIn("live_feed", component_types)
        self.assertIn("combobox", control_widgets)
        self.assertIn("upload", control_widgets)
        self.assertIn("rich_text", control_widgets)
        self.assertTrue(fingerprint["has_combobox"])
        self.assertTrue(fingerprint["has_upload"])
        self.assertTrue(fingerprint["has_rich_text"])
        self.assertTrue(fingerprint["has_otp_flow"])
        self.assertTrue(fingerprint["has_sso"])
        self.assertTrue(fingerprint["has_live_updates"])

    def test_extract_page_info_collects_action_labels_and_link_context(self):
        html = """
        <main>
          <section aria-label="Hero Banner">
            <h2>Welcome</h2>
            <a href="/pricing" title="View pricing"></a>
            <button aria-label="Start Trial"></button>
          </section>
          <div>
            <span id="email-label">Work Email</span>
            <input type="email" aria-labelledby="email-label" title="Email field" />
          </div>
        </main>
        """
        soup = BeautifulSoup(html, "html.parser")
        info = self.scanner._empty_page_info("https://example.com")

        self.scanner._extract_page_info(soup, info, runtime_info={})

        self.assertIn("View pricing", [item["text"] for item in info["links"]])
        self.assertEqual(info["links"][0]["context"], "Welcome")
        self.assertIn("Start Trial", info["buttons"])
        self.assertIn("Start Trial", info["sections"][0]["action_labels"])
        self.assertEqual(info["forms"], [])
        standalone = self.scanner._extract_standalone_controls(soup)
        self.assertEqual(standalone[0]["aria_labelledby"], "Work Email")
        self.assertEqual(standalone[0]["title"], "Email field")

    def test_build_section_graph_includes_action_labels_and_aria_label(self):
        html = """
        <section aria-label="Search panel">
          <h2>Search Stories</h2>
          <a href="/stories">Browse stories</a>
          <button>Search</button>
        </section>
        """
        soup = BeautifulSoup(html, "html.parser")

        graph = self.scanner._build_section_graph(soup)
        node = graph["nodes"][0]

        self.assertEqual(node["aria_label"], "Search panel")
        self.assertIn("Browse stories", node["action_labels"])
        self.assertIn("Search", node["action_labels"])

    def test_build_section_graph_and_stateful_probe_configs(self):
        html = """
        <main id="app-main">
          <section id="hero">
            <h1>Hero Banner</h1>
            <button>Get Started</button>
          </section>
          <section id="search-panel">
            <h2>Search Area</h2>
            <form><input type="search" name="q" /><button>Search</button></form>
          </section>
        </main>
        """
        soup = BeautifulSoup(html, "html.parser")

        graph = self.scanner._build_section_graph(soup)
        probe_types = {item["type"] for item in self.scanner._stateful_probe_configs()}

        self.assertGreaterEqual(len(graph["nodes"]), 2)
        self.assertTrue(any(node["heading"] == "Hero Banner" for node in graph["nodes"]))
        self.assertIn("combobox", probe_types)
        self.assertIn("menu", probe_types)
        self.assertIn("datepicker", probe_types)
        self.assertIn("carousel", probe_types)
        self.assertIn("async_drawer", probe_types)
        self.assertIn("type: 'infinite_scroll'", inspect.getsource(self.scanner._discover_stateful_interactions))


if __name__ == "__main__":
    unittest.main()
