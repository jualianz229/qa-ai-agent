import os
import time
import json
from google import genai
from google.genai import types
from dotenv import load_dotenv
from core.guardrails import build_allowed_vocabulary, validate_page_scope, validate_test_scenarios

load_dotenv()

SYSTEM_PROMPT = """
You are an Expert QA Automation Engineer and Test Scenario Designer.
Your job is to generate THOROUGH test cases that verify the most relevant scope of the target web page based primarily on the actual page content, structure, controls, sections, navigation, tables, lists, forms, visible texts, APIs, and user instructions.

You will be provided with:
1. Information about the website elements (headings, texts, buttons, links, etc).
2. Specific instructions from the USER.

=== YOUR OUTPUT MUST BE STRICTLY JSON ===
Generate a comprehensive list of Test Scenarios in strict JSON array format.
MAXIMIZE THE NUMBER OF TEST CASES! You MUST include:
- Positive test cases (Happy paths)
- Negative test cases (Invalid inputs)
- Edge cases / Boundary values
- Error validations (Missing fields, wrong formats, etc.)
- Scope-focused cases that match the actual page context discovered from the page itself. Example: article page -> content, sharing, breadcrumbs, comments, metadata; club page -> roster, standings, filters, navigation; football page -> fixtures, tables, stats, search, pagination.

Each object in the JSON array must have EXACTLY these keys in order:
ID, Module, Category, Test Type, Title, Precondition, Steps to Reproduce, Expected Result, Actual Result, Severity, Priority, Evidence

CRITICAL RULES:
- ONLY output a valid JSON array of objects.
- Do NOT include markdown formatting like ```json or ```.
- Start directly with the JSON opening bracket `[` and end with `]`.
- For multi-line fields like 'Steps to Reproduce', use the literal characters '\\n' to represent new lines (e.g., "1. Step 1\\n2. Step 2").
- Provide realistic default values for Severity (e.g., High, Medium, Low) and Priority (e.g., P1, P2, P3).
- 'Actual Result' and 'Evidence' can be left as empty ("") since this is a test scenario generation phase.
"""

PAGE_SCOPE_SCHEMA = """
Return STRICT JSON object with exactly these keys:
- page_type: short string describing the page type
- primary_goal: short string describing the main user goal on this page
- key_modules: array of strings
- critical_user_flows: array of strings
- priority_areas: array of strings
- risks: array of strings
- scope_summary: short paragraph
- confidence: number from 0.0 to 1.0
"""

# ── Model Pool ─────────────────────────────────────────────────────────────
MODEL_POOL = [
    # 30 RPM — Gemma 3 (limit terbesar), terbesar dulu
    {"name": "gemma-3-27b-it",   "supports_system": False, "rpm": 30},
    {"name": "gemma-3-12b-it",   "supports_system": False, "rpm": 30},
    {"name": "gemma-3-4b-it",    "supports_system": False, "rpm": 30},
    {"name": "gemma-3n-e2b-it",  "supports_system": False, "rpm": 30},
    {"name": "gemma-3-1b-it",    "supports_system": False, "rpm": 30},
    # 20 RPM — Flash Lite
    {"name": "gemini-2.5-flash-lite",  "supports_system": True, "rpm": 20},
    # 5 RPM — Gemini Flash utama
    {"name": "gemini-3-flash-preview", "supports_system": True, "rpm": 5},
    {"name": "gemini-2.5-flash",       "supports_system": True, "rpm": 5},
]

_QUOTA_KEYWORDS = (
    "quota", "rate limit", "resource_exhausted", "resourceexhausted",
    "429", "too many requests", "limit exceeded", "ratelimitexceeded",
)


class AIEngine:
    def __init__(self):
        api_key = os.getenv("GEMINI_API_KEY")
        if not api_key or api_key == "your_gemini_api_key_here":
            raise ValueError("GEMINI_API_KEY belum diset di file .env!")
        self.client = genai.Client(api_key=api_key)
        self._pool       = MODEL_POOL
        self._idx        = 0
        self._chat_turns = 0
        self.last_scope_validation = {}
        self.last_scenario_validation = {}
        self._init_model()

    def _init_model(self) -> None:
        cfg    = self._pool[self._idx]
        kwargs = {}
        if cfg["supports_system"]:
            kwargs["system_instruction"] = SYSTEM_PROMPT
        self.config = types.GenerateContentConfig(**kwargs)
        
        self.chat = self.client.chats.create(
            model=cfg["name"],
            config=self.config
        )
        self._chat_turns = 0

    @property
    def current_model(self) -> str:
        return self._pool[self._idx]["name"]

    @property
    def current_rpm(self) -> int:
        return self._pool[self._idx].get("rpm", 0)

    @property
    def models_status(self) -> list:
        result = []
        for i, m in enumerate(self._pool):
            tag = "active" if i == self._idx else ("used" if i < self._idx else "waiting")
            result.append({"name": m["name"], "status": tag, "rpm": m.get("rpm", 0)})
        return result

    def _is_quota_error(self, exc: Exception) -> bool:
        msg = str(exc).lower()
        return any(kw.lower() in msg for kw in _QUOTA_KEYWORDS)

    def _try_next_model(self, exc: Exception) -> bool:
        if not self._is_quota_error(exc):
            return False
        if self._idx >= len(self._pool) - 1:
            return False
        self._idx += 1
        self._init_model()
        return True

    def _call_chat(self, prompt: str | list) -> str:
        max_retries = 3
        
        while True:
            for attempt in range(max_retries):
                try:
                    cfg = self._pool[self._idx]
                    if not cfg["supports_system"]:
                        if isinstance(prompt, list):
                            full = [f"[SYSTEM INSTRUCTIONS]\n{SYSTEM_PROMPT[:2500]}\n[/SYSTEM]\n\n"] + prompt
                        else:
                            full = f"[SYSTEM INSTRUCTIONS]\n{SYSTEM_PROMPT[:2500]}\n[/SYSTEM]\n\n{prompt}"
                    else:
                        full = prompt
                    return self.chat.send_message(full).text
                except Exception as e:
                    if attempt < max_retries - 1:
                        time.sleep(3 ** attempt)
                        continue
                    # Jika gagal 3x di model ini, fallback ke model berikutnya
                    if self._try_next_model(e):
                        break # break the internal for loop to restart while loop with new model
                    raise # if out of models, raise error

    def _call_stateless(self, prompt: str) -> str:
        max_retries = 3
        while True:
            for attempt in range(max_retries):
                try:
                    cfg = self._pool[self._idx]
                    if not cfg["supports_system"]:
                        full = f"[SYSTEM INSTRUCTIONS]\n{SYSTEM_PROMPT[:2500]}\n[/SYSTEM]\n\n{prompt}"
                        return self.client.models.generate_content(
                            model=cfg["name"],
                            contents=full,
                        ).text
                    else:
                        return self.client.models.generate_content(
                            model=cfg["name"],
                            contents=prompt,
                            config=self.config
                        ).text
                except Exception as e:
                    if attempt < max_retries - 1:
                        time.sleep(3 ** attempt)
                        continue
                    if self._try_next_model(e):
                        break
                    raise

    def generate_test_scenarios(
        self,
        url: str,
        website_title: str,
        page_info: dict,
        page_model: dict | None = None,
        page_scope: dict | None = None,
        custom_instruction: str = "",
        csv_sep: str = ",",
    ) -> str:
        """Menghasilkan CSV berisi Test Scenario berdasarkan scan halaman (STATEFUL)."""
        self.last_scenario_validation = {}
        headings = [h.get('text', '')[:50] for h in page_info.get("headings", []) if isinstance(h, dict)]
        texts = [str(t)[:50] for t in page_info.get("texts", [])]
        buttons = page_info.get("buttons", [])
        links = [l.get('text', '')[:50] for l in page_info.get("links", [])]
        forms = page_info.get("forms", [])
        apis = page_info.get("apis", [])
        sections = page_info.get("sections", [])
        tables = page_info.get("tables", [])
        lists = page_info.get("lists", [])
        navigation = page_info.get("navigation", [])
        metadata = page_info.get("metadata", {})
        fingerprint = page_info.get("page_fingerprint", {})
        crawled_pages = page_info.get("crawled_pages", [])

        allowed = build_allowed_vocabulary(page_model, page_scope, page_info)
        correction_notes = []

        for attempt in range(2):
            prompt = (
                f"I am building a QA Test Scope for the website '{website_title}' ({url}).\n"
                f"PAGE ANALYSIS:\n"
                f"- Headings: {headings[:15]}\n"
                f"- Texts: {texts[:10]}\n"
                f"- Buttons: {buttons[:15]}\n"
                f"- Links: {links[:15]}\n"
                f"- Forms: {forms[:5]}\n"
                f"- Sections: {sections[:6]}\n"
                f"- Tables: {tables[:4]}\n"
                f"- Lists: {lists[:4]}\n"
                f"- Navigation: {navigation[:4]}\n"
                f"- Metadata: {metadata}\n"
                f"- Page Fingerprint: {fingerprint}\n"
                f"- Linked Pages Sample: {crawled_pages[:5]}\n"
                f"- APIs (JS endpoints): {apis}\n"
                f"- ALLOWED VOCABULARY / FACTS: {json.dumps(allowed, ensure_ascii=False)}\n\n"
            )
            if page_model:
                prompt += f"NORMALIZED PAGE MODEL:\n{json.dumps(page_model, ensure_ascii=False)[:4000]}\n\n"
            if page_scope:
                prompt += (
                    "AI PAGE SCOPE ANALYSIS:\n"
                    f"- Page Type: {page_scope.get('page_type', '')}\n"
                    f"- Primary Goal: {page_scope.get('primary_goal', '')}\n"
                    f"- Key Modules: {page_scope.get('key_modules', [])}\n"
                    f"- Critical User Flows: {page_scope.get('critical_user_flows', [])}\n"
                    f"- Priority Areas: {page_scope.get('priority_areas', [])}\n"
                    f"- Risks: {page_scope.get('risks', [])}\n"
                    f"- Scope Summary: {page_scope.get('scope_summary', '')}\n\n"
                    f"- Confidence: {page_scope.get('confidence', 0)}\n\n"
                )
            if custom_instruction:
                prompt += f"USER INSTRUCTION: {custom_instruction}\n\n"
            if correction_notes:
                prompt += "GROUNDING CORRECTIONS FROM PREVIOUS ATTEMPT:\n"
                prompt += "\n".join(f"- {note}" for note in correction_notes[:8]) + "\n\n"
            if page_model and page_model.get("field_catalog"):
                prompt += f"SEMANTIC FIELD SUMMARY:\n{self._summarize_field_catalog(page_model)}\n\n"

            prompt += (
                "=== YOUR TASK ===\n"
                "Based on the grounded page analysis and user instructions above, act as an Expert QA Test Designer.\n"
                "Generate a comprehensive list of Test Scenarios in strict JSON format.\n"
                "Use only modules, flows, and page facts supported by the page analysis.\n"
                "MAXIMIZE THE NUMBER OF TEST CASES! You MUST include:\n"
                "- Positive test cases (Happy paths)\n"
                "- Negative test cases (Invalid inputs)\n"
                "- Edge cases / Boundary values\n"
                "- Error validations (Missing fields, wrong formats, etc.)\n"
                "- Scope-based cases that are relevant to the actual page type inferred from the actual page content and structure.\n"
                "- Specific cases requested via 'USER INSTRUCTION' when they are still grounded in the detected page facts.\n\n"
                "CRITICAL RULES:\n"
                "- ONLY output a valid JSON array of objects. Do NOT include markdown formatting like ```json or ```.\n"
                "- Each object in the JSON array MUST have EXACTLY these keys:\n"
                "  \"ID\", \"Module\", \"Category\", \"Test Type\", \"Title\", \"Precondition\", \"Steps to Reproduce\", \"Expected Result\", \"Actual Result\", \"Severity\", \"Priority\", \"Evidence\", \"Automation\"\n"
                "- Do not invent forms, auth flows, tables, search, filters, pagination, or any module that is not supported by the page facts.\n"
                "- For the 'ID' column, use a contextual prefix derived from the module or page area (examples: ART for article, NAV for navigation, SRH for search, FRM for form).\n"
                "- The 'Test Type' column must strictly be 'Positive' or 'Negative'.\n"
                f"- For 'Steps to Reproduce', step 1 MUST ALWAYS be '1. Open the site {url}'. Step 2 and beyond are the actual interactions.\n"
                "- NEVER use the word 'Enter' when describing typing actions. ALWAYS use the word 'Input'.\n"
                "- Do not assume the page type from the URL alone.\n"
                "- 'Actual Result' and 'Evidence' should be left empty strings \"\" since this is planning phase.\n"
            )
            if self._chat_turns >= 6:
                self.reset_chat()

            raw = self._call_chat(prompt)
            self._chat_turns += 1
            raw = raw.strip()
            if raw.startswith("```json"):
                raw = raw[7:]
            elif raw.startswith("```"):
                raw = raw[3:]
            if raw.endswith("```"):
                raw = raw[:-3]

            try:
                parsed_data = json.loads(raw.strip())
                normalized = [self._normalize_scenario(item) for item in parsed_data]
            except Exception as e:
                if attempt == 1:
                    raise ValueError(f"Failed to parse AI output as JSON: {e}\nRaw Output: {raw[:200]}...")
                correction_notes = [f"Return strict JSON only. Parsing failed: {e}"]
                continue

            validation = validate_test_scenarios(normalized, page_model, page_scope, page_info)
            self.last_scenario_validation = validation
            if validation["is_valid"] or attempt == 1:
                if validation["valid_cases"]:
                    return validation["valid_cases"]
                raise ValueError("AI scenarios were rejected by grounding validator and no valid cases remained.")
            correction_notes = validation["issues"][:8]

        raise ValueError("AI scenario generation failed after grounding retries.")

    def analyze_page_scope(
        self,
        url: str,
        website_title: str,
        page_info: dict,
        page_model: dict | None = None,
        custom_instruction: str = "",
    ) -> dict:
        """Analyze page context first, so test scenarios are derived from page scope instead of assumptions."""
        self.last_scope_validation = {}
        headings = [h.get('text', '')[:80] for h in page_info.get("headings", []) if isinstance(h, dict)]
        texts = [str(t)[:80] for t in page_info.get("texts", [])]
        buttons = [str(b)[:80] for b in page_info.get("buttons", [])]
        links = [f"{l.get('text', '')} -> {l.get('href', '')}" for l in page_info.get("links", []) if isinstance(l, dict)]
        forms = page_info.get("forms", [])
        apis = page_info.get("apis", [])
        sections = page_info.get("sections", [])
        tables = page_info.get("tables", [])
        lists = page_info.get("lists", [])
        navigation = page_info.get("navigation", [])
        metadata = page_info.get("metadata", {})
        fingerprint = page_info.get("page_fingerprint", {})
        crawled_pages = page_info.get("crawled_pages", [])

        allowed = build_allowed_vocabulary(page_model, None, page_info)
        correction_notes = []

        for attempt in range(2):
            prompt = (
                f"You are analyzing a web page before creating QA scenarios.\n"
                f"Target URL: {url}\n"
                f"Website Title: {website_title}\n"
                f"Detected headings: {headings[:15]}\n"
                f"Detected texts: {texts[:12]}\n"
                f"Detected buttons: {buttons[:15]}\n"
                f"Detected links: {links[:15]}\n"
                f"Detected forms: {forms[:6]}\n"
                f"Detected sections: {sections[:8]}\n"
                f"Detected tables: {tables[:4]}\n"
                f"Detected lists: {lists[:4]}\n"
                f"Detected navigation: {navigation[:4]}\n"
                f"Detected metadata: {metadata}\n"
                f"Detected page fingerprint: {fingerprint}\n"
                f"Detected linked pages sample: {crawled_pages[:5]}\n"
                f"Detected APIs: {apis[:10]}\n"
                f"ALLOWED VOCABULARY / FACTS: {json.dumps(allowed, ensure_ascii=False)}\n\n"
            )
            if page_model:
                prompt += f"NORMALIZED PAGE MODEL:\n{json.dumps(page_model, ensure_ascii=False)[:4000]}\n\n"
            if custom_instruction:
                prompt += f"USER INSTRUCTION: {custom_instruction}\n\n"
            if correction_notes:
                prompt += "GROUNDING CORRECTIONS FROM PREVIOUS ATTEMPT:\n"
                prompt += "\n".join(f"- {note}" for note in correction_notes[:8]) + "\n\n"
            if page_model and page_model.get("field_catalog"):
                prompt += f"SEMANTIC FIELD SUMMARY:\n{self._summarize_field_catalog(page_model)}\n\n"
            prompt += (
                "Decide what this page most likely is and what should be prioritized in QA.\n"
                "Do not assume page type from the URL alone.\n"
                "Infer the page context primarily from the actual content, sections, tables, lists, forms, controls, navigation, visible cues, and any linked page samples when available.\n"
                "Use the URL only as secondary context if the page content is ambiguous.\n"
                "Only mention modules and flows that are grounded in the page facts.\n"
                f"{PAGE_SCOPE_SCHEMA}\n"
                "Output ONLY valid JSON object.\n"
            )

            raw = self._call_chat(prompt).strip()
            if raw.startswith("```json"):
                raw = raw[7:]
            elif raw.startswith("```"):
                raw = raw[3:]
            if raw.endswith("```"):
                raw = raw[:-3]

            try:
                parsed = json.loads(raw.strip())
            except Exception as e:
                if attempt == 1:
                    raise ValueError(f"Failed to parse page scope analysis as JSON: {e}\nRaw Output: {raw[:200]}...")
                correction_notes = [f"Return strict JSON only. Parsing failed: {e}"]
                continue

            validation = validate_page_scope(parsed, page_model, page_info)
            self.last_scope_validation = validation
            if validation["is_valid"] or attempt == 1:
                return validation["page_scope"]
            correction_notes = validation["issues"][:8]

        raise ValueError("AI page scope analysis failed after grounding retries.")

    def _normalize_scenario(self, item: dict) -> dict:
        scenario = dict(item)
        scenario["Automation"] = self._normalize_automation_value(
            scenario.get("Automation") or self._infer_automation_level(scenario)
        )
        return scenario

    def _normalize_automation_value(self, value: object) -> str:
        text = str(value or "").strip().lower()
        if text in {"yes", "true", "auto", "automated"}:
            return "auto"
        if text in {"semi", "semi-auto", "partial"}:
            return "semi-auto"
        if text in {"no", "false", "manual"}:
            return "manual"
        return "auto"

    def _summarize_field_catalog(self, page_model: dict) -> str:
        rows = []
        for field in page_model.get("field_catalog", [])[:20]:
            rows.append(
                {
                    "field_key": field.get("field_key", ""),
                    "semantic_type": field.get("semantic_type", ""),
                    "semantic_label": field.get("semantic_label", ""),
                    "aliases": field.get("aliases", [])[:6],
                    "required": field.get("required", False),
                    "tag": field.get("tag", ""),
                    "type": field.get("type", ""),
                }
            )
        return json.dumps(rows, ensure_ascii=False)

    def _infer_automation_level(self, item: dict) -> str:
        text = "\n".join(
            str(item.get(key, "")) for key in ["Module", "Title", "Steps to Reproduce", "Expected Result"]
        ).lower()
        manual_terms = (
            "captcha", "otp", "2fa", "email received", "sms", "third-party redirect",
            "pdf download", "print dialog", "payment gateway", "map", "drag", "canvas",
            "upload", "video playback", "audio playback", "performance", "visual layout",
            "cross-browser", "responsive", "accessibility audit", "seo", "shadow dom",
            "iframe", "embedded widget", "captcha verification", "magic link", "device verification"
        )
        semi_auto_terms = (
            "download", "share", "open new tab", "modal", "toast", "tooltip", "hover",
            "pagination", "filter", "sort", "table", "chart", "graph", "carousel",
            "drawer", "rich text", "editor", "combobox", "date picker", "time picker",
            "infinite scroll", "virtualized", "sso", "single sign-on", "continue with google", "continue with microsoft"
        )
        if any(term in text for term in manual_terms):
            return "manual"
        if any(term in text for term in semi_auto_terms):
            return "semi-auto"
        return "auto"



    def analyze_results(self, output: str, error: str, website_title: str) -> str:
        """Analisa hasil test dan beri rekomendasi - STATELESS."""
        failed_lines = [l for l in output.split("\n") if "[FAIL]" in l][:10]
        failed_str   = "\n".join(failed_lines) or "No explicit FAIL lines found"
        short_error  = error.strip()[:600] if error else "No stderr"

        prompt = (
            f"QA Test Results for '{website_title}':\n\n"
            f"FAILED TESTS:\n{failed_str}\n\n"
            f"ERROR OUTPUT:\n{short_error}\n\n"
            "Explain each failure briefly and suggest fixes. Max 250 words. Be specific."
        )
        return self._call_stateless(prompt).strip()

    def generate_bug_report(self, test_name, url, error, steps) -> str:
        steps_str   = "\n".join(f"{i+1}. {s}" for i, s in enumerate(steps))
        short_error = error.strip()[:400]
        prompt = (
            "Write a concise QA bug report.\n"
            f"Feature: {test_name}\nURL: {url}\nError: {short_error}\nSteps:\n{steps_str}\n\n"
            "Format: Title | Severity | Description | Steps | Expected | Actual"
        )
        return self._call_stateless(prompt).strip()

    def reset_chat(self) -> None:
        cfg = self._pool[self._idx]
        self.chat = self.client.chats.create(
            model=cfg["name"],
            config=self.config
        )
        self._chat_turns = 0

    def generate_executive_summary(self, url: str, website_title: str, page_info: dict, parsed_data: list) -> str:
        """Membuat Ringkasan Eksekutif .md berdasarkan hasil test list of dicts"""
        sample_scenarios = json.dumps(parsed_data[:5], indent=2)
        prompt = (
            f"I have just generated a QA Test Scope for '{website_title}' ({url}).\n"
            f"Here is a snippet of elements we found: {str(page_info)[:500]}...\n\n"
            f"Here is a snippet of the generated scenarios (first 5):\n{sample_scenarios}\n"
            f"Total Test Cases Generated: {len(parsed_data)}\n\n"
            "=== YOUR TASK ===\n"
            "Create a professional Markdown (.md) Executive Summary for the QA Plan of this website.\n"
            "Include:\n"
            "1. Website Name & Target URL\n"
            "2. Brief Overview of the site's capability based on the elements\n"
            "3. QA Test Case Statistics (Total test cases created, grouped by Positive/Negative roughly by observing the snippet)\n"
            "4. Top 3 Biggest Security or Functionality Risks recommended to be prioritized\n\n"
            "Do NOT output markdown format block like ```markdown, output raw markdown text. Keep it concise, 300 words max."
        )
        return self._call_stateless(prompt).strip()
