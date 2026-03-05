import os
import time
import json
import re
import math
from google import genai
from google.genai import types
from dotenv import load_dotenv
from modules.test_case_generator.src.case_memory import load_case_memory_snapshot
from core.confidence import build_historical_confidence_signal, compute_composite_confidence
from core.guardrails import build_allowed_vocabulary, build_task_contract, validate_page_scope, validate_test_scenarios
from core.site_profiles import get_failure_memory, get_ranked_selector_candidates

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
- Scope-focused cases that match the actual page context discovered from the page itself.

Each object in the JSON array must have EXACTLY these keys in order:
ID, Module, Category, Test Type, Risk Rating, Anchored Selector, Title, Precondition, Steps to Reproduce, Expected Result, Actual Result, Severity, Priority, Evidence, Automation

CRITICAL RULES:
- ONLY output a valid JSON array of objects.
- 'Risk Rating' must be one of: High, Medium, Low. Calculate this based on a 3x3 Risk Matrix (Impact x Probability).
- 'Anchored Selector' MUST be a concrete element ID, data-testid, or unique DOM path found in the provided context (e.g., "#login-btn", "form > input[name=email]"). If no specific element, use the nearest section ID or heading.
- Do NOT include markdown formatting like ```json or ```.
- Start directly with the JSON opening bracket `[` and end with `]`.
- For multi-line fields like 'Steps to Reproduce', use the literal characters '\\n' to represent new lines (e.g., "1. Step 1\\n2. Step 2").
- Provide realistic values for Severity and Priority (P1-P3).
- 'Actual Result' and 'Evidence' can be left as empty ("").
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
            raise ValueError("GEMINI_API_KEY is not set in the .env file.")
        self.client = genai.Client(api_key=api_key)
        self._pool       = MODEL_POOL
        self._idx        = 0
        self._chat_turns = 0
        self.deterministic_mode = str(os.getenv("QA_AI_DETERMINISTIC_MODE", "1")).strip().lower() in {"1", "true", "yes", "on"}
        self.last_scope_validation = {}
        self.last_scenario_validation = {}
        self.last_routing = {}
        self.usage_events = []
        self._active_usage_stage = "generic"
        self._init_model()

    def _init_model(self) -> None:
        cfg    = self._pool[self._idx]
        kwargs = {}
        if cfg["supports_system"]:
            kwargs["system_instruction"] = SYSTEM_PROMPT
        kwargs["temperature"] = 0.1 if self.deterministic_mode else 0.35
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
                    response = self.chat.send_message(full).text
                    self._record_usage(self._active_usage_stage, "chat", cfg["name"], full, response)
                    return response
                except Exception as e:
                    if attempt < max_retries - 1:
                        time.sleep(3 ** attempt)
                        continue
                    # If it fails 3 times on this model, fall back to the next model
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
                        response = self.client.models.generate_content(
                            model=cfg["name"],
                            contents=full,
                        ).text
                        self._record_usage(self._active_usage_stage, "stateless", cfg["name"], full, response)
                        return response
                    else:
                        response = self.client.models.generate_content(
                            model=cfg["name"],
                            contents=prompt,
                            config=self.config
                        ).text
                        self._record_usage(self._active_usage_stage, "stateless", cfg["name"], prompt, response)
                        return response
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
        allowed = build_allowed_vocabulary(page_model, page_scope, page_info)
        fact_pack = self._build_fact_pack(page_info, page_model, page_scope)
        feedback_signal = self._build_feedback_learning_signal(url, page_model, page_scope)
        route = self._build_task_route(
            task_type="scenario_generation",
            page_info=page_info,
            page_model=page_model,
            page_scope=page_scope,
            fact_pack=fact_pack,
            custom_instruction=custom_instruction,
        )
        self.last_routing["scenario_generation"] = route
        scenario_volume = self._derive_scenario_volume(page_model, page_scope, fact_pack, custom_instruction)
        correction_notes = []
        if self._should_use_conservative_scenario_fallback(page_model, page_scope, fact_pack):
            heuristic_cases = self._heuristic_scenarios_from_facts(
                url,
                page_info,
                page_model,
                page_scope,
                custom_instruction,
                target_count=max(12, min(80, scenario_volume["target_count"])),
            )
            validation = validate_test_scenarios(
                heuristic_cases,
                page_model,
                page_scope,
                page_info,
                custom_instruction=custom_instruction,
            )
            if validation["valid_cases"]:
                calibrated = self._annotate_case_confidence(validation["valid_cases"])
                validation = {**validation, "valid_cases": calibrated}
                prioritized = self._prioritize_cases_by_risk(calibrated, page_scope, feedback_signal)
                quality = self._build_ai_quality_report(
                    validation=validation,
                    scenario_volume=scenario_volume,
                    fact_pack=fact_pack,
                    route={**route, "mode": "heuristic", "reason": "sparse grounded facts require conservative scenario fallback"},
                    critique_report={"issues": ["heuristic fallback path used"], "removed_count": 0},
                    feedback_signal=feedback_signal,
                )
                self.last_scenario_validation = {
                    **validation,
                    "composite_confidence": {},
                    "routing": quality["routing"],
                    "fact_pack_summary": fact_pack.get("summary", {}),
                    "historical_signal": {},
                    "task_contract": build_task_contract(page_model, page_scope, page_info, custom_instruction=custom_instruction),
                    "generation_target": scenario_volume,
                    "valid_cases": prioritized,
                    "feedback_learning_signal": feedback_signal,
                    "self_critique": quality["self_critique"],
                    "ai_quality": quality["ai_quality"],
                }
                return prioritized

        max_attempts = 3
        for attempt in range(max_attempts):
            task_contract = build_task_contract(page_model, page_scope, page_info, custom_instruction=custom_instruction)
            context_pack = self._build_context_pack(
                url=url,
                website_title=website_title,
                page_info=page_info,
                page_model=page_model,
                page_scope=page_scope,
                custom_instruction=custom_instruction,
                allowed=allowed,
                fact_pack=fact_pack,
                compact=bool(route.get("compact_context", False)),
                route=route,
            )
            prompt = (
                f"I am building a QA Test Scope for the website '{website_title}' ({url}).\n"
                "PASS 1 has already extracted grounded facts. PASS 2 is your reasoning step.\n"
                "Use only the grounded structured context and fact pack below.\n\n"
                f"CONTEXT PACK JSON:\n{json.dumps(context_pack, ensure_ascii=False)}\n\n"
            )
            if correction_notes:
                prompt += "GROUNDING CORRECTIONS FROM PREVIOUS ATTEMPT:\n"
                prompt += "\n".join(f"- {note}" for note in correction_notes[:8]) + "\n\n"

            prompt += (
                "=== YOUR TASK ===\n"
                "Based on the grounded page analysis and user instructions above, act as an Expert QA Test Designer.\n"
                "Generate a comprehensive list of Test Scenarios in strict JSON format.\n"
                "Use only modules, flows, fields, controls, sections, states, and APIs supported by the page analysis.\n"
                "Maximize coverage only inside grounded evidence. If evidence is weak, produce fewer cases.\n"
                f"- Target at least {scenario_volume['target_count']} grounded scenarios when evidence allows.\n"
                f"- Minimum acceptable validated scenarios is {scenario_volume['min_valid_count']}.\n"
                "You MUST include when grounded:\n"
                "- Positive test cases (Happy paths)\n"
                "- Negative test cases (Invalid inputs)\n"
                "- Edge cases / Boundary values\n"
                "- Error validations (Missing fields, wrong formats, etc.)\n"
                "- Scope-based cases that are relevant to the actual page type inferred from the actual page content and structure.\n"
                "- Specific cases requested via 'USER INSTRUCTION' when they are still grounded in the detected page facts.\n\n"
                "CRITICAL RULES:\n"
                "- Follow TASK CONTRACT strictly. Prefer omission over invention.\n"
                "- If the user instruction mentions unsupported surfaces, do NOT create a testcase for them.\n"
                "- Every testcase must align with TASK CONTRACT focus terms or grounded page surfaces.\n"
                "- Every interactive testcase must mention at least one concrete grounded target such as a detected field label, button text, link text, section heading, component label, or state label.\n"
                "- If you cannot name a concrete grounded target from the context pack, omit that testcase.\n"
                "- Do not generalize from common website patterns. Use only what is explicitly present in the context pack.\n"
                "- ONLY output a valid JSON array of objects. Do NOT include markdown formatting like ```json or ```.\n"
                "- Each object in the JSON array MUST have EXACTLY these keys:\n"
                "  \"ID\", \"Module\", \"Category\", \"Test Type\", \"Risk Rating\", \"Anchored Selector\", \"Title\", \"Precondition\", \"Steps to Reproduce\", \"Expected Result\", \"Actual Result\", \"Severity\", \"Priority\", \"Evidence\", \"Automation\"\n"
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

            self._active_usage_stage = "scenario_generation"
            if route.get("mode") == "stateless":
                raw = self._call_stateless(prompt)
            else:
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

            normalized, critique_report = self._self_critique_and_refine_cases(normalized, allowed, page_scope, custom_instruction)
            normalized = self._prioritize_cases_by_risk(normalized, page_scope, feedback_signal)

            validation = validate_test_scenarios(
                normalized,
                page_model,
                page_scope,
                page_info,
                custom_instruction=custom_instruction,
            )
            validation = {**validation, "valid_cases": self._annotate_case_confidence(validation.get("valid_cases", []))}
            base_scope = dict(page_scope or {})
            if "ai_confidence" in base_scope:
                base_scope["confidence"] = base_scope.get("ai_confidence", base_scope.get("confidence", 0))
            historical_signal = build_historical_confidence_signal(
                url=url,
                page_model=page_model,
                page_scope=page_scope,
                site_profile=(page_model or {}).get("site_profile", {}),
            )
            composite_confidence = compute_composite_confidence(
                page_scope=base_scope,
                page_info=page_info,
                page_model=page_model,
                scope_validation=self.last_scope_validation,
                scenario_validation=validation,
                historical_signal=historical_signal,
            )
            quality = self._build_ai_quality_report(
                validation=validation,
                scenario_volume=scenario_volume,
                fact_pack=fact_pack,
                route=route,
                critique_report=critique_report,
                feedback_signal=feedback_signal,
            )
            self.last_scenario_validation = {
                **validation,
                "composite_confidence": composite_confidence,
                "routing": quality["routing"],
                "fact_pack_summary": fact_pack.get("summary", {}),
                "historical_signal": historical_signal,
                "task_contract": task_contract,
                "generation_target": scenario_volume,
                "feedback_learning_signal": feedback_signal,
                "self_critique": quality["self_critique"],
                "ai_quality": quality["ai_quality"],
            }
            valid_case_count = len(validation["valid_cases"])
            if valid_case_count >= scenario_volume["min_valid_count"] and (validation["is_valid"] or attempt == max_attempts - 1):
                if validation["valid_cases"]:
                    return self._prioritize_cases_by_risk(validation["valid_cases"], page_scope, feedback_signal)
                raise ValueError("AI scenarios were rejected by grounding validator and no valid cases remained.")
            if attempt == max_attempts - 1 and validation["valid_cases"]:
                return self._prioritize_cases_by_risk(validation["valid_cases"], page_scope, feedback_signal)
            correction_notes = validation["issues"][:8]
            correction_notes.extend(quality["ai_quality"].get("retry_hints", [])[:3])
            if validation.get("rejected_cases"):
                correction_notes.extend(
                    f"{item.get('case', {}).get('ID', 'UNKNOWN')}: {', '.join(item.get('issues', [])[:2])}"
                    for item in validation["rejected_cases"][:4]
                )
            if valid_case_count < scenario_volume["min_valid_count"]:
                correction_notes.append(
                    f"Increase grounded scenarios: produced {valid_case_count}, minimum required {scenario_volume['min_valid_count']}."
                )

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
        allowed = build_allowed_vocabulary(page_model, None, page_info)
        fact_pack = self._build_fact_pack(page_info, page_model, None)
        route = self._build_task_route(
            task_type="page_scope",
            page_info=page_info,
            page_model=page_model,
            page_scope=None,
            fact_pack=fact_pack,
            custom_instruction=custom_instruction,
        )
        self.last_routing["page_scope"] = route
        correction_notes = []

        if route.get("mode") == "heuristic":
            parsed = self._heuristic_scope_from_facts(page_model, page_info, fact_pack)
            validation = validate_page_scope(parsed, page_model, page_info, custom_instruction=custom_instruction)
            raw_ai_confidence = float(validation["page_scope"].get("confidence", 0.0) or 0.0)
            historical_signal = build_historical_confidence_signal(
                url=url,
                page_model=page_model,
                page_scope=validation["page_scope"],
                site_profile=(page_model or {}).get("site_profile", {}),
            )
            composite = compute_composite_confidence(
                page_scope=validation["page_scope"],
                page_info=page_info,
                page_model=page_model,
                scope_validation=validation,
                historical_signal=historical_signal,
            )
            validation["page_scope"]["confidence"] = composite["score"]
            validation["page_scope"]["confidence_breakdown"] = composite["breakdown"]
            validation["page_scope"]["confidence_explanation"] = composite["explanation"]
            validation["page_scope"]["confidence_class"] = composite["confidence_class"]
            validation["page_scope"]["ai_confidence"] = raw_ai_confidence
            self.last_scope_validation = {
                **validation,
                "composite_confidence": composite,
                "routing": route,
                "fact_pack_summary": fact_pack.get("summary", {}),
                "historical_signal": historical_signal,
                "task_contract": validation.get("task_contract", {}),
            }
            return validation["page_scope"]

        for attempt in range(2):
            task_contract = build_task_contract(page_model, None, page_info, custom_instruction=custom_instruction)
            context_pack = self._build_context_pack(
                url=url,
                website_title=website_title,
                page_info=page_info,
                page_model=page_model,
                custom_instruction=custom_instruction,
                allowed=allowed,
                fact_pack=fact_pack,
                compact=bool(route.get("compact_context", False)),
                route=route,
            )
            prompt = (
                "You are analyzing a web page before creating QA scenarios.\n"
                "PASS 1 has already extracted grounded page facts. PASS 2 is your reasoning step.\n"
                "Use only the grounded structured context and fact pack below.\n\n"
                f"CONTEXT PACK JSON:\n{json.dumps(context_pack, ensure_ascii=False)}\n\n"
            )
            if correction_notes:
                prompt += "GROUNDING CORRECTIONS FROM PREVIOUS ATTEMPT:\n"
                prompt += "\n".join(f"- {note}" for note in correction_notes[:8]) + "\n\n"
            prompt += (
                "Decide what this page most likely is and what should be prioritized in QA.\n"
                "Do not assume page type from the URL alone.\n"
                "Infer the page context primarily from the actual content, sections, tables, lists, forms, controls, navigation, visible cues, and any linked page samples when available.\n"
                "Use the URL only as secondary context if the page content is ambiguous.\n"
                "Only mention modules and flows that are grounded in the page facts.\n"
                "Follow TASK CONTRACT strictly. Prefer omission over invention.\n"
                "If the user instruction mentions unsupported surfaces, do not force them into page scope.\n"
                f"{PAGE_SCOPE_SCHEMA}\n"
                "Output ONLY valid JSON object.\n"
            )

            self._active_usage_stage = "page_scope"
            if route.get("mode") == "stateless":
                raw = self._call_stateless(prompt).strip()
            else:
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

            validation = validate_page_scope(parsed, page_model, page_info, custom_instruction=custom_instruction)
            raw_ai_confidence = float(validation["page_scope"].get("confidence", 0.0) or 0.0)
            historical_signal = build_historical_confidence_signal(
                url=url,
                page_model=page_model,
                page_scope=validation["page_scope"],
                site_profile=(page_model or {}).get("site_profile", {}),
            )
            composite = compute_composite_confidence(
                page_scope=validation["page_scope"],
                page_info=page_info,
                page_model=page_model,
                scope_validation=validation,
                historical_signal=historical_signal,
            )
            validation["page_scope"]["confidence"] = composite["score"]
            validation["page_scope"]["confidence_breakdown"] = composite["breakdown"]
            validation["page_scope"]["confidence_explanation"] = composite["explanation"]
            validation["page_scope"]["confidence_class"] = composite["confidence_class"]
            validation["page_scope"]["ai_confidence"] = raw_ai_confidence
            self.last_scope_validation = {**validation, "composite_confidence": composite}
            self.last_scope_validation["routing"] = route
            self.last_scope_validation["fact_pack_summary"] = fact_pack.get("summary", {})
            self.last_scope_validation["historical_signal"] = historical_signal
            self.last_scope_validation["task_contract"] = validation.get("task_contract", task_contract)
            if validation["is_valid"] or attempt == 1:
                return validation["page_scope"]
            correction_notes = validation["issues"][:8]

        raise ValueError("AI page scope analysis failed after grounding retries.")

    def _normalize_scenario(self, item: dict) -> dict:
        scenario = dict(item)
        scenario["Automation"] = self._normalize_automation_value(
            scenario.get("Automation") or self._infer_automation_level(scenario)
        )
        if "Risk Rating" not in scenario:
            scenario["Risk Rating"] = "Medium"
        if "Anchored Selector" not in scenario:
            scenario["Anchored Selector"] = ""
        return scenario

    def _self_critique_and_refine_cases(
        self,
        cases: list[dict],
        allowed: dict,
        page_scope: dict | None,
        custom_instruction: str,
    ) -> tuple[list[dict], dict]:
        refined = []
        issues = []
        removed = 0
        seen = set()
        page_scope = page_scope or {}
        instruction_lower = str(custom_instruction or "").strip().lower()
        for case in cases:
            normalized = self._normalize_scenario(case)
            signature = self._case_signature(normalized)
            if signature in seen:
                removed += 1
                issues.append(f"duplicate removed: {normalized.get('ID', 'UNKNOWN')}")
                continue
            seen.add(signature)
            title = str(normalized.get("Title", "")).strip()
            steps = str(normalized.get("Steps to Reproduce", "")).strip()
            if len(title) < 8 or len(steps) < 18:
                removed += 1
                issues.append(f"low-information case removed: {normalized.get('ID', 'UNKNOWN')}")
                continue
            if "only negative" in instruction_lower and str(normalized.get("Test Type", "")).strip().lower() != "negative":
                removed += 1
                issues.append(f"instruction-only-negative removed: {normalized.get('ID', 'UNKNOWN')}")
                continue
            if "only positive" in instruction_lower and str(normalized.get("Test Type", "")).strip().lower() != "positive":
                removed += 1
                issues.append(f"instruction-only-positive removed: {normalized.get('ID', 'UNKNOWN')}")
                continue
            normalized["_risk_score"] = self._risk_score_case(normalized, page_scope, allowed)
            refined.append(normalized)
        return refined, {"removed_count": removed, "issues": issues[:20]}

    def _prioritize_cases_by_risk(
        self,
        cases: list[dict],
        page_scope: dict | None,
        feedback_signal: dict | None,
    ) -> list[dict]:
        page_scope = page_scope or {}
        feedback_signal = feedback_signal or {}
        risk_terms = [str(item).strip().lower() for item in list(page_scope.get("risks", []) or [])[:12] if str(item).strip()]
        hotspot_terms = [str(item).strip().lower() for item in list(feedback_signal.get("hotspot_terms", []) or [])[:12] if str(item).strip()]
        ranked = []
        for idx, case in enumerate(cases):
            score = float(case.get("_risk_score", 0.0) or 0.0)
            text = " ".join(str(case.get(key, "")) for key in ("Module", "Title", "Expected Result", "Steps to Reproduce")).lower()
            score += sum(0.25 for term in risk_terms if term and term in text)
            score += sum(0.35 for term in hotspot_terms if term and term in text)
            ranked.append((score, idx, case))
        ranked.sort(key=lambda item: (-item[0], item[1]))
        return [item[2] for item in ranked]

    def _annotate_case_confidence(self, cases: list[dict]) -> list[dict]:
        rows = []
        for case in cases:
            row = dict(case)
            grounding = row.get("_grounding", {}) if isinstance(row.get("_grounding", {}), dict) else {}
            alignment = row.get("_task_alignment", {}) if isinstance(row.get("_task_alignment", {}), dict) else {}
            grounding_score = float(grounding.get("score", 0.0) or 0.0)
            coverage_score = float(grounding.get("coverage_score", 0.0) or 0.0)
            alignment_score = float(alignment.get("score", 0.0) or 0.0)
            calibrated = max(0.0, min(1.0, (grounding_score * 0.45) + (coverage_score * 0.3) + (alignment_score * 0.25)))
            row["_calibrated_confidence"] = round(calibrated, 3)
            rows.append(row)
        return rows

    def _risk_score_case(self, case: dict, page_scope: dict, allowed: dict) -> float:
        score = 0.0
        severity = str(case.get("Severity", "")).strip().lower()
        priority = str(case.get("Priority", "")).strip().lower()
        risk_rating = str(case.get("Risk Rating", "")).strip().lower()
        case_type = str(case.get("Test Type", "")).strip().lower()
        title = " ".join(str(case.get(key, "")) for key in ("Title", "Expected Result", "Steps to Reproduce")).lower()
        
        if risk_rating == "high":
            score += 2.0
        elif risk_rating == "medium":
            score += 1.0
        else:
            score += 0.4

        if severity in {"critical", "highest", "high", "blocker"}:
            score += 1.6
        elif severity in {"medium"}:
            score += 1.0
        else:
            score += 0.5
        if priority in {"p0", "p1", "highest", "high"}:
            score += 1.4
        elif priority in {"p2", "medium"}:
            score += 0.9
        else:
            score += 0.4
        if case_type == "negative":
            score += 0.35
        if any(token in title for token in ("auth", "login", "payment", "checkout", "session", "permission", "security", "token")):
            score += 0.45
        if allowed.get("page_facts", {}).get("auth", False) and any(token in title for token in ("login", "password", "session")):
            score += 0.25
        return round(score, 2)

    def _build_feedback_learning_signal(
        self,
        url: str,
        page_model: dict | None,
        page_scope: dict | None,
    ) -> dict:
        snapshot = load_case_memory_snapshot(url, page_model=page_model or {}, page_scope=page_scope or {})
        patterns = list(snapshot.get("patterns", []) or [])
        hotspot_terms = []
        preferred_modules = []
        for pattern in patterns[:8]:
            module = str(pattern.get("module", "")).strip()
            if module and module.lower() not in [item.lower() for item in preferred_modules]:
                preferred_modules.append(module)
            title = str(pattern.get("example_title", "")).strip()
            for token in re.findall(r"[a-z0-9_]+", title.lower()):
                if len(token) >= 5 and token not in hotspot_terms:
                    hotspot_terms.append(token)
                    if len(hotspot_terms) >= 12:
                        break
            if len(hotspot_terms) >= 12:
                break
        return {
            "pattern_count": int(snapshot.get("summary", {}).get("pattern_count", 0) or 0),
            "preferred_modules": preferred_modules[:8],
            "hotspot_terms": hotspot_terms[:12],
            "updated_at": str(snapshot.get("summary", {}).get("updated_at", "")),
        }

    def _build_ai_quality_report(
        self,
        validation: dict,
        scenario_volume: dict,
        fact_pack: dict,
        route: dict,
        critique_report: dict,
        feedback_signal: dict,
    ) -> dict:
        valid_cases = list(validation.get("valid_cases", []) or [])
        rejected_cases = list(validation.get("rejected_cases", []) or [])
        target = int(scenario_volume.get("target_count", 0) or 0)
        valid_count = len(valid_cases)
        rejected_count = len(rejected_cases)
        grounded_count = int(validation.get("grounding_summary", {}).get("valid_grounded_cases", 0) or 0)
        grounding_ratio = (grounded_count / valid_count) if valid_count else 0.0
        rejection_ratio = (rejected_count / max(1, valid_count + rejected_count))
        target_coverage = (valid_count / max(1, target))
        contradiction_count = sum(
            1
            for item in rejected_cases
            if any("contradiction" in str(issue).lower() for issue in item.get("issues", []))
        )
        duplicate_count = sum(
            1
            for item in rejected_cases
            if any("duplicate" in str(issue).lower() for issue in item.get("issues", []))
        )
        intent_count = sum(
            1
            for item in rejected_cases
            if any("intent-to-action" in str(issue).lower() for issue in item.get("issues", []))
        )
        hallucination_risk = 1.0 - max(0.0, min(1.0, (grounding_ratio * 0.55) + ((1.0 - rejection_ratio) * 0.25) + (min(1.0, target_coverage) * 0.2)))
        risk_class = "low" if hallucination_risk < 0.28 else ("medium" if hallucination_risk < 0.5 else "high")
        retry_hints = []
        if grounding_ratio < 0.65:
            retry_hints.append("increase concrete grounded labels in steps and expected result")
        if rejection_ratio > 0.35:
            retry_hints.append("remove unsupported surfaces and ambiguous flow assumptions")
        if target_coverage < 0.8:
            retry_hints.append("expand grounded negative and boundary scenarios")
        if contradiction_count > 0:
            retry_hints.append("fix contradictory test type vs expected outcome semantics")
        case_confidences = [float(item.get("_calibrated_confidence", 0.0) or 0.0) for item in valid_cases]
        avg_case_confidence = sum(case_confidences) / len(case_confidences) if case_confidences else 0.0
        return {
            "routing": {
                **dict(route or {}),
                "deterministic_mode": bool(self.deterministic_mode),
            },
            "self_critique": {
                "removed_count": int(critique_report.get("removed_count", 0) or 0),
                "issues": list(critique_report.get("issues", []) or [])[:20],
            },
            "ai_quality": {
                "grounding_ratio": round(grounding_ratio, 3),
                "target_coverage": round(target_coverage, 3),
                "rejection_ratio": round(rejection_ratio, 3),
                "hallucination_risk_score": round(hallucination_risk, 3),
                "hallucination_risk_class": risk_class,
                "deterministic_mode": bool(self.deterministic_mode),
                "feedback_pattern_count": int(feedback_signal.get("pattern_count", 0) or 0),
                "average_case_confidence": round(avg_case_confidence, 3),
                "contradiction_rejections": contradiction_count,
                "duplicate_rejections": duplicate_count,
                "intent_rejections": intent_count,
                "fact_count": int(fact_pack.get("summary", {}).get("fact_count", 0) or 0),
                "retry_hints": retry_hints[:5],
            },
        }

    def _case_signature(self, case: dict) -> str:
        text = " ".join(
            str(case.get(key, "")).strip().lower()
            for key in ("Module", "Test Type", "Title", "Steps to Reproduce", "Expected Result")
        )
        text = re.sub(r"\s+", " ", text).strip()
        text = re.sub(r"\d+", "#", text)
        return text[:420]

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

    def _build_context_pack(
        self,
        url: str,
        website_title: str,
        page_info: dict,
        page_model: dict | None = None,
        page_scope: dict | None = None,
        custom_instruction: str = "",
        allowed: dict | None = None,
        fact_pack: dict | None = None,
        compact: bool = False,
        route: dict | None = None,
    ) -> dict:
        page_model = page_model or {}
        page_scope = page_scope or {}
        allowed = allowed or {}
        fact_pack = fact_pack or {}
        route = route or {}
        site_profile = page_model.get("site_profile", {}) if page_model else {}
        heuristic_scope = page_model.get("heuristic_scope", {})
        priority_terms = self._priority_terms(page_model, page_scope)
        prioritized_components = self._prioritize_components(
            page_model.get("component_catalog", []),
            priority_terms,
            limit=8 if compact else 12,
        )
        prioritized_flows = self._prioritize_flows(
            page_model.get("possible_flows", []),
            priority_terms,
            limit=6 if compact else 8,
        )
        case_memory = load_case_memory_snapshot(url, page_model=page_model, page_scope=page_scope)
        task_contract = build_task_contract(page_model, page_scope, page_info, custom_instruction=custom_instruction)
        return {
            "context_pack_version": 6,
            "page_identity": {
                "website_title": website_title,
                "target_url": url,
                "page_title": page_info.get("title", ""),
                "metadata": page_info.get("metadata", {}),
            },
            "routing": {
                "mode": route.get("mode", ""),
                "reason": route.get("reason", ""),
                "compact_context": bool(route.get("compact_context", False)),
            },
            "page_facts": allowed.get("page_facts", {}),
            "fingerprint": page_info.get("page_fingerprint", {}),
            "task_contract": task_contract,
            "headings": self._prioritize_text_items(
                [h.get("text", "")[:120] for h in page_info.get("headings", []) if isinstance(h, dict)],
                priority_terms,
                limit=6 if compact else 12,
            ),
            "texts": self._prioritize_text_items(page_info.get("texts", []), priority_terms, limit=6 if compact else 10, max_len=140),
            "buttons": self._prioritize_text_items(page_info.get("buttons", []), priority_terms, limit=8 if compact else 12, max_len=100),
            "links": [
                {"text": link.get("text", "")[:80], "href": link.get("href", "")[:140], "context": link.get("context", "")[:100]}
                for link in self._prioritize_links(page_info.get("links", []), priority_terms, limit=6 if compact else 12)
                if isinstance(link, dict)
            ],
            "forms": self._summarize_forms(page_model, page_info),
            "section_graph": self._summarize_section_graph(page_model, priority_terms),
            "fact_pack": fact_pack,
            "components": prioritized_components,
            "possible_flows": prioritized_flows,
            "heuristic_scope": heuristic_scope,
            "discovered_states": page_info.get("discovered_states", [])[:8],
            "linked_pages": [
                {
                    "url": page.get("url", ""),
                    "title": page.get("title", ""),
                    "headings": [heading.get("text", "") for heading in page.get("headings", [])[:3]],
                    "fingerprint": page.get("fingerprint", {}) if isinstance(page.get("fingerprint", {}), dict) else {},
                }
                for page in page_info.get("crawled_pages", [])[:5]
            ],
            "allowed_vocabulary": {
                "component_types": allowed.get("component_types", []),
                "field_aliases": allowed.get("field_aliases", [])[:20],
                "module_labels": allowed.get("module_labels", []),
                "flow_names": allowed.get("flow_names", []),
                "action_types": allowed.get("action_types", []),
            },
            "page_scope": {
                "page_type": page_scope.get("page_type", ""),
                "primary_goal": page_scope.get("primary_goal", ""),
                "key_modules": page_scope.get("key_modules", []),
                "critical_user_flows": page_scope.get("critical_user_flows", []),
                "priority_areas": page_scope.get("priority_areas", []),
                "risks": page_scope.get("risks", []),
                "confidence": page_scope.get("confidence", 0),
            },
            "knowledge_bank": self._summarize_relevant_knowledge(page_model),
            "case_memory": case_memory,
            "human_feedback": site_profile.get("human_feedback", {}),
            "context_budget": {
                "priority_terms": priority_terms[:12],
                "component_count": len(prioritized_components),
                "flow_count": len(prioritized_flows),
                "section_count": len(page_model.get("section_graph", {}).get("nodes", [])) if page_model else 0,
                "fact_count": len(fact_pack.get("facts", [])),
                "negative_fact_count": len(fact_pack.get("negative_facts", [])),
                "concrete_target_count": self._concrete_target_count(page_model),
            },
            "user_instruction": custom_instruction.strip(),
        }

    def _build_fact_pack(self, page_info: dict, page_model: dict | None, page_scope: dict | None) -> dict:
        page_model = page_model or {}
        page_scope = page_scope or {}
        facts = []

        for node in page_model.get("section_graph", {}).get("nodes", [])[:10]:
            facts.append(
                {
                    "fact_id": f"section::{node.get('block_id', '')}",
                    "kind": "section",
                    "label": node.get("heading", "") or node.get("tag", ""),
                    "evidence": {
                        "field_count": node.get("field_count", 0),
                        "button_count": node.get("button_count", 0),
                        "link_count": node.get("link_count", 0),
                    },
                }
            )
        for field in page_model.get("field_catalog", [])[:12]:
            facts.append(
                {
                    "fact_id": f"field::{field.get('field_key', '')}",
                    "kind": "field",
                    "label": field.get("semantic_label", "") or field.get("label", ""),
                    "evidence": {
                        "semantic_type": field.get("semantic_type", ""),
                        "required": field.get("required", False),
                        "container_hints": field.get("container_hints", [])[:3],
                    },
                }
            )
        for component in page_model.get("component_catalog", [])[:12]:
            facts.append(
                {
                    "fact_id": f"component::{component.get('component_key', '')}",
                    "kind": "component",
                    "label": component.get("label", "") or component.get("type", ""),
                    "evidence": {
                        "type": component.get("type", ""),
                        "aliases": component.get("aliases", [])[:4],
                    },
                }
            )
        for state in page_info.get("discovered_states", [])[:8]:
            facts.append(
                {
                    "fact_id": f"state::{state.get('state_id', '')}",
                    "kind": "state",
                    "label": state.get("label", ""),
                    "evidence": {
                        "trigger_action": state.get("trigger_action", ""),
                        "trigger_label": state.get("trigger_label", ""),
                    },
                }
            )
        for endpoint in page_model.get("api_endpoints", [])[:8]:
            facts.append(
                {
                    "fact_id": f"api::{self._normalize_learning_key(endpoint)}",
                    "kind": "api_endpoint",
                    "label": str(endpoint)[:160],
                    "evidence": {},
                }
            )
        negative_facts = []
        task_contract = build_task_contract(page_model, page_scope, page_info)
        for surface in task_contract.get("unsupported_surfaces", [])[:10]:
            negative_facts.append(
                {
                    "fact_id": f"negative::{self._normalize_learning_key(surface)}",
                    "kind": "negative_surface",
                    "label": surface,
                    "evidence": {"unsupported": True},
                }
            )

        return {
            "summary": {
                "fact_count": len(facts),
                "negative_fact_count": len(negative_facts),
                "page_type_hint": page_scope.get("page_type", "") or page_model.get("heuristic_scope", {}).get("likely_page_type", ""),
                "component_count": len(page_model.get("component_catalog", [])),
                "field_count": len(page_model.get("field_catalog", [])),
                "state_count": len(page_info.get("discovered_states", [])),
                "api_count": len(page_model.get("api_endpoints", [])),
                "unsupported_surface_count": len(task_contract.get("unsupported_surfaces", [])),
            },
            "facts": facts[:24],
            "negative_facts": negative_facts,
        }

    def _build_task_route(
        self,
        task_type: str,
        page_info: dict,
        page_model: dict | None,
        page_scope: dict | None,
        fact_pack: dict | None,
        custom_instruction: str = "",
    ) -> dict:
        page_model = page_model or {}
        page_scope = page_scope or {}
        fact_pack = fact_pack or {}
        heuristic_confidence = float(page_model.get("heuristic_scope", {}).get("confidence", 0.0) or 0.0)
        page_facts = page_model.get("page_facts", {})
        dynamic_surface = sum(
            1
            for key in ("spa_shell", "live_updates", "graphql", "iframe", "shadow_dom", "captcha")
            if page_facts.get(key)
        )
        complexity = (
            min(len(page_model.get("component_catalog", [])), 10) * 0.4
            + min(len(page_model.get("field_catalog", [])), 10) * 0.25
            + min(len(page_info.get("discovered_states", [])), 8) * 0.6
            + dynamic_surface * 1.4
            + (0.8 if custom_instruction.strip() else 0.0)
        )
        concrete_target_count = self._concrete_target_count(page_model)
        sparse_targets = concrete_target_count <= 4
        case_memory = load_case_memory_snapshot(page_info.get("url", "") or page_model.get("page_identity", {}).get("url", ""), page_model=page_model, page_scope=page_scope)
        case_memory_hits = len(case_memory.get("patterns", []))

        if task_type == "page_scope":
            if heuristic_confidence >= 0.82 and fact_pack.get("summary", {}).get("fact_count", 0) >= 8 and complexity <= 4.5 and not custom_instruction.strip() and not sparse_targets:
                return {"mode": "heuristic", "reason": "high heuristic confidence with dense grounded facts", "compact_context": True}
            if sparse_targets and complexity <= 5.5:
                return {"mode": "stateless", "reason": "sparse concrete targets require conservative scope routing", "compact_context": True}
            if complexity <= 6.5:
                return {"mode": "stateless", "reason": "moderate complexity scope analysis", "compact_context": True}
            return {"mode": "chat", "reason": "complex or dynamic scope analysis", "compact_context": False}

        if float(page_scope.get("confidence", 0.0) or 0.0) >= 0.88 and case_memory_hits >= 2 and complexity <= 6.5 and not sparse_targets:
            return {"mode": "stateless", "reason": "strong scope confidence with reusable case memory", "compact_context": True}
        if sparse_targets and complexity <= 6.0:
            return {"mode": "stateless", "reason": "sparse concrete targets require conservative scenario routing", "compact_context": True}
        if complexity <= 5.5 and case_memory_hits:
            return {"mode": "stateless", "reason": "low-complexity page with matching memory patterns", "compact_context": True}
        if fact_pack.get("summary", {}).get("fact_count", 0) <= 5 and complexity <= 3.5:
            return {"mode": "stateless", "reason": "sparse grounded facts require conservative generation", "compact_context": True}
        return {"mode": "chat", "reason": "complex scenario generation surface", "compact_context": False}

    def _should_use_conservative_scenario_fallback(
        self,
        page_model: dict | None,
        page_scope: dict | None,
        fact_pack: dict | None,
    ) -> bool:
        page_model = page_model or {}
        page_scope = page_scope or {}
        fact_pack = fact_pack or {}
        fact_count = int(fact_pack.get("summary", {}).get("fact_count", 0) or 0)
        concrete_targets = self._concrete_target_count(page_model)
        scope_confidence = float(page_scope.get("confidence", 0.0) or 0.0)
        return fact_count <= 6 and concrete_targets <= 4 and scope_confidence <= 0.72

    def _heuristic_scope_from_facts(self, page_model: dict | None, page_info: dict, fact_pack: dict) -> dict:
        page_model = page_model or {}
        heuristic_scope = page_model.get("heuristic_scope", {})
        page_facts = page_model.get("page_facts", {})
        modules = list(heuristic_scope.get("priority_modules", []))[:6] or self._fallback_modules_from_facts(page_facts)
        flows = list(heuristic_scope.get("recommended_flows", []))[:6] or self._fallback_flows_from_facts(page_facts)
        risks = []
        if page_facts.get("form"):
            risks.append("Field validation and submission handling may regress.")
        if page_facts.get("live_updates"):
            risks.append("Async content or live updates may create unstable UI states.")
        if page_facts.get("graphql") or page_facts.get("api_surface"):
            risks.append("Backend responses should remain consistent with UI state.")
        return {
            "page_type": heuristic_scope.get("likely_page_type", "") or "generic_page",
            "primary_goal": self._heuristic_primary_goal(page_model, page_info),
            "key_modules": modules[:6],
            "critical_user_flows": flows[:6],
            "priority_areas": modules[:6],
            "risks": risks[:4],
            "scope_summary": f"Scope inferred from grounded facts across {fact_pack.get('summary', {}).get('fact_count', 0)} extracted page facts.",
            "confidence": min(0.9, 0.55 + (fact_pack.get("summary", {}).get("fact_count", 0) * 0.01)),
        }

    def _heuristic_scenarios_from_facts(
        self,
        url: str,
        page_info: dict,
        page_model: dict | None,
        page_scope: dict | None,
        custom_instruction: str = "",
        target_count: int = 8,
    ) -> list[dict]:
        page_model = page_model or {}
        page_scope = page_scope or {}
        page_facts = page_model.get("page_facts", {})
        cases = []
        case_no = 1

        def add_case(module: str, test_type: str, title: str, steps: list[str], expected: str, severity: str = "Medium", priority: str = "P2") -> None:
            nonlocal case_no
            prefix = self._scenario_prefix(module)
            numbered_steps = [f"1. Open the site {url}"]
            numbered_steps.extend(f"{index}. {step}" for index, step in enumerate(steps, start=2))
            cases.append(
                {
                    "ID": f"{prefix}-{case_no:03d}",
                    "Module": module,
                    "Category": "Functional",
                    "Test Type": test_type,
                    "Title": title,
                    "Precondition": "",
                    "Steps to Reproduce": "\n".join(numbered_steps),
                    "Expected Result": expected,
                    "Actual Result": "",
                    "Severity": severity,
                    "Priority": priority,
                    "Evidence": "",
                    "Automation": "auto",
                }
            )
            case_no += 1

        primary_field = next((field for field in page_model.get("field_catalog", []) if field.get("semantic_label") or field.get("label")), None)
        primary_component = next((component for component in page_model.get("component_catalog", []) if component.get("label")), None)
        primary_section = next((node for node in page_model.get("section_graph", {}).get("nodes", []) if node.get("heading")), None)
        field_label = (primary_field or {}).get("semantic_label") or (primary_field or {}).get("label") or "Search"
        component_label = (primary_component or {}).get("label") or (primary_component or {}).get("type", "").replace("_", " ").title() or "Primary component"
        section_label = (primary_section or {}).get("heading") or "Primary section"

        if page_facts.get("search") and primary_field:
            add_case(
                "Search",
                "Positive",
                f"Use '{field_label}' to retrieve relevant results",
                [
                    f"Input 'sample' into the '{field_label}' field.",
                    f"Click the '{component_label if component_label.lower() != 'primary component' else field_label}' control.",
                ],
                "Relevant results are displayed for the entered keyword.",
            )
            add_case(
                "Search",
                "Negative",
                f"Submit '{field_label}' with an empty keyword",
                [
                    f"Input '' into the '{field_label}' field.",
                    f"Click the '{component_label if component_label.lower() != 'primary component' else field_label}' control.",
                ],
                "The page prevents an invalid search or clearly handles the empty-search state.",
            )
        elif page_facts.get("navigation") and primary_component:
            add_case(
                "Navigation",
                "Positive",
                f"Open the grounded navigation target '{component_label}'",
                [f"Click the '{component_label}' control."],
                "The related destination or page state is displayed successfully.",
            )
        elif page_facts.get("content") and primary_section:
            add_case(
                "Content",
                "Positive",
                f"Review the grounded content section '{section_label}'",
                [f"Scroll to the '{section_label}' section."],
                "The section content is visible and readable without missing content blocks.",
                severity="Low",
                priority="P3",
            )
        elif primary_component:
            add_case(
                page_scope.get("key_modules", ["General"])[0] if page_scope.get("key_modules") else "General",
                "Positive",
                f"Interact with the grounded component '{component_label}'",
                [f"Click the '{component_label}' control."],
                "The component responds without error and the expected page state is shown.",
            )

        if not cases and primary_section:
            add_case(
                "Content",
                "Positive",
                f"Verify the visible section '{section_label}'",
                [f"Scroll to the '{section_label}' section."],
                "The visible section is rendered correctly and remains accessible to the user.",
                severity="Low",
                priority="P3",
            )

        instruction_text = str(custom_instruction or "").lower()
        if "negative" in instruction_text and cases and not any(case["Test Type"] == "Negative" for case in cases):
            base = cases[0]
            add_case(
                base["Module"],
                "Negative",
                f"Trigger a safe invalid state on '{base['Module']}' without inventing new controls",
                [step.split(". ", 1)[1] for step in base["Steps to Reproduce"].splitlines()[1:]],
                "The page handles the invalid or empty interaction gracefully.",
            )

        return self._expand_heuristic_cases(cases, target_count, url, page_model, page_scope)

    def _expand_heuristic_cases(
        self,
        base_cases: list[dict],
        target_count: int,
        url: str,
        page_model: dict | None,
        page_scope: dict | None,
    ) -> list[dict]:
        page_model = page_model or {}
        page_scope = page_scope or {}
        desired_count = max(8, min(120, int(target_count or 16)))
        if not base_cases:
            return []
        if len(base_cases) >= desired_count:
            return base_cases[:desired_count]

        cases = [dict(item) for item in base_cases]
        module = str(page_scope.get("key_modules", [cases[0].get("Module", "General")])[0] if page_scope.get("key_modules") else cases[0].get("Module", "General"))
        prefix = self._scenario_prefix(module)
        existing_ids = {
            str(item.get("ID", "")).strip()
            for item in cases
            if str(item.get("ID", "")).strip()
        }
        existing_titles = {str(item.get("Title", "")).strip().lower() for item in cases}
        fields = [
            field for field in page_model.get("field_catalog", [])[:10]
            if field.get("semantic_label") or field.get("label")
        ]
        components = [
            component for component in page_model.get("component_catalog", [])[:10]
            if component.get("label") or component.get("type")
        ]
        field_labels = [
            (field.get("semantic_label") or field.get("label") or field.get("field_key", "").replace("_", " ")).strip()
            for field in fields
        ]
        component_labels = [
            (component.get("label") or component.get("type", "").replace("_", " ").title() or "Primary component").strip()
            for component in components
        ]
        targets = [label for label in (field_labels + component_labels) if label]
        if not targets:
            targets = ["primary interface"]

        next_no = len(cases) + 1
        target_index = 0
        max_iterations = desired_count * 8
        iterations = 0
        while len(cases) < desired_count and iterations < max_iterations:
            iterations += 1
            target_label = targets[target_index % len(targets)]
            is_positive = (next_no % 2) == 1
            test_type = "Positive" if is_positive else "Negative"
            variant_no = (target_index // len(targets)) + 1
            title = (
                f"Validate stable interaction on '{target_label}'"
                if is_positive
                else f"Validate invalid or empty input handling on '{target_label}'"
            )
            if variant_no > 1:
                title = f"{title} (variant {variant_no})"
            if title.lower() in existing_titles:
                target_index += 1
                next_no += 1
                continue
            case_id = f"{prefix}-{next_no:03d}"
            while case_id in existing_ids:
                next_no += 1
                case_id = f"{prefix}-{next_no:03d}"
            step_two = (
                f"Input a valid value into '{target_label}'."
                if is_positive
                else f"Input an empty or invalid value into '{target_label}'."
            )
            step_three = (
                f"Interact with '{target_label}' submit or confirm control."
                if is_positive
                else f"Attempt to continue using '{target_label}' without valid input."
            )
            expected = (
                "The page accepts the interaction and keeps a consistent state."
                if is_positive
                else "The page blocks invalid interaction and shows safe validation feedback."
            )
            cases.append(
                {
                    "ID": case_id,
                    "Module": module or "General",
                    "Category": "Functional",
                    "Test Type": test_type,
                    "Title": title,
                    "Precondition": "",
                    "Steps to Reproduce": f"1. Open the site {url}\n2. {step_two}\n3. {step_three}",
                    "Expected Result": expected,
                    "Actual Result": "",
                    "Severity": "Medium",
                    "Priority": "P2",
                    "Evidence": "",
                    "Automation": "auto",
                }
            )
            existing_ids.add(case_id)
            existing_titles.add(title.lower())
            target_index += 1
            next_no += 1
        if len(cases) < desired_count:
            # Safety fallback: allow repeated generic variants rather than risking an infinite expansion loop.
            while len(cases) < desired_count:
                case_id = f"{prefix}-{next_no:03d}"
                while case_id in existing_ids:
                    next_no += 1
                    case_id = f"{prefix}-{next_no:03d}"
                target_label = targets[(next_no - 1) % len(targets)]
                cases.append(
                    {
                        "ID": case_id,
                        "Module": module or "General",
                        "Category": "Functional",
                        "Test Type": "Negative" if (next_no % 2 == 0) else "Positive",
                        "Title": f"Repeat grounded validation on '{target_label}' (fallback {next_no})",
                        "Precondition": "",
                        "Steps to Reproduce": f"1. Open the site {url}\n2. Input a value into '{target_label}'.\n3. Verify the page responds safely.",
                        "Expected Result": "The interaction remains stable and safely handled.",
                        "Actual Result": "",
                        "Severity": "Medium",
                        "Priority": "P2",
                        "Evidence": "",
                        "Automation": "auto",
                    }
                )
                existing_ids.add(case_id)
                next_no += 1
        return cases[:desired_count]

    def _derive_scenario_volume(
        self,
        page_model: dict | None,
        page_scope: dict | None,
        fact_pack: dict | None,
        custom_instruction: str = "",
    ) -> dict:
        page_model = page_model or {}
        page_scope = page_scope or {}
        fact_pack = fact_pack or {}
        fact_count = int(fact_pack.get("summary", {}).get("fact_count", 0) or 0)
        concrete_targets = int(self._concrete_target_count(page_model))
        scope_confidence = float(page_scope.get("confidence", 0.0) or 0.0)
        instruction_text = str(custom_instruction or "").lower()

        if fact_count >= 16 and concrete_targets >= 14 and scope_confidence >= 0.7:
            target_count = 64
            min_valid_count = 24
        elif fact_count >= 12 and concrete_targets >= 10:
            target_count = 48
            min_valid_count = 18
        elif fact_count >= 8 and concrete_targets >= 7:
            target_count = 36
            min_valid_count = 14
        elif fact_count >= 5 and concrete_targets >= 5:
            target_count = 24
            min_valid_count = 10
        else:
            target_count = 16
            min_valid_count = 6

        if any(term in instruction_text for term in ("thorough", "comprehensive", "maximize", "lebih banyak", "sebanyak mungkin", "semua")):
            target_count = min(120, int(target_count * 1.6) + 8)
            min_valid_count = min(target_count, max(min_valid_count + 4, int(target_count * 0.45)))

        return {
            "fact_count": fact_count,
            "concrete_target_count": concrete_targets,
            "scope_confidence": round(scope_confidence, 2),
            "target_count": int(target_count),
            "min_valid_count": int(min_valid_count),
        }

    def _scenario_prefix(self, module: object) -> str:
        text = re.sub(r"[^A-Za-z]+", "", str(module or "").upper())
        return (text[:3] or "GEN")

    def _summarize_forms(self, page_model: dict, page_info: dict) -> list[dict]:
        if page_model.get("form_catalog"):
            forms = []
            for form in page_model.get("form_catalog", [])[:6]:
                forms.append(
                    {
                        "form_key": form.get("form_key", ""),
                        "submit_texts": form.get("submit_texts", [])[:4],
                        "context_text": str(form.get("context_text", ""))[:160],
                        "fields": [
                            {
                                "field_key": field.get("field_key", ""),
                                "semantic_type": field.get("semantic_type", ""),
                                "semantic_label": field.get("semantic_label", ""),
                                "required": field.get("required", False),
                                "widget": field.get("widget", ""),
                            }
                            for field in form.get("fields", [])[:10]
                        ],
                    }
                )
            return forms
        return page_info.get("forms", [])[:4]

    def _summarize_components(self, page_model: dict) -> list[dict]:
        rows = []
        for component in page_model.get("component_catalog", [])[:16]:
            rows.append(
                {
                    "component_key": component.get("component_key", ""),
                    "type": component.get("type", ""),
                    "label": component.get("label", ""),
                    "aliases": component.get("aliases", [])[:6],
                }
            )
        return rows

    def _summarize_section_graph(self, page_model: dict, priority_terms: list[str]) -> dict:
        graph = page_model.get("section_graph", {}) if page_model else {}
        nodes = []
        lowered_terms = [term.lower() for term in priority_terms if term]
        scored = []
        for index, node in enumerate(graph.get("nodes", [])[:28]):
            haystack = " ".join(
                [
                    str(node.get("heading", "")),
                    str(node.get("text", "")),
                    str(node.get("tag", "")),
                ]
            ).lower()
            score = sum(1 for term in lowered_terms if term in haystack)
            scored.append((score, index, node))
        scored.sort(key=lambda item: (-item[0], item[1]))
        for _, _, node in scored[:10]:
            nodes.append(
                {
                    "block_id": node.get("block_id", ""),
                    "parent_block_id": node.get("parent_block_id", ""),
                    "tag": node.get("tag", ""),
                    "heading": node.get("heading", ""),
                    "text": str(node.get("text", ""))[:180],
                    "aria_label": str(node.get("aria_label", ""))[:120],
                    "link_count": node.get("link_count", 0),
                    "button_count": node.get("button_count", 0),
                    "field_count": node.get("field_count", 0),
                    "action_labels": [str(item)[:80] for item in node.get("action_labels", [])[:4]],
                }
            )
        return {
            "nodes": nodes,
            "edge_count": len(graph.get("edges", [])),
        }

    def _concrete_target_count(self, page_model: dict | None) -> int:
        page_model = page_model or {}
        section_actions = 0
        for node in page_model.get("section_graph", {}).get("nodes", [])[:20]:
            section_actions += len(node.get("action_labels", [])[:4])
        return (
            len(page_model.get("field_catalog", [])[:16])
            + len(page_model.get("component_catalog", [])[:16])
            + min(section_actions, 16)
        )

    def _prioritize_components(self, components: list[dict], priority_terms: list[str], limit: int = 12) -> list[dict]:
        lowered_terms = [term.lower() for term in priority_terms if term]
        scored = []
        for index, component in enumerate(components[:24]):
            haystack = " ".join(
                [
                    str(component.get("component_key", "")),
                    str(component.get("type", "")),
                    str(component.get("label", "")),
                    " ".join(str(item) for item in component.get("aliases", [])[:6]),
                ]
            ).lower()
            score = sum(1 for term in lowered_terms if term in haystack)
            scored.append((score, index, component))
        scored.sort(key=lambda item: (-item[0], item[1]))
        rows = []
        for _, _, component in scored[:limit]:
            rows.append(
                {
                    "component_key": component.get("component_key", ""),
                    "type": component.get("type", ""),
                    "label": component.get("label", ""),
                    "aliases": component.get("aliases", [])[:6],
                }
            )
        return rows

    def _prioritize_flows(self, flows: list[dict], priority_terms: list[str], limit: int = 8) -> list[dict]:
        lowered_terms = [term.lower() for term in priority_terms if term]
        scored = []
        for index, flow in enumerate(flows[:20]):
            haystack = " ".join(
                [
                    str(flow.get("name", "")),
                    str(flow.get("type", "")),
                    str(flow.get("summary", "")),
                    " ".join(str(item) for item in flow.get("triggers", [])[:4]),
                ]
            ).lower()
            score = sum(1 for term in lowered_terms if term in haystack)
            scored.append((score, index, flow))
        scored.sort(key=lambda item: (-item[0], item[1]))
        return [item[2] for item in scored[:limit]]

    def _prioritize_text_items(
        self,
        values: list[object],
        priority_terms: list[str],
        limit: int,
        max_len: int = 120,
    ) -> list[str]:
        lowered_terms = [term.lower() for term in priority_terms if term]
        rows = []
        for index, value in enumerate(values[:40]):
            text = str(value or "").strip()
            if not text:
                continue
            normalized = text[:max_len]
            haystack = normalized.lower()
            score = sum(1 for term in lowered_terms if term in haystack)
            rows.append((score, index, normalized))
        rows.sort(key=lambda item: (-item[0], item[1]))
        return [item[2] for item in rows[:limit]]

    def _prioritize_links(self, links: list[dict], priority_terms: list[str], limit: int = 12) -> list[dict]:
        lowered_terms = [term.lower() for term in priority_terms if term]
        scored = []
        for index, link in enumerate(links[:32]):
            if not isinstance(link, dict):
                continue
            haystack = f"{str(link.get('text', ''))} {str(link.get('href', ''))}".lower()
            score = sum(1 for term in lowered_terms if term in haystack)
            scored.append((score, index, link))
        scored.sort(key=lambda item: (-item[0], item[1]))
        return [item[2] for item in scored[:limit]]

    def _priority_terms(self, page_model: dict, page_scope: dict) -> list[str]:
        heuristic_scope = page_model.get("heuristic_scope", {}) if page_model else {}
        values = [
            heuristic_scope.get("likely_page_type", ""),
            page_scope.get("page_type", ""),
            *heuristic_scope.get("priority_modules", [])[:8],
            *heuristic_scope.get("recommended_flows", [])[:8],
            *page_scope.get("key_modules", [])[:8],
            *page_scope.get("critical_user_flows", [])[:8],
        ]
        terms = []
        seen = set()
        for value in values:
            text = " ".join(str(value or "").strip().split())
            if text and text.lower() not in seen:
                terms.append(text)
                seen.add(text.lower())
        return terms

    def _fallback_modules_from_facts(self, page_facts: dict) -> list[str]:
        rows = []
        for key, label in [
            ("navigation", "Navigation"),
            ("search", "Search"),
            ("filter", "Filter"),
            ("listing", "Listing"),
            ("content", "Content"),
            ("form", "Form"),
            ("table", "Table"),
            ("upload", "Upload"),
            ("live_updates", "Live Updates"),
        ]:
            if page_facts.get(key):
                rows.append(label)
        return rows or ["General"]

    def _fallback_flows_from_facts(self, page_facts: dict) -> list[str]:
        rows = []
        if page_facts.get("navigation"):
            rows.append("navigate primary sections")
        if page_facts.get("search"):
            rows.append("search by keyword")
        if page_facts.get("filter"):
            rows.append("refine visible results")
        if page_facts.get("form"):
            rows.append("complete and submit form")
        if page_facts.get("content"):
            rows.append("review visible content blocks")
        if page_facts.get("listing"):
            rows.append("open item details from listing")
        return rows or ["review visible page state"]

    def _heuristic_primary_goal(self, page_model: dict, page_info: dict) -> str:
        heuristic_scope = page_model.get("heuristic_scope", {})
        page_type = heuristic_scope.get("likely_page_type", "")
        headings = [item.get("text", "") for item in page_info.get("headings", [])[:2] if isinstance(item, dict)]
        if page_type:
            return f"Interact with the primary {page_type.replace('_', ' ')} surface and validate its core outcomes."
        if headings:
            return f"Validate the main user journey presented in '{headings[0]}'."
        return "Validate the primary interactions and visible content on this page."

    def _summarize_relevant_knowledge(self, page_model: dict) -> dict:
        site_profile = page_model.get("site_profile", {}) if page_model else {}
        learning = site_profile.get("learning", {})
        semantic_patterns = learning.get("semantic_patterns", {})

        relevant_field_keys = []
        for field in page_model.get("field_catalog", [])[:16]:
            for value in (
                field.get("field_key", ""),
                field.get("semantic_type", ""),
                field.get("semantic_label", ""),
            ):
                normalized = self._normalize_learning_key(value)
                if normalized and normalized not in relevant_field_keys:
                    relevant_field_keys.append(normalized)

        relevant_action_keys = []
        for component in page_model.get("component_catalog", [])[:16]:
            for value in (component.get("component_key", ""), component.get("type", ""), component.get("label", "")):
                normalized = self._normalize_learning_key(value)
                if normalized and normalized not in relevant_action_keys:
                    relevant_action_keys.append(normalized)

        return {
            "field_selector_hints": {
                key: get_ranked_selector_candidates(learning, "field_selectors", key, limit=4)
                for key in relevant_field_keys[:12]
                if get_ranked_selector_candidates(learning, "field_selectors", key, limit=1)
            },
            "action_selector_hints": {
                key: get_ranked_selector_candidates(learning, "action_selectors", key, limit=4)
                for key in relevant_action_keys[:12]
                if get_ranked_selector_candidates(learning, "action_selectors", key, limit=1)
            },
            "semantic_patterns": {
                key: {
                    "hits": semantic_patterns.get(key, {}).get("hits", 0),
                    "score": semantic_patterns.get(key, {}).get("score", 0),
                    "selectors": semantic_patterns.get(key, {}).get("selectors", [])[:4],
                }
                for key in relevant_field_keys[:12]
                if semantic_patterns.get(key)
            },
            "anti_patterns": {
                key: [
                    {
                        "selector": item.get("selector", ""),
                        "failures": item.get("failures", 0),
                    }
                    for item in get_failure_memory(learning, "field_selectors", key, limit=3)
                ]
                for key in relevant_field_keys[:8]
                if get_failure_memory(learning, "field_selectors", key, limit=1)
            },
            "summary": site_profile.get("knowledge_bank", {}),
        }

    def _normalize_learning_key(self, value: object) -> str:
        text = "".join(ch.lower() if ch.isalnum() else "_" for ch in str(value or ""))
        return "_".join(part for part in text.split("_") if part)

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
        self._active_usage_stage = "result_analysis"
        return self._call_stateless(prompt).strip()

    def generate_bug_report(self, test_name, url, error, steps) -> str:
        steps_str   = "\n".join(f"{i+1}. {s}" for i, s in enumerate(steps))
        short_error = error.strip()[:400]
        prompt = (
            "Write a concise QA bug report.\n"
            f"Feature: {test_name}\nURL: {url}\nError: {short_error}\nSteps:\n{steps_str}\n\n"
            "Format: Title | Severity | Description | Steps | Expected | Actual"
        )
        self._active_usage_stage = "bug_report"
        return self._call_stateless(prompt).strip()

    def reset_chat(self) -> None:
        cfg = self._pool[self._idx]
        self.chat = self.client.chats.create(
            model=cfg["name"],
            config=self.config
        )
        self._chat_turns = 0

    def generate_executive_summary(self, url: str, website_title: str, page_info: dict, parsed_data: list) -> str:
        """Create an Executive Summary .md from test result list of dicts."""
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
        self._active_usage_stage = "executive_summary"
        return self._call_stateless(prompt).strip()

    def reset_usage(self) -> None:
        self.usage_events = []
        self._active_usage_stage = "generic"

    def usage_snapshot(self) -> dict:
        by_stage = {}
        total_input = 0
        total_output = 0
        for item in self.usage_events:
            stage = item.get("stage", "generic")
            bucket = by_stage.setdefault(
                stage,
                {
                    "calls": 0,
                    "estimated_input_tokens": 0,
                    "estimated_output_tokens": 0,
                    "estimated_total_tokens": 0,
                    "modes": {},
                    "models": {},
                },
            )
            bucket["calls"] += 1
            bucket["estimated_input_tokens"] += int(item.get("estimated_input_tokens", 0) or 0)
            bucket["estimated_output_tokens"] += int(item.get("estimated_output_tokens", 0) or 0)
            bucket["estimated_total_tokens"] += int(item.get("estimated_total_tokens", 0) or 0)
            mode = item.get("mode", "")
            model = item.get("model", "")
            if mode:
                bucket["modes"][mode] = bucket["modes"].get(mode, 0) + 1
            if model:
                bucket["models"][model] = bucket["models"].get(model, 0) + 1
            total_input += int(item.get("estimated_input_tokens", 0) or 0)
            total_output += int(item.get("estimated_output_tokens", 0) or 0)
        return {
            "summary": {
                "calls": len(self.usage_events),
                "estimated_input_tokens": total_input,
                "estimated_output_tokens": total_output,
                "estimated_total_tokens": total_input + total_output,
            },
            "by_stage": by_stage,
            "events": self.usage_events[-40:],
        }

    def _record_usage(self, stage: str, mode: str, model: str, prompt: object, response: object) -> None:
        prompt_text = self._flatten_usage_text(prompt)
        response_text = self._flatten_usage_text(response)
        input_tokens = self._estimate_tokens(prompt_text)
        output_tokens = self._estimate_tokens(response_text)
        self.usage_events.append(
            {
                "stage": stage or "generic",
                "mode": mode,
                "model": model,
                "estimated_input_tokens": input_tokens,
                "estimated_output_tokens": output_tokens,
                "estimated_total_tokens": input_tokens + output_tokens,
                "prompt_chars": len(prompt_text),
                "response_chars": len(response_text),
            }
        )

    def _flatten_usage_text(self, value: object) -> str:
        if isinstance(value, list):
            return "\n".join(str(item) for item in value if item is not None)
        return str(value or "")

    def _estimate_tokens(self, text: str) -> int:
        compact = re.sub(r"\s+", " ", text or "").strip()
        if not compact:
            return 0
        return max(1, math.ceil(len(compact) / 4))
