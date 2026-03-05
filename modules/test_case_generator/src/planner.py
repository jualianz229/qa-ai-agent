import json
import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

from core.ontology import ACTION_ONTOLOGY, COMPONENT_ONTOLOGY, FIELD_ONTOLOGY
from core.site_profiles import get_failure_memory, get_ranked_selector_candidates


@dataclass
class PageModelBuilder:
    page_info: dict

    def build(self) -> dict:
        fingerprint = self.page_info.get("page_fingerprint", {})
        components = self._build_components()
        form_catalog = self._build_form_catalog()
        field_catalog = [field for form in form_catalog for field in form.get("fields", [])]
        component_catalog = self._build_component_catalog(components)
        actions = self._build_actions(components, form_catalog, component_catalog)
        entities = self._build_entities(form_catalog, component_catalog)
        navigation_graph = self._build_navigation_graph()
        section_graph = self._build_section_graph()
        state_graph = self._build_state_graph(components, actions)
        possible_flows = self._build_possible_flows(components, actions, component_catalog)
        page_facts = self._derive_page_facts(components, fingerprint)
        runtime_observer = self._build_runtime_observer()
        session_model = self._build_session_model(page_facts, component_catalog)
        heuristic_scope = self._build_heuristic_scope(page_facts, component_catalog, possible_flows)
        return {
            "page_identity": {
                "url": self.page_info.get("url", ""),
                "title": self.page_info.get("title", ""),
                "metadata": self.page_info.get("metadata", {}),
            },
            "api_endpoints": self.page_info.get("apis", []),
            "components": components,
            "actions": actions,
            "entities": entities,
            "content_blocks": self.page_info.get("sections", []),
            "section_graph": section_graph,
            "section_catalog": section_graph.get("nodes", []),
            "navigation_graph": navigation_graph,
            "state_graph": state_graph,
            "possible_flows": possible_flows,
            "form_catalog": form_catalog,
            "field_catalog": field_catalog,
            "field_alias_map": {field["field_key"]: field.get("aliases", []) for field in field_catalog},
            "component_catalog": component_catalog,
            "component_alias_map": {component["component_key"]: component.get("aliases", []) for component in component_catalog},
            "component_ontology": COMPONENT_ONTOLOGY,
            "action_ontology": ACTION_ONTOLOGY,
            "field_ontology": FIELD_ONTOLOGY,
            "fingerprint": fingerprint,
            "page_facts": page_facts,
            "runtime_observer": runtime_observer,
            "session_model": session_model,
            "heuristic_scope": heuristic_scope,
            "site_profile": self.page_info.get("site_profile", {}),
            "linked_pages": self.page_info.get("crawled_pages", []),
            "discovered_states": self.page_info.get("discovered_states", []),
            "interaction_probes": self.page_info.get("interaction_probes", []),
        }

    def _build_components(self) -> list[dict]:
        components = []
        fingerprint = self.page_info.get("page_fingerprint", {})
        if fingerprint.get("has_search"):
            components.append({"type": "search", "source": "fingerprint"})
        if fingerprint.get("has_filters"):
            components.append({"type": "filter", "source": "fingerprint"})
        if fingerprint.get("has_pagination"):
            components.append({"type": "pagination", "source": "fingerprint"})
        if fingerprint.get("has_table"):
            components.append({"type": "table", "source": "tables"})
        if fingerprint.get("has_form"):
            components.append({"type": "form", "source": "forms"})
        if fingerprint.get("has_standalone_controls"):
            components.append({"type": "form", "source": "standalone_controls"})
        if fingerprint.get("has_navigation"):
            components.append({"type": "navigation", "source": "navigation"})
        if fingerprint.get("has_article_like_sections"):
            components.append({"type": "content", "source": "sections"})
        if fingerprint.get("has_listing_pattern"):
            components.append({"type": "listing", "source": "lists_links"})
        for key, component_type in [
            ("has_combobox", "combobox"),
            ("has_datepicker", "datepicker"),
            ("has_timepicker", "timepicker"),
            ("has_toast", "toast"),
            ("has_drawer", "drawer"),
            ("has_upload", "file_upload"),
            ("has_drag_drop", "drag_drop"),
            ("has_rich_text", "rich_text_editor"),
            ("has_infinite_scroll", "infinite_scroll"),
            ("has_carousel", "carousel"),
            ("has_iframe", "iframe"),
            ("has_shadow_dom", "shadow_dom"),
            ("has_chart", "chart"),
            ("has_map", "map"),
            ("has_cookie_banner", "consent_banner"),
            ("has_captcha", "captcha"),
            ("has_spa_shell", "spa_shell"),
            ("has_graphql", "graphql_surface"),
            ("has_websocket", "live_feed"),
            ("has_live_updates", "live_feed"),
            ("has_otp_flow", "otp_verification"),
            ("has_sso", "sso_login"),
        ]:
            if fingerprint.get(key):
                components.append({"type": component_type, "source": "fingerprint"})

        for heading in self.page_info.get("headings", []):
            text = heading.get("text", "").lower()
            for component_type, spec in COMPONENT_ONTOLOGY.items():
                if any(pattern in text for pattern in spec["signals"]):
                    components.append({"type": component_type, "source": "heading", "label": heading.get("text", "")})

        deduped = []
        seen = set()
        for component in components:
            key = (component.get("type"), component.get("label", ""))
            if key not in seen:
                deduped.append(component)
                seen.add(key)
        return deduped

    def _build_actions(self, components: list[dict], form_catalog: list[dict], component_catalog: list[dict]) -> list[dict]:
        actions = [{"type": "open_url", "target": self.page_info.get("url", "")}]
        button_texts = self.page_info.get("buttons", [])[:10]
        for text in button_texts:
            actions.append({"type": "click", "target": text, "kind": "button"})
        for link in self.page_info.get("links", [])[:10]:
            if isinstance(link, dict):
                actions.append({"type": "click", "target": link.get("text", ""), "kind": "link", "href": link.get("href", "")})
        for form in form_catalog[:4]:
            for field in form.get("fields", [])[:8]:
                target = field.get("semantic_label") or field.get("label") or field.get("name") or field.get("id")
                actions.append(
                    {
                        "type": self._field_action_type(field),
                        "target": target,
                        "input_type": field.get("type", "text"),
                        "input_kind": field.get("widget", "") or field.get("type", ""),
                        "field_key": field.get("field_key", ""),
                        "semantic_type": field.get("semantic_type", ""),
                        "aliases": field.get("aliases", []),
                    }
                )
        for component in component_catalog[:12]:
            if component.get("type") in {
                "tabs", "accordion", "breadcrumb", "pagination", "card", "modal", "drawer",
                "carousel", "consent_banner", "toast", "sso_login"
            }:
                target = component.get("label", "")
                if target:
                    actions.append(
                        {
                            "type": "dismiss" if component.get("type") == "consent_banner" else "click",
                            "target": target,
                            "component_key": component.get("component_key", ""),
                            "component_type": component.get("type", ""),
                            "aliases": component.get("aliases", []),
                        }
                    )
        if any(component.get("type") == "live_feed" for component in component_catalog):
            actions.append({"type": "wait_for_text", "target": "live update", "component_type": "live_feed"})
        if not actions:
            actions.append({"type": "inspect", "target": "page"})
        return actions

    def _build_entities(self, form_catalog: list[dict], component_catalog: list[dict]) -> list[dict]:
        entities = []
        for heading in self.page_info.get("headings", [])[:8]:
            entities.append({"type": "heading", "value": heading.get("text", "")})
        for button in self.page_info.get("buttons", [])[:12]:
            entities.append({"type": "button", "value": button})
        for link in self.page_info.get("links", [])[:12]:
            if isinstance(link, dict):
                entities.append({"type": "link", "value": link.get("text", "")})
        for table in self.page_info.get("tables", [])[:4]:
            entities.append({"type": "table_headers", "value": table})
        for form in form_catalog[:4]:
            for field in form.get("fields", [])[:10]:
                entities.append({"type": "field", "value": field.get("semantic_label") or field.get("label") or field.get("field_key", "")})
        for component in component_catalog[:16]:
            entities.append({"type": "component", "value": component.get("label", "") or component.get("component_key", "")})
        return entities

    def _build_navigation_graph(self) -> dict:
        nodes = [self.page_info.get("url", "")]
        edges = []
        for link in self.page_info.get("links", [])[:20]:
            if isinstance(link, dict):
                href = link.get("href", "")
                if href:
                    edges.append({"from": self.page_info.get("url", ""), "to": href, "label": link.get("text", "")})
        for page in self.page_info.get("crawled_pages", []):
            page_url = page.get("url", "")
            if page_url:
                nodes.append(page_url)
        return {"nodes": nodes, "edges": edges}

    def _build_section_graph(self) -> dict:
        raw_graph = self.page_info.get("section_graph", {}) if isinstance(self.page_info.get("section_graph", {}), dict) else {}
        if raw_graph.get("nodes"):
            nodes = []
            for node in raw_graph.get("nodes", [])[:24]:
                nodes.append(
                    {
                        "block_id": str(node.get("block_id", "")).strip(),
                        "tag": str(node.get("tag", "")).strip(),
                        "heading": str(node.get("heading", "")).strip(),
                        "text": str(node.get("text", ""))[:220],
                        "dom_path": str(node.get("dom_path", ""))[:180],
                        "parent_block_id": str(node.get("parent_block_id", "")).strip(),
                        "link_count": int(node.get("link_count", 0) or 0),
                        "button_count": int(node.get("button_count", 0) or 0),
                        "field_count": int(node.get("field_count", 0) or 0),
                    }
                )
            return {
                "nodes": nodes,
                "edges": list(raw_graph.get("edges", []))[:32],
            }

        nodes = []
        edges = []
        for index, section in enumerate(self.page_info.get("sections", [])[:12], start=1):
            block_id = f"block_{index}"
            nodes.append(
                {
                    "block_id": block_id,
                    "tag": "section",
                    "heading": str(section.get("heading", "")).strip(),
                    "text": str(section.get("text", ""))[:220],
                    "dom_path": "",
                    "parent_block_id": "",
                    "link_count": 0,
                    "button_count": 0,
                    "field_count": 0,
                }
            )
        return {"nodes": nodes, "edges": edges}

    def _build_state_graph(self, components: list[dict], actions: list[dict]) -> dict:
        states = [{"id": "landing", "label": "Initial page load"}]
        transitions = []
        component_types = {component.get("type") for component in components}
        seen_states = {"landing"}

        if "navigation" in component_types:
            states.append({"id": "navigated", "label": "After navigation click"})
            transitions.append({"from": "landing", "to": "navigated", "via": "click"})
            seen_states.add("navigated")
        if "search" in component_types:
            states.append({"id": "searched", "label": "After search input/action"})
            transitions.append({"from": "landing", "to": "searched", "via": "fill/click"})
            seen_states.add("searched")
        if "filter" in component_types:
            states.append({"id": "filtered", "label": "After filter/sort change"})
            transitions.append({"from": "landing", "to": "filtered", "via": "select/click"})
            seen_states.add("filtered")
        if "pagination" in component_types:
            states.append({"id": "paginated", "label": "After pagination change"})
            transitions.append({"from": "landing", "to": "paginated", "via": "click"})
            seen_states.add("paginated")
        if "form" in component_types:
            states.append({"id": "submitted", "label": "After form submission"})
            transitions.append({"from": "landing", "to": "submitted", "via": "fill/click"})
            seen_states.add("submitted")
        if "consent_banner" in component_types:
            states.append({"id": "consent_dismissed", "label": "After consent banner is dismissed"})
            transitions.append({"from": "landing", "to": "consent_dismissed", "via": "dismiss"})
            seen_states.add("consent_dismissed")
        if "otp_verification" in component_types:
            states.append({"id": "otp_verified", "label": "After OTP or verification code is submitted"})
            transitions.append({"from": "submitted", "to": "otp_verified", "via": "fill/click"})
            seen_states.add("otp_verified")
        if "sso_login" in component_types:
            states.append({"id": "sso_redirect", "label": "After SSO provider redirect begins"})
            transitions.append({"from": "landing", "to": "sso_redirect", "via": "click"})
            seen_states.add("sso_redirect")
        if "drawer" in component_types:
            states.append({"id": "drawer_open", "label": "After drawer is opened"})
            transitions.append({"from": "landing", "to": "drawer_open", "via": "click"})
            seen_states.add("drawer_open")
        if "modal" in component_types:
            states.append({"id": "dialog_open", "label": "After modal/dialog is opened"})
            transitions.append({"from": "landing", "to": "dialog_open", "via": "click"})
            seen_states.add("dialog_open")
        if "carousel" in component_types:
            states.append({"id": "slide_changed", "label": "After carousel slide change"})
            transitions.append({"from": "landing", "to": "slide_changed", "via": "click"})
            seen_states.add("slide_changed")
        if "infinite_scroll" in component_types:
            states.append({"id": "list_extended", "label": "After more content is loaded"})
            transitions.append({"from": "landing", "to": "list_extended", "via": "scroll/click"})
            seen_states.add("list_extended")
        if "spa_shell" in component_types:
            states.append({"id": "route_changed", "label": "After in-app route change"})
            transitions.append({"from": "landing", "to": "route_changed", "via": "click/wait"})
            seen_states.add("route_changed")
        if "live_feed" in component_types:
            states.append({"id": "live_updated", "label": "After live data updates on the page"})
            transitions.append({"from": "landing", "to": "live_updated", "via": "wait"})
            seen_states.add("live_updated")
        if len(states) == 1 and actions:
            states.append({"id": "inspected", "label": "After generic inspection"})
            transitions.append({"from": "landing", "to": "inspected", "via": actions[0].get("type", "inspect")})
            seen_states.add("inspected")
        for state in self.page_info.get("discovered_states", [])[:12]:
            state_id = str(state.get("state_id", "")).strip()
            if not state_id or state_id in seen_states:
                continue
            states.append({"id": state_id, "label": str(state.get("label", ""))[:160]})
            transitions.append(
                {
                    "from": "landing",
                    "to": state_id,
                    "via": str(state.get("trigger_action", "click") or "click"),
                    "trigger": str(state.get("trigger_label", ""))[:120],
                }
            )
            seen_states.add(state_id)
        return {"states": states, "transitions": transitions}

    def _build_possible_flows(self, components: list[dict], actions: list[dict], component_catalog: list[dict]) -> list[dict]:
        flows = [{"name": "open_page", "steps": [{"action": "open_url", "target": self.page_info.get("url", "")}]}]
        component_types = {component.get("type") for component in components}
        if "navigation" in component_types:
            flows.append({"name": "navigate_via_menu", "steps": [action for action in actions if action.get("kind") == "link"][:3]})
        if "search" in component_types:
            flows.append({"name": "use_search", "steps": [action for action in actions if action.get("type") in {"fill", "click"}][:3]})
        if "form" in component_types:
            flows.append({"name": "submit_form", "steps": [action for action in actions if action.get("type") in {"fill", "click"}][:4]})
        if any(component.get("type") == "tabs" for component in component_catalog):
            flows.append({"name": "switch_tabs", "steps": [action for action in actions if action.get("component_type") == "tabs"][:3]})
        if any(component.get("type") == "accordion" for component in component_catalog):
            flows.append({"name": "expand_collapse_sections", "steps": [action for action in actions if action.get("component_type") == "accordion"][:3]})
        if any(component.get("type") == "pagination" for component in component_catalog):
            flows.append({"name": "change_page", "steps": [action for action in actions if action.get("component_type") == "pagination"][:3]})
        if any(component.get("type") == "card" for component in component_catalog):
            flows.append({"name": "open_card_detail", "steps": [action for action in actions if action.get("component_type") == "card"][:3]})
        if any(component.get("type") == "drawer" for component in component_catalog):
            flows.append({"name": "open_close_drawer", "steps": [action for action in actions if action.get("component_type") == "drawer"][:3]})
        if any(component.get("type") == "carousel" for component in component_catalog):
            flows.append({"name": "navigate_carousel", "steps": [action for action in actions if action.get("component_type") == "carousel"][:3]})
        if any(component.get("type") == "file_upload" for component in component_catalog):
            flows.append({"name": "upload_file", "steps": [action for action in actions if action.get("type") == "upload"][:3]})
        if any(component.get("type") == "rich_text_editor" for component in component_catalog):
            flows.append({"name": "edit_rich_text", "steps": [action for action in actions if action.get("input_kind") == "rich_text"][:3]})
        if any(component.get("type") == "consent_banner" for component in component_catalog):
            flows.append({"name": "dismiss_consent_banner", "steps": [action for action in actions if action.get("component_type") == "consent_banner"][:2]})
        if any(component.get("type") == "otp_verification" for component in component_catalog):
            flows.append({"name": "verify_otp", "steps": [action for action in actions if action.get("semantic_type") == "otp_code"][:3]})
        if any(component.get("type") == "sso_login" for component in component_catalog):
            flows.append({"name": "start_sso_login", "steps": [action for action in actions if action.get("component_type") == "sso_login"][:2]})
        if any(component.get("type") == "live_feed" for component in component_catalog):
            flows.append({"name": "observe_live_updates", "steps": [action for action in actions if action.get("component_type") == "live_feed" or action.get("type") == "wait_for_text"][:2]})
        for state in self.page_info.get("discovered_states", [])[:6]:
            trigger_label = str(state.get("trigger_label", "")).strip()
            if trigger_label:
                flows.append(
                    {
                        "name": str(state.get("state_id", "discovered_state")).strip(),
                        "steps": [
                            {"action": "open_url", "target": self.page_info.get("url", "")},
                            {"action": str(state.get("trigger_action", "click") or "click"), "target": trigger_label},
                        ],
                    }
                )
        return flows

    def _derive_page_facts(self, components: list[dict], fingerprint: dict) -> dict:
        component_types = {component.get("type") for component in components}
        return {
            "form": bool(fingerprint.get("has_form") or "form" in component_types),
            "auth": bool(fingerprint.get("has_auth_pattern") or "sso_login" in component_types or "otp_verification" in component_types),
            "search": bool(fingerprint.get("has_search") or "search" in component_types),
            "filter": bool(fingerprint.get("has_filters") or "filter" in component_types),
            "pagination": bool(fingerprint.get("has_pagination") or "pagination" in component_types),
            "table": bool(fingerprint.get("has_table") or "table" in component_types),
            "navigation": bool(fingerprint.get("has_navigation") or "navigation" in component_types),
            "listing": bool(fingerprint.get("has_listing_pattern") or "listing" in component_types),
            "content": bool(fingerprint.get("has_article_like_sections") or "content" in component_types),
            "upload": bool(fingerprint.get("has_upload") or "file_upload" in component_types),
            "rich_text": bool(fingerprint.get("has_rich_text") or "rich_text_editor" in component_types),
            "iframe": bool(fingerprint.get("has_iframe") or "iframe" in component_types),
            "shadow_dom": bool(fingerprint.get("has_shadow_dom") or "shadow_dom" in component_types),
            "consent_banner": bool(fingerprint.get("has_cookie_banner") or "consent_banner" in component_types),
            "captcha": bool(fingerprint.get("has_captcha") or "captcha" in component_types),
            "combobox": bool(fingerprint.get("has_combobox") or "combobox" in component_types),
            "datepicker": bool(fingerprint.get("has_datepicker") or "datepicker" in component_types),
            "timepicker": bool(fingerprint.get("has_timepicker") or "timepicker" in component_types),
            "toast": bool(fingerprint.get("has_toast") or "toast" in component_types),
            "drawer": bool(fingerprint.get("has_drawer") or "drawer" in component_types),
            "carousel": bool(fingerprint.get("has_carousel") or "carousel" in component_types),
            "infinite_scroll": bool(fingerprint.get("has_infinite_scroll") or "infinite_scroll" in component_types),
            "map": bool(fingerprint.get("has_map") or "map" in component_types),
            "chart": bool(fingerprint.get("has_chart") or "chart" in component_types),
            "spa_shell": bool(fingerprint.get("has_spa_shell") or "spa_shell" in component_types),
            "graphql": bool(fingerprint.get("has_graphql") or "graphql_surface" in component_types),
            "api_surface": bool(self.page_info.get("apis") or fingerprint.get("has_graphql") or "graphql_surface" in component_types),
            "websocket": bool(fingerprint.get("has_websocket") or "live_feed" in component_types),
            "live_updates": bool(fingerprint.get("has_live_updates") or "live_feed" in component_types),
            "otp_flow": bool(fingerprint.get("has_otp_flow") or "otp_verification" in component_types),
            "sso": bool(fingerprint.get("has_sso") or "sso_login" in component_types),
            "auth_checkpoint": bool(fingerprint.get("has_auth_checkpoint") or "otp_verification" in component_types or "captcha" in component_types),
        }

    def _build_heuristic_scope(self, page_facts: dict, component_catalog: list[dict], possible_flows: list[dict]) -> dict:
        likely_page_type = "generic_page"
        if page_facts.get("auth") and page_facts.get("form"):
            likely_page_type = "authentication_form"
        elif page_facts.get("search") and page_facts.get("listing"):
            likely_page_type = "search_listing"
        elif page_facts.get("content") and not page_facts.get("listing"):
            likely_page_type = "content_detail"
        elif page_facts.get("table") or (page_facts.get("filter") and page_facts.get("pagination")):
            likely_page_type = "data_listing"
        elif page_facts.get("form"):
            likely_page_type = "general_form"
        elif page_facts.get("navigation") and page_facts.get("spa_shell"):
            likely_page_type = "application_shell"

        priority_modules = []
        if page_facts.get("auth"):
            priority_modules.extend(["authentication", "session"])
        if page_facts.get("search"):
            priority_modules.append("search")
        if page_facts.get("filter"):
            priority_modules.append("filter")
        if page_facts.get("pagination"):
            priority_modules.append("pagination")
        if page_facts.get("form"):
            priority_modules.append("form_validation")
        if page_facts.get("table"):
            priority_modules.append("table_results")
        if page_facts.get("content"):
            priority_modules.append("content_integrity")
        if page_facts.get("upload"):
            priority_modules.append("file_upload")
        if page_facts.get("live_updates"):
            priority_modules.append("live_state")

        likely_risks = []
        if page_facts.get("auth"):
            likely_risks.extend(["login_failure", "session_state"])
        if page_facts.get("form"):
            likely_risks.extend(["validation_gap", "required_field_gap"])
        if page_facts.get("search") and page_facts.get("listing"):
            likely_risks.extend(["empty_results", "irrelevant_results"])
        if page_facts.get("filter") or page_facts.get("pagination"):
            likely_risks.append("state_persistence")
        if page_facts.get("upload"):
            likely_risks.append("upload_constraints")
        if page_facts.get("live_updates"):
            likely_risks.append("stale_live_state")
        if page_facts.get("consent_banner"):
            likely_risks.append("blocked_primary_flow")

        recommended_flows = [flow.get("name", "") for flow in possible_flows[:8] if flow.get("name")]
        interaction_density = sum(1 for component in component_catalog if component.get("type")) + len(recommended_flows)
        confidence = 0.45
        confidence += min(len(priority_modules), 5) * 0.08
        confidence += 0.12 if likely_page_type != "generic_page" else 0.0
        confidence += 0.08 if interaction_density >= 4 else 0.0
        confidence = round(min(0.95, confidence), 2)

        return {
            "likely_page_type": likely_page_type,
            "priority_modules": _merge_unique_list(priority_modules),
            "likely_risks": _merge_unique_list(likely_risks),
            "recommended_flows": recommended_flows,
            "confidence": confidence,
        }

    def _build_runtime_observer(self) -> dict:
        signals = dict(self.page_info.get("runtime_signals", {}))
        return {
            "xhr_count": int(signals.get("xhr_count", 0) or 0),
            "fetch_count": int(signals.get("fetch_count", 0) or 0),
            "graphql_request_count": int(signals.get("graphql_request_count", 0) or 0),
            "websocket_count": int(signals.get("websocket_count", 0) or 0),
            "history_length": int(signals.get("history_length", 0) or 0),
            "route_kind": str(signals.get("route_kind", "")),
            "local_storage_keys": list(signals.get("local_storage_keys", []))[:10],
            "session_storage_keys": list(signals.get("session_storage_keys", []))[:10],
            "embedded_contexts": list(self.page_info.get("embedded_contexts", []))[:10],
            "stateful_probe_count": len(self.page_info.get("discovered_states", [])),
        }

    def _build_session_model(self, page_facts: dict, component_catalog: list[dict]) -> dict:
        component_types = {component.get("type") for component in component_catalog}
        login_surface = page_facts.get("auth", False) and page_facts.get("form", False)
        auth_entry = "sso_login" in component_types or "otp_verification" in component_types or login_surface
        return {
            "auth_entry": auth_entry,
            "requires_authenticated_session": page_facts.get("auth_checkpoint", False) and not login_surface,
            "supported_auth_modes": [
                mode
                for mode, enabled in [
                    ("session_restore", True),
                    ("login_form", login_surface),
                    ("otp", page_facts.get("otp_flow", False)),
                    ("sso", page_facts.get("sso", False)),
                ]
                if enabled
            ],
            "has_manual_checkpoint": bool(page_facts.get("auth_checkpoint", False) or page_facts.get("captcha", False)),
            "consent_present": bool(page_facts.get("consent_banner", False)),
            "spa_surface": bool(page_facts.get("spa_shell", False)),
        }

    def _build_component_catalog(self, components: list[dict]) -> list[dict]:
        catalog = []
        counts = {}
        for raw in self.page_info.get("visual_components", [])[:40]:
            component_type = str(raw.get("type", "")).strip().lower()
            if not component_type:
                continue
            counts[component_type] = counts.get(component_type, 0) + 1
            suffix = f"_{counts[component_type]}" if counts[component_type] > 1 else ""
            label = str(raw.get("label", "")).strip()
            aliases = self._build_component_aliases(raw)
            catalog.append(
                {
                    "component_key": f"{component_type}{suffix}",
                    "type": component_type,
                    "label": label,
                    "aliases": aliases,
                    "items": list(raw.get("items", []))[:10],
                    "container_hints": list(raw.get("container_hints", []))[:6],
                    "dom_path": str(raw.get("dom_path", ""))[:180],
                    "selector_candidates": list(raw.get("selector_candidates", []))[:10],
                    "details": {
                        key: value
                        for key, value in raw.items()
                        if key not in {"type", "label", "items", "container_hints", "dom_path", "selector_candidates"}
                    },
                }
            )
        for component in components:
            component_type = str(component.get("type", "")).strip().lower()
            if component_type and all(existing.get("type") != component_type for existing in catalog):
                counts[component_type] = counts.get(component_type, 0) + 1
                catalog.append(
                    {
                        "component_key": f"{component_type}_{counts[component_type]}",
                        "type": component_type,
                        "label": str(component.get("label", "")).strip(),
                        "aliases": self._build_component_aliases(component),
                        "container_hints": list(component.get("container_hints", []))[:6],
                        "dom_path": str(component.get("dom_path", ""))[:180],
                        "selector_candidates": list(component.get("selector_candidates", []))[:10],
                        "items": [],
                        "details": {k: v for k, v in component.items() if k not in {"type", "label", "container_hints", "dom_path", "selector_candidates"}},
                    }
                )
        return catalog

    def _build_form_catalog(self) -> list[dict]:
        catalog = []
        field_counts = {}
        for index, form in enumerate(self.page_info.get("forms", [])[:8], start=1):
            if not isinstance(form, dict):
                continue
            form_key = f"form_{index}"
            form_entry = {
                "form_key": form_key,
                "id": str(form.get("id", "")).strip(),
                "name": str(form.get("name", "")).strip(),
                "action": str(form.get("action", "")).strip(),
                "method": str(form.get("method", "get")).strip().lower(),
                "submit_texts": list(form.get("submit_texts", []))[:6],
                "context_text": str(form.get("context_text", "")).strip(),
                "container_heading": str(form.get("container_heading", "")).strip(),
                "container_text": str(form.get("container_text", "")).strip(),
                "dom_path": str(form.get("dom_path", "")).strip(),
                "container_hints": list(form.get("container_hints", []))[:6],
                "fields": [],
            }
            for field in form.get("fields", [])[:20]:
                enriched = self._enrich_field(field, form_entry, field_counts)
                form_entry["fields"].append(enriched)
            catalog.append(form_entry)
        standalone_controls = self.page_info.get("standalone_controls", [])
        if standalone_controls:
            pseudo_form = {
                "form_key": f"form_{len(catalog) + 1}",
                "id": "",
                "name": "standalone_controls",
                "action": "",
                "method": "get",
                "submit_texts": [],
                "context_text": "Standalone controls detected outside forms",
                "container_heading": "",
                "container_text": "Standalone controls detected outside forms",
                "dom_path": "",
                "container_hints": ["Standalone controls"],
                "fields": [],
            }
            for field in standalone_controls[:20]:
                pseudo_form["fields"].append(self._enrich_field(field, pseudo_form, field_counts))
            catalog.append(pseudo_form)
        return catalog

    def _enrich_field(self, field: dict, form_entry: dict, field_counts: dict) -> dict:
        semantic_type, confidence = self._infer_field_semantics(field, form_entry)
        semantic_label = FIELD_ONTOLOGY.get(semantic_type, FIELD_ONTOLOGY["generic_text"])["label"]
        aliases = self._build_field_aliases(field, semantic_type)
        base_key = semantic_type or "generic_text"
        field_counts[base_key] = field_counts.get(base_key, 0) + 1
        suffix = f"_{field_counts[base_key]}" if field_counts[base_key] > 1 else ""
        selector_candidates = self._build_selector_candidates(field, semantic_type)
        learned_path_hints = self._learned_field_selectors(field, semantic_type)[:6]
        return {
            "field_key": f"{base_key}{suffix}",
            "form_key": form_entry.get("form_key", ""),
            "semantic_type": semantic_type,
            "semantic_label": semantic_label,
            "semantic_confidence": confidence,
            "tag": field.get("tag", ""),
            "type": field.get("type", ""),
            "name": field.get("name", ""),
            "id": field.get("id", ""),
            "label": field.get("label", ""),
            "placeholder": field.get("placeholder", ""),
            "aria_label": field.get("aria_label", ""),
            "autocomplete": field.get("autocomplete", ""),
            "role": field.get("role", ""),
            "widget": field.get("widget", ""),
            "list_id": field.get("list_id", ""),
            "contenteditable": bool(field.get("contenteditable", False)),
            "accept": field.get("accept", ""),
            "multiple": bool(field.get("multiple", False)),
            "required": bool(field.get("required", False)),
            "aliases": aliases,
            "selector_candidates": selector_candidates,
            "learned_path_hints": learned_path_hints,
            "container_hints": list(dict.fromkeys(
                [item for item in [
                    form_entry.get("label", ""),
                    form_entry.get("context_text", ""),
                    field.get("container_heading", ""),
                    field.get("container_text", ""),
                    field.get("context_text", ""),
                    *form_entry.get("container_hints", []),
                    *field.get("container_hints", []),
                ] if str(item or "").strip()]
            ))[:6],
            "nearby_texts": list(field.get("nearby_texts", []))[:6],
            "dom_path": str(field.get("dom_path", "")).strip(),
            "options": list(field.get("options", []))[:12],
            "context_text": field.get("context_text", ""),
        }

    def _infer_field_semantics(self, field: dict, form_entry: dict) -> tuple[str, float]:
        haystack = " ".join(
            str(part or "")
            for part in [
                field.get("label", ""),
                field.get("name", ""),
                field.get("id", ""),
                field.get("placeholder", ""),
                field.get("aria_label", ""),
                field.get("autocomplete", ""),
                field.get("inputmode", ""),
                field.get("data_testid", ""),
                field.get("semantic_text", ""),
                form_entry.get("context_text", ""),
            ]
        ).lower()
        input_type = str(field.get("type", "")).lower()
        autocomplete = str(field.get("autocomplete", "")).lower()
        widget = str(field.get("widget", "")).lower()
        best_key = "generic_text"
        best_score = 0

        if input_type == "file" or widget == "upload":
            return "file_upload", 0.99
        if input_type == "time" or widget == "timepicker":
            return "time", 0.96
        if input_type in {"date", "datetime-local", "month", "week"} or widget == "datepicker":
            return "date", 0.96
        if widget == "rich_text":
            return "rich_text", 0.95
        if widget == "combobox":
            return "combobox_selection", 0.92

        for key, spec in FIELD_ONTOLOGY.items():
            score = 0
            for signal in spec.get("signals", ()):
                if signal in haystack:
                    score += max(4, len(signal.split()))
            if autocomplete and autocomplete in spec.get("autocomplete", ()):
                score += 8
            if input_type and input_type in spec.get("input_types", ()):
                score += 3
            if key == "generic_text":
                score += 1
            if score > best_score:
                best_key = key
                best_score = score

        if input_type == "password":
            return "password", 0.99
        if input_type == "email":
            return "email", 0.99
        if input_type == "tel":
            return "phone_number", 0.99
        if input_type == "search":
            return "search_query", 0.99
        if input_type == "url":
            return "url", 0.95
        if input_type == "date":
            return "date", 0.95
        if field.get("tag") == "textarea" and best_key == "generic_text":
            return "message", 0.8
        if input_type == "number" and best_key == "generic_text":
            return "quantity", 0.65
        confidence = round(min(0.99, 0.25 + (best_score * 0.07)), 2)
        return best_key, confidence

    def _build_field_aliases(self, field: dict, semantic_type: str) -> list[str]:
        candidates = list(FIELD_ONTOLOGY.get(semantic_type, {}).get("aliases", ()))
        candidates.extend(
            [
                field.get("label", ""),
                field.get("name", ""),
                field.get("id", ""),
                field.get("placeholder", ""),
                field.get("aria_label", ""),
                field.get("autocomplete", ""),
                field.get("data_testid", ""),
                field.get("role", ""),
                field.get("list_id", ""),
                field.get("widget", ""),
            ]
        )
        aliases = []
        seen = set()
        for item in candidates:
            text = re.sub(r"\s+", " ", str(item or "")).strip()
            if not text:
                continue
            lowered = text.lower()
            compact = re.sub(r"[^a-z0-9]+", " ", lowered).strip()
            for variant in [text, compact]:
                normalized = re.sub(r"\s+", " ", variant).strip()
                if normalized and normalized.lower() not in seen:
                    aliases.append(normalized)
                    seen.add(normalized.lower())
        return aliases[:20]

    def _build_selector_candidates(self, field: dict, semantic_type: str) -> list[str]:
        selectors = []
        tag = field.get("tag", "") or "input"
        role = str(field.get("role", "")).strip()
        widget = str(field.get("widget", "")).strip().lower()
        if field.get("contenteditable"):
            selectors.append('[contenteditable="true"]')
        if role:
            selectors.append(f'[role="{role}"]')
        for attr in ["name", "id", "placeholder", "aria_label", "data_testid"]:
            value = str(field.get(attr, "")).strip()
            if not value:
                continue
            safe = value.replace("\\", "\\\\").replace('"', '\\"')
            if attr == "id":
                selectors.append(f'{tag}[id="{safe}"]')
            elif attr == "aria_label":
                selectors.append(f'{tag}[aria-label="{safe}"]')
            elif attr == "data_testid":
                selectors.append(f'{tag}[data-testid="{safe}"]')
                selectors.append(f'{tag}[data-test="{safe}"]')
            else:
                selectors.append(f'{tag}[{attr}="{safe}"]')
        if field.get("list_id"):
            safe_list = str(field.get("list_id", "")).replace("\\", "\\\\").replace('"', '\\"')
            selectors.append(f'{tag}[list="{safe_list}"]')
        if widget == "combobox":
            selectors.append('[role="combobox"]')
            selectors.append('[aria-autocomplete]')
        if widget == "rich_text":
            selectors.extend([
                '[contenteditable="true"]',
                '.ql-editor',
                '.ProseMirror',
                '.tox-edit-area',
                '.ck-editor__editable',
            ])
        if widget == "upload":
            selectors.append('input[type="file"]')
        selectors.extend(self._learned_field_selectors(field, semantic_type))
        return list(dict.fromkeys(selectors))[:16]

    def _learned_field_selectors(self, field: dict, semantic_type: str) -> list[str]:
        learning = self.page_info.get("site_profile", {}).get("learning", {})
        keys = [
            semantic_type,
            field.get("label", ""),
            field.get("name", ""),
            field.get("id", ""),
            field.get("aria_label", ""),
        ]
        selectors = []
        for key in keys:
            normalized = _normalize_learning_key(key)
            if normalized:
                selectors.extend(get_ranked_selector_candidates(learning, "field_selectors", normalized, limit=4))
                for failure in get_failure_memory(learning, "field_selectors", normalized, limit=2):
                    failed_selector = str(failure.get("selector", "")).strip()
                    if failed_selector and failed_selector in selectors:
                        selectors.remove(failed_selector)
        return _merge_unique_list(selectors)[:8]

    def _field_action_type(self, field: dict) -> str:
        widget = str(field.get("widget", "")).lower()
        if field.get("type") == "file" or widget == "upload":
            return "upload"
        if field.get("tag") == "select" or widget == "combobox":
            return "select"
        return "fill"

    def _build_component_aliases(self, component: dict) -> list[str]:
        aliases = []
        seen = set()
        values = [component.get("label", ""), component.get("type", "")]
        values.extend(component.get("items", []))
        for value in values:
            text = re.sub(r"\s+", " ", str(value or "")).strip()
            normalized = re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()
            for variant in [text, normalized]:
                clean = re.sub(r"\s+", " ", str(variant or "")).strip()
                if clean and clean.lower() not in seen:
                    aliases.append(clean)
                    seen.add(clean.lower())
        return aliases[:15]


def build_normalized_page_model(page_info: dict) -> dict:
    return PageModelBuilder(page_info).build()


def save_json_artifact(data: dict, output_path: Path) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    return output_path


def build_execution_plan(test_cases: list[dict], page_model: dict, base_url: str, site_profile: dict | None = None) -> dict:
    site_profile = site_profile or page_model.get("site_profile", {})
    execution_settings = _build_execution_settings(page_model, site_profile)
    network_policy = _build_network_policy(page_model, base_url, site_profile)
    plans = []
    for case in test_cases:
        steps = str(case.get("Steps to Reproduce", ""))
        expected = str(case.get("Expected Result", ""))
        automation = str(case.get("Automation", "auto")).strip().lower() or "auto"
        actions = _extract_actions(steps, page_model)
        pre_actions = _derive_pre_actions(case, page_model, site_profile)
        checkpoints = _infer_manual_checkpoints(case, page_model, site_profile)
        plan = {
            "id": str(case.get("ID", "")).strip(),
            "title": str(case.get("Title", "")).strip(),
            "module": str(case.get("Module", "")).strip(),
            "automation": automation,
            "target_url": _extract_target_url(steps, base_url),
            "pre_actions": pre_actions,
            "actions": actions,
            "assertions": _extract_assertions(expected, page_model),
            "dependencies": [],
            "state_targets": _infer_state_targets(page_model, case),
            "checkpoints": checkpoints,
            "session_strategy": _build_session_strategy(page_model, case, site_profile),
            "orchestration": _build_orchestration(page_model, automation, checkpoints),
            "interaction_hints": _build_interaction_hints(page_model, actions, execution_settings),
            "expected_request_map": _build_expected_request_map(page_model, case, base_url, network_policy),
            "scenario_grounding": dict(case.get("_grounding", {})),
            "scenario_alignment": dict(case.get("_task_alignment", {})),
            "source_case": {
                "category": str(case.get("Category", "")).strip(),
                "test_type": str(case.get("Test Type", "")).strip(),
                "expected_result": expected,
            },
        }
        conservative_mode = _should_use_conservative_plan(plan, page_model)
        if conservative_mode:
            plan = _apply_conservative_plan_mode(plan, page_model)
        plan["planning_mode"] = "conservative" if conservative_mode else "normal"
        plan["grounding_summary"] = _build_grounding_summary(plan)
        plan["evidence_trace"] = _build_plan_evidence_trace(plan)
        plans.append(plan)
    return {
        "version": 3,
        "base_url": base_url,
        "page_identity": page_model.get("page_identity", {}),
        "state_graph": page_model.get("state_graph", {}),
        "session_model": page_model.get("session_model", {}),
        "runtime_observer": page_model.get("runtime_observer", {}),
        "page_facts": page_model.get("page_facts", {}),
        "site_profile": site_profile,
        "network_policy": network_policy,
        "settings": execution_settings,
        "plans": plans,
    }


def _infer_state_targets(page_model: dict, case: dict | None = None) -> list[str]:
    state_graph = page_model.get("state_graph", {})
    states = [state.get("id", "") for state in state_graph.get("states", [])[1:5] if state.get("id")]
    case_text = " ".join(str(case.get(key, "")) for key in ("Title", "Steps to Reproduce", "Expected Result")) if case else ""
    lowered = case_text.lower()
    preferred = []
    for state in states:
        if "consent" in lowered and "consent" in state:
            preferred.append(state)
        elif "otp" in lowered and "otp" in state:
            preferred.append(state)
        elif any(term in lowered for term in ("sso", "google", "microsoft")) and "sso" in state:
            preferred.append(state)
        elif any(term in lowered for term in ("route", "navigate", "redirect")) and any(token in state for token in ("route", "navigated")):
            preferred.append(state)
        elif any(term in lowered for term in ("live", "update", "refresh")) and "live" in state:
            preferred.append(state)
    return preferred[:3] or states[:3]


def _build_execution_settings(page_model: dict, site_profile: dict | None) -> dict:
    site_profile = site_profile or {}
    interaction = site_profile.get("interaction", {})
    runtime = page_model.get("runtime_observer", {})
    return {
        "step_delay_ms": int(interaction.get("step_delay_ms", 700)),
        "settle_delay_ms": int(interaction.get("settle_delay_ms", 1000)),
        "final_delay_ms": int(interaction.get("final_delay_ms", 1400)),
        "retry_count": int(interaction.get("retry_count", 2)),
        "has_live_runtime": bool(runtime.get("websocket_count", 0) or runtime.get("graphql_request_count", 0)),
    }


def _derive_pre_actions(case: dict, page_model: dict, site_profile: dict | None) -> list[dict]:
    site_profile = site_profile or {}
    case_text = " ".join(str(case.get(key, "")) for key in ("Title", "Steps to Reproduce", "Expected Result")).lower()
    actions = []
    if (
        site_profile.get("execution", {}).get("auto_dismiss_consent", True)
        and page_model.get("page_facts", {}).get("consent_banner")
        and "cookie" not in case_text
        and "consent" not in case_text
    ):
        actions.append(
            {
                "type": "dismiss",
                "target": "Accept cookies",
                "role": "button",
                "component_type": "consent_banner",
                "aliases": ["accept", "agree", "allow", "ok", "got it", "accept cookies"],
            }
        )
    return [_ground_action(action, page_model) for action in actions]


def _infer_manual_checkpoints(case: dict, page_model: dict, site_profile: dict | None) -> list[dict]:
    site_profile = site_profile or {}
    case_text = " ".join(str(case.get(key, "")) for key in ("Title", "Steps to Reproduce", "Expected Result")).lower()
    page_facts = page_model.get("page_facts", {})
    checkpoints = []
    manual_terms = [term.lower() for term in site_profile.get("auth", {}).get("manual_checkpoint_terms", [])]
    if page_facts.get("captcha") or "captcha" in case_text:
        checkpoints.append({"type": "captcha", "mode": "manual", "reason": "Captcha or bot verification may block automation."})
    if page_facts.get("otp_flow") or any(term in case_text for term in manual_terms if "otp" in term or "verification" in term):
        checkpoints.append({"type": "otp", "mode": "manual", "reason": "OTP or verification code may require human input."})
    if page_facts.get("sso") and any(term in case_text for term in ("google", "microsoft", "sso", "single sign-on")):
        checkpoints.append({"type": "sso", "mode": "semi-auto", "reason": "SSO redirect may require external confirmation."})
    return checkpoints


def _build_session_strategy(page_model: dict, case: dict, site_profile: dict | None) -> dict:
    site_profile = site_profile or {}
    session_model = page_model.get("session_model", {})
    case_text = " ".join(str(case.get(key, "")) for key in ("Title", "Steps to Reproduce", "Expected Result")).lower()
    return {
        "auth_entry": bool(session_model.get("auth_entry", False)),
        "requires_session": bool(session_model.get("requires_authenticated_session", False) and "login" not in case_text),
        "supported_auth_modes": list(session_model.get("supported_auth_modes", [])),
        "storage_state_candidates": list(site_profile.get("auth", {}).get("storage_state_candidates", [])),
        "manual_checkpoints": list(_infer_manual_checkpoints(case, page_model, site_profile)),
    }


def _build_orchestration(page_model: dict, automation: str, checkpoints: list[dict]) -> dict:
    page_facts = page_model.get("page_facts", {})
    mode = automation
    if checkpoints and mode == "auto":
        mode = "semi-auto"
    return {
        "mode": mode,
        "has_manual_checkpoint": bool(checkpoints),
        "checkpoint_count": len(checkpoints),
        "supports_partial_execution": mode in {"auto", "semi-auto"},
        "spa_sensitive": bool(page_facts.get("spa_shell") or page_facts.get("live_updates")),
    }


def _build_interaction_hints(page_model: dict, actions: list[dict], settings: dict) -> dict:
    page_facts = page_model.get("page_facts", {})
    dynamic_surface = bool(page_facts.get("spa_shell") or page_facts.get("live_updates") or page_facts.get("graphql"))
    list_like = bool(page_facts.get("listing") or page_facts.get("infinite_scroll"))
    has_clicks = any(action.get("type") in {"click", "dismiss"} for action in actions)
    return {
        "step_delay_ms": settings.get("step_delay_ms", 700),
        "settle_delay_ms": settings.get("settle_delay_ms", 1000) + (300 if dynamic_surface else 0),
        "final_delay_ms": settings.get("final_delay_ms", 1400),
        "expects_route_change": has_clicks and bool(page_facts.get("navigation") or page_facts.get("spa_shell")),
        "prefers_scroll_after_click": list_like,
        "watch_live_updates": bool(page_facts.get("live_updates")),
    }


def _build_network_policy(page_model: dict, base_url: str, site_profile: dict | None) -> dict:
    site_profile = site_profile or {}
    network_profile = site_profile.get("network", {})
    base_host = re.sub(r"^www\.", "", urlparse(base_url).netloc.strip().lower()) if base_url else ""
    api_endpoints = [str(item or "").strip() for item in page_model.get("api_endpoints", []) if str(item or "").strip()]
    allowed_hosts = []
    for endpoint in api_endpoints[:12]:
        match = re.search(r"^https?://([^/]+)", endpoint, flags=re.IGNORECASE)
        host = re.sub(r"^www\.", "", match.group(1).strip().lower()) if match else base_host
        if host and host not in allowed_hosts:
            allowed_hosts.append(host)
    for host in network_profile.get("allowed_hosts", []):
        normalized = re.sub(r"^www\.", "", str(host or "").strip().lower())
        if normalized and normalized not in allowed_hosts:
            allowed_hosts.append(normalized)
    if base_host and base_host not in allowed_hosts:
        allowed_hosts.insert(0, base_host)
    endpoint_tokens = []
    for endpoint in api_endpoints[:12]:
        token = endpoint.split("?", 1)[0].strip()
        if token and token not in endpoint_tokens:
            endpoint_tokens.append(token)
    return {
        "base_host": base_host,
        "allowed_hosts": allowed_hosts[:12],
        "allowed_endpoint_tokens": endpoint_tokens[:12],
        "cross_origin_mode": str(network_profile.get("cross_origin_mode", "same-origin")).strip().lower() or "same-origin",
        "graphql_error_keys": list(network_profile.get("graphql_error_keys", ["errors", "error", "extensions"]))[:6],
    }


def _build_expected_request_map(page_model: dict, case: dict, base_url: str, network_policy: dict) -> dict:
    text = " ".join(str(case.get(key, "")) for key in ("Title", "Steps to Reproduce", "Expected Result")).lower()
    api_endpoints = [str(item or "").strip() for item in page_model.get("api_endpoints", []) if str(item or "").strip()]
    expected_endpoints = []
    for endpoint in api_endpoints[:12]:
        endpoint_lower = endpoint.lower()
        if any(token in endpoint_lower for token in ("search", "query")) and any(token in text for token in ("search", "query", "find", "keyword")):
            expected_endpoints.append(endpoint)
        elif any(token in endpoint_lower for token in ("login", "auth", "session")) and any(token in text for token in ("login", "auth", "password", "username", "sign in")):
            expected_endpoints.append(endpoint)
        elif any(token in endpoint_lower for token in ("upload", "file", "media")) and any(token in text for token in ("upload", "file", "attachment")):
            expected_endpoints.append(endpoint)
        elif any(token in endpoint_lower for token in ("submit", "contact", "save", "apply")) and any(token in text for token in ("submit", "send", "contact", "save", "apply", "phone number")):
            expected_endpoints.append(endpoint)
        elif "graphql" in endpoint_lower and "graphql" in text:
            expected_endpoints.append(endpoint)
    if not expected_endpoints:
        expected_endpoints = api_endpoints[:4]
    return {
        "same_origin_required": network_policy.get("cross_origin_mode", "same-origin") == "same-origin",
        "allowed_hosts": list(network_policy.get("allowed_hosts", []))[:8],
        "expected_endpoints": expected_endpoints[:8],
        "graphql_expected": "graphql" in text or any("graphql" in endpoint.lower() for endpoint in expected_endpoints[:4]),
    }


def _extract_target_url(steps: str, fallback_url: str) -> str:
    match = re.search(r"1\.\s*Open the site\s+(\S+)", steps, flags=re.IGNORECASE)
    return match.group(1).strip().rstrip(".,") if match else fallback_url


def _extract_actions(steps: str, page_model: dict | None = None) -> list[dict]:
    actions = []
    input_pattern = re.compile(
        r"^Input\s+['\"]([^'\"]*)['\"]\s+into\s+the\s+(?:['\"]([^'\"]+)['\"]|(.+?))(?:\s+field)?\.?$",
        flags=re.IGNORECASE,
    )
    click_pattern = re.compile(
        r"^Click\s+the\s+(?:['\"]([^'\"]+)['\"]|(.+?))(?:\s+(button|link|menu|tab))?\.?$",
        flags=re.IGNORECASE,
    )
    select_pattern = re.compile(
        r"^(?:Select|Choose)\s+['\"]([^'\"]+)['\"]\s+from\s+the\s+(?:['\"]([^'\"]+)['\"]|(.+?))(?:\s+field)?\.?$",
        flags=re.IGNORECASE,
    )
    upload_pattern = re.compile(
        r"^Upload\s+['\"]([^'\"]+)['\"]\s+into\s+the\s+(?:['\"]([^'\"]+)['\"]|(.+?))(?:\s+field)?\.?$",
        flags=re.IGNORECASE,
    )
    hover_pattern = re.compile(
        r"^Hover\s+(?:over\s+)?the\s+(?:['\"]([^'\"]+)['\"]|(.+?))(?:\s+(button|link|menu|tab))?\.?$",
        flags=re.IGNORECASE,
    )
    scroll_pattern = re.compile(
        r"^Scroll(?:\s+to\s+the\s+(?:['\"]([^'\"]+)['\"]|(.+?)))?(?:\s+section)?\.?$",
        flags=re.IGNORECASE,
    )
    wait_pattern = re.compile(
        r"^Wait\s+for\s+(?:the\s+text\s+)?(?:['\"]([^'\"]+)['\"]|(.+?))\.?$",
        flags=re.IGNORECASE,
    )
    dismiss_pattern = re.compile(
        r"^(?:Dismiss|Close|Accept)\s+(?:the\s+)?(?:['\"]([^'\"]+)['\"]|(.+?))(?:\s+(banner|modal|dialog|popup))?\.?$",
        flags=re.IGNORECASE,
    )
    clear_pattern = re.compile(
        r"^Leave\s+the\s+(?:['\"]([^'\"]+)['\"]|(.+?))\s+field\s+(?:empty|blank)\.?$",
        flags=re.IGNORECASE,
    )

    for step_index, raw_line in enumerate(steps.splitlines(), start=1):
        line = re.sub(r"^\s*\d+\.\s*", "", str(raw_line or "")).strip()
        if not line:
            continue

        input_match = input_pattern.match(line)
        if input_match:
            value, quoted_target, plain_target = input_match.groups()
            action = {"type": "fill", "target": (quoted_target or plain_target or "").strip(), "value": value, "step_index": step_index, "step_text": line}
            actions.append(_ground_action(_enrich_field_action(action, page_model), page_model))
            continue

        select_match = select_pattern.match(line)
        if select_match:
            value, quoted_target, plain_target = select_match.groups()
            action = {"type": "select", "target": (quoted_target or plain_target or "").strip(), "value": value, "step_index": step_index, "step_text": line}
            actions.append(_ground_action(_enrich_field_action(action, page_model), page_model))
            continue

        upload_match = upload_pattern.match(line)
        if upload_match:
            value, quoted_target, plain_target = upload_match.groups()
            action = {"type": "upload", "target": (quoted_target or plain_target or "").strip(), "value": value, "step_index": step_index, "step_text": line}
            actions.append(_ground_action(_enrich_field_action(action, page_model), page_model))
            continue

        clear_match = clear_pattern.match(line)
        if clear_match:
            quoted_target, plain_target = clear_match.groups()
            action = {"type": "fill", "target": (quoted_target or plain_target or "").strip(), "value": "", "step_index": step_index, "step_text": line}
            actions.append(_ground_action(_enrich_field_action(action, page_model), page_model))
            continue

        click_match = click_pattern.match(line)
        if click_match:
            quoted_target, plain_target, role = click_match.groups()
            actions.append(_ground_action({"type": "click", "target": (quoted_target or plain_target or "").strip(), "role": (role or "").strip().lower(), "step_index": step_index, "step_text": line}, page_model))
            continue

        hover_match = hover_pattern.match(line)
        if hover_match:
            quoted_target, plain_target, role = hover_match.groups()
            actions.append(_ground_action({"type": "hover", "target": (quoted_target or plain_target or "").strip(), "role": (role or "").strip().lower(), "step_index": step_index, "step_text": line}, page_model))
            continue

        scroll_match = scroll_pattern.match(line)
        if scroll_match:
            quoted_target, plain_target = scroll_match.groups()
            actions.append(_ground_action({"type": "scroll", "target": (quoted_target or plain_target or "").strip(), "step_index": step_index, "step_text": line}, page_model))
            continue

        wait_match = wait_pattern.match(line)
        if wait_match:
            quoted_target, plain_target = wait_match.groups()
            actions.append(_ground_action({"type": "wait_for_text", "target": (quoted_target or plain_target or "").strip(), "step_index": step_index, "step_text": line}, page_model))
            continue

        dismiss_match = dismiss_pattern.match(line)
        if dismiss_match:
            quoted_target, plain_target, role = dismiss_match.groups()
            actions.append(_ground_action({"type": "dismiss", "target": (quoted_target or plain_target or "").strip(), "role": (role or "button").strip().lower(), "step_index": step_index, "step_text": line}, page_model))

    if not actions:
        actions.append(_ground_action({"type": "inspect", "target": "page", "step_index": 0, "step_text": "Inspect the page"}, page_model))
    return actions


def _extract_assertions(expected: str, page_model: dict | None = None) -> list[dict]:
    assertions = []
    quoted = re.findall(r"'([^']+)'|\"([^\"]+)\"", expected)
    quoted_values = [a or b for a, b in quoted if a or b]
    expected_lower = expected.lower()
    explicit_endpoints = re.findall(r"https?://[^\s'\"]+|/[a-zA-Z0-9\-_./?=&]+", expected)
    if any(term in expected_lower for term in ("api", "request", "response", "endpoint", "network", "graphql", "fetch", "xhr")):
        endpoint = explicit_endpoints[0] if explicit_endpoints else _default_network_target(page_model, expected_lower)
        assertions.append(_ground_assertion({"type": "assert_network_seen", "value": endpoint, "source_text": expected}, page_model))
        if any(term in expected_lower for term in ("200", "ok", "success response", "successful response", "without error", "status code")):
            assertions.append(_ground_assertion({"type": "assert_network_status_ok", "value": endpoint, "source_text": expected}, page_model))
        if "graphql" in expected_lower:
            assertions.append(_ground_assertion({"type": "assert_graphql_ok", "value": endpoint, "source_text": expected}, page_model))
    if any(term in expected_lower for term in ("same-origin", "same origin", "third-party", "third party", "cross-origin", "cross origin", "allowlist", "allow-list")):
        assertions.append(_ground_assertion({"type": "assert_cross_origin_safe", "value": "same-origin", "source_text": expected}, page_model))
        if any(term in expected_lower for term in ("allowlist", "allow-list", "approved endpoint", "approved domain")):
            assertions.append(_ground_assertion({"type": "assert_endpoint_allowlist", "value": "allowlist", "source_text": expected}, page_model))
    if "url" in expected_lower or "redirect" in expected_lower or "open" in expected_lower or "navigate" in expected_lower:
        path_match = re.search(r"/[a-zA-Z0-9\-_/?=&]+", expected)
        if path_match:
            assertions.append(_ground_assertion({"type": "assert_url_contains", "value": path_match.group(0), "source_text": expected}, page_model))
        else:
            redirect_match = re.search(r"redirect(?:ed)?\s+to\s+the\s+([a-z0-9 _-]+?)\s+page", expected_lower)
            if redirect_match:
                assertions.append(_ground_assertion({"type": "assert_url_contains", "value": _slug_phrase(redirect_match.group(1)), "source_text": expected}, page_model))
    if any(term in expected_lower for term in ("title", "page title")) and quoted_values:
        assertions.append(_ground_assertion({"type": "assert_title_contains", "value": quoted_values[0], "source_text": expected}, page_model))
    if any(term in expected_lower for term in ("not visible", "not displayed", "not shown", "should disappear", "hidden")):
        for text in quoted_values[:2]:
            assertions.append(_ground_assertion({"type": "assert_text_not_visible", "value": text, "source_text": expected}, page_model))
    if "button" in expected_lower and any(term in expected_lower for term in ("visible", "enabled", "shown")) and quoted_values:
        assertions.append(_ground_assertion({"type": "assert_control_visible", "value": quoted_values[0], "source_text": expected}, page_model))
    if "link" in expected_lower and any(term in expected_lower for term in ("visible", "shown")) and quoted_values:
        assertions.append(_ground_assertion({"type": "assert_control_visible", "value": quoted_values[0], "source_text": expected}, page_model))
    if "display" in expected_lower or "visible" in expected_lower or "shown" in expected_lower or "message" in expected_lower:
        for text in quoted_values[:2]:
            assertions.append(_ground_assertion({"type": "assert_text_visible", "value": text, "source_text": expected}, page_model))
    if "text" in expected_lower and quoted_values:
        assertions.append(_ground_assertion({"type": "assert_control_text", "value": quoted_values[0], "source_text": expected}, page_model))
    if not assertions:
        generic_texts = _generic_assertion_texts(expected_lower, page_model)
        if generic_texts:
            assertions.append(_ground_assertion({"type": "assert_any_text_visible", "values": generic_texts, "source_text": expected}, page_model))
    if not assertions and quoted_values:
        assertions.append(_ground_assertion({"type": "assert_text_visible", "value": quoted_values[0], "source_text": expected}, page_model))
    return assertions


def _enrich_field_action(action: dict, page_model: dict | None) -> dict:
    if not page_model:
        return action
    match = _match_field_reference(action.get("target", ""), page_model)
    if not match:
        return action
    enriched = dict(action)
    enriched["field_key"] = match.get("field_key", "")
    enriched["semantic_type"] = match.get("semantic_type", "")
    enriched["semantic_label"] = match.get("semantic_label", "")
    enriched["aliases"] = match.get("aliases", [])
    enriched["selector_candidates"] = _merge_unique_list(match.get("selector_candidates", []), _learned_field_selectors(match, page_model))
    enriched["learned_path_hints"] = match.get("learned_path_hints", [])
    enriched["container_hints"] = match.get("container_hints", [])
    enriched["nearby_texts"] = match.get("nearby_texts", [])
    enriched["dom_path"] = match.get("dom_path", "")
    enriched["input_kind"] = match.get("widget", "") or match.get("type", "")
    return enriched


def _match_field_reference(target: str, page_model: dict) -> dict | None:
    target_text = re.sub(r"\s+", " ", str(target or "")).strip().lower()
    if not target_text:
        return None
    target_compact = re.sub(r"[^a-z0-9]+", "", target_text)
    best = None
    best_score = 0
    for field in page_model.get("field_catalog", []):
        score = 0
        for alias in field.get("aliases", []):
            alias_text = str(alias).strip().lower()
            alias_compact = re.sub(r"[^a-z0-9]+", "", alias_text)
            if alias_text == target_text:
                score += 14
            elif alias_compact and alias_compact == target_compact:
                score += 12
            elif alias_text and alias_text in target_text:
                score += 8
            elif target_text in alias_text:
                score += 8
            elif alias_compact and target_compact and (alias_compact in target_compact or target_compact in alias_compact):
                score += 7
        semantic_label = str(field.get("semantic_label", "")).lower()
        if semantic_label == target_text:
            score += 10
        if score > best_score:
            best = {**field, "_match_score": score, "_matched_target": target}
            best_score = score
    return best if best_score >= 7 else None


def _ground_action(action: dict, page_model: dict | None) -> dict:
    enriched = dict(action)
    refs = []
    action_type = str(action.get("type", "")).strip().lower()
    if not page_model:
        enriched["grounding_refs"] = refs
        enriched["grounded"] = action_type in {"open_url", "inspect"}
        enriched["grounding_confidence"] = 1.0 if enriched["grounded"] else 0.0
        return enriched

    if action_type in {"fill", "select", "upload"}:
        field_match = _match_field_reference(action.get("target", ""), page_model)
        if field_match:
            refs.append(
                {
                    "source_type": "field",
                    "source_key": field_match.get("field_key", ""),
                    "source_label": field_match.get("semantic_label") or field_match.get("label", ""),
                    "matched_text": action.get("target", ""),
                    "score": field_match.get("_match_score", 0),
                    "form_key": field_match.get("form_key", ""),
                }
            )
    elif action_type in {"click", "hover", "dismiss", "scroll", "wait_for_text"}:
        refs.extend(_match_interaction_refs(action, page_model))
        component_match = _match_component_reference(action.get("target", ""), page_model)
        if component_match:
            enriched["aliases"] = component_match.get("aliases", [])
            enriched["container_hints"] = component_match.get("container_hints", [])
            enriched["nearby_texts"] = component_match.get("items", [])[:6]
            enriched["dom_path"] = component_match.get("dom_path", "")
            enriched["learned_path_hints"] = _learned_action_selectors(action.get("target", ""), page_model)
            enriched["selector_candidates"] = _merge_unique_list(
                component_match.get("selector_candidates", []),
                enriched.get("selector_candidates", []),
                enriched.get("learned_path_hints", []),
            )

    if action_type in {"open_url", "inspect"} and not refs:
        refs.append({"source_type": "page", "source_key": "page_identity", "source_label": "Page identity", "matched_text": action.get("target", ""), "score": 10})

    if refs:
        enriched["grounding_refs"] = refs[:6]
        enriched["evidence_refs"] = _build_step_evidence_refs(refs)
        enriched["evidence_summary"] = _build_evidence_summary(refs)
        enriched["grounded"] = True
        enriched["grounding_confidence"] = round(min(1.0, max(ref.get("score", 0) for ref in refs) / 14), 2)
        enriched["fact_coverage_score"] = _reference_coverage_score(refs, expected_types={"field", "submit_control"} if action_type in {"fill", "select", "upload"} else {"component", "submit_control", "heading", "button", "link", "field", "page"})
        if action_type in {"click", "hover", "dismiss"} and not enriched.get("selector_candidates"):
            enriched["selector_candidates"] = _learned_action_selectors(action.get("target", ""), page_model)
    else:
        enriched["grounding_refs"] = []
        enriched["evidence_refs"] = []
        enriched["evidence_summary"] = ""
        enriched["grounded"] = False
        enriched["grounding_confidence"] = 0.0
        enriched["fact_coverage_score"] = 0.0
    return enriched


def _ground_assertion(assertion: dict, page_model: dict | None) -> dict:
    enriched = dict(assertion)
    assertion_type = str(assertion.get("type", "")).strip().lower()
    refs = []
    source_text = str(assertion.get("source_text", "")).lower()

    if not page_model:
        enriched["grounding_refs"] = refs
        enriched["grounded"] = False
        enriched["grounding_confidence"] = 0.0
        return enriched

    value = assertion.get("value", "")
    if assertion_type in {"assert_text_visible", "assert_text_not_visible", "assert_any_text_visible"}:
        candidate_values = assertion.get("values", []) if assertion_type == "assert_any_text_visible" else [value]
        for candidate in candidate_values:
            refs.extend(_match_text_refs(candidate, page_model))
        if not refs and candidate_values:
            refs.extend(_match_interaction_refs({"type": "wait_for_text", "target": candidate_values[0]}, page_model))
        if not refs:
            refs.extend(_generic_assertion_refs(candidate_values, source_text, page_model))
    elif assertion_type in {"assert_control_visible", "assert_control_text"}:
        refs.extend(_match_interaction_refs({"type": "click", "target": value}, page_model))
    elif assertion_type == "assert_title_contains":
        title = str(page_model.get("page_identity", {}).get("title", ""))
        if value and title:
            refs.append(
                {
                    "source_type": "page_identity",
                    "source_key": "title",
                    "source_label": title[:120],
                    "matched_text": value,
                    "score": 12 if value.lower() in title.lower() else 8,
                }
            )
    elif assertion_type == "assert_url_contains":
        for state in page_model.get("state_graph", {}).get("states", []):
            label = str(state.get("label", ""))
            if value and value.lower().replace("_", " ") in label.lower():
                refs.append(
                    {
                        "source_type": "state",
                        "source_key": state.get("id", ""),
                        "source_label": label,
                        "matched_text": value,
                        "score": 10,
                    }
                )
        if not refs:
            refs.append(
                {
                    "source_type": "page_fact",
                    "source_key": "navigation_surface",
                    "source_label": "Navigation or route change surface",
                    "matched_text": value,
                    "score": 8,
                }
            )
    elif assertion_type in {"assert_network_seen", "assert_network_status_ok", "assert_graphql_ok", "assert_endpoint_allowlist", "assert_cross_origin_safe"}:
        api_endpoints = [str(item or "") for item in page_model.get("api_endpoints", [])]
        candidate = str(value or "").strip().lower()
        if candidate:
            for endpoint in api_endpoints[:8]:
                endpoint_lower = endpoint.lower()
                if candidate in endpoint_lower or endpoint_lower.endswith(candidate):
                    refs.append(
                        {
                            "source_type": "api_endpoint",
                            "source_key": endpoint,
                            "source_label": endpoint,
                            "matched_text": value,
                            "score": 10,
                        }
                    )
        if not refs and api_endpoints:
            refs.append(
                {
                    "source_type": "page_fact",
                    "source_key": "api_surface",
                    "source_label": api_endpoints[0][:140],
                    "matched_text": value or "api",
                    "score": 8,
                }
            )
        if assertion_type in {"assert_endpoint_allowlist", "assert_cross_origin_safe"}:
            refs.append(
                {
                    "source_type": "page_fact",
                    "source_key": "network_policy",
                    "source_label": "Network allowlist and cross-origin policy",
                    "matched_text": value or "network policy",
                    "score": 8,
                }
            )
        if assertion_type == "assert_graphql_ok":
            refs.append(
                {
                    "source_type": "page_fact",
                    "source_key": "graphql_surface",
                    "source_label": "GraphQL response surface",
                    "matched_text": value or "graphql",
                    "score": 9,
                }
            )

    if refs:
        enriched["grounding_refs"] = refs[:6]
        enriched["evidence_refs"] = _build_step_evidence_refs(refs)
        enriched["evidence_summary"] = _build_evidence_summary(refs)
        enriched["grounded"] = True
        enriched["grounding_confidence"] = round(min(1.0, max(ref.get("score", 0) for ref in refs) / 12), 2)
        enriched["fact_coverage_score"] = _reference_coverage_score(
            refs,
            expected_types={"component", "heading", "button", "link", "field", "page_identity", "page_fact", "state", "api_endpoint"},
        )
    else:
        enriched["grounding_refs"] = []
        enriched["evidence_refs"] = []
        enriched["evidence_summary"] = ""
        enriched["grounded"] = False
        enriched["grounding_confidence"] = 0.0
        enriched["fact_coverage_score"] = 0.0
    return enriched


def _build_step_evidence_refs(refs: list[dict]) -> list[dict]:
    rows = []
    for ref in refs[:6]:
        rows.append(
            {
                "kind": str(ref.get("source_type", "")).strip(),
                "key": str(ref.get("source_key", "")).strip(),
                "label": str(ref.get("source_label", "")).strip(),
                "matched_text": str(ref.get("matched_text", "")).strip(),
                "score": float(ref.get("score", 0) or 0),
            }
        )
    return rows


def _build_evidence_summary(refs: list[dict]) -> str:
    parts = []
    for ref in refs[:4]:
        label = str(ref.get("source_label", "")).strip() or str(ref.get("source_key", "")).strip()
        kind = str(ref.get("source_type", "")).strip()
        if label and kind:
            parts.append(f"{kind}:{label}")
    return "; ".join(parts)


def _build_plan_evidence_trace(plan: dict) -> dict:
    actions = list(plan.get("pre_actions", [])) + list(plan.get("actions", []))
    assertions = list(plan.get("assertions", []))
    trace = {
        "grounded_action_count": sum(1 for item in actions if item.get("grounded")),
        "grounded_assertion_count": sum(1 for item in assertions if item.get("grounded")),
        "average_action_fact_coverage": round(
            sum(float(item.get("fact_coverage_score", 0.0) or 0.0) for item in actions) / len(actions),
            2,
        ) if actions else 0.0,
        "average_assertion_fact_coverage": round(
            sum(float(item.get("fact_coverage_score", 0.0) or 0.0) for item in assertions) / len(assertions),
            2,
        ) if assertions else 0.0,
        "weak_steps": [],
        "step_evidence": [],
    }
    for index, item in enumerate(actions, start=1):
        step_key = f"action_{index}"
        trace["step_evidence"].append(
            {
                "step_key": step_key,
                "type": item.get("type", ""),
                "target": item.get("target", ""),
                "grounded": bool(item.get("grounded", False)),
                "confidence": float(item.get("grounding_confidence", 0.0) or 0.0),
                "fact_coverage_score": float(item.get("fact_coverage_score", 0.0) or 0.0),
                "evidence_refs": list(item.get("evidence_refs", []))[:4],
            }
        )
        if float(item.get("grounding_confidence", 0.0) or 0.0) < 0.55:
            trace["weak_steps"].append(step_key)
    for index, item in enumerate(assertions, start=1):
        step_key = f"assertion_{index}"
        trace["step_evidence"].append(
            {
                "step_key": step_key,
                "type": item.get("type", ""),
                "target": item.get("value", "") or item.get("values", []),
                "grounded": bool(item.get("grounded", False)),
                "confidence": float(item.get("grounding_confidence", 0.0) or 0.0),
                "fact_coverage_score": float(item.get("fact_coverage_score", 0.0) or 0.0),
                "evidence_refs": list(item.get("evidence_refs", []))[:4],
            }
        )
        if float(item.get("grounding_confidence", 0.0) or 0.0) < 0.55:
            trace["weak_steps"].append(step_key)
    return trace


def _generic_assertion_refs(values: list[str], source_text: str, page_model: dict) -> list[dict]:
    text_blob = " ".join(str(value or "") for value in values).lower()
    facts = page_model.get("page_facts", {})
    refs = []
    if any(term in text_blob or term in source_text for term in ("error", "invalid", "required")) and facts.get("form"):
        refs.append({"source_type": "page_fact", "source_key": "form_validation", "source_label": "Form validation state", "matched_text": text_blob or source_text, "score": 8})
    if any(term in text_blob or term in source_text for term in ("success", "saved", "submitted")) and any(
        facts.get(key) for key in ("form", "upload", "rich_text")
    ):
        refs.append({"source_type": "page_fact", "source_key": "submission_feedback", "source_label": "Submission feedback surface", "matched_text": text_blob or source_text, "score": 8})
    if any(term in text_blob or term in source_text for term in ("result", "results")) and any(
        facts.get(key) for key in ("search", "listing", "table")
    ):
        refs.append({"source_type": "page_fact", "source_key": "results_surface", "source_label": "Search or results surface", "matched_text": text_blob or source_text, "score": 8})
    if any(term in text_blob or term in source_text for term in ("modal", "dialog")):
        refs.append({"source_type": "page_fact", "source_key": "dialog_surface", "source_label": "Modal or dialog surface", "matched_text": text_blob or source_text, "score": 7})
    if any(term in text_blob or term in source_text for term in ("api", "request", "response", "endpoint", "graphql", "network")) and page_model.get("api_endpoints"):
        refs.append({"source_type": "page_fact", "source_key": "api_surface", "source_label": "Observed API surface", "matched_text": text_blob or source_text, "score": 8})
    return refs[:4]


def _default_network_target(page_model: dict | None, expected_lower: str) -> str:
    if not page_model:
        return ""
    endpoints = [str(item or "") for item in page_model.get("api_endpoints", [])]
    if not endpoints:
        return "api"
    lowered = expected_lower or ""
    for endpoint in endpoints:
        endpoint_lower = endpoint.lower()
        if any(token in endpoint_lower for token in ("graphql", "search", "login", "auth", "upload", "save", "submit")) and any(
            token in lowered for token in ("graphql", "search", "login", "auth", "upload", "save", "submit", "request", "response")
        ):
            return endpoint
    return endpoints[0]


def _match_interaction_refs(action: dict, page_model: dict) -> list[dict]:
    target_text = re.sub(r"\s+", " ", str(action.get("target", "") or "")).strip()
    if not target_text:
        return []

    refs = []
    component_match = _match_component_reference(target_text, page_model)
    if component_match:
        refs.append(
            {
                "source_type": "component",
                "source_key": component_match.get("component_key", ""),
                "source_label": component_match.get("label", "") or component_match.get("type", ""),
                "matched_text": target_text,
                "score": component_match.get("_match_score", 0),
            }
        )
    for form in page_model.get("form_catalog", []):
        submit_texts = [str(text).strip() for text in form.get("submit_texts", []) if str(text).strip()]
        for submit_text in submit_texts:
            if _score_text_match(target_text, submit_text) >= 8:
                refs.append(
                    {
                        "source_type": "submit_control",
                        "source_key": form.get("form_key", ""),
                        "source_label": submit_text,
                        "matched_text": target_text,
                        "score": _score_text_match(target_text, submit_text),
                    }
                )
                break
    refs.extend(_match_text_refs(target_text, page_model))
    deduped = []
    seen = set()
    for ref in sorted(refs, key=lambda item: item.get("score", 0), reverse=True):
        key = (ref.get("source_type", ""), ref.get("source_key", ""), ref.get("source_label", ""))
        if key in seen:
            continue
        deduped.append(ref)
        seen.add(key)
    return deduped[:6]


def _match_component_reference(target: str, page_model: dict) -> dict | None:
    target_text = re.sub(r"\s+", " ", str(target or "")).strip().lower()
    if not target_text:
        return None
    best = None
    best_score = 0
    for component in page_model.get("component_catalog", []):
        score = 0
        for alias in component.get("aliases", []):
            score = max(score, _score_text_match(target_text, alias))
        label = str(component.get("label", "")).strip().lower()
        if label:
            score = max(score, _score_text_match(target_text, label))
        if score > best_score:
            best = {**component, "_match_score": score}
            best_score = score
    return best if best_score >= 7 else None


def _match_text_refs(target: str, page_model: dict) -> list[dict]:
    target_text = re.sub(r"\s+", " ", str(target or "")).strip().lower()
    if not target_text:
        return []
    refs = []
    for entity in page_model.get("entities", []):
        value = str(entity.get("value", "")).strip()
        score = _score_text_match(target_text, value)
        if score >= 7:
            refs.append(
                {
                    "source_type": entity.get("type", "entity"),
                    "source_key": str(entity.get("value", ""))[:60],
                    "source_label": value[:120],
                    "matched_text": target,
                    "score": score,
                }
            )
    return refs[:6]


def _score_text_match(left: str, right: str) -> int:
    left_text = re.sub(r"\s+", " ", str(left or "")).strip().lower()
    right_text = re.sub(r"\s+", " ", str(right or "")).strip().lower()
    if not left_text or not right_text:
        return 0
    left_compact = re.sub(r"[^a-z0-9]+", "", left_text)
    right_compact = re.sub(r"[^a-z0-9]+", "", right_text)
    if left_text == right_text:
        return 12
    if left_compact and right_compact and left_compact == right_compact:
        return 10
    if left_text in right_text or right_text in left_text:
        return 8
    if left_compact and right_compact and (left_compact in right_compact or right_compact in left_compact):
        return 7
    return 0


def _merge_unique_list(*values: list[str]) -> list[str]:
    merged = []
    seen = set()
    for value_list in values:
        for value in value_list or []:
            text = str(value or "").strip()
            if text and text not in seen:
                merged.append(text)
                seen.add(text)
    return merged


def _normalize_learning_key(value: object) -> str:
    text = re.sub(r"[^a-z0-9]+", "_", str(value or "").strip().lower())
    return text.strip("_")


def _learned_field_selectors(field_match: dict, page_model: dict) -> list[str]:
    learning = page_model.get("site_profile", {}).get("learning", {})
    keys = [
        field_match.get("field_key", ""),
        field_match.get("semantic_type", ""),
        field_match.get("semantic_label", ""),
        field_match.get("label", ""),
        field_match.get("name", ""),
        field_match.get("id", ""),
    ]
    selectors = []
    for key in keys:
        selectors.extend(get_ranked_selector_candidates(learning, "field_selectors", key, limit=4))
    for key in keys[:3]:
        for failure in get_failure_memory(learning, "field_selectors", key, limit=2):
            selector = str(failure.get("selector", "")).strip()
            if selector and selector in selectors:
                selectors.remove(selector)
    return _merge_unique_list(selectors)


def _learned_action_selectors(target: str, page_model: dict) -> list[str]:
    learning = page_model.get("site_profile", {}).get("learning", {})
    selectors = get_ranked_selector_candidates(learning, "action_selectors", target, limit=5)
    for failure in get_failure_memory(learning, "action_selectors", target, limit=2):
        selector = str(failure.get("selector", "")).strip()
        if selector and selector in selectors:
            selectors.remove(selector)
    return selectors


def _build_grounding_summary(plan: dict) -> dict:
    items = list(plan.get("pre_actions", [])) + list(plan.get("actions", [])) + list(plan.get("assertions", []))
    grounded_items = [item for item in items if item.get("grounded")]
    weak_items = [
        item for item in grounded_items
        if float(item.get("grounding_confidence", 0.0) or 0.0) < 0.45
    ]
    scenario_grounding = plan.get("scenario_grounding", {}) or {}
    return {
        "total_items": len(items),
        "grounded_items": len(grounded_items),
        "weak_grounding_items": len(weak_items),
        "coverage": round((len(grounded_items) / len(items)), 2) if items else 0.0,
        "planning_mode": str(plan.get("planning_mode", "normal")),
        "scenario_fact_count": len(scenario_grounding.get("fact_ids", []) or []),
        "scenario_fact_coverage_score": float(scenario_grounding.get("coverage_score", 0.0) or 0.0),
        "scenario_ref_count": int(scenario_grounding.get("ref_count", 0) or 0),
        "scenario_grounding_score": float(scenario_grounding.get("score", 0.0) or 0.0),
        "scenario_alignment_score": float(plan.get("scenario_alignment", {}).get("score", 0.0) or 0.0),
        "average_step_fact_coverage_score": round(
            sum(float(item.get("fact_coverage_score", 0.0) or 0.0) for item in items) / len(items),
            2,
        ) if items else 0.0,
    }


def _should_use_conservative_plan(plan: dict, page_model: dict) -> bool:
    page_facts = page_model.get("page_facts", {})
    scenario_grounding = plan.get("scenario_grounding", {}) or {}
    scenario_alignment = plan.get("scenario_alignment", {}) or {}
    has_grounding_signal = bool(scenario_grounding)
    has_alignment_signal = bool(scenario_alignment)
    if not has_grounding_signal and not has_alignment_signal:
        return False

    score = float(scenario_grounding.get("score", 0.0) or 0.0)
    if "coverage_score" in scenario_grounding:
        coverage = float(scenario_grounding.get("coverage_score", 0.0) or 0.0)
    else:
        coverage = score
    if "ref_count" in scenario_grounding:
        ref_count = int(scenario_grounding.get("ref_count", 0) or 0)
    else:
        ref_count = len(scenario_grounding.get("fact_ids", []) or [])
    alignment_score = float(scenario_alignment.get("score", 1.0) or 1.0)
    dynamic_surface = any(
        page_facts.get(key, False)
        for key in ("spa_shell", "live_updates", "graphql", "iframe", "shadow_dom")
    )
    weak_signals = 0
    weak_signals += int(score < 0.45)
    weak_signals += int(coverage < 0.4)
    weak_signals += int(ref_count < 1)
    weak_signals += int(alignment_score < 0.35)
    return weak_signals >= 2 or (dynamic_surface and score < 0.6 and alignment_score < 0.5)


def _apply_conservative_plan_mode(plan: dict, page_model: dict) -> dict:
    updated = dict(plan)
    pre_actions = list(updated.get("pre_actions", []))
    actions = list(updated.get("actions", []))
    assertions = list(updated.get("assertions", []))
    checkpoints = list(updated.get("checkpoints", []))

    safe_actions = []
    if actions:
        safe_actions.append(actions[0])
    for item in actions[1:]:
        if item.get("type") in {"wait_for_text", "scroll"}:
            safe_actions.append(item)
            break
    if not safe_actions:
        safe_actions = [_ground_action({"type": "inspect", "target": "page", "step_text": "Inspect conservative page state"}, page_model)]

    safe_assertions = [item for item in assertions if item.get("type") in {"assert_text_visible", "assert_any_text_visible", "assert_title_contains"}]
    if not safe_assertions:
        generic_texts = _generic_assertion_texts(str(updated.get("source_case", {}).get("expected_result", "")).lower(), page_model)
        if generic_texts:
            safe_assertions.append(_ground_assertion({"type": "assert_any_text_visible", "values": generic_texts[:3], "source_text": "conservative fallback"}, page_model))
        else:
            safe_assertions.append(_ground_assertion({"type": "assert_title_contains", "value": page_model.get("page_identity", {}).get("title", ""), "source_text": "conservative fallback"}, page_model))

    if not checkpoints:
        checkpoints.append({"type": "manual_review", "mode": "manual", "reason": "Scenario grounding/alignment is weak, require manual verification."})

    orchestration = dict(updated.get("orchestration", {}))
    orchestration["mode"] = "semi-auto"
    orchestration["has_manual_checkpoint"] = True
    orchestration["checkpoint_count"] = len(checkpoints)
    orchestration["conservative"] = True

    interaction_hints = dict(updated.get("interaction_hints", {}))
    interaction_hints["settle_delay_ms"] = int(interaction_hints.get("settle_delay_ms", 1000)) + 300
    interaction_hints["conservative"] = True

    updated["pre_actions"] = pre_actions
    updated["actions"] = safe_actions[:2]
    updated["assertions"] = safe_assertions[:3]
    updated["checkpoints"] = checkpoints[:2]
    updated["orchestration"] = orchestration
    updated["interaction_hints"] = interaction_hints
    return updated


def _reference_coverage_score(refs: list[dict], expected_types: set[str] | None = None) -> float:
    refs = list(refs or [])
    if not refs:
        return 0.0
    expected_types = set(expected_types or set())
    source_types = {str(ref.get("source_type", "")).strip().lower() for ref in refs if str(ref.get("source_type", "")).strip()}
    base = min(1.0, len(refs) / 3)
    overlap = 0
    if expected_types and source_types:
        overlap = len(source_types & expected_types)
        type_score = overlap / max(min(len(expected_types), 3), 1)
    else:
        type_score = 0.5
    blended = min(1.0, (base * 0.45) + (type_score * 0.55))
    return round(max(blended, type_score if overlap else 0.0, base * 0.8), 2)


def _generic_assertion_texts(expected_lower: str, page_model: dict | None) -> list[str]:
    texts = []
    if "error message" in expected_lower or "invalid" in expected_lower or "required" in expected_lower:
        texts.extend(["error", "invalid", "required"])
    if "success" in expected_lower or "submitted" in expected_lower or "saved" in expected_lower:
        texts.extend(["success", "submitted", "saved"])
    if "empty state" in expected_lower:
        texts.extend(["no data", "no results", "empty"])
    if "search result" in expected_lower or "results" in expected_lower:
        texts.extend(["results", "result"])
    if page_model and any(component.get("type") == "modal" for component in page_model.get("component_catalog", [])):
        if "modal" in expected_lower or "dialog" in expected_lower:
            texts.extend(["dialog", "modal"])
    if page_model and any(component.get("type") == "toast" for component in page_model.get("component_catalog", [])):
        if "toast" in expected_lower or "notification" in expected_lower or "snackbar" in expected_lower:
            texts.extend(["toast", "notification", "saved"])
    if page_model and any(component.get("type") == "consent_banner" for component in page_model.get("component_catalog", [])):
        if "cookie" in expected_lower or "consent" in expected_lower or "privacy" in expected_lower:
            texts.extend(["cookie", "consent", "privacy"])
    if page_model and any(component.get("type") == "drawer" for component in page_model.get("component_catalog", [])):
        if "drawer" in expected_lower or "side panel" in expected_lower or "sidebar" in expected_lower:
            texts.extend(["drawer", "sidebar", "menu"])
    if page_model and any(component.get("type") == "otp_verification" for component in page_model.get("component_catalog", [])):
        if "otp" in expected_lower or "verification" in expected_lower:
            texts.extend(["otp", "verification"])
    if page_model and any(component.get("type") == "sso_login" for component in page_model.get("component_catalog", [])):
        if "sso" in expected_lower or "single sign-on" in expected_lower:
            texts.extend(["sign in", "continue with"])
    if page_model and any(component.get("type") == "live_feed" for component in page_model.get("component_catalog", [])):
        if "live" in expected_lower or "updated" in expected_lower or "refresh" in expected_lower:
            texts.extend(["live", "updated", "refresh"])
    deduped = []
    seen = set()
    for text in texts:
        if text not in seen:
            deduped.append(text)
            seen.add(text)
    return deduped[:4]


def _slug_phrase(value: str) -> str:
    text = re.sub(r"[^a-z0-9]+", "-", value.strip().lower())
    return text.strip("-")
