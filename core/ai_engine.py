import os
import time
import json
from google import genai
from google.genai import types
from dotenv import load_dotenv

load_dotenv()

SYSTEM_PROMPT = """
You are an Expert QA Automation Engineer and Test Scenario Designer.
Your job is to generate THOROUGH test cases that verify EVERYTHING on a web page.

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
        custom_instruction: str = "",
        csv_sep: str = ",",
        screenshot_path: str = ""
    ) -> str:
        """Menghasilkan CSV berisi Test Scenario berdasarkan scan halaman (STATEFUL)."""
        headings = [h.get('text', '')[:50] for h in page_info.get("headings", [])]
        texts    = [t.get('text', '')[:50] for t in page_info.get("texts", [])]
        buttons  = page_info.get("buttons", [])
        links    = [l.get('text', '')[:50] for l in page_info.get("links", [])]
        forms    = page_info.get("forms", [])
        apis     = page_info.get("apis", [])

        prompt = (
            f"I am building a QA Test Scope for the website '{website_title}' ({url}).\n"
            f"HTML analysis:\n"
            f"- Headings: {headings[:15]}\n"
            f"- Texts: {texts[:10]}\n"
            f"- Buttons: {buttons[:15]}\n"
            f"- Links: {links[:15]}\n"
            f"- Forms: {forms[:5]}\n"
            f"- APIs (JS endpoints): {apis}\n\n"
        )
        if custom_instruction:
            prompt += f"USER INSTRUCTION: {custom_instruction}\n\n"
            
        prompt += (
            "=== YOUR TASK ===\n"
            "Based on the page analysis and user instructions above, act as an Expert QA Test Designer.\n"
            "Generate a comprehensive list of Test Scenarios in strict JSON format.\n"
            "MAXIMIZE THE NUMBER OF TEST CASES! You MUST include:\n"
            "- Positive test cases (Happy paths)\n"
            "- Negative test cases (Invalid inputs)\n"
            "- Edge cases / Boundary values\n"
            "- Error validations (Missing fields, wrong formats, etc.)\n"
            "- Specific cases requested via 'USER INSTRUCTION' (e.g., test all listed usernames).\n\n"
            "CRITICAL RULES:\n"
            "- ONLY output a valid JSON array of objects. Do NOT include markdown formatting like ```json or ```.\n"
            "- Each object in the JSON array MUST have EXACTLY these keys:\n"
            "  \"ID\", \"Module\", \"Category\", \"Test Type\", \"Title\", \"Precondition\", \"Steps to Reproduce\", \"Expected Result\", \"Actual Result\", \"Severity\", \"Priority\", \"Evidence\"\n"
            "- For the 'ID' column, use an auto-prefix based on the Module name (e.g., 'LGN-001' for Login, 'REG-001' for Register) instead of just numbers.\n"
            "- The 'Test Type' column must strictly be 'Positive' or 'Negative'.\n"
            f"- For 'Steps to Reproduce', step 1 MUST ALWAYS be '1. Open the site {url}'. Step 2 and beyond are the actual interactions.\n"
            "- NEVER use the word 'Enter' when describing typing actions (e.g., 'Enter username'). ALWAYS use the word 'Input' instead (e.g., 'Input username').\n"
            "- 'Actual Result' and 'Evidence' should be left empty strings \"\" since this is planning phase.\n"
        )
        if self._chat_turns >= 6:
            self.reset_chat()

        content_parts = [prompt]
        if screenshot_path and os.path.exists(screenshot_path):
            try:
                from PIL import Image
                img = Image.open(screenshot_path)
                content_parts.insert(0, img)
            except Exception as e:
                pass

        raw = self._call_chat(content_parts)
        self._chat_turns += 1
        
        # Clean specific markdown fallbacks if AI disobeys
        raw = raw.strip()
        if raw.startswith("```json"):
            raw = raw[7:]
        elif raw.startswith("```"):
            raw = raw[3:]
        if raw.endswith("```"):
            raw = raw[:-3]
            
        try:
            parsed_data = json.loads(raw.strip())
            return parsed_data
        except Exception as e:
            # Jika gagal parse JSON murni
            raise ValueError(f"Failed to parse AI output as JSON: {e}\nRaw Output: {raw[:200]}...")



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
