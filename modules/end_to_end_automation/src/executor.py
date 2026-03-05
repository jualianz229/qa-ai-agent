import json
from pathlib import Path

from rich.console import Console

console = Console()


class CodeGenerator:
    def __init__(self, ai_engine):
        self.ai = ai_engine

    def generate_pom_script(self, project_info: dict, execution_plan_path: Path, headless: bool = True):
        """Generate an action-based Playwright runner from execution_plan.json."""
        run_dir = Path(project_info["run_dir"])
        scripts_dir = run_dir / "scripts"
        scripts_dir.mkdir(parents=True, exist_ok=True)
        script_file = scripts_dir / "pom_runner.py"

        plan = json.loads(Path(execution_plan_path).read_text(encoding="utf-8"))
        script_file.write_text(
            self._render_runner(execution_plan_path.name, plan.get("base_url", ""), headless),
            encoding="utf-8",
        )
        console.print(f"[green]  [OK] Action runner digenerate: {script_file}[/green]")
        return script_file

    def _render_runner(self, plan_filename: str, default_url: str, headless: bool) -> str:
        return f'''import csv
import json
import os
import re
import sys
import unicodedata
from pathlib import Path
from urllib.parse import urlparse

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import expect, sync_playwright


ROOT_DIR = Path(__file__).resolve().parents[2]
RUN_DIR = Path(__file__).resolve().parents[1]
VIDEO_DIR = RUN_DIR / "Evidence" / "Video"
JSON_DIR = RUN_DIR / "JSON"
AUTH_FILE = ROOT_DIR / "auth" / "auth_state.json"
EXECUTION_PLAN_FILE = JSON_DIR / "{plan_filename}"
RESULT_FILE = JSON_DIR / "Execution_Results.json"
DEBUG_FILE = JSON_DIR / "Execution_Debug.json"
LEARNING_FILE = JSON_DIR / "Execution_Learning.json"
CHECKPOINT_FILE = JSON_DIR / "Execution_Checkpoints.json"
NETWORK_FILE = JSON_DIR / "Execution_Network.json"
ANTI_HALLUCINATION_AUDIT_FILE = JSON_DIR / "Anti_Hallucination_Audit.json"
ALLOW_LOW_ANTI_HALLU_ENV = "QA_AI_ALLOW_LOW_ANTI_HALLU"
DEFAULT_URL = {default_url!r}
HEADLESS = {headless!r}
STEP_DELAY_MS = 700
SETTLE_DELAY_MS = 1000
FINAL_DELAY_MS = 1400
OVERLAY_LEAD_IN_MS = 220
OVERLAY_HOLD_MS = 320


def _cli_safe_text(value):
    text = str(value or "")
    replacements = {{
        "\\u2013": "-",
        "\\u2014": "-",
        "\\u2018": "'",
        "\\u2019": "'",
        "\\u201c": '"',
        "\\u201d": '"',
        "\\u2026": "...",
        "\\u2022": "*",
        "\\u21b5": " -> ",
    }}
    for source, target in replacements.items():
        text = text.replace(source, target)
    text = unicodedata.normalize("NFKD", text)
    text = text.encode("ascii", errors="replace").decode("ascii")
    return text


def safe_print(*parts):
    message = " ".join(_cli_safe_text(part) for part in parts)
    print(message, flush=True)


class ActionResolutionError(Exception):
    def __init__(self, message, debug):
        super().__init__(message)
        self.debug = debug


class CheckpointRequiredError(Exception):
    def __init__(self, checkpoint):
        message = checkpoint.get("reason", "Manual checkpoint required.")
        super().__init__(message)
        self.checkpoint = checkpoint


class ActionEngine:
    def __init__(self, page, settings=None):
        self.page = page
        self.settings = settings or {{}}
        self.step_delay_ms = int(self.settings.get("step_delay_ms", STEP_DELAY_MS))
        self.settle_delay_ms = int(self.settings.get("settle_delay_ms", SETTLE_DELAY_MS))
        self.final_delay_ms = int(self.settings.get("final_delay_ms", FINAL_DELAY_MS))
        self.overlay_lead_in_ms = int(self.settings.get("overlay_lead_in_ms", OVERLAY_LEAD_IN_MS))
        self.overlay_hold_ms = int(self.settings.get("overlay_hold_ms", OVERLAY_HOLD_MS))
        self.selector_memory = {{}}
        self.last_debug = {{}}
        self.network_observer = {{"entries": [], "policy": {{}}, "expected_request_map": {{}}}}

    def execute(self, action):
        action_type = action.get("type")
        self.last_debug = {{"stage": "action", "action": action}}
        if action_type in {{"fill", "select", "upload", "click", "dismiss", "hover", "scroll", "wait_for_text"}}:
            return self._execute_with_self_healing(action)
        if action_type == "checkpoint":
            raise CheckpointRequiredError(action)
        elif action_type == "inspect":
            return

    def _execute_with_self_healing(self, action):
        last_error = None
        for attempt_no, candidate in enumerate(self._action_variants(action), start=1):
            try:
                self.last_debug = {{
                    "stage": "action",
                    "action": candidate,
                    "attempt_no": attempt_no,
                    "self_healing": attempt_no > 1,
                    "original_action": action,
                }}
                self._execute_once(candidate)
                if attempt_no > 1:
                    self.last_debug = {{
                        **self.last_debug,
                        "self_healed": True,
                        "healed_target": candidate.get("target", ""),
                        "healed_role": candidate.get("role", ""),
                    }}
                return
            except ActionResolutionError as exc:
                last_error = exc
                self.last_debug = {{
                    **exc.debug,
                    "attempt_no": attempt_no,
                    "self_healing": attempt_no > 1,
                    "original_action": action,
                }}
                continue
        if action.get("type") == "dismiss" and last_error:
            return
        if last_error:
            raise last_error

    def _execute_once(self, action):
        action_type = action.get("type")
        if action_type == "fill":
            field = self._resolve_field(action)
            field.click()
            self._fill_control(field, action.get("value", ""), action)
        elif action_type == "select":
            self._select_or_choose(action)
        elif action_type == "upload":
            target = self._resolve_upload_target(action)
            target.set_input_files(self._resolve_upload_path(action.get("value", "")))
        elif action_type == "click":
            self._resolve_click_target(action.get("target", ""), action.get("role", ""), action).click()
        elif action_type == "dismiss":
            self._resolve_click_target(action.get("target", ""), action.get("role", ""), action).click()
        elif action_type == "hover":
            self._resolve_click_target(action.get("target", ""), action.get("role", ""), action).hover()
        elif action_type == "scroll":
            self._scroll_target(action)
        elif action_type == "wait_for_text":
            target = action.get("value", "") or action.get("target", "")
            self.page.get_by_text(target, exact=False).first.wait_for(state="visible", timeout=8000)

    def _action_variants(self, action):
        base = dict(action)
        candidates = []
        raw_selectors = list(base.get("selector_candidates", [])[:16])
        memory_selectors = self._memory_selector_candidates(base)
        if base.get("type") in {{"fill", "select", "upload"}}:
            selector_variants = [
                raw_selectors,
                self._merge_unique(raw_selectors, memory_selectors),
                memory_selectors,
                [],
            ]
        else:
            selector_variants = [
                self._merge_unique(memory_selectors, raw_selectors),
                self._merge_unique(raw_selectors, memory_selectors),
                raw_selectors,
                [],
            ]
        target_variants = [base.get("target", "")]
        target_variants.extend(self._field_text_candidates(base))
        if base.get("type") in {{"click", "dismiss", "hover"}}:
            target_variants.extend(self._interaction_text_candidates(base))
        role_variants = [base.get("role", "")]
        if base.get("type") in {{"click", "dismiss", "hover"}} and not base.get("role"):
            role_variants.extend(["button", "link", "tab", "menu", ""])

        seen = set()
        for target in target_variants:
            for role in role_variants:
                for selectors in selector_variants:
                    variant = dict(base)
                    if target:
                        variant["target"] = target
                    if role:
                        variant["role"] = role
                    elif "role" in variant:
                        variant["role"] = ""
                    variant["selector_candidates"] = selectors
                    signature = (
                        variant.get("type", ""),
                        str(variant.get("target", "")),
                        str(variant.get("role", "")),
                        tuple(variant.get("selector_candidates", [])[:6]),
                    )
                    if signature in seen:
                        continue
                    candidates.append(variant)
                    seen.add(signature)
        return candidates[:18] or [base]

    def assert_expectation(self, assertion):
        assertion_type = assertion.get("type")
        self.last_debug = {{"stage": "assertion", "assertion": assertion}}
        if assertion_type == "assert_text_visible":
            expect(self.page.get_by_text(assertion.get("value", ""), exact=False).first).to_be_visible()
        elif assertion_type == "assert_text_not_visible":
            expect(self.page.get_by_text(assertion.get("value", ""), exact=False).first).not_to_be_visible()
        elif assertion_type == "assert_any_text_visible":
            values = assertion.get("values", [])
            self._assert_any_text_visible(values)
        elif assertion_type == "assert_control_text":
            expect(self._resolve_click_target(assertion.get("value", ""), "", assertion)).to_contain_text(assertion.get("value", ""))
        elif assertion_type == "assert_control_visible":
            expect(self._resolve_click_target(assertion.get("value", ""), "", assertion)).to_be_visible()
        elif assertion_type == "assert_title_contains":
            expect(self.page).to_have_title(re.compile(rf".*{{re.escape(assertion.get('value', ''))}}.*", re.IGNORECASE))
        elif assertion_type == "assert_url_contains":
            fragment = assertion.get("value", "")
            expect(self.page).to_have_url(re.compile(rf".*{{re.escape(fragment)}}.*", re.IGNORECASE))
        elif assertion_type == "assert_network_seen":
            self._assert_network_seen(assertion.get("value", ""))
        elif assertion_type == "assert_network_status_ok":
            self._assert_network_status_ok(assertion.get("value", ""))
        elif assertion_type == "assert_graphql_ok":
            self._assert_graphql_ok(assertion.get("value", ""))
        elif assertion_type == "assert_endpoint_allowlist":
            self._assert_endpoint_allowlist(assertion.get("value", ""))
        elif assertion_type == "assert_cross_origin_safe":
            self._assert_cross_origin_safe(assertion.get("value", ""))

    def goto(self, url):
        self.page.goto(url or DEFAULT_URL, wait_until="domcontentloaded", timeout=30000)
        self.page.wait_for_timeout(self.settle_delay_ms)

    def _iter_contexts(self):
        contexts = [("page", self.page)]
        try:
            main_frame = self.page.main_frame
            for index, frame in enumerate(self.page.frames):
                if frame == main_frame:
                    continue
                contexts.append((f"frame:{{index}}", frame))
        except Exception:
            pass
        return contexts[:8]

    def _container_candidate_locators(self, scope, action, target, resolver_kind):
        candidates = []
        target_regex = self._text_regex(target) if target else None
        container_hints = [item for item in action.get("container_hints", []) if str(item or "").strip()]
        nearby_texts = [item for item in action.get("nearby_texts", []) if str(item or "").strip()]
        container_selector = "form, section, article, main, aside, nav, li, td, [role='dialog'], [role='region'], [class*='card' i], [class*='drawer' i], [data-testid*='section' i]"
        search_selector = "input, textarea, select, [contenteditable='true'], [role='combobox'], [role='textbox']"
        click_selector = "button, a, [role='button'], [role='tab'], [role='menuitem']"

        for hint in container_hints[:5]:
            fuzzy_hint = self._text_regex(hint)
            container = scope.locator(container_selector).filter(has_text=fuzzy_hint)
            if resolver_kind in {{"field", "select", "upload"}}:
                if target_regex:
                    if resolver_kind == "field":
                        candidates.extend(self._scoped_attribute_token_locators(container, ("input", "textarea", "select"), target, f"container-field:{{hint}}"))
                    elif resolver_kind == "select":
                        candidates.extend(self._scoped_attribute_token_locators(container, ("select",), target, f"container-select:{{hint}}"))
                    elif resolver_kind == "upload":
                        candidates.append((container.locator('input[type="file"]'), f"container-upload:{{hint}}"))
                    candidates.append((container.locator("label").filter(has_text=target_regex).locator("xpath=following::input[1]"), f"container-label-input:{{hint}}"))
                    candidates.append((container.locator("label").filter(has_text=target_regex).locator("xpath=following::textarea[1]"), f"container-label-textarea:{{hint}}"))
                    candidates.append((container.locator("label").filter(has_text=target_regex).locator("xpath=following::select[1]"), f"container-label-select:{{hint}}"))
                    candidates.append((container.locator("label").filter(has_text=target_regex).locator("xpath=following::*[@contenteditable='true'][1]"), f"container-label-editor:{{hint}}"))
            else:
                if target_regex:
                    candidates.append((container.locator(click_selector).filter(has_text=target_regex), f"container-click-text:{{hint}}"))
        for text in nearby_texts[:4]:
            fuzzy_text = self._text_regex(text)
            if resolver_kind in {{"field", "select", "upload"}}:
                candidates.append((scope.locator("label, span, div, p").filter(has_text=fuzzy_text).locator("xpath=following::input[1]"), f"nearby-input:{{text}}"))
                candidates.append((scope.locator("label, span, div, p").filter(has_text=fuzzy_text).locator("xpath=following::textarea[1]"), f"nearby-textarea:{{text}}"))
                candidates.append((scope.locator("label, span, div, p").filter(has_text=fuzzy_text).locator("xpath=following::select[1]"), f"nearby-select:{{text}}"))
            else:
                candidates.append((scope.locator(click_selector).filter(has_text=fuzzy_text), f"nearby-click:{{text}}"))
        return candidates

    def _resolve_field(self, action):
        field_name = action.get("target", "")
        slug = self._slug(field_name)
        raw = field_name.strip()
        dashed = slug.replace("_", "-")
        fuzzy = self._text_regex(field_name)
        debug = self._build_resolution_debug("field", action)
        candidates = []
        if self._looks_like_password_field(action):
            for context_name, scope in self._iter_contexts():
                candidates.extend([
                    (scope.locator('input[type="password"]'), f'{{context_name}}|input[type="password"]'),
                    (scope.locator('input[autocomplete="current-password"]'), f'{{context_name}}|input[current-password]'),
                    (scope.locator('input[name*="pass" i]'), f'{{context_name}}|input[name*=pass i]'),
                    (scope.locator('input[id*="pass" i]'), f'{{context_name}}|input[id*=pass i]'),
                ])
        if self._looks_like_username_field(action):
            for context_name, scope in self._iter_contexts():
                candidates.extend([
                    (scope.locator('input[autocomplete="username"]'), f'{{context_name}}|input[autocomplete=username]'),
                    (scope.locator('input[name*="user" i]'), f'{{context_name}}|input[name*=user i]'),
                    (scope.locator('input[id*="user" i]'), f'{{context_name}}|input[id*=user i]'),
                    (scope.locator('input[name*="email" i]'), f'{{context_name}}|input[name*=email i]'),
                    (scope.locator('input[id*="email" i]'), f'{{context_name}}|input[id*=email i]'),
                ])
        candidates.extend(
            self._selector_candidate_locators(
                self._merge_unique(action.get("learned_path_hints", []), action.get("selector_candidates", [])),
                prefer_last=False,
            )
        )
        for context_name, scope in self._iter_contexts():
            candidates.extend([(locator, f"{{context_name}}|{{description}}") for locator, description in self._container_candidate_locators(scope, action, field_name, "field")])
            for alias in self._field_text_candidates(action):
                candidates.extend([
                    (scope.get_by_label(alias, exact=False), f"{{context_name}}|label:{{alias}}"),
                    (scope.get_by_placeholder(alias, exact=False), f"{{context_name}}|placeholder:{{alias}}"),
                    (scope.locator(f'[aria-label="{{alias}}"]'), f"{{context_name}}|aria-label:{{alias}}"),
                    (scope.locator(f'[role="textbox"][aria-label*="{{alias}}" i]'), f"{{context_name}}|role=textbox:{{alias}}"),
                ])
            candidates.extend([
                (scope.locator(f'input[name="{{raw}}"]'), f'{{context_name}}|input[name="{{raw}}"]'),
                (scope.locator(f'input[id="{{raw}}"]'), f'{{context_name}}|input[id="{{raw}}"]'),
                (scope.locator(f'textarea[name="{{raw}}"]'), f'{{context_name}}|textarea[name="{{raw}}"]'),
                (scope.locator(f'[name="{{raw}}"]'), f'{{context_name}}|[name="{{raw}}"]'),
                (scope.locator(f'[id="{{raw}}"]'), f'{{context_name}}|[id="{{raw}}"]'),
                (scope.locator(f'input[name="{{slug}}"]'), f'{{context_name}}|input[name="{{slug}}"]'),
                (scope.locator(f'input[id="{{slug}}"]'), f'{{context_name}}|input[id="{{slug}}"]'),
                (scope.locator(f'input[name="{{dashed}}"]'), f'{{context_name}}|input[name="{{dashed}}"]'),
                (scope.locator(f'input[id="{{dashed}}"]'), f'{{context_name}}|input[id="{{dashed}}"]'),
                (scope.locator(f'textarea[name="{{slug}}"]'), f'{{context_name}}|textarea[name="{{slug}}"]'),
                (scope.locator(f'select[name="{{slug}}"]'), f'{{context_name}}|select[name="{{slug}}"]'),
                (scope.locator(f'[aria-label="{{field_name}}"]'), f'{{context_name}}|aria-label:{{field_name}}'),
                (scope.locator(f'input[placeholder*="{{field_name}}" i], textarea[placeholder*="{{field_name}}" i], select[name*="{{field_name}}" i]'), f'{{context_name}}|placeholder/name*:{{field_name}}'),
                (scope.locator('[contenteditable="true"]').filter(has_text=fuzzy), f'{{context_name}}|contenteditable text~{{field_name}}'),
                (scope.locator('[role="textbox"]').filter(has_text=fuzzy), f'{{context_name}}|role=textbox text~{{field_name}}'),
                (scope.locator('[role="combobox"]').filter(has_text=fuzzy), f'{{context_name}}|role=combobox text~{{field_name}}'),
                (scope.locator("label").filter(has_text=fuzzy).locator("xpath=following::input[1]"), f'{{context_name}}|following input after label:{{field_name}}'),
                (scope.locator("label").filter(has_text=fuzzy).locator("xpath=following::textarea[1]"), f'{{context_name}}|following textarea after label:{{field_name}}'),
                (scope.locator("label").filter(has_text=fuzzy).locator("xpath=following::select[1]"), f'{{context_name}}|following select after label:{{field_name}}'),
                (scope.locator("label").filter(has_text=fuzzy).locator("xpath=following::*[@contenteditable='true'][1]"), f'{{context_name}}|following editor after label:{{field_name}}'),
            ])
        for alias in self._field_text_candidates(action):
            candidates.extend(self._attribute_token_locators(("input", "textarea", "select"), alias))
        if "search" in field_name.lower():
            for context_name, scope in self._iter_contexts():
                candidates.append((scope.locator('input[type="search"]'), f'{{context_name}}|input[type="search"]'))
        return self._first_match(candidates, f"field '{{field_name}}'", debug)

    def _resolve_click_target(self, label, role_hint, action=None):
        fuzzy = self._text_regex(label)
        debug = {{"resolver": "click_target", "target": label, "role_hint": role_hint}}
        action = action or {{}}
        candidates = self._selector_candidate_locators(self._merge_unique(action.get("learned_path_hints", []), action.get("selector_candidates", [])))
        for context_name, scope in self._iter_contexts():
            candidates.extend([(locator, f"{{context_name}}|{{description}}") for locator, description in self._container_candidate_locators(scope, action, label, "click")])
            if role_hint == "link":
                candidates.extend([
                    (scope.get_by_role("link", name=label, exact=False), f"{{context_name}}|role=link:{{label}}"),
                    (scope.locator(f'a:has-text("{{label}}")'), f'{{context_name}}|a:has-text("{{label}}")'),
                    (scope.locator("a").filter(has_text=fuzzy), f"{{context_name}}|a text~{{label}}"),
                ])
            elif role_hint == "tab":
                candidates.extend([
                    (scope.get_by_role("tab", name=label, exact=False), f"{{context_name}}|role=tab:{{label}}"),
                    (scope.locator('[role="tab"]').filter(has_text=fuzzy), f"{{context_name}}|[role=tab] text~{{label}}")
                ])
            elif role_hint == "menu":
                candidates.extend([
                    (scope.get_by_role("menuitem", name=label, exact=False), f"{{context_name}}|role=menuitem:{{label}}"),
                    (scope.locator('[role="menuitem"]').filter(has_text=fuzzy), f"{{context_name}}|[role=menuitem] text~{{label}}")
                ])
            else:
                candidates.extend([
                    (scope.get_by_role("button", name=label, exact=False), f"{{context_name}}|role=button:{{label}}"),
                    (scope.get_by_role("link", name=label, exact=False), f"{{context_name}}|role=link:{{label}}"),
                    (scope.get_by_text(label, exact=False), f"{{context_name}}|text:{{label}}"),
                    (scope.locator(f'button:has-text("{{label}}")'), f'{{context_name}}|button:has-text("{{label}}")'),
                    (scope.locator(f'a:has-text("{{label}}")'), f'{{context_name}}|a:has-text("{{label}}")'),
                    (scope.locator("button").filter(has_text=fuzzy), f"{{context_name}}|button text~{{label}}"),
                    (scope.locator("a").filter(has_text=fuzzy), f"{{context_name}}|a text~{{label}}"),
                    (scope.locator('[role="button"]').filter(has_text=fuzzy), f"{{context_name}}|[role=button] text~{{label}}"),
                    (scope.locator('[type="submit"]').filter(has_text=fuzzy), f"{{context_name}}|[type=submit] text~{{label}}"),
                ])
        return self._first_match(candidates, f"click target '{{label}}'", debug)

    def _resolve_select(self, action):
        field_name = action.get("target", "")
        slug = self._slug(field_name)
        raw = field_name.strip()
        dashed = slug.replace("_", "-")
        fuzzy = self._text_regex(field_name)
        debug = self._build_resolution_debug("select", action)
        candidates = self._selector_candidate_locators(
            self._merge_unique(action.get("learned_path_hints", []), action.get("selector_candidates", [])),
            prefer_last=False,
        )
        for context_name, scope in self._iter_contexts():
            candidates.extend([(locator, f"{{context_name}}|{{description}}") for locator, description in self._container_candidate_locators(scope, action, field_name, "select")])
            for alias in self._field_text_candidates(action):
                candidates.extend([
                    (scope.get_by_label(alias, exact=False), f"{{context_name}}|label:{{alias}}"),
                    (scope.locator('[role="combobox"]').filter(has_text=self._text_regex(alias)), f"{{context_name}}|combobox text~{{alias}}"),
                ])
            candidates.extend([
                (scope.locator(f'select[name="{{raw}}"]'), f'{{context_name}}|select[name="{{raw}}"]'),
                (scope.locator(f'select[id="{{raw}}"]'), f'{{context_name}}|select[id="{{raw}}"]'),
                (scope.locator(f'select[name="{{slug}}"]'), f'{{context_name}}|select[name="{{slug}}"]'),
                (scope.locator(f'select[id="{{slug}}"]'), f'{{context_name}}|select[id="{{slug}}"]'),
                (scope.locator(f'select[name="{{dashed}}"]'), f'{{context_name}}|select[name="{{dashed}}"]'),
                (scope.locator(f'select[id="{{dashed}}"]'), f'{{context_name}}|select[id="{{dashed}}"]'),
                (scope.locator(f'select[name*="{{field_name}}" i]'), f'{{context_name}}|select[name*="{{field_name}}" i]'),
                (scope.locator('[role="combobox"]'), f'{{context_name}}|[role="combobox"]'),
                (scope.locator('[aria-autocomplete]'), f'{{context_name}}|[aria-autocomplete]'),
                (scope.locator("label").filter(has_text=fuzzy).locator("xpath=following::select[1]"), f'{{context_name}}|following select after label:{{field_name}}'),
                (scope.locator("label").filter(has_text=fuzzy).locator("xpath=following::*[@role='combobox'][1]"), f'{{context_name}}|following combobox after label:{{field_name}}'),
                (scope.locator("select").filter(has=scope.locator("option")), f"{{context_name}}|select with option"),
            ])
        for alias in self._field_text_candidates(action):
            candidates.extend(self._attribute_token_locators(("select",), alias))
        return self._first_match(candidates, f"select '{{field_name}}'", debug)

    def _resolve_upload_target(self, action):
        target = action.get("target", "")
        fuzzy = self._text_regex(target)
        debug = self._build_resolution_debug("upload", action)
        candidates = self._selector_candidate_locators(
            self._merge_unique(action.get("learned_path_hints", []), action.get("selector_candidates", [])),
            prefer_last=False,
        )
        for context_name, scope in self._iter_contexts():
            candidates.extend([(locator, f"{{context_name}}|{{description}}") for locator, description in self._container_candidate_locators(scope, action, target, "upload")])
            candidates.extend([
                (scope.locator('input[type="file"]'), f'{{context_name}}|input[type="file"]'),
                (scope.locator('[data-testid*="upload" i]'), f'{{context_name}}|[data-testid*=upload]'),
                (scope.locator('[data-upload]'), f'{{context_name}}|[data-upload]'),
                (scope.locator("label").filter(has_text=fuzzy).locator("xpath=following::input[@type='file'][1]"), f'{{context_name}}|following upload after label:{{target}}'),
            ])
        return self._first_match(candidates, f"upload target '{{target}}'", debug)

    def _first_match(self, candidates, label, debug=None):
        attempted = []
        debug = debug or {{}}
        for candidate, description in candidates:
            attempted.append(description)
            try:
                count = candidate.count()
                if count:
                    resolved = None
                    for index in range(min(count, 3)):
                        probe = candidate.nth(index)
                        try:
                            if probe.is_visible():
                                resolved = probe
                                break
                        except Exception:
                            continue
                    resolved = resolved or candidate.first
                    selector_hint = description.split("|", 1)[1] if "|" in description else description
                    self.selector_memory["last_selector"] = selector_hint
                    history = self.selector_memory.setdefault("successful_selectors", [])
                    if selector_hint in history:
                        history.remove(selector_hint)
                    history.insert(0, selector_hint)
                    self.selector_memory["successful_selectors"] = history[:12]
                    self.last_debug = {{
                        **debug,
                        "resolved_with": description,
                        "resolved_selector": selector_hint,
                        "attempted": attempted[:20],
                    }}
                    return resolved
            except Exception:
                continue
        self.last_debug = {{**debug, "attempted": attempted[:20]}}
        raise ActionResolutionError(f"Unable to resolve {{label}}", self.last_debug)

    def _slug(self, value):
        text = re.sub(r"[^a-z0-9]+", "_", value.strip().lower())
        return text.strip("_")

    def _text_regex(self, value):
        parts = [re.escape(part) for part in re.split(r"\\s+", value.strip()) if part]
        pattern = ".*".join(parts) if parts else re.escape(value)
        return re.compile(pattern, re.IGNORECASE)

    def _field_text_candidates(self, action):
        values = [
            action.get("target", ""),
            action.get("semantic_label", ""),
            action.get("semantic_type", "").replace("_", " "),
            action.get("field_key", "").replace("_", " "),
        ]
        values.extend(action.get("aliases", []))
        deduped = []
        seen = set()
        for value in values:
            text = re.sub(r"\\s+", " ", str(value or "")).strip()
            if text and text.lower() not in seen:
                deduped.append(text)
                seen.add(text.lower())
        return deduped[:12]

    def _memory_selector_candidates(self, action):
        selectors = list(self.selector_memory.get("successful_selectors", [])[:12])
        if action.get("type") not in {{"fill", "select", "upload"}}:
            return selectors[:8]
        token_bag = []
        for item in self._field_text_candidates(action):
            token_bag.extend(self._tokens(item))
        token_bag.extend(self._tokens(action.get("field_key", "").replace("_", " ")))
        tokens = [item for item in dict.fromkeys(token_bag) if item]
        if not tokens:
            return []
        filtered = []
        for selector in selectors:
            selector_lower = str(selector or "").lower()
            if any(token in selector_lower for token in tokens):
                filtered.append(selector)
        return filtered[:8]

    def _looks_like_password_field(self, action):
        text = " ".join(
            str(action.get(key, ""))
            for key in ("target", "semantic_label", "semantic_type", "field_key", "step_text")
        ).lower()
        return any(token in text for token in ("password", "passcode", "pin"))

    def _looks_like_username_field(self, action):
        text = " ".join(
            str(action.get(key, ""))
            for key in ("target", "semantic_label", "semantic_type", "field_key", "step_text")
        ).lower()
        return any(token in text for token in ("username", "email", "user id", "login id"))

    def _interaction_text_candidates(self, action):
        values = [
            action.get("target", ""),
            action.get("component_type", "").replace("_", " "),
            action.get("component_key", "").replace("_", " "),
        ]
        values.extend(action.get("aliases", []))
        deduped = []
        seen = set()
        for value in values:
            text = re.sub(r"\\s+", " ", str(value or "")).strip()
            if text and text.lower() not in seen:
                deduped.append(text)
                seen.add(text.lower())
        return deduped[:10]

    def _selector_candidate_locators(self, selectors, prefer_last=True):
        locators = []
        prioritized = list(selectors[:20])
        memory_selector = self.selector_memory.get("last_selector")
        if prefer_last and memory_selector and memory_selector in prioritized:
            prioritized = [memory_selector] + [selector for selector in prioritized if selector != memory_selector]
        for selector in prioritized:
            for context_name, scope in self._iter_contexts():
                try:
                    locators.append((scope.locator(selector), f"{{context_name}}|{{selector}}"))
                except Exception:
                    continue
        return locators

    def _merge_unique(self, primary, secondary):
        merged = []
        seen = set()
        for bucket in (primary or [], secondary or []):
            for item in bucket:
                text = str(item or "").strip()
                if text and text not in seen:
                    merged.append(text)
                    seen.add(text)
        return merged

    def _tokens(self, value):
        base = value.strip().lower()
        normalized = re.sub(r"[^a-z0-9]+", " ", base)
        tokens = [token for token in normalized.split() if token]
        compact = re.sub(r"[^a-z0-9]+", "", base)
        if compact and compact not in tokens:
            tokens.append(compact)
        return tokens[:6]

    def _attribute_token_locators(self, tags, value):
        locators = []
        for token in self._tokens(value):
            for context_name, scope in self._iter_contexts():
                for tag in tags:
                    locators.append((scope.locator(f'{{tag}}[name*="{{token}}" i]'), f'{{context_name}}|{{tag}}[name*="{{token}}" i]'))
                    locators.append((scope.locator(f'{{tag}}[id*="{{token}}" i]'), f'{{context_name}}|{{tag}}[id*="{{token}}" i]'))
                    locators.append((scope.locator(f'{{tag}}[placeholder*="{{token}}" i]'), f'{{context_name}}|{{tag}}[placeholder*="{{token}}" i]'))
                    locators.append((scope.locator(f'{{tag}}[aria-label*="{{token}}" i]'), f'{{context_name}}|{{tag}}[aria-label*="{{token}}" i]'))
        return locators

    def _scoped_attribute_token_locators(self, scope, tags, value, prefix):
        locators = []
        for token in self._tokens(value):
            for tag in tags:
                locators.append((scope.locator(f'{{tag}}[name*="{{token}}" i]'), f'{{prefix}}|{{tag}}[name*="{{token}}" i]'))
                locators.append((scope.locator(f'{{tag}}[id*="{{token}}" i]'), f'{{prefix}}|{{tag}}[id*="{{token}}" i]'))
                locators.append((scope.locator(f'{{tag}}[placeholder*="{{token}}" i]'), f'{{prefix}}|{{tag}}[placeholder*="{{token}}" i]'))
                locators.append((scope.locator(f'{{tag}}[aria-label*="{{token}}" i]'), f'{{prefix}}|{{tag}}[aria-label*="{{token}}" i]'))
        return locators

    def _build_resolution_debug(self, resolver, action):
        return {{
            "resolver": resolver,
            "target": action.get("target", ""),
            "field_key": action.get("field_key", ""),
            "semantic_type": action.get("semantic_type", ""),
            "semantic_label": action.get("semantic_label", ""),
            "aliases": action.get("aliases", [])[:12],
            "selector_candidates": action.get("selector_candidates", [])[:12],
            "page_url": self.page.url,
            "page_title": self.page.title(),
            "runtime_state": self.capture_runtime_state(),
        }}

    def _fill_control(self, control, value, action):
        input_kind = str(action.get("input_kind", "")).lower()
        if input_kind in {{"rich_text", "contenteditable"}}:
            try:
                control.fill(value)
                return
            except Exception:
                pass
            control.click()
            try:
                control.evaluate(
                    """(el, val) => {{
                        if ('value' in el) {{
                            el.value = val;
                        }} else {{
                            el.innerHTML = '';
                            el.textContent = val;
                        }}
                        el.dispatchEvent(new Event('input', {{ bubbles: true }}));
                        el.dispatchEvent(new Event('change', {{ bubbles: true }}));
                    }}""",
                    value,
                )
                return
            except Exception:
                pass
        try:
            control.fill(value)
            return
        except Exception:
            control.click()
            try:
                control.press("Control+A")
            except Exception:
                pass
            self.page.keyboard.type(value)

    def _select_or_choose(self, action):
        control = self._resolve_select(action)
        value = action.get("value", "")
        try:
            control.select_option(label=value)
            return
        except Exception:
            pass
        try:
            control.select_option(value=value)
            return
        except Exception:
            pass
        control.click()
        self._fill_control(control, value, {{**action, "input_kind": "combobox"}})
        try:
            self.page.keyboard.press("Enter")
        except Exception:
            pass

    def _resolve_upload_path(self, value):
        candidate = Path(str(value or "").strip())
        possible = [candidate, ROOT_DIR / candidate, RUN_DIR / candidate]
        for item in possible:
            if item.exists():
                return str(item.resolve())
        raise FileNotFoundError(f"Upload file not found: {{value}}")

    def _scroll_target(self, action):
        target = action.get("target", "")
        if target:
            try:
                self._resolve_click_target(target, action.get("role", "")).scroll_into_view_if_needed()
                return
            except Exception:
                pass
        self.page.mouse.wheel(0, 900)

    def _assert_any_text_visible(self, values):
        last_error = None
        for value in values[:6]:
            try:
                expect(self.page.get_by_text(value, exact=False).first).to_be_visible(timeout=5000)
                return
            except Exception as exc:
                last_error = exc
        raise AssertionError(f"None of the expected texts were visible: {{values}}") from last_error

    def _assert_network_seen(self, expected):
        summary = summarize_network_entries(self.network_observer.get("entries", []))
        if not summary.get("request_count"):
            raise AssertionError("No network activity was captured for this test case.")
        target = str(expected or "").strip().lower()
        if not target:
            return
        for item in self.network_observer.get("entries", []):
            haystack = f"{{item.get('url', '')}} {{item.get('resource_type', '')}} {{item.get('method', '')}}".lower()
            if target in haystack:
                return
        raise AssertionError(f"Expected network activity matching '{{expected}}' was not observed.")

    def _assert_network_status_ok(self, expected):
        entries = [item for item in self.network_observer.get("entries", []) if item.get("event") == "response"]
        if not entries:
            raise AssertionError("No network responses were captured for this test case.")
        target = str(expected or "").strip().lower()
        if target:
            entries = [
                item for item in entries
                if target in f"{{item.get('url', '')}} {{item.get('resource_type', '')}}".lower()
            ]
        if not entries:
            raise AssertionError(f"No response matched the expected network target '{{expected}}'.")
        if any(int(item.get("status", 0) or 0) >= 400 for item in entries):
            raise AssertionError(f"Observed failing network status for '{{expected}}'.")
        if any(item.get("logical_error") for item in entries):
            raise AssertionError(f"Observed logical API error payload for '{{expected}}'.")

    def _assert_graphql_ok(self, expected):
        entries = [item for item in self.network_observer.get("entries", []) if item.get("event") == "response" and item.get("is_graphql")]
        if not entries:
            raise AssertionError("No GraphQL responses were captured for this test case.")
        target = str(expected or "").strip().lower()
        if target:
            entries = [item for item in entries if target in f"{{item.get('url', '')}} {{item.get('graphql_operation', '')}}".lower()]
        if not entries:
            raise AssertionError(f"No GraphQL response matched '{{expected}}'.")
        if any(item.get("graphql_error") for item in entries):
            raise AssertionError(f"GraphQL response contained errors for '{{expected}}'.")

    def _assert_endpoint_allowlist(self, expected):
        summary = summarize_network_entries(self.network_observer.get("entries", []))
        allowlist = list(self.network_observer.get("policy", {{}}).get("allowed_endpoint_tokens", []))
        allowlist.extend(self.network_observer.get("expected_request_map", {{}}).get("expected_endpoints", []))
        allowlist = [str(item or "").strip().lower() for item in allowlist if str(item or "").strip()]
        if expected and expected.lower() not in {{"allowlist", "same-origin"}}:
            allowlist.append(str(expected).strip().lower())
        if not allowlist:
            return
        unexpected = []
        for item in self.network_observer.get("entries", []):
            if item.get("event") != "response" or not item.get("same_origin", False):
                continue
            path = str(item.get("path", "") or urlparse(item.get("url", "")).path).lower()
            if path and not any(token in path for token in allowlist):
                unexpected.append(path)
        if unexpected:
            raise AssertionError(f"Observed endpoint outside allowlist: {{unexpected[0]}}")
        if summary.get("unexpected_host_count", 0) > 0:
            raise AssertionError("Observed response from unexpected host outside allowlist.")

    def _assert_cross_origin_safe(self, expected):
        policy = self.network_observer.get("policy", {{}})
        mode = str(policy.get("cross_origin_mode", "same-origin")).strip().lower()
        summary = summarize_network_entries(self.network_observer.get("entries", []))
        if mode == "allow-all":
            return
        if summary.get("unexpected_host_count", 0) > 0:
            raise AssertionError("Unexpected cross-origin network request detected.")

    def bind_network_observer(self, entries, policy=None, expected_request_map=None):
        self.network_observer = {{
            "entries": entries,
            "policy": policy or {{}},
            "expected_request_map": expected_request_map or {{}},
        }}

    def capture_runtime_state(self):
        try:
            return self.page.evaluate(
                """() => ({{
                    url: location.href,
                    title: document.title,
                    dialogs: document.querySelectorAll('[role="dialog"], dialog, .modal, [aria-modal="true"]').length,
                    drawers: document.querySelectorAll('.drawer, .offcanvas, [data-drawer], [class*="drawer" i], [class*="offcanvas" i]').length,
                    toasts: document.querySelectorAll('[role="alert"], [role="status"], .toast, .snackbar').length,
                    route_kind: location.hash ? 'hash' : 'path',
                    dom_nodes: document.querySelectorAll('body *').length,
                }})"""
            )
        except Exception:
            return {{"url": self.page.url, "title": self.page.title()}}

    def show_step_overlay(self, label):
        safe_label = str(label or "")[:160]
        try:
            self.page.evaluate(
                """(message) => {{
                    const id = '__qa_agent_step_overlay__';
                    let node = document.getElementById(id);
                    if (!node) {{
                        node = document.createElement('div');
                        node.id = id;
                        Object.assign(node.style, {{
                            position: 'fixed',
                            top: '16px',
                            right: '16px',
                            maxWidth: '360px',
                            padding: '10px 14px',
                            background: 'rgba(17, 24, 39, 0.92)',
                            color: '#f9fafb',
                            fontFamily: 'Consolas, monospace',
                            fontSize: '14px',
                            lineHeight: '1.45',
                            borderRadius: '10px',
                            zIndex: '2147483647',
                            boxShadow: '0 10px 25px rgba(0, 0, 0, 0.35)',
                            border: '1px solid rgba(255,255,255,0.12)',
                            pointerEvents: 'none',
                            whiteSpace: 'pre-wrap'
                        }});
                        document.documentElement.appendChild(node);
                    }}
                    node.textContent = message;
                }}""",
                safe_label,
            )
        except Exception:
            return

    def clear_step_overlay(self):
        try:
            self.page.evaluate(
                """() => {{
                    const node = document.getElementById('__qa_agent_step_overlay__');
                    if (node) node.remove();
                }}"""
            )
        except Exception:
            return

    def settle_after_action(self, action):
        try:
            self.page.wait_for_load_state("networkidle", timeout=3000)
        except Exception:
            pass
        delay = self.final_delay_ms if action.get("type") in {{"click", "dismiss"}} else self.settle_delay_ms
        if self.settings.get("watch_live_updates"):
            delay += 300
        self.page.wait_for_timeout(delay)


def describe_action(action):
    step_text = str(action.get("step_text", "")).strip()
    if step_text:
        return step_text
    action_type = action.get("type", "")
    target = action.get("target", "")
    value = action.get("value", "")
    if action_type == "fill":
        return f"Input '{{value}}' into '{{target}}'"
    if action_type == "select":
        return f"Select '{{value}}' from '{{target}}'"
    if action_type == "upload":
        return f"Upload '{{value}}' into '{{target}}'"
    if action_type == "click":
        role = action.get("role", "")
        suffix = f" {{role}}" if role else ""
        return f"Click '{{target}}'{{suffix}}".strip()
    return f"Inspect '{{target}}'"


def format_step_overlay(test_id, phase_label, index, total, action):
    detail = _cli_safe_text(describe_action(action))
    return f"Test: {{test_id}}\\n{{phase_label}} {{index}}/{{total}}\\n{{detail}}"


def load_execution_plan():
    return json.loads(EXECUTION_PLAN_FILE.read_text(encoding="utf-8"))


def load_storage_state(execution_plan):
    site_profile = execution_plan.get("site_profile", {{}})
    candidates = [AUTH_FILE]
    for candidate in site_profile.get("auth", {{}}).get("storage_state_candidates", []):
        path = Path(str(candidate))
        possible = [path, ROOT_DIR / path, RUN_DIR / path]
        for item in possible:
            if item.exists():
                return str(item.resolve())
    if AUTH_FILE.exists():
        return str(AUTH_FILE)
    return None


def save_json(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def check_execution_gate():
    if not ANTI_HALLUCINATION_AUDIT_FILE.exists():
        return {{"blocked": False, "reason": "", "signals": {{}}}}
    try:
        payload = json.loads(ANTI_HALLUCINATION_AUDIT_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {{"blocked": False, "reason": "", "signals": {{}}}}
    gate = payload.get("execution_gate", {{}})
    if not isinstance(gate, dict) or not gate.get("blocked", False):
        return {{"blocked": False, "reason": "", "signals": gate.get("signals", {{}}) if isinstance(gate, dict) else {{}}}}
    allow_override = str(os.getenv(ALLOW_LOW_ANTI_HALLU_ENV, "")).strip().lower() in {{"1", "true", "yes", "on"}}
    if allow_override:
        return {{"blocked": False, "reason": "override enabled", "signals": gate.get("signals", {{}})}}
    reasons = [str(item).strip() for item in gate.get("reasons", []) if str(item).strip()]
    reason = "; ".join(reasons[:3]) if reasons else "Execution blocked by anti-hallucination gate."
    return {{"blocked": True, "reason": reason, "signals": gate.get("signals", {{}})}}


def summarize_network_entries(entries):
    requests = [item for item in entries if item.get("event") == "request"]
    responses = [item for item in entries if item.get("event") == "response"]
    failing = [item for item in responses if int(item.get("status", 0) or 0) >= 400]
    graphql_errors = [item for item in responses if item.get("graphql_error")]
    unexpected_hosts = [item for item in requests + responses if item.get("unexpected_host")]
    endpoint_hits = {{}}
    host_hits = {{}}
    for item in responses[:80]:
        url = str(item.get("url", ""))
        path = urlparse(url).path or url
        host = urlparse(url).netloc or ""
        endpoint_hits[path] = endpoint_hits.get(path, 0) + 1
        if host:
            host_hits[host] = host_hits.get(host, 0) + 1
    return {{
        "request_count": len(requests),
        "response_count": len(responses),
        "failing_response_count": len(failing),
        "graphql_error_count": len(graphql_errors),
        "unexpected_host_count": len(unexpected_hosts),
        "top_endpoints": [
            {{"path": key, "hits": value}}
            for key, value in sorted(endpoint_hits.items(), key=lambda entry: (-entry[1], entry[0]))[:8]
        ],
        "top_hosts": [
            {{"host": key, "hits": value}}
            for key, value in sorted(host_hits.items(), key=lambda entry: (-entry[1], entry[0]))[:6]
        ],
    }}


def wire_network_capture(page, bucket, base_url, network_policy=None):
    policy = network_policy or {{}}
    host = urlparse(base_url or DEFAULT_URL).netloc.lower()
    allowed_hosts = {{item.lower() for item in policy.get("allowed_hosts", []) if str(item or "").strip()}}
    if host:
        allowed_hosts.add(host)
    graphql_error_keys = [str(item or "").strip().lower() for item in policy.get("graphql_error_keys", []) if str(item or "").strip()]
    if not graphql_error_keys:
        graphql_error_keys = ["errors", "error", "extensions"]

    def host_state(url):
        parsed = urlparse(url)
        if not parsed.netloc:
            return True, True, False
        normalized = parsed.netloc.lower()
        same_origin = normalized == host
        allowed_host = normalized in allowed_hosts
        unexpected_host = not same_origin and not allowed_host
        return same_origin, allowed_host, unexpected_host

    def on_request(request):
        try:
            url = request.url
            same_origin, allowed_host, unexpected_host = host_state(url)
            bucket.append(
                {{
                    "event": "request",
                    "method": request.method,
                    "url": url,
                    "path": urlparse(url).path or url,
                    "host": urlparse(url).netloc,
                    "resource_type": request.resource_type,
                    "same_origin": same_origin,
                    "allowed_host": allowed_host,
                    "unexpected_host": unexpected_host,
                }}
            )
        except Exception:
            return

    def on_response(response):
        try:
            url = response.url
            same_origin, allowed_host, unexpected_host = host_state(url)
            request = response.request
            content_type = response.headers.get("content-type", "") if getattr(response, "headers", None) else ""
            logical_error = False
            graphql_error = False
            graphql_operation = ""
            if same_origin or allowed_host:
                try:
                    post_data = request.post_data if request else ""
                    if post_data:
                        graphql_operation = re.search(r'"operationName"\\s*:\\s*"([^"]+)"', post_data)
                        graphql_operation = graphql_operation.group(1) if graphql_operation else ""
                    is_graphql = "graphql" in url.lower() or "graphql" in content_type.lower() or bool(graphql_operation)
                    preview = ""
                    if "json" in content_type.lower() or is_graphql:
                        try:
                            preview = response.text()[:1200]
                        except Exception:
                            preview = ""
                    if preview:
                        lowered_preview = preview.lower()
                        logical_error = any(token in lowered_preview for token in ['"success":false', '"ok":false', '"error":true'])
                        if is_graphql:
                            graphql_error = any(token in lowered_preview for token in graphql_error_keys)
                except Exception:
                    pass
            bucket.append(
                {{
                    "event": "response",
                    "method": request.method if request else "",
                    "url": url,
                    "path": urlparse(url).path or url,
                    "host": urlparse(url).netloc,
                    "resource_type": request.resource_type if request else "",
                    "status": response.status,
                    "same_origin": same_origin,
                    "allowed_host": allowed_host,
                    "unexpected_host": unexpected_host,
                    "logical_error": logical_error,
                    "is_graphql": "graphql" in url.lower() or bool(graphql_operation),
                    "graphql_error": graphql_error,
                    "graphql_operation": graphql_operation,
                }}
            )
        except Exception:
            return

    page.on("request", on_request)
    page.on("response", on_response)
    return on_request, on_response


def detach_network_capture(page, handlers):
    request_handler, response_handler = handlers
    try:
        page.remove_listener("request", request_handler)
    except Exception:
        pass
    try:
        page.remove_listener("response", response_handler)
    except Exception:
        pass


def build_learning_entry(test_id, plan, engine, status, error_message):
    return {{
        "id": test_id,
        "title": plan.get("title", ""),
        "status": status,
        "error": error_message,
        "resolved_with": engine.last_debug.get("resolved_with", ""),
        "resolved_selector": engine.last_debug.get("resolved_selector", ""),
        "attempted": engine.last_debug.get("attempted", []),
        "details": engine.last_debug,
        "runtime_state": engine.capture_runtime_state(),
        "fact_ids": list(plan.get("scenario_grounding", {{}}).get("fact_ids", [])),
        "grounding_score": float(plan.get("scenario_grounding", {{}}).get("score", 0.0) or 0.0),
    }}


def finalize_video(page, context, test_id):
    video = page.video
    page.wait_for_timeout(FINAL_DELAY_MS)
    page.close()
    context.close()
    if not video:
        return
    final_path = VIDEO_DIR / f"{{test_id}}.webm"
    if final_path.exists():
        final_path.unlink()
    video.save_as(str(final_path))
    try:
        video.delete()
    except Exception:
        pass


def run_tests():
    VIDEO_DIR.mkdir(parents=True, exist_ok=True)
    JSON_DIR.mkdir(parents=True, exist_ok=True)
    gate = check_execution_gate()
    if gate.get("blocked", False):
        message = str(gate.get("reason", "")).strip() or "Execution blocked by anti-hallucination gate."
        safe_print(f"[BLOCKED] {{message}}")
        safe_print(f"[HINT] Set {{ALLOW_LOW_ANTI_HALLU_ENV}}=1 to override manually.")
        save_json(RESULT_FILE, {{"results": [{{"id": "RUN_GATE", "title": "Execution gate", "status": "blocked", "error": message, "automation": "auto", "network_summary": {{}}, "fact_ids": [], "grounding_score": 0.0}}]}})
        save_json(DEBUG_FILE, {{"debug_entries": [{{"id": "RUN_GATE", "stage": "gate", "error": message, "details": gate}}]}})
        save_json(LEARNING_FILE, {{"learning_entries": []}})
        save_json(CHECKPOINT_FILE, {{"checkpoints": [{{"id": "RUN_GATE", "type": "anti_hallucination_gate", "mode": "manual", "reason": message, "signals": gate.get("signals", {{}})}}]}})
        save_json(NETWORK_FILE, {{"network_entries": []}})
        return
    execution_plan = load_execution_plan()
    test_plans = execution_plan.get("plans", [])
    storage_state = load_storage_state(execution_plan)
    execution_settings = execution_plan.get("settings", {{}})
    results = []
    debug_entries = []
    learning_entries = []
    checkpoint_entries = []
    network_entries = []

    safe_print(f"Plans found: {{len(test_plans)}}")
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=HEADLESS)
        try:
            for plan in test_plans:
                test_id = plan.get("id", "").strip()
                automation = plan.get("automation", "auto")
                orchestration = plan.get("orchestration", {{}})
                session_strategy = plan.get("session_strategy", {{}})
                safe_print(f"--- Running: {{test_id}} ---")
                if automation == "manual":
                    safe_print(f"  [Skip] {{test_id}} is marked manual.")
                    results.append({{"id": test_id, "title": plan.get("title", ""), "status": "skipped", "error": "", "automation": automation}})
                    continue
                if session_strategy.get("requires_session") and not storage_state:
                    message = "Authenticated session required. Provide auth/auth_state.json or site-profile storage state."
                    safe_print(f"  [Checkpoint] {{test_id}} requires session: {{message}}")
                    results.append({{"id": test_id, "title": plan.get("title", ""), "status": "checkpoint_required", "error": message, "automation": automation}})
                    checkpoint_entries.append({{"id": test_id, "type": "session", "mode": "manual", "reason": message}})
                    continue

                context_kwargs = {{"record_video_dir": str(VIDEO_DIR)}}
                if storage_state:
                    context_kwargs["storage_state"] = storage_state
                context = browser.new_context(**context_kwargs)
                page = context.new_page()
                test_network = []
                network_policy = execution_plan.get("network_policy", {{}})
                expected_request_map = plan.get("expected_request_map", {{}})
                network_handlers = wire_network_capture(
                    page,
                    test_network,
                    plan.get("target_url") or execution_plan.get("base_url") or DEFAULT_URL,
                    network_policy,
                )
                plan_settings = {{
                    "step_delay_ms": int(plan.get("interaction_hints", {{}}).get("step_delay_ms", execution_settings.get("step_delay_ms", STEP_DELAY_MS))),
                    "settle_delay_ms": int(plan.get("interaction_hints", {{}}).get("settle_delay_ms", execution_settings.get("settle_delay_ms", SETTLE_DELAY_MS))),
                    "final_delay_ms": int(plan.get("interaction_hints", {{}}).get("final_delay_ms", execution_settings.get("final_delay_ms", FINAL_DELAY_MS))),
                    "overlay_lead_in_ms": int(plan.get("interaction_hints", {{}}).get("overlay_lead_in_ms", execution_settings.get("overlay_lead_in_ms", OVERLAY_LEAD_IN_MS))),
                    "overlay_hold_ms": int(plan.get("interaction_hints", {{}}).get("overlay_hold_ms", execution_settings.get("overlay_hold_ms", OVERLAY_HOLD_MS))),
                    "watch_live_updates": bool(plan.get("interaction_hints", {{}}).get("watch_live_updates", False)),
                }}
                engine = ActionEngine(page, settings=plan_settings)
                engine.bind_network_observer(test_network, network_policy, expected_request_map)
                status = "passed"
                error_message = ""

                try:
                    engine.goto(plan.get("target_url") or execution_plan.get("base_url") or DEFAULT_URL)
                    pre_actions = plan.get("pre_actions", [])
                    for index, action in enumerate(pre_actions, start=1):
                        engine.show_step_overlay(format_step_overlay(test_id, "Preparation", index, len(pre_actions), action))
                        page.wait_for_timeout(engine.overlay_lead_in_ms)
                        engine.execute(action)
                        engine.settle_after_action(action)
                        page.wait_for_timeout(engine.overlay_hold_ms)
                    actions = plan.get("actions", [])
                    for index, action in enumerate(actions, start=1):
                        engine.show_step_overlay(format_step_overlay(test_id, "Step", index, len(actions), action))
                        page.wait_for_timeout(engine.overlay_lead_in_ms)
                        engine.execute(action)
                        engine.settle_after_action(action)
                        page.wait_for_timeout(engine.overlay_hold_ms)
                    if orchestration.get("mode") == "semi-auto" and plan.get("checkpoints"):
                        raise CheckpointRequiredError(plan.get("checkpoints")[0])
                    for assertion in plan.get("assertions", []):
                        engine.show_step_overlay(f"Test: {{test_id}}\\nAssertion\\n{{assertion.get('type', '')}}")
                        page.wait_for_timeout(engine.overlay_lead_in_ms)
                        engine.assert_expectation(assertion)
                        page.wait_for_timeout(engine.overlay_hold_ms)
                    engine.clear_step_overlay()
                    page.wait_for_timeout(engine.final_delay_ms)
                    safe_print(f"  [Pass] {{test_id}} done.")
                except CheckpointRequiredError as exc:
                    status = "checkpoint_required"
                    error_message = str(exc)
                    safe_print(f"  [Checkpoint] {{test_id}}: {{exc}}")
                    checkpoint_entries.append({{"id": test_id, **exc.checkpoint, "details": engine.last_debug}})
                    debug_entries.append({{"id": test_id, "stage": "checkpoint", "details": engine.last_debug, "error": str(exc)}})
                except PlaywrightTimeoutError as exc:
                    status = "failed"
                    error_message = f"timeout: {{exc}}"
                    safe_print(f"  [Error] {{test_id}} timeout: {{exc}}")
                    debug_entries.append({{"id": test_id, "stage": "timeout", "details": engine.last_debug}})
                except ActionResolutionError as exc:
                    status = "failed"
                    error_message = str(exc)
                    safe_print(f"  [Error] {{test_id}} failed: {{exc}}")
                    debug_entries.append({{"id": test_id, "stage": "resolution", "details": exc.debug}})
                except Exception as exc:
                    status = "failed"
                    error_message = str(exc)
                    safe_print(f"  [Error] {{test_id}} failed: {{exc}}")
                    debug_entries.append({{"id": test_id, "stage": "runtime", "details": engine.last_debug, "error": str(exc)}})
                finally:
                    detach_network_capture(page, network_handlers)
                    network_summary = summarize_network_entries(test_network)
                    results.append({{
                        "id": test_id,
                        "title": plan.get("title", ""),
                        "status": status,
                        "error": error_message,
                        "automation": automation,
                        "network_summary": network_summary,
                        "fact_ids": list(plan.get("scenario_grounding", {{}}).get("fact_ids", [])),
                        "grounding_score": float(plan.get("scenario_grounding", {{}}).get("score", 0.0) or 0.0),
                    }})
                    learning_entries.append(build_learning_entry(test_id, plan, engine, status, error_message))
                    network_entries.append({{
                        "id": test_id,
                        "title": plan.get("title", ""),
                        "entries": test_network[:120],
                        "summary": network_summary,
                        "policy": network_policy,
                        "expected_request_map": expected_request_map,
                    }})
                    finalize_video(page, context, test_id)
        finally:
            browser.close()
    save_json(RESULT_FILE, {{"results": results}})
    save_json(DEBUG_FILE, {{"debug_entries": debug_entries}})
    save_json(LEARNING_FILE, {{"learning_entries": learning_entries}})
    save_json(CHECKPOINT_FILE, {{"checkpoints": checkpoint_entries}})
    save_json(NETWORK_FILE, {{"network_entries": network_entries}})


if __name__ == "__main__":
    run_tests()
'''
