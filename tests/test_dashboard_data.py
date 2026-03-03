import csv
import json
import tempfile
import unittest
from pathlib import Path

from core.dashboard_data import (
    build_benchmark_snapshot,
    build_knowledge_snapshot,
    build_run_comparison,
    build_run_detail,
    build_run_summary,
    list_runs,
    safe_run_artifact,
)


class DashboardDataTests(unittest.TestCase):
    def test_build_run_summary_collects_status_and_videos(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "example_run"
            learned_dir = Path(tmp) / "site_profiles" / "learned"
            (run_dir / "JSON").mkdir(parents=True)
            (run_dir / "Evidence" / "Video").mkdir(parents=True)
            learned_dir.mkdir(parents=True)
            (run_dir / "JSON" / "raw_scan_example.json").write_text(
                json.dumps({"title": "Example", "url": "https://example.com"}),
                encoding="utf-8",
            )
            (run_dir / "JSON" / "Page_Scope_example.json").write_text(
                json.dumps({"page_type": "form", "confidence": 0.75, "scope_summary": "Form page"}),
                encoding="utf-8",
            )
            (run_dir / "JSON" / "Execution_Results.json").write_text(
                json.dumps({"results": [{"id": "TC-1", "status": "passed", "fact_ids": ["component::search"], "grounding_score": 0.74}, {"id": "TC-2", "status": "failed"}]}),
                encoding="utf-8",
            )
            (run_dir / "JSON" / "Scenario_Validation_example.json").write_text(
                json.dumps(
                    {
                        "valid_cases": [
                            {
                                "ID": "TC-1",
                                "_grounding": {
                                    "fact_ids": ["component::search"],
                                    "coverage_score": 0.82,
                                    "summary": "component:Search",
                                    "mentioned_surfaces": ["search"],
                                    "covered_surfaces": ["search"],
                                    "refs": [{"fact_id": "component::search", "source_type": "component", "source_label": "Search"}],
                                },
                            }
                        ],
                        "rejected_cases": [],
                        "unsupported_surface_report": {"unsupported_requested_surfaces": ["auth"], "avoid_surfaces": [], "instruction_conflicts": []},
                    }
                ),
                encoding="utf-8",
            )
            (run_dir / "JSON" / "Execution_Plan_example.json").write_text(
                json.dumps(
                    {
                        "plans": [
                            {
                                "id": "TC-1",
                                "scenario_grounding": {"fact_ids": ["component::search"]},
                                "grounding_summary": {"average_step_fact_coverage_score": 0.77},
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            (run_dir / "JSON" / "Contradiction_Analysis.json").write_text(
                json.dumps({"summary": {"contradiction_count": 1}, "issues": [{"case_id": "TC-1", "message": "search coverage mismatch"}]}),
                encoding="utf-8",
            )
            (run_dir / "JSON" / "Execution_Network.json").write_text(
                json.dumps(
                    {
                        "network_entries": [
                            {
                                "id": "TC-1",
                                "summary": {
                                    "request_count": 3,
                                    "response_count": 3,
                                    "failing_response_count": 1,
                                    "top_endpoints": [{"path": "/api/search", "hits": 3}],
                                },
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            (run_dir / "JSON" / "Execution_Learning.json").write_text(
                json.dumps({"learning_entries": [{"id": "TC-2"}]}),
                encoding="utf-8",
            )
            (run_dir / "JSON" / "Execution_Checkpoints.json").write_text(
                json.dumps({"checkpoints": [{"id": "TC-3"}]}),
                encoding="utf-8",
            )
            (learned_dir / "_global.json").write_text(
                json.dumps(
                    {
                        "learning": {
                            "field_selectors": {"username": ["input[name='user-name']"]},
                            "selector_stats": {
                                "field_selectors": {
                                    "username": {
                                        "input[name='user-name']": {"successes": 2, "score": 5.2}
                                    }
                                }
                            },
                        }
                    }
                ),
                encoding="utf-8",
            )
            (learned_dir / "example.com.json").write_text(
                json.dumps(
                    {
                        "learning": {
                            "action_selectors": {"login": ["button:has-text('Login')"]},
                            "selector_stats": {
                                "action_selectors": {
                                    "login": {
                                        "button:has-text('Login')": {"successes": 1, "score": 3.4}
                                    }
                                }
                            },
                        }
                    }
                ),
                encoding="utf-8",
            )
            (run_dir / "Evidence" / "Video" / "TC-1.webm").write_text("demo", encoding="utf-8")
            csv_path = run_dir / "example.csv"
            with csv_path.open("w", encoding="utf-8-sig", newline="") as handle:
                writer = csv.DictWriter(
                    handle,
                    fieldnames=["ID", "Title", "Steps to Reproduce", "Automation", "Priority", "Severity", "Execution Status"],
                )
                writer.writeheader()
                writer.writerow(
                    {
                        "ID": "TC-1",
                        "Title": "Demo",
                        "Steps to Reproduce": "1. Open the site https://example.com 2. Click Sign in 3. Verify dashboard",
                        "Automation": "auto",
                        "Priority": "P1",
                        "Severity": "High",
                        "Execution Status": "passed",
                    }
                )

            summary = build_run_summary(run_dir)
            detail = build_run_detail(run_dir)
            snapshot = build_knowledge_snapshot("https://example.com/login", profiles_dir=Path(tmp) / "site_profiles")

            self.assertEqual(summary["title"], "Example")
            self.assertEqual(summary["page_type"], "form")
            self.assertEqual(summary["video_count"], 1)
            self.assertEqual(summary["status_counts"]["passed"], 1)
            self.assertEqual(summary["status_counts"]["failed"], 1)
            self.assertEqual(summary["network_summary"]["request_count"], 3)
            self.assertEqual(summary["network_summary"]["top_endpoints"][0]["path"], "/api/search")
            self.assertIn("confidence_class", summary)
            self.assertIn("confidence_explanation", summary)
            self.assertTrue(summary["confidence_explanation"])
            self.assertIn("source_trust_detail", summary)
            self.assertIn("negative_evidence_detail", summary)
            self.assertEqual(detail["csv_rows"][0]["ID"], "TC-1")
            self.assertEqual(detail["case_rows"][0]["status"], "passed")
            self.assertEqual(detail["case_rows"][0]["automation"], "auto")
            self.assertTrue(detail["case_rows"][0]["is_p1p2"])
            self.assertEqual(
                detail["case_rows"][0]["steps"],
                ["Open the site https://example.com", "Click Sign in", "Verify dashboard"],
            )
            self.assertEqual(detail["filter_options"]["priorities"][0]["value"], "p1")
            self.assertEqual(detail["filter_options"]["severities"][0]["value"], "high")
            self.assertIn("knowledge_snapshot", detail)
            self.assertEqual(detail["case_rows"][0]["scenario_fact_ids"], ["component::search"])
            self.assertEqual(detail["case_rows"][0]["contradiction_count"], 1)
            self.assertIn("auth", detail["guardrail_summary"]["unsupported_requested_surfaces"])
            self.assertEqual(detail["visual_signature"]["heading_count"], 0)
            self.assertEqual(detail["visual_signature"]["link_count"], 0)
            self.assertGreaterEqual(snapshot["global"]["field_selector_count"], 1)

    def test_list_runs_and_safe_artifact(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run_dir = root / "run_one"
            (run_dir / "JSON").mkdir(parents=True)
            file_path = run_dir / "JSON" / "sample.json"
            file_path.write_text("{}", encoding="utf-8")

            runs = list_runs(root)
            artifact = safe_run_artifact("run_one", "JSON/sample.json", root)

            self.assertEqual(len(runs), 1)
            self.assertEqual(runs[0]["run_name"], "run_one")
            self.assertEqual(artifact, file_path.resolve())

    def test_build_run_comparison_and_benchmark_snapshot(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for index, status in enumerate(("failed", "passed"), start=1):
                run_dir = root / f"run_{index}"
                (run_dir / "JSON").mkdir(parents=True)
                (run_dir / "JSON" / f"raw_scan_run_{index}.json").write_text(
                    json.dumps(
                        {
                            "title": f"Run {index}",
                            "url": "https://example.com",
                            "headings": [{"text": "Landing"}],
                            "buttons": ["Search"],
                            "links": [{"text": "More", "href": "/more"}],
                            "sections": [{"heading": "Main", "text": "Body"}],
                            "forms": [],
                            "texts": ["Search content"],
                            "page_fingerprint": {"sampled_page_count": 1},
                        }
                    ),
                    encoding="utf-8",
                )
                (run_dir / "JSON" / f"Page_Scope_run_{index}.json").write_text(
                    json.dumps(
                        {
                            "page_type": "listing",
                            "confidence": 0.8 + (index * 0.05),
                            "scope_summary": "Listing page",
                            "key_modules": ["Search"],
                            "critical_user_flows": ["Use search"],
                        }
                    ),
                    encoding="utf-8",
                )
                (run_dir / "JSON" / f"Normalized_Page_Model_run_{index}.json").write_text(
                    json.dumps(
                        {
                            "component_catalog": [{"type": "search"}],
                            "components": [{"type": "search"}],
                            "heuristic_scope": {
                                "likely_page_type": "listing",
                                "priority_modules": ["Search"],
                                "recommended_flows": ["Use search"],
                            },
                            "page_facts": {"search": True},
                            "possible_flows": [{"name": "use_search"}],
                        }
                    ),
                    encoding="utf-8",
                )
                (run_dir / "JSON" / "Execution_Results.json").write_text(
                    json.dumps({"results": [{"id": "TC-1", "status": status}]}),
                    encoding="utf-8",
                )
                (run_dir / "JSON" / "Contradiction_Analysis.json").write_text(
                    json.dumps({"summary": {"contradiction_count": index - 1}, "issues": []}),
                    encoding="utf-8",
                )
                with (run_dir / f"run_{index}.csv").open("w", encoding="utf-8-sig", newline="") as handle:
                    writer = csv.DictWriter(
                        handle,
                        fieldnames=["ID", "Title", "Steps to Reproduce", "Automation", "Priority", "Severity", "Execution Status"],
                    )
                    writer.writeheader()
                    writer.writerow(
                        {
                            "ID": "TC-1",
                            "Title": f"Case {index}",
                            "Steps to Reproduce": "1. Open the site https://example.com\n2. Input 'news' into the 'Search' field.\n3. Click the 'Search' button.",
                            "Automation": "auto",
                            "Priority": "P2",
                            "Severity": "Medium",
                            "Execution Status": status,
                        }
                    )

            comparison = build_run_comparison(root / "run_1", root / "run_2", root)
            benchmark = build_benchmark_snapshot(root, limit=4)

            self.assertEqual(comparison["delta"]["passed"], 1)
            self.assertEqual(comparison["delta"]["failed"], -1)
            self.assertIn("confidence_diff", comparison)
            self.assertIn("anti_hallu_delta", comparison)
            self.assertEqual(benchmark["total_cases"], 2)
            self.assertGreaterEqual(benchmark["average_confidence"], 0)
            self.assertIn("average_source_trust", benchmark)
            self.assertIn("average_stability", benchmark)

    def test_build_benchmark_snapshot_returns_complete_empty_shape(self):
        with tempfile.TemporaryDirectory() as tmp:
            benchmark = build_benchmark_snapshot(Path(tmp), limit=4)

            self.assertEqual(benchmark["total_cases"], 0)
            self.assertEqual(benchmark["results"], [])
            self.assertEqual(benchmark["cluster_keys"], [])
            self.assertEqual(benchmark["average_confidence"], 0)


if __name__ == "__main__":
    unittest.main()
