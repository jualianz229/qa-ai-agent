import csv
import io
import json
import os
import re
from datetime import datetime
from pathlib import Path
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup
from PIL import Image, ImageChops
from rich.console import Console

from core.common.artifacts import (
    json_artifact_path,
    visual_diff_path,
    visual_baseline_path,
    visual_regression_approval_path,
    visual_regression_path,
)
from core.common.site_profiles import load_site_profile

console = Console(force_terminal=True)
DEFAULT_VISUAL_VIEWPORTS = [
    {"name": "desktop", "width": 1280, "height": 720},
    {"name": "tablet", "width": 1024, "height": 768},
    {"name": "mobile", "width": 390, "height": 844},
]
DEFAULT_IGNORE_SELECTORS = [
    "[data-qa-ignore-visual='true']",
    "[data-testid*='timestamp' i]",
    "[data-testid*='clock' i]",
    "time",
]


class Scanner:
    def __init__(self, reports_dir: str = "Result"):
        self.reports_dir = Path(reports_dir)
        self.reports_dir.mkdir(parents=True, exist_ok=True)

    def _build_run_context(self, url: str, run_name: str | None = None) -> tuple[str, str, Path]:
        domain = urlparse(url).netloc.replace("www.", "") or "unknown"
        safe_domain = re.sub(r"[^\w]", "_", domain).lower()
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        if not run_name:
            return safe_domain, timestamp, self.reports_dir / f"{safe_domain}_{timestamp}"

        candidate = (self.reports_dir / str(run_name)).resolve()
        reports_root = self.reports_dir.resolve()
        if not candidate.is_relative_to(reports_root):
            raise ValueError("Invalid run path.")
        return safe_domain, timestamp, candidate

    def scan_website(
        self,
        url: str,
        use_auth: bool = False,
        crawl_limit: int = 3,
        site_profile: dict | None = None,
        run_name: str | None = None,
    ) -> tuple[dict, dict, str]:
        """Scan one page, then optionally mini-crawl a few relevant internal links generically."""
        from playwright.sync_api import sync_playwright

        safe_domain, timestamp, run_dir = self._build_run_context(url, run_name=run_name)
        domain = urlparse(url).netloc.replace("www.", "") or "unknown"
        site_profile = site_profile or load_site_profile(url)

        console.print(f"[dim]  Membuka browser Playwright ke {url}...[/dim]")
        console.print(f"[dim]  Folder output: {run_dir}[/dim]")

        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            context_kwargs = {
                "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                "viewport": {"width": 1280, "height": 720},
            }
            auth_file = Path("auth") / "auth_state.json"
            if use_auth and auth_file.exists():
                console.print(f"[green]  Menggunakan bypass login ({auth_file})[/green]")
                context_kwargs["storage_state"] = str(auth_file)
            elif use_auth:
                console.print(f"[yellow]  Auth file not found at {auth_file}. Continuing without session bypass.[/yellow]")

            context = browser.new_context(**context_kwargs)
            try:
                root_info, _, _ = self._scan_single_page(context, url, safe_domain, allow_spider=True, run_dir=run_dir)
                root_info["site_profile"] = site_profile
                candidates = self._select_internal_candidates(url, root_info, crawl_limit, site_profile)
                crawled_pages = []
                root_info["crawl_selection"] = candidates
                for index, candidate in enumerate(candidates, start=1):
                    console.print(
                        f"[dim]  Mini-crawl [{index}/{len(candidates)}]: {candidate.get('url', '')}"
                        f" | reason: {', '.join(candidate.get('reasons', [])[:3])}[/dim]"
                    )
                    child_info, _, _ = self._scan_single_page(
                        context,
                        candidate.get("url", ""),
                        f"{safe_domain}_{index}",
                        allow_spider=False,
                        run_dir=run_dir,
                    )
                    crawled_pages.append(child_info)

                merged_info = self._merge_page_infos(root_info, crawled_pages)
                merged_info["visual_render_regression"] = self._build_visual_regression_report(
                    merged_info.get("url", url),
                    run_dir=run_dir,
                    current_artifact=merged_info.get("visual_render_artifact", ""),
                    render_variants=merged_info.get("visual_render_variants", []),
                )
                merged_info["visual_regression"] = self._analyze_visual_snapshot(
                    merged_info.get("url", url),
                    merged_info.get("visual_snapshot", {}),
                    run_dir=run_dir,
                )
            finally:
                context.close()
                browser.close()

        project_info = {
            "title": root_info.get("title") or domain,
            "domain": domain,
            "project_name": f"{domain} {root_info.get('title', '')[:40]}".strip(),
            "run_dir": str(run_dir),
            "timestamp": timestamp,
            "safe_name": safe_domain,
            "site_profile": site_profile,
        }

        self._print_scan_summary(merged_info)
        return project_info, merged_info, ""

    def _scan_single_page(self, context, url: str, safe_name: str, allow_spider: bool, run_dir: Path | None = None) -> tuple[dict, str, str]:
        page = context.new_page()
        info = self._empty_page_info(url)

        def handle_route(route):
            request_url = route.request.url
            if "/api/" in request_url and urlparse(request_url).netloc == urlparse(url).netloc:
                if request_url not in info["apis"]:
                    info["apis"].append(request_url)
            route.continue_()

        page.route("**/*", handle_route)

        try:
            page.goto(url, wait_until="networkidle", timeout=20000)
        except Exception as exc:
            console.print(f"[yellow]  Warning during goto {url}: {exc}. Continuing DOM analysis.[/yellow]")

        if allow_spider:
            try:
                page.evaluate(
                    """
                    () => {
                        const buttons = document.querySelectorAll('button, .btn, .dropdown, [role="button"], [aria-haspopup="true"]');
                        let count = 0;
                        for (const b of buttons) {
                            if (count > 15) break;
                            try { b.click(); count++; } catch (e) {}
                        }
                    }
                    """
                )
                page.wait_for_timeout(1500)
            except Exception as exc:
                console.print(f"[yellow]  Warning Spider: {exc}[/yellow]")

        info["title"] = page.title()
        runtime_info = self._collect_runtime_signals(page)
        soup = BeautifulSoup(page.content(), "html.parser")
        self._extract_page_info(soup, info, runtime_info)
        discovery = self._discover_stateful_interactions(page)
        info["discovered_states"] = discovery.get("states", [])
        info["interaction_probes"] = discovery.get("probes", [])
        info["visual_components"] = self._merge_component_lists(
            info.get("visual_components", []),
            discovery.get("visual_components", []),
        )
        info["runtime_signals"]["stateful_probe_count"] = len(info["discovered_states"])
        info["page_fingerprint"]["discovered_state_count"] = len(info["discovered_states"])
        info["visual_snapshot"] = self._capture_visual_snapshot(page)
        render_bundle = self._capture_visual_render_variants(page, safe_name, run_dir)
        info["visual_render_artifact"] = render_bundle.get("primary_artifact", "")
        info["visual_render_variants"] = render_bundle.get("variants", [])

        page.close()
        info["apis"] = list(dict.fromkeys(info["apis"]))[:10]
        return info, "", ""

    def _empty_page_info(self, url: str) -> dict:
        return {
            "title": "",
            "url": url,
            "headings": [],
            "texts": [],
            "buttons": [],
            "links": [],
            "forms": [],
            "standalone_controls": [],
            "images": [],
            "apis": [],
            "sections": [],
            "tables": [],
            "lists": [],
            "navigation": [],
            "section_graph": {"nodes": [], "edges": []},
            "visual_components": [],
            "embedded_contexts": [],
            "metadata": {},
            "page_fingerprint": {},
            "runtime_signals": {},
            "discovered_states": [],
            "interaction_probes": [],
            "visual_snapshot": {},
            "visual_render_artifact": "",
            "visual_render_variants": [],
            "crawled_pages": [],
            "crawl_selection": [],
            "site_profile": {},
        }

    def _extract_page_info(self, soup: BeautifulSoup, info: dict, runtime_info: dict | None = None) -> None:
        runtime_info = runtime_info or {}
        title = info.get("title", "")
        meta_desc = soup.find("meta", attrs={"name": "description"})
        canonical = soup.find("link", attrs={"rel": "canonical"})
        info["metadata"] = {
            "title": title[:120] if title else "",
            "description": meta_desc.get("content", "")[:180] if meta_desc else "",
            "canonical": canonical.get("href", "")[:180] if canonical else "",
        }

        for tag in ["h1", "h2", "h3", "h4", "h5"]:
            for el in soup.find_all(tag, limit=8):
                text = el.get_text(strip=True)
                if text:
                    info["headings"].append({"tag": tag, "text": text[:120]})

        for tag in ["p", "label", "li", "td", ".message", ".alert", ".error", ".success"]:
            for el in soup.select(tag, limit=8):
                text = el.get_text(strip=True)
                if 5 < len(text) < 250 and text not in info["texts"]:
                    info["texts"].append(text)

        for el in soup.select('button, input[type="submit"], input[type="button"], [role="button"]')[:20]:
            text = self._interactive_label(el, soup)
            if text and text not in info["buttons"]:
                info["buttons"].append(text[:80])

        for el in soup.find_all("a", limit=30):
            text = self._interactive_label(el, soup)
            href = el.get("href", "")
            if text and href and not href.startswith("#"):
                info["links"].append(
                    {
                        "text": text[:60],
                        "href": href[:160],
                        "context": self._link_context_label(el)[:120],
                    }
                )

        for nav in soup.find_all(["nav", "header"], limit=4):
            nav_links = []
            for el in nav.find_all("a", limit=10):
                text = self._interactive_label(el, soup)
                href = el.get("href", "")
                if text:
                    nav_links.append(
                        {
                            "text": text[:40],
                            "href": href[:100],
                            "context": self._link_context_label(el)[:120],
                            "area_label": self._container_label(nav)[:80],
                        }
                    )
            if nav_links:
                info["navigation"].append(nav_links)

        for form in soup.find_all("form", limit=6):
            form_info = self._extract_form_info(form, soup)
            if form_info.get("fields"):
                info["forms"].append(form_info)

        info["standalone_controls"] = self._extract_standalone_controls(soup)

        for section in soup.find_all(["section", "article", "main"], limit=10):
            heading = self._container_label(section)[:100]
            text = section.get_text(" ", strip=True)[:180]
            action_labels = []
            for node in section.select('button, a, [role="button"], input[type="submit"], input[type="button"]')[:6]:
                label = self._interactive_label(node, soup)
                if label and label.lower() not in {item.lower() for item in action_labels}:
                    action_labels.append(label[:80])
            if heading or text:
                info["sections"].append(
                    {
                        "heading": heading,
                        "text": text,
                        "action_labels": action_labels[:6],
                        "landmark": (section.get("aria-label") or section.get("role") or "")[:80],
                    }
                )

        for list_el in soup.find_all(["ul", "ol"], limit=8):
            items = []
            for li in list_el.find_all("li", limit=8):
                text = li.get_text(" ", strip=True)
                if text:
                    items.append(text[:80])
            if items:
                info["lists"].append(items)

        for table in soup.find_all("table", limit=5):
            headers = []
            first_row = table.find("tr")
            if first_row:
                for cell in first_row.find_all(["th", "td"], limit=10):
                    text = cell.get_text(" ", strip=True)
                    if text:
                        headers.append(text[:60])
            if headers:
                info["tables"].append(headers)

        for img in soup.find_all("img", limit=8):
            alt = img.get("alt") or img.get("src") or ""
            if alt:
                info["images"].append(alt[:80])

        info["section_graph"] = self._build_section_graph(soup)
        info["visual_components"] = self._merge_component_lists(
            self._extract_visual_components(soup),
            runtime_info.get("visual_components", []),
        )
        info["embedded_contexts"] = runtime_info.get("embedded_contexts", [])
        info["runtime_signals"] = runtime_info.get("signals", {})
        info["page_fingerprint"] = self._build_page_fingerprint(soup, info, runtime_info.get("fingerprint", {}))

    def _extract_form_info(self, form, soup: BeautifulSoup) -> dict:
        fields = []
        submit_texts = []
        container_meta = self._container_metadata(form)
        for button in form.find_all(["button", "input"], limit=10):
            button_type = (button.get("type") or "").lower()
            if button.name == "button" or button_type in {"submit", "button"}:
                text = (button.get_text(strip=True) or button.get("value", "") or button.get("aria-label", "")).strip()
                if text:
                    submit_texts.append(text[:80])

        for index, inp in enumerate(form.find_all(["input", "textarea", "select"], limit=20), start=1):
            typ = (inp.get("type") or "text").lower()
            if typ in {"hidden", "submit", "button", "image", "reset"}:
                continue
            field_info = self._extract_field_info(inp, form, soup, index, container_meta=container_meta)
            if field_info:
                fields.append(field_info)

        form_text = form.get_text(" ", strip=True)
        return {
            "id": (form.get("id") or "")[:80],
            "name": (form.get("name") or "")[:80],
            "action": (form.get("action") or "")[:160],
            "method": (form.get("method") or "get").lower()[:20],
            "submit_texts": submit_texts[:6],
            "field_count": len(fields),
            "context_text": form_text[:220],
            "container_heading": container_meta.get("container_heading", ""),
            "container_text": container_meta.get("container_text", ""),
            "dom_path": container_meta.get("dom_path", ""),
            "container_hints": container_meta.get("container_hints", []),
            "fields": fields,
        }

    def _extract_standalone_controls(self, soup: BeautifulSoup) -> list[dict]:
        controls = []
        selector = (
            'input, textarea, select, [contenteditable="true"], [role="combobox"], '
            '[role="textbox"][contenteditable], [aria-autocomplete]'
        )
        for index, control in enumerate(soup.select(selector)[:24], start=1):
            if control.find_parent("form"):
                continue
            typ = (control.get("type") or "").lower()
            if control.name == "input" and typ in {"hidden", "submit", "button", "image", "reset", "checkbox", "radio"}:
                continue
            field_info = self._extract_field_info(control, soup, soup, index, container_meta=self._container_metadata(control))
            if field_info:
                controls.append(field_info)
        return controls

    def _extract_field_info(self, inp, form, soup: BeautifulSoup, index: int, container_meta: dict | None = None) -> dict:
        container_meta = container_meta or self._container_metadata(inp)
        label = self._field_label_text(inp, form, soup)
        options = []
        if inp.name == "select":
            for option in inp.find_all("option", limit=20):
                text = option.get_text(" ", strip=True)
                if text:
                    options.append(text[:80])
        role = (inp.get("role") or "").lower()
        contenteditable_attr = inp.get("contenteditable")
        contenteditable = contenteditable_attr is not None and str(contenteditable_attr).lower() in {"", "true", "plaintext-only"}
        widget = self._infer_field_widget(inp, role, contenteditable, options)
        input_type = (
            inp.get("type")
            or ("textarea" if inp.name == "textarea" else "select" if inp.name == "select" else "text")
        )
        if contenteditable and inp.name not in {"input", "textarea", "select"}:
            input_type = "contenteditable"

        raw_parts = [
            label,
            inp.get("name", ""),
            inp.get("id", ""),
            inp.get("placeholder", ""),
            inp.get("aria-label", ""),
            inp.get("title", ""),
            inp.get("autocomplete", ""),
            inp.get("inputmode", ""),
            inp.get("data-testid", ""),
            self._resolve_labelledby_text(inp, soup),
            self._nearby_field_text(inp, form),
        ]
        semantic_text = " ".join(part for part in raw_parts if part).strip()
        return {
            "index": index,
            "tag": inp.name,
            "type": str(input_type).lower()[:30],
            "role": role[:40],
            "widget": widget[:40],
            "name": (inp.get("name") or "")[:120],
            "id": (inp.get("id") or "")[:120],
            "placeholder": (inp.get("placeholder") or "")[:120],
            "aria_label": (inp.get("aria-label") or "")[:120],
            "title": (inp.get("title") or "")[:120],
            "aria_labelledby": self._resolve_labelledby_text(inp, soup)[:120],
            "autocomplete": (inp.get("autocomplete") or "")[:60],
            "inputmode": (inp.get("inputmode") or "")[:40],
            "list_id": (inp.get("list") or "")[:80],
            "label": label[:120],
            "required": inp.has_attr("required") or inp.get("aria-required") == "true",
            "pattern": (inp.get("pattern") or "")[:120],
            "maxlength": str(inp.get("maxlength") or "")[:20],
            "minlength": str(inp.get("minlength") or "")[:20],
            "data_testid": (inp.get("data-testid") or inp.get("data-test") or "")[:120],
            "class_tokens": [token[:40] for token in (inp.get("class") or [])[:6]],
            "contenteditable": contenteditable,
            "accept": (inp.get("accept") or "")[:120],
            "multiple": inp.has_attr("multiple"),
            "options": options[:12],
            "context_text": self._nearby_field_text(inp, form)[:180],
            "semantic_text": semantic_text[:260],
            "dom_path": container_meta.get("dom_path", ""),
            "container_heading": container_meta.get("container_heading", ""),
            "container_text": container_meta.get("container_text", ""),
            "container_hints": container_meta.get("container_hints", [])[:6],
            "nearby_texts": container_meta.get("nearby_texts", [])[:6],
        }

    def _infer_field_widget(self, inp, role: str, contenteditable: bool, options: list[str]) -> str:
        input_type = str(inp.get("type") or "").lower()
        classes = " ".join(inp.get("class", [])).lower()
        attrs_text = " ".join(
            str(value or "")
            for value in [
                inp.get("aria-autocomplete", ""),
                inp.get("placeholder", ""),
                inp.get("data-testid", ""),
                inp.get("name", ""),
                inp.get("id", ""),
            ]
        ).lower()
        if input_type == "file":
            return "upload"
        if input_type in {"date", "datetime-local", "month", "week"}:
            return "datepicker"
        if input_type == "time":
            return "timepicker"
        if role == "combobox" or inp.has_attr("list") or "autocomplete" in attrs_text:
            return "combobox"
        if contenteditable or any(token in classes for token in ["editor", "ql-editor", "prosemirror", "tox-edit-area", "ck-editor"]):
            return "rich_text"
        if options or inp.name == "select":
            return "select"
        return ""

    def _container_metadata(self, element) -> dict:
        block = element.find_parent(["form", "section", "article", "main", "aside", "nav", "div"])
        if not block:
            block = element
        heading = self._container_label(block)[:120]
        container_text = block.get_text(" ", strip=True)[:220] if hasattr(block, "get_text") else ""
        nearby_texts = []
        for sibling in list(getattr(element, "previous_siblings", []))[:3] + list(getattr(element, "next_siblings", []))[:3]:
            if hasattr(sibling, "get_text"):
                text = sibling.get_text(" ", strip=True)
                if 0 < len(text) <= 120:
                    nearby_texts.append(text[:120])
        container_hints = []
        for value in [heading, container_text[:120], element.get("aria-label", ""), element.get("placeholder", ""), element.get("name", ""), element.get("title", "")]:
            normalized = re.sub(r"\s+", " ", str(value or "")).strip()
            if normalized and normalized.lower() not in {item.lower() for item in container_hints}:
                container_hints.append(normalized)
        return {
            "dom_path": self._dom_path(block),
            "container_heading": heading,
            "container_text": container_text,
            "container_hints": container_hints[:6],
            "nearby_texts": nearby_texts[:6],
        }

    def _dom_path(self, element, max_depth: int = 5) -> str:
        parts = []
        current = element
        depth = 0
        while current is not None and getattr(current, "name", None) and depth < max_depth:
            tag = current.name.lower()
            identifier = current.get("id") or ""
            role = current.get("role") or ""
            class_tokens = "-".join((current.get("class") or [])[:2])
            segment = tag
            if identifier:
                segment += f"#{identifier[:30]}"
            elif role:
                segment += f"[role={role[:20]}]"
            elif class_tokens:
                segment += f".{class_tokens[:30]}"
            parts.append(segment)
            current = current.parent
            depth += 1
        return " > ".join(reversed(parts))

    def _resolve_labelledby_text(self, element, soup: BeautifulSoup) -> str:
        ids = str(element.get("aria-labelledby", "") or "").split()
        values = []
        for item_id in ids[:4]:
            target = soup.find(id=item_id)
            if not target:
                continue
            text = target.get_text(" ", strip=True)
            if text and text.lower() not in {value.lower() for value in values}:
                values.append(text[:80])
        return " | ".join(values[:2])

    def _interactive_label(self, element, soup: BeautifulSoup | None = None) -> str:
        pieces = [
            element.get_text(" ", strip=True),
            element.get("value", ""),
            element.get("aria-label", ""),
            element.get("title", ""),
        ]
        if soup is not None:
            pieces.append(self._resolve_labelledby_text(element, soup))
        for value in (
            element.get("placeholder", ""),
            element.get("name", ""),
            element.get("id", ""),
            element.get("data-testid", "") or element.get("data-test", ""),
        ):
            if value:
                pieces.append(str(value))
        for piece in pieces:
            normalized = re.sub(r"\s+", " ", str(piece or "")).strip()
            if normalized:
                return normalized[:120]
        return ""

    def _container_label(self, element) -> str:
        if not element:
            return ""
        heading_el = element.find(["h1", "h2", "h3", "h4"]) if hasattr(element, "find") else None
        if heading_el:
            text = heading_el.get_text(" ", strip=True)
            if text:
                return text[:120]
        for value in (
            element.get("aria-label", ""),
            element.get("title", ""),
            element.get("data-testid", ""),
            element.get("id", ""),
            element.get("role", ""),
        ):
            normalized = re.sub(r"\s+", " ", str(value or "")).strip()
            if normalized:
                return normalized[:120]
        if hasattr(element, "get_text"):
            text = element.get_text(" ", strip=True)
            if text:
                return text[:120]
        return ""

    def _link_context_label(self, element) -> str:
        block = element.find_parent(["nav", "header", "section", "article", "main", "aside", "div"])
        return self._container_label(block)

    def _build_section_graph(self, soup: BeautifulSoup) -> dict:
        selector = "main, section, article, aside, nav, form, [role='region'], [role='complementary'], [role='main'], [data-testid*='section']"
        raw_nodes = []
        seen = set()
        for element in soup.select(selector)[:28]:
            marker = id(element)
            if marker in seen:
                continue
            seen.add(marker)
            raw_nodes.append(element)

        element_to_id = {}
        nodes = []
        for index, element in enumerate(raw_nodes, start=1):
            block_id = f"block_{index}"
            element_to_id[id(element)] = block_id
            heading_el = element.find(["h1", "h2", "h3", "h4"])
            heading = self._container_label(element)[:120]
            action_labels = []
            for node in element.select('button, a, [role="button"], input[type="submit"], input[type="button"]')[:6]:
                label = self._interactive_label(node, soup)
                if label and label.lower() not in {item.lower() for item in action_labels}:
                    action_labels.append(label[:60])
            nodes.append(
                {
                    "block_id": block_id,
                    "tag": element.name.lower(),
                    "role": str(element.get("role", "")).strip()[:40],
                    "aria_label": str(element.get("aria-label", "")).strip()[:120],
                    "heading": heading,
                    "text": element.get_text(" ", strip=True)[:220],
                    "depth": len(list(element.parents)),
                    "dom_path": self._dom_path(element),
                    "link_count": len(element.find_all("a", limit=16)),
                    "button_count": len(element.find_all(["button", "input"], limit=16)),
                    "field_count": len(element.find_all(["input", "textarea", "select"], limit=20)),
                    "action_labels": action_labels[:6],
                    "parent_block_id": "",
                }
            )

        edges = []
        node_map = {node["block_id"]: node for node in nodes}
        for element in raw_nodes:
            block_id = element_to_id.get(id(element), "")
            parent_block_id = ""
            for parent in element.parents:
                if id(parent) in element_to_id:
                    parent_block_id = element_to_id[id(parent)]
                    break
            if block_id and parent_block_id and block_id in node_map:
                node_map[block_id]["parent_block_id"] = parent_block_id
                edges.append({"from": parent_block_id, "to": block_id, "relation": "contains"})
        return {"nodes": nodes[:24], "edges": edges[:32]}

    def _field_label_text(self, inp, form, soup: BeautifulSoup) -> str:
        field_id = inp.get("id", "")
        label_texts = []
        if field_id:
            for label in soup.find_all("label", attrs={"for": field_id}, limit=3):
                text = label.get_text(" ", strip=True)
                if text:
                    label_texts.append(text)
        parent_label = inp.find_parent("label")
        if parent_label:
            text = parent_label.get_text(" ", strip=True)
            if text:
                label_texts.append(text)
        if not label_texts:
            previous = inp.find_previous(["label", "span", "div", "p"])
            if previous and previous in form.descendants:
                text = previous.get_text(" ", strip=True)
                if 0 < len(text) <= 120:
                    label_texts.append(text)
        deduped = []
        seen = set()
        for text in label_texts:
            clean = re.sub(r"\s+", " ", text).strip()
            if clean and clean.lower() not in seen:
                deduped.append(clean)
                seen.add(clean.lower())
        return " | ".join(deduped[:2])

    def _nearby_field_text(self, inp, form) -> str:
        pieces = []
        parent = inp.parent
        if parent and parent != form:
            text = parent.get_text(" ", strip=True)
            if text:
                pieces.append(text[:120])
        previous = inp.find_previous(["small", "span", "p", "div"])
        if previous and previous in form.descendants:
            text = previous.get_text(" ", strip=True)
            if 0 < len(text) <= 120:
                pieces.append(text)
        next_node = inp.find_next(["small", "span", "p", "div"])
        if next_node and next_node in form.descendants:
            text = next_node.get_text(" ", strip=True)
            if 0 < len(text) <= 120:
                pieces.append(text)
        deduped = []
        seen = set()
        for text in pieces:
            clean = re.sub(r"\s+", " ", text).strip()
            if clean and clean.lower() not in seen:
                deduped.append(clean)
                seen.add(clean.lower())
        return " | ".join(deduped[:2])

    def _select_internal_candidates(self, root_url: str, root_info: dict, limit: int, site_profile: dict | None = None) -> list[dict]:
        base = urlparse(root_url)
        scored = []
        seen = {root_url.rstrip("/")}
        site_profile = site_profile or {}
        blacklist_terms = [term.lower() for term in site_profile.get("link_selection", {}).get("blacklist_terms", [])]
        priority_terms = [term.lower() for term in site_profile.get("link_selection", {}).get("priority_terms", [])]

        def add_candidate(text: str, href: str, source: str):
            if not href:
                return
            absolute = urljoin(root_url, href)
            parsed = urlparse(absolute)
            if parsed.netloc != base.netloc:
                return
            normalized = absolute.rstrip("/")
            if normalized in seen:
                return
            if any(token in parsed.path.lower() for token in [".jpg", ".png", ".pdf", ".zip", ".svg"]):
                return
            full_text = f"{text or ''} {href or ''}".lower()
            if any(term in full_text for term in blacklist_terms):
                return

            score = 0
            reasons = []
            text_l = (text or "").lower()
            href_l = parsed.path.lower()
            if source == "navigation":
                score += 4
                reasons.append("navigation source")
            if len(text_l) > 2:
                score += 2
                reasons.append("has visible text")
            if href_l and href_l != base.path.lower():
                score += 2
                reasons.append("distinct internal path")
            if any(token in text_l for token in ["next", "more", "detail", "view", "read"]):
                score += 1
                reasons.append("action-oriented link text")
            if any(token in href_l for token in ["page=", "?page=", "/page/"]):
                score += 1
                reasons.append("pagination-like path")
            if any(term in full_text for term in priority_terms):
                score += 2
                reasons.append("profile-prioritized term")
            scored.append({
                "score": score,
                "url": normalized,
                "text": text[:80],
                "source": source,
                "reasons": reasons or ["generic internal link"],
            })
            seen.add(normalized)

        for group in root_info.get("navigation", []):
            for link in group:
                if isinstance(link, dict):
                    add_candidate(link.get("text", ""), link.get("href", ""), "navigation")

        for link in root_info.get("links", []):
            if isinstance(link, dict):
                add_candidate(link.get("text", ""), link.get("href", ""), "content")

        scored.sort(key=lambda item: item["score"], reverse=True)
        return scored[: max(limit, 0)]

    def _merge_page_infos(self, root_info: dict, crawled_pages: list[dict]) -> dict:
        merged = dict(root_info)
        merged["crawled_pages"] = [
            {
                "url": page.get("url", ""),
                "title": page.get("title", ""),
                "fingerprint": page.get("page_fingerprint", {}),
                "headings": page.get("headings", [])[:4],
            }
            for page in crawled_pages
        ]
        merged["crawl_selection"] = root_info.get("crawl_selection", [])

        for key in [
            "headings", "texts", "buttons", "links", "forms", "standalone_controls",
            "images", "apis", "sections", "tables", "lists", "visual_components", "embedded_contexts",
            "discovered_states", "interaction_probes"
        ]:
            combined = list(root_info.get(key, []))
            for page in crawled_pages:
                for item in page.get(key, []):
                    if item not in combined:
                        combined.append(item)
            merged[key] = combined

        combined_nav = list(root_info.get("navigation", []))
        for page in crawled_pages:
            for group in page.get("navigation", []):
                if group not in combined_nav:
                    combined_nav.append(group)
        merged["navigation"] = combined_nav

        merged["page_fingerprint"] = self._combine_fingerprints(
            [root_info.get("page_fingerprint", {})] + [page.get("page_fingerprint", {}) for page in crawled_pages]
        )
        merged["section_graph"] = root_info.get("section_graph", {"nodes": [], "edges": []})
        merged["metadata"] = root_info.get("metadata", {})
        merged["runtime_signals"] = root_info.get("runtime_signals", {})
        merged["site_profile"] = root_info.get("site_profile", {})
        merged["visual_render_variants"] = list(root_info.get("visual_render_variants", []))
        return merged

    def _combine_fingerprints(self, fingerprints: list[dict]) -> dict:
        if not fingerprints:
            return {}
        boolean_keys = [
            "has_search", "has_filters", "has_pagination", "has_auth_pattern",
            "has_table", "has_form", "has_navigation", "has_article_like_sections", "has_listing_pattern",
            "has_combobox", "has_datepicker", "has_timepicker", "has_toast", "has_drawer",
            "has_upload", "has_drag_drop", "has_rich_text", "has_infinite_scroll", "has_carousel",
            "has_iframe", "has_shadow_dom", "has_chart", "has_map", "has_cookie_banner",
            "has_captcha", "has_spa_shell", "has_standalone_controls", "has_graphql",
            "has_websocket", "has_live_updates", "has_otp_flow", "has_sso", "has_auth_checkpoint",
        ]
        count_keys = [
            "button_count", "link_count", "form_count", "standalone_control_count", "section_count", "table_count",
            "iframe_count", "shadow_host_count", "xhr_count", "fetch_count", "graphql_request_count", "websocket_count",
            "discovered_state_count",
        ]
        combined = {}
        for key in boolean_keys:
            combined[key] = any(bool(fp.get(key)) for fp in fingerprints)
        for key in count_keys:
            combined[key] = max(int(fp.get(key, 0)) for fp in fingerprints)
        combined["sampled_page_count"] = len(fingerprints)
        return combined

    def _print_scan_summary(self, info: dict) -> None:
        console.print(
            f"[green]  Scan selesai:[/green] "
            f"[cyan]{len(info['headings'])}[/cyan] heading  "
            f"[cyan]{len(info['texts'])}[/cyan] teks  "
            f"[cyan]{len(info['buttons'])}[/cyan] tombol  "
            f"[cyan]{len(info['links'])}[/cyan] link  "
            f"[cyan]{len(info['forms'])}[/cyan] form  "
            f"[cyan]{len(info.get('standalone_controls', []))}[/cyan] standalone control  "
            f"[cyan]{len(info['sections'])}[/cyan] section  "
            f"[cyan]{len(info['tables'])}[/cyan] table  "
            f"[cyan]{len(info['lists'])}[/cyan] list  "
            f"[cyan]{len(info.get('visual_components', []))}[/cyan] component  "
            f"[cyan]{len(info.get('embedded_contexts', []))}[/cyan] embedded  "
            f"[cyan]{len(info['apis'])}[/cyan] api  "
            f"[cyan]{len(info.get('crawled_pages', []))}[/cyan] linked page"
        )

    def save_csv_scenarios(self, data_list: list, project_info: dict, sep: str = ",") -> Path:
        output = io.StringIO()
        if data_list:
            fieldnames = [
                "ID", "Module", "Category", "Test Type", "Risk Rating", "Anchored Selector", "Title", "Precondition",
                "Steps to Reproduce", "Expected Result", "Actual Result", "Severity",
                "Priority", "Evidence", "Automation",
            ]
            actual_keys = list(data_list[0].keys())
            if not all(field in actual_keys for field in fieldnames[:4]):
                fieldnames = actual_keys

            writer = csv.DictWriter(output, fieldnames=fieldnames, delimiter=sep, quoting=csv.QUOTE_MINIMAL, lineterminator="\n")
            writer.writeheader()
            for row in data_list:
                clean_row = {}
                for key in fieldnames:
                    value = str(row.get(key, ""))
                    if key in ["Steps to Reproduce", "Expected Result", "Precondition", "Actual Result"]:
                        value = self._normalize_csv_text(value, numbered=(key == "Steps to Reproduce"))
                    if key == "Evidence":
                        value = "[Menunggu dieksekusi oleh AI Executor]"
                    if key == "Automation" and not value:
                        value = "auto"
                    clean_row[key] = value
                writer.writerow(clean_row)

        timestamp = project_info.get("timestamp", datetime.now().strftime("%Y%m%d_%H%M%S"))
        safe_name = project_info.get("safe_name", "unknown")
        run_dir = Path(project_info.get("run_dir", self.reports_dir))
        csv_path = run_dir / f"{safe_name}_{timestamp}.csv"
        csv_path.write_text(output.getvalue(), encoding="utf-8-sig", newline="")
        return csv_path

    def _normalize_csv_text(self, value: str, numbered: bool = False) -> str:
        if not value:
            return ""
        text = "".join(char for char in str(value) if char.isprintable() or char in "\n\r\t")
        text = text.replace('"', "'")
        text = text.replace("\\n", "\n") if numbered else text
        
        text = re.sub(r"\r\n?", "\n", text)
        text = re.sub(r"[ \t]+\n", "\n", text)
        text = re.sub(r"\n[ \t]+\n", "\n", text)
        
        if numbered:
            text = re.sub(r"\n\s*\n+", "\n", text)
            text = re.sub(r"(?<=\d)\.(?=[A-Za-z])", ". ", text)
            text = re.sub(r"(?<=\S)\s+(?=\d+\.)", "\n", text)
            
            lines = [line.strip() for line in text.split("\n") if line.strip()]
            if lines and any(re.match(r"^\d+\.", line) for line in lines):
                text = "\n".join(lines)
        else:
            text = re.sub(r"\n{3,}", "\n\n", text)
            
        return text.strip()

    def save_executive_summary(self, md_content: str, project_info: dict) -> Path:
        timestamp = project_info.get("timestamp", datetime.now().strftime("%Y%m%d_%H%M%S"))
        safe_name = project_info.get("safe_name", "unknown")
        run_dir = Path(project_info.get("run_dir", self.reports_dir))
        md_path = run_dir / f"Test_Plan_Summary_{safe_name}_{timestamp}.md"
        md_path.write_text(md_content, encoding="utf-8")
        return md_path

    def save_page_scope(self, page_scope: dict, project_info: dict) -> Path:
        timestamp = project_info.get("timestamp", datetime.now().strftime("%Y%m%d_%H%M%S"))
        safe_name = project_info.get("safe_name", "unknown")
        run_dir = project_info.get("run_dir", self.reports_dir)
        json_path = json_artifact_path(run_dir, f"Page_Scope_{safe_name}_{timestamp}.json")
        json_path.write_text(json.dumps(page_scope, indent=2, ensure_ascii=False), encoding="utf-8")
        return json_path

    def save_crawled_pages(self, page_info: dict, project_info: dict) -> Path:
        timestamp = project_info.get("timestamp", datetime.now().strftime("%Y%m%d_%H%M%S"))
        safe_name = project_info.get("safe_name", "unknown")
        run_dir = project_info.get("run_dir", self.reports_dir)
        json_path = json_artifact_path(run_dir, f"Crawled_Pages_{safe_name}_{timestamp}.json")
        payload = {
            "root_url": page_info.get("url", ""),
            "sampled_page_count": len(page_info.get("crawled_pages", [])),
            "selection": page_info.get("crawl_selection", []),
            "pages": page_info.get("crawled_pages", []),
        }
        json_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        return json_path

    def save_raw_scan(self, page_info: dict, project_info: dict) -> Path:
        timestamp = project_info.get("timestamp", datetime.now().strftime("%Y%m%d_%H%M%S"))
        safe_name = project_info.get("safe_name", "unknown")
        run_dir = project_info.get("run_dir", self.reports_dir)
        json_path = json_artifact_path(run_dir, f"raw_scan_{safe_name}_{timestamp}.json")
        json_path.write_text(json.dumps(page_info, indent=2, ensure_ascii=False), encoding="utf-8")
        return json_path

    def save_visual_regression_artifacts(self, page_info: dict, project_info: dict) -> tuple[Path, Path]:
        run_dir = project_info.get("run_dir", self.reports_dir)
        vr = page_info.get("visual_regression", {}) if isinstance(page_info, dict) else {}
        vr_render = page_info.get("visual_render_regression", {}) if isinstance(page_info, dict) else {}
        render_variants = page_info.get("visual_render_variants", []) if isinstance(page_info, dict) else []
        
        baseline_payload = {
            "url": page_info.get("url", ""),
            "captured_at": datetime.now().isoformat(timespec="seconds"),
            "snapshot": vr.get("current_snapshot", {}),
            "summary": vr.get("snapshot_summary", {}),
            "render_variants": render_variants,
        }
        diff_payload = {
            "url": page_info.get("url", ""),
            "compared_at": datetime.now().isoformat(timespec="seconds"),
            "baseline_run": vr.get("baseline_run", ""),
            "has_baseline": bool(vr.get("has_baseline", False)),
            "has_changes": bool(vr.get("has_changes", False)),
            "summary": vr.get("diff_summary", {}),
            "changed_areas": vr.get("changed_areas", [])[:120],
        }
        
        baseline_path = visual_baseline_path(run_dir)
        diff_path = visual_diff_path(run_dir)
        
        baseline_path.write_text(json.dumps(baseline_payload, indent=2, ensure_ascii=False), encoding="utf-8")
        diff_path.write_text(json.dumps(diff_payload, indent=2, ensure_ascii=False), encoding="utf-8")
        
        visual_regression_path(run_dir).write_text(
            json.dumps(vr_render, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        return baseline_path, diff_path

    def _capture_visual_render_variants(self, page, safe_name: str, run_dir: Path | None) -> dict:
        if run_dir is None:
            return {"primary_artifact": "", "variants": []}
        variants = []
        viewports = self._get_visual_viewports()
        ignore_selectors = self._get_visual_ignore_selectors()
        original_viewport = None
        try:
            original_viewport = page.viewport_size
        except Exception:
            original_viewport = None
        try:
            visual_dir = Path(run_dir) / "Evidence" / "Visual"
            visual_dir.mkdir(parents=True, exist_ok=True)
            for viewport in viewports:
                viewport_name = str(viewport.get("name", "desktop")).strip() or "desktop"
                width = int(viewport.get("width", 1280) or 1280)
                height = int(viewport.get("height", 720) or 720)
                page.set_viewport_size({"width": width, "height": height})
                page.wait_for_timeout(120)
                image_path = visual_dir / f"{safe_name}_{viewport_name}_render.png"
                page.screenshot(path=str(image_path), full_page=True)
                variants.append(
                    {
                        "viewport_name": viewport_name,
                        "viewport": {"width": width, "height": height},
                        "artifact": str(image_path.relative_to(run_dir)).replace("\\", "/"),
                        "ignore_regions": self._collect_ignore_regions(page, ignore_selectors),
                    }
                )
        except Exception:
            return {"primary_artifact": "", "variants": []}
        finally:
            if original_viewport:
                try:
                    page.set_viewport_size(original_viewport)
                except Exception:
                    pass

        primary_artifact = ""
        for item in variants:
            if item.get("viewport_name") == "desktop":
                primary_artifact = str(item.get("artifact", ""))
                break
        if not primary_artifact and variants:
            primary_artifact = str(variants[0].get("artifact", ""))
        return {"primary_artifact": primary_artifact, "variants": variants}

    def _get_visual_viewports(self) -> list[dict]:
        raw = str(os.getenv("QA_AI_VISUAL_VIEWPORTS", "")).strip()
        if not raw:
            return [dict(item) for item in DEFAULT_VISUAL_VIEWPORTS]
        parts = [segment.strip() for segment in raw.split(",") if segment.strip()]
        parsed = []
        for item in parts:
            chunks = [token.strip() for token in item.split(":") if token.strip()]
            if len(chunks) != 2:
                continue
            name = chunks[0].lower()
            dims = chunks[1].lower().split("x")
            if len(dims) != 2:
                continue
            try:
                width = int(dims[0])
                height = int(dims[1])
            except Exception:
                continue
            if width < 200 or height < 200:
                continue
            parsed.append({"name": name, "width": width, "height": height})
        if not parsed:
            return [dict(item) for item in DEFAULT_VISUAL_VIEWPORTS]
        return parsed[:5]

    def _get_visual_ignore_selectors(self) -> list[str]:
        raw = str(os.getenv("QA_AI_VISUAL_IGNORE_SELECTORS", "")).strip()
        if not raw:
            return list(DEFAULT_IGNORE_SELECTORS)
        selectors = [segment.strip() for segment in raw.split(",") if segment.strip()]
        return selectors[:20] if selectors else list(DEFAULT_IGNORE_SELECTORS)

    def _collect_ignore_regions(self, page, selectors: list[str]) -> list[dict]:
        if not selectors:
            return []
        try:
            payload = page.evaluate(
                """(selectors) => {
                    const rows = [];
                    const seen = new Set();
                    for (const selector of selectors || []) {
                        let nodes = [];
                        try {
                            nodes = Array.from(document.querySelectorAll(selector));
                        } catch (error) {
                            continue;
                        }
                        for (const node of nodes) {
                            const rect = node.getBoundingClientRect();
                            if (!rect || rect.width <= 0 || rect.height <= 0) continue;
                            const key = `${Math.round(rect.x)}:${Math.round(rect.y)}:${Math.round(rect.width)}:${Math.round(rect.height)}`;
                            if (seen.has(key)) continue;
                            seen.add(key);
                            rows.push({
                                selector,
                                x: Math.round(rect.x),
                                y: Math.round(rect.y),
                                width: Math.round(rect.width),
                                height: Math.round(rect.height),
                            });
                        }
                    }
                    return rows.slice(0, 40);
                }""",
                selectors,
            )
        except Exception:
            return []
        return [item for item in list(payload or []) if isinstance(item, dict)]

    def update_csv_with_execution_results(self, csv_path: Path, results_path: Path, sep: str = ",") -> Path:
        results_payload = json.loads(results_path.read_text(encoding="utf-8"))
        result_map = {
            str(item.get("id", "")).strip(): item
            for item in results_payload.get("results", [])
            if str(item.get("id", "")).strip()
        }

        with csv_path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle, delimiter=sep)
            rows = list(reader)
            fieldnames = reader.fieldnames or []

        if "Execution Status" not in fieldnames:
            fieldnames.append("Execution Status")

        for row in rows:
            test_id = str(row.get("ID", "")).strip()
            result = result_map.get(test_id)
            if not result:
                continue
            row["Execution Status"] = result.get("status", "")
            status = result.get("status", "")
            if status == "passed":
                row["Actual Result"] = "Executed successfully."
                row["Evidence"] = f"Video/{test_id}.webm"
            elif status == "failed":
                row["Actual Result"] = result.get("error", "")
                row["Evidence"] = f"Video/{test_id}.webm"
            elif status == "skipped":
                row["Actual Result"] = "Skipped by executor."
                row["Evidence"] = ""
            elif status == "checkpoint_required":
                row["Actual Result"] = result.get("error", "") or "Manual checkpoint required."
                row["Evidence"] = f"Video/{test_id}.webm"

        output = io.StringIO()
        writer = csv.DictWriter(output, fieldnames=fieldnames, delimiter=sep, quoting=csv.QUOTE_MINIMAL, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
        csv_path.write_text(output.getvalue(), encoding="utf-8-sig", newline="")
        return csv_path

    def _capture_visual_snapshot(self, page) -> dict:
        try:
            return page.evaluate(
                """() => {
                    const selectors = [
                        "h1,h2,h3,h4,h5,h6,p,span,label,a,button,input,textarea,select,li,td,th,[role='button'],[role='link'],[role='tab']"
                    ];
                    const nodes = Array.from(document.querySelectorAll(selectors.join(",")));
                    const visible = nodes.filter((node) => {
                        const rect = node.getBoundingClientRect();
                        const style = window.getComputedStyle(node);
                        if (!rect || rect.width < 1 || rect.height < 1) return false;
                        if (style.visibility === "hidden" || style.display === "none") return false;
                        return true;
                    }).slice(0, 280);
                    const normalize = (value) => (value || "").toString().replace(/\\s+/g, " ").trim();
                    const buildPath = (node) => {
                        const chunks = [];
                        let current = node;
                        let depth = 0;
                        while (current && current.nodeType === 1 && depth < 5) {
                            const tag = current.tagName.toLowerCase();
                            const id = normalize(current.id);
                            if (id) {
                                chunks.unshift(`${tag}#${id}`);
                                break;
                            }
                            let nth = 1;
                            let prev = current.previousElementSibling;
                            while (prev) {
                                if (prev.tagName === current.tagName) nth += 1;
                                prev = prev.previousElementSibling;
                            }
                            chunks.unshift(`${tag}:nth-of-type(${nth})`);
                            current = current.parentElement;
                            depth += 1;
                        }
                        return chunks.join(" > ");
                    };
                    const elements = visible.map((node) => {
                        const style = window.getComputedStyle(node);
                        const rect = node.getBoundingClientRect();
                        const label = normalize(node.innerText || node.textContent || node.value || node.getAttribute("aria-label") || node.getAttribute("placeholder") || "");
                        const key = `${buildPath(node)}|${normalize(node.getAttribute("name") || "")}|${normalize(node.getAttribute("id") || "")}|${label.slice(0, 32)}`;
                        return {
                            key,
                            path: buildPath(node),
                            tag: node.tagName.toLowerCase(),
                            text: label.slice(0, 180),
                            placeholder: normalize(node.getAttribute("placeholder") || ""),
                            name: normalize(node.getAttribute("name") || ""),
                            aria_label: normalize(node.getAttribute("aria-label") || ""),
                            rect: {
                                x: Math.round(rect.x),
                                y: Math.round(rect.y),
                                width: Math.round(rect.width),
                                height: Math.round(rect.height),
                            },
                            style: {
                                font_size: style.fontSize,
                                font_family: style.fontFamily,
                                font_weight: style.fontWeight,
                                line_height: style.lineHeight,
                                color: style.color,
                                background_color: style.backgroundColor,
                                padding_top: style.paddingTop,
                                padding_right: style.paddingRight,
                                padding_bottom: style.paddingBottom,
                                padding_left: style.paddingLeft,
                                margin_top: style.marginTop,
                                margin_right: style.marginRight,
                                margin_bottom: style.marginBottom,
                                margin_left: style.marginLeft,
                                border_radius: style.borderRadius,
                            },
                        };
                    });
                    return {
                        url: location.href,
                        title: document.title,
                        viewport: {
                            width: window.innerWidth,
                            height: window.innerHeight,
                        },
                        element_count: elements.length,
                        elements,
                    };
                }"""
            )
        except Exception:
            return {"url": page.url, "title": "", "viewport": {}, "element_count": 0, "elements": []}

    def _analyze_visual_snapshot(self, url: str, current_snapshot: dict, run_dir: Path) -> dict:
        previous = self._find_previous_visual_snapshot_data(url, run_dir)
        baseline_snapshot = previous.get("snapshot", {}) if previous else {}
        diff = self._compare_visual_snapshots(current_snapshot, baseline_snapshot)
        return {
            "has_baseline": bool(previous),
            "baseline_run": previous.get("run_name", "") if previous else "",
            "has_changes": bool(diff.get("summary", {}).get("total_changed", 0)),
            "snapshot_summary": {
                "element_count": int(current_snapshot.get("element_count", 0) or 0),
                "viewport": current_snapshot.get("viewport", {}),
                "title": current_snapshot.get("title", ""),
            },
            "current_snapshot": current_snapshot,
            "diff_summary": diff.get("summary", {}),
            "changed_areas": diff.get("changes", [])[:120],
        }

    def _build_visual_regression_report(
        self,
        url: str,
        *args,
        run_dir: Path | None = None,
        current_artifact: str = "",
        render_variants: list[dict] | None = None,
        **kwargs,
    ) -> dict:
        # Backward-compatible argument normalization:
        # supports both positional and keyword run_dir/current_artifact/render_variants.
        if run_dir is None and args:
            run_dir = args[0]
            args = args[1:]
        if run_dir is None:
            run_dir = kwargs.pop("run_dir", None)
        if not current_artifact and args:
            current_artifact = args[0]
            args = args[1:]
        if not current_artifact:
            current_artifact = kwargs.pop("current_artifact", "")
        if render_variants is None and args:
            render_variants = args[0]
            args = args[1:]
        if render_variants is None:
            render_variants = kwargs.pop("render_variants", None)
        run_dir = Path(run_dir or self.reports_dir)

        try:
            threshold_ratio = float(str(os.getenv("QA_AI_VISUAL_DIFF_THRESHOLD", "0.01")).strip() or 0.01)
        except Exception:
            threshold_ratio = 0.01
        variants = self._build_visual_variants(current_artifact, render_variants)
        if not variants:
            return {
                "url": url,
                "has_baseline": False,
                "status": "no_current_render",
                "reason": "Current render image is missing.",
                "variants": [],
            }

        variant_reports = []
        has_any_baseline = False
        has_failed = False
        has_passed = False
        has_missing_current = False

        for variant in variants:
            viewport_name = str(variant.get("viewport_name", "desktop")).strip() or "desktop"
            current_artifact_path = str(variant.get("artifact", "")).strip()
            current_path = (run_dir / current_artifact_path).resolve() if current_artifact_path else None
            if not current_path or not current_path.exists():
                has_missing_current = True
                variant_reports.append(
                    {
                        "viewport_name": viewport_name,
                        "viewport": variant.get("viewport", {}),
                        "has_baseline": False,
                        "status": "no_current_render",
                        "current_artifact": current_artifact_path,
                        "ignore_region_count": len(variant.get("ignore_regions", [])),
                    }
                )
                continue

            previous = self._find_previous_visual_baseline(url, run_dir, viewport_name=viewport_name)
            if not previous:
                variant_reports.append(
                    {
                        "viewport_name": viewport_name,
                        "viewport": variant.get("viewport", {}),
                        "has_baseline": False,
                        "status": "baseline_created",
                        "threshold_ratio": threshold_ratio,
                        "current_artifact": current_artifact_path,
                        "ignore_region_count": len(variant.get("ignore_regions", [])),
                    }
                )
                continue

            has_any_baseline = True
            baseline_path = previous["image_path"]
            visual_dir = run_dir / "Evidence" / "Visual"
            visual_dir.mkdir(parents=True, exist_ok=True)
            diff_artifact = visual_dir / f"visual_diff_{viewport_name}.png"
            comparison = self._compare_visual_images(
                current_path,
                baseline_path,
                diff_artifact,
                threshold_ratio=threshold_ratio,
                ignore_regions=list(variant.get("ignore_regions", [])),
            )
            variant_status = "failed" if comparison.get("has_difference", False) else "passed"
            has_failed = has_failed or variant_status == "failed"
            has_passed = has_passed or variant_status == "passed"
            previous_run_dir = previous.get("run_dir", run_dir)
            current_artifact_rel = self._safe_relative_path(current_path, run_dir)
            baseline_artifact_rel = self._safe_relative_path(baseline_path, previous_run_dir)
            diff_artifact_rel = self._safe_relative_path(diff_artifact, run_dir)
            variant_reports.append(
                {
                    "viewport_name": viewport_name,
                    "viewport": variant.get("viewport", {}),
                    "has_baseline": True,
                    "baseline_run": previous.get("run_name", ""),
                    "status": variant_status,
                    "threshold_ratio": threshold_ratio,
                    "current_artifact": current_artifact_rel,
                    "baseline_artifact": baseline_artifact_rel,
                    "diff_artifact": diff_artifact_rel,
                    "comparison": comparison,
                    "ignore_region_count": len(variant.get("ignore_regions", [])),
                    "baseline_approval_status": str(previous.get("approval_status", "")),
                }
            )

        status = "baseline_created"
        if has_failed:
            status = "failed"
        elif has_passed:
            status = "passed"
        elif has_missing_current and not has_any_baseline:
            status = "no_current_render"

        primary_variant = variant_reports[0] if variant_reports else {}
        return {
            "url": url,
            "has_baseline": has_any_baseline,
            "status": status,
            "threshold_ratio": threshold_ratio,
            "current_artifact": primary_variant.get("current_artifact", ""),
            "baseline_artifact": primary_variant.get("baseline_artifact", ""),
            "diff_artifact": primary_variant.get("diff_artifact", ""),
            "baseline_run": primary_variant.get("baseline_run", ""),
            "comparison": primary_variant.get("comparison", {}),
            "variants": variant_reports,
        }

    def _build_visual_variants(self, current_artifact: str, render_variants: list[dict] | None) -> list[dict]:
        variants = []
        for item in list(render_variants or []):
            artifact = str(item.get("artifact", "")).strip()
            if not artifact:
                continue
            viewport_name = str(item.get("viewport_name", "")).strip() or "desktop"
            variants.append(
                {
                    "viewport_name": viewport_name,
                    "viewport": item.get("viewport", {}),
                    "artifact": artifact,
                    "ignore_regions": list(item.get("ignore_regions", [])),
                }
            )
        if variants:
            return variants
        if not current_artifact:
            return []
        return [
            {
                "viewport_name": "desktop",
                "viewport": {"width": 1280, "height": 720},
                "artifact": str(current_artifact),
                "ignore_regions": [],
            }
        ]

    def _find_previous_visual_baseline(self, url: str, current_run_dir: Path, viewport_name: str = "desktop") -> dict | None:
        normalized_url = self._normalize_url(url)
        candidates = []
        for run_dir in self.reports_dir.iterdir():
            if not run_dir.is_dir() or run_dir.resolve() == current_run_dir.resolve():
                continue
            raw_candidates = sorted((run_dir / "JSON").glob("raw_scan_*.json"))
            if not raw_candidates:
                continue
            try:
                raw_payload = json.loads(raw_candidates[0].read_text(encoding="utf-8"))
            except Exception:
                continue
            if self._normalize_url(raw_payload.get("url", "")) != normalized_url:
                continue

            approval_payload = self._load_visual_approval(run_dir)
            approval_status = str(approval_payload.get("status", "")).strip().lower()
            if approval_status == "rejected":
                continue

            image_path = self._resolve_baseline_variant(run_dir, viewport_name)
            if not image_path or not image_path.exists():
                continue
            candidates.append(
                {
                    "run_name": run_dir.name,
                    "run_dir": run_dir,
                    "image_path": image_path,
                    "modified_ts": run_dir.stat().st_mtime,
                    "approval_status": approval_status,
                    "approval_priority": 1 if approval_status == "approved" else 0,
                }
            )
        if not candidates:
            return None
        candidates.sort(key=lambda item: (item.get("approval_priority", 0), item["modified_ts"]), reverse=True)
        return candidates[0]

    def _compare_visual_images(
        self,
        current_path: Path,
        baseline_path: Path,
        diff_output_path: Path,
        threshold_ratio: float = 0.01,
        ignore_regions: list[dict] | None = None,
    ) -> dict:
        with Image.open(current_path) as current_img_raw, Image.open(baseline_path) as baseline_img_raw:
            current = current_img_raw.convert("RGBA")
            baseline = baseline_img_raw.convert("RGBA")
            size_changed = current.size != baseline.size
            compare_width = min(current.width, baseline.width)
            compare_height = min(current.height, baseline.height)
            if compare_width <= 0 or compare_height <= 0:
                return {
                    "has_difference": True,
                    "reason": "empty_image",
                    "changed_pixels": 0,
                    "total_pixels": 0,
                    "ratio": 1.0,
                    "size_changed": size_changed,
                }
            current_crop = current.crop((0, 0, compare_width, compare_height))
            baseline_crop = baseline.crop((0, 0, compare_width, compare_height))
            if ignore_regions:
                current_crop = self._mask_ignore_regions(current_crop, ignore_regions)
                baseline_crop = self._mask_ignore_regions(baseline_crop, ignore_regions)

            diff = ImageChops.difference(current_crop, baseline_crop)
            mask = diff.convert("L").point(lambda p: 255 if p > 16 else 0)
            histogram = mask.histogram()
            changed_pixels = int(sum(histogram[1:])) if histogram else 0
            total_pixels = int(compare_width * compare_height)
            ratio = (changed_pixels / total_pixels) if total_pixels else 1.0
            has_difference = size_changed or (ratio > threshold_ratio)

            overlay = current_crop.copy()
            red_overlay = Image.new("RGBA", (compare_width, compare_height), (255, 0, 0, 120))
            overlay.paste(red_overlay, (0, 0), mask)
            overlay.save(diff_output_path)

            return {
                "has_difference": has_difference,
                "changed_pixels": changed_pixels,
                "total_pixels": total_pixels,
                "ratio": round(ratio, 6),
                "size_changed": size_changed,
                "current_size": {"width": current.width, "height": current.height},
                "baseline_size": {"width": baseline.width, "height": baseline.height},
                "ignored_regions": len(ignore_regions or []),
            }

    def _resolve_baseline_variant(self, run_dir: Path, viewport_name: str) -> Path | None:
        payload = {}
        regression_path = visual_regression_path(run_dir, create=False)
        if regression_path.exists():
            try:
                payload = json.loads(regression_path.read_text(encoding="utf-8"))
            except Exception:
                payload = {}
        variants = list(payload.get("variants", [])) if isinstance(payload, dict) else []
        for item in variants:
            if str(item.get("viewport_name", "")).strip() != viewport_name:
                continue
            artifact = str(item.get("current_artifact", "")).strip()
            if not artifact:
                continue
            resolved = (run_dir / artifact).resolve()
            if resolved.exists():
                return resolved
        visual_dir = run_dir / "Evidence" / "Visual"
        viewport_candidates = sorted(visual_dir.glob(f"*_{viewport_name}_render.png"))
        if viewport_candidates:
            return viewport_candidates[0]
        legacy_candidates = sorted(visual_dir.glob("*_render.png"))
        if legacy_candidates:
            return legacy_candidates[0]
        return None

    def _load_visual_approval(self, run_dir: Path) -> dict:
        approval_path = visual_regression_approval_path(run_dir, create=False)
        if not approval_path.exists():
            return {}
        try:
            return json.loads(approval_path.read_text(encoding="utf-8"))
        except Exception:
            return {}

    def _safe_relative_path(self, target_path: Path, base_dir: Path) -> str:
        try:
            target_resolved = Path(target_path).resolve()
            base_resolved = Path(base_dir).resolve()
            return str(target_resolved.relative_to(base_resolved)).replace("\\", "/")
        except Exception:
            return str(Path(target_path)).replace("\\", "/")

    def _mask_ignore_regions(self, image: Image.Image, ignore_regions: list[dict]) -> Image.Image:
        if not ignore_regions:
            return image
        masked = image.copy()
        for region in ignore_regions:
            try:
                x = max(0, int(region.get("x", 0) or 0))
                y = max(0, int(region.get("y", 0) or 0))
                w = max(0, int(region.get("width", 0) or 0))
                h = max(0, int(region.get("height", 0) or 0))
            except Exception:
                continue
            if w <= 0 or h <= 0:
                continue
            right = min(masked.width, x + w)
            bottom = min(masked.height, y + h)
            if right <= x or bottom <= y:
                continue
            overlay = Image.new("RGBA", (right - x, bottom - y), (127, 127, 127, 255))
            masked.paste(overlay, (x, y))
        return masked

    def _find_previous_visual_snapshot_data(self, url: str, current_run_dir: Path) -> dict | None:
        normalized_url = self._normalize_url(url)
        candidates = []
        for run_dir in self.reports_dir.iterdir():
            if not run_dir.is_dir() or run_dir.resolve() == current_run_dir.resolve():
                continue
            snapshot_path = visual_baseline_path(run_dir, create=False)
            if not snapshot_path.exists():
                continue
            try:
                payload = json.loads(snapshot_path.read_text(encoding="utf-8"))
            except Exception:
                continue
            snapshot_url = self._normalize_url(payload.get("url", ""))
            if snapshot_url != normalized_url:
                continue
            candidates.append(
                {
                    "run_name": run_dir.name,
                    "snapshot": payload.get("snapshot", {}),
                    "modified_ts": run_dir.stat().st_mtime,
                }
            )
        if not candidates:
            return None
        candidates.sort(key=lambda item: item["modified_ts"], reverse=True)
        return candidates[0]

    def _compare_visual_snapshots(self, current: dict, baseline: dict) -> dict:
        current_items = {str(item.get("key", "")): item for item in list(current.get("elements", [])) if str(item.get("key", ""))}
        baseline_items = {str(item.get("key", "")): item for item in list(baseline.get("elements", [])) if str(item.get("key", ""))}
        added_keys = sorted(set(current_items) - set(baseline_items))
        removed_keys = sorted(set(baseline_items) - set(current_items))
        shared_keys = sorted(set(current_items) & set(baseline_items))

        changes = []
        text_changes = 0
        style_changes = 0
        layout_changes = 0

        tracked_styles = [
            "font_size",
            "font_family",
            "font_weight",
            "line_height",
            "color",
            "background_color",
            "padding_top",
            "padding_right",
            "padding_bottom",
            "padding_left",
            "margin_top",
            "margin_right",
            "margin_bottom",
            "margin_left",
            "border_radius",
        ]

        for key in shared_keys:
            current_item = current_items[key]
            baseline_item = baseline_items[key]
            changed_fields = []
            if str(current_item.get("text", "")) != str(baseline_item.get("text", "")):
                text_changes += 1
                changed_fields.append(
                    {
                        "field": "text",
                        "before": baseline_item.get("text", ""),
                        "after": current_item.get("text", ""),
                    }
                )
            for style_key in tracked_styles:
                before = str((baseline_item.get("style", {}) or {}).get(style_key, ""))
                after = str((current_item.get("style", {}) or {}).get(style_key, ""))
                if before != after:
                    style_changes += 1
                    changed_fields.append({"field": style_key, "before": before, "after": after})
            for rect_key in ("x", "y", "width", "height"):
                before = int((baseline_item.get("rect", {}) or {}).get(rect_key, 0) or 0)
                after = int((current_item.get("rect", {}) or {}).get(rect_key, 0) or 0)
                if abs(before - after) >= 3:
                    layout_changes += 1
                    changed_fields.append({"field": f"rect_{rect_key}", "before": before, "after": after})
            if changed_fields:
                changes.append(
                    {
                        "area": current_item.get("path", "") or current_item.get("tag", ""),
                        "tag": current_item.get("tag", ""),
                        "key": key,
                        "changed_fields": changed_fields[:14],
                    }
                )

        for key in added_keys[:120]:
            item = current_items[key]
            changes.append(
                {"area": item.get("path", "") or item.get("tag", ""), "tag": item.get("tag", ""), "key": key, "change_type": "added"}
            )
        for key in removed_keys[:120]:
            item = baseline_items[key]
            changes.append(
                {"area": item.get("path", "") or item.get("tag", ""), "tag": item.get("tag", ""), "key": key, "change_type": "removed"}
            )

        return {
            "summary": {
                "total_changed": len(changes),
                "added_count": len(added_keys),
                "removed_count": len(removed_keys),
                "text_change_count": text_changes,
                "style_change_count": style_changes,
                "layout_change_count": layout_changes,
            },
            "changes": changes,
        }

    def _normalize_url(self, value: str) -> str:
        parsed = urlparse(str(value or "").strip())
        host = (parsed.netloc or "").replace("www.", "").lower()
        path = parsed.path or "/"
        path = path.rstrip("/") or "/"
        return f"{parsed.scheme.lower()}://{host}{path}"

    def _build_page_fingerprint(self, soup: BeautifulSoup, info: dict, runtime_fingerprint: dict | None = None) -> dict:
        runtime_fingerprint = runtime_fingerprint or {}
        search_terms = ("search", "cari", "keyword", "q")
        filter_terms = ("filter", "sort", "urut", "kategori", "category")
        pagination_terms = ("next", "previous", "page", "halaman")
        text_blob = " ".join(info.get("texts", [])).lower()
        button_blob = " ".join(info.get("buttons", [])).lower()
        link_blob = " ".join(link.get("text", "") for link in info.get("links", []) if isinstance(link, dict)).lower()
        combined_blob = f"{text_blob} {button_blob} {link_blob}"

        has_search = any(term in combined_blob for term in search_terms) or bool(
            soup.select('input[type="search"], input[name*="search"], input[placeholder*="search" i]')
        )
        has_filters = any(term in combined_blob for term in filter_terms) or bool(
            soup.select('select, [data-filter], .filter, .sort, [role="combobox"]')
        )
        has_pagination = any(term in combined_blob for term in pagination_terms) or bool(
            soup.select('.pagination, nav[aria-label*="pagination" i], a[rel="next"], a[rel="prev"]')
        )
        has_auth = bool(soup.select('input[type="password"], input[name*="login" i], input[name*="email" i]'))
        has_otp = bool(soup.select('input[autocomplete="one-time-code"], input[name*="otp" i], input[id*="otp" i]'))
        has_sso = any(
            token in combined_blob
            for token in ("continue with google", "continue with microsoft", "sign in with", "single sign-on", "sso")
        )
        repeated_links = max((len(group) for group in info.get("navigation", [])), default=0)
        list_density = sum(len(items) for items in info.get("lists", []))
        standalone_controls = info.get("standalone_controls", [])

        return {
            "has_search": has_search or bool(runtime_fingerprint.get("has_search")),
            "has_filters": has_filters or bool(runtime_fingerprint.get("has_filters")),
            "has_pagination": has_pagination or bool(runtime_fingerprint.get("has_pagination")),
            "has_auth_pattern": has_auth or bool(runtime_fingerprint.get("has_auth_pattern")),
            "has_table": bool(info.get("tables")),
            "has_form": bool(info.get("forms")) or bool(runtime_fingerprint.get("has_form")),
            "has_standalone_controls": bool(standalone_controls),
            "has_navigation": bool(info.get("navigation")),
            "has_article_like_sections": any(section.get("heading") or len(section.get("text", "")) > 120 for section in info.get("sections", [])),
            "has_listing_pattern": list_density >= 6 or repeated_links >= 5,
            "has_combobox": bool(runtime_fingerprint.get("has_combobox")) or any(field.get("widget") == "combobox" for field in standalone_controls),
            "has_datepicker": bool(runtime_fingerprint.get("has_datepicker")),
            "has_timepicker": bool(runtime_fingerprint.get("has_timepicker")),
            "has_toast": bool(runtime_fingerprint.get("has_toast")),
            "has_drawer": bool(runtime_fingerprint.get("has_drawer")),
            "has_upload": bool(runtime_fingerprint.get("has_upload")) or any(field.get("widget") == "upload" for field in standalone_controls),
            "has_drag_drop": bool(runtime_fingerprint.get("has_drag_drop")),
            "has_rich_text": bool(runtime_fingerprint.get("has_rich_text")) or any(field.get("widget") == "rich_text" for field in standalone_controls),
            "has_infinite_scroll": bool(runtime_fingerprint.get("has_infinite_scroll")),
            "has_carousel": bool(runtime_fingerprint.get("has_carousel")),
            "has_iframe": bool(runtime_fingerprint.get("has_iframe")),
            "has_shadow_dom": bool(runtime_fingerprint.get("has_shadow_dom")),
            "has_chart": bool(runtime_fingerprint.get("has_chart")),
            "has_map": bool(runtime_fingerprint.get("has_map")),
            "has_cookie_banner": bool(runtime_fingerprint.get("has_cookie_banner")),
            "has_captcha": bool(runtime_fingerprint.get("has_captcha")),
            "has_spa_shell": bool(runtime_fingerprint.get("has_spa_shell")),
            "has_graphql": bool(runtime_fingerprint.get("has_graphql")),
            "has_websocket": bool(runtime_fingerprint.get("has_websocket")),
            "has_live_updates": bool(runtime_fingerprint.get("has_live_updates")),
            "has_otp_flow": has_otp or bool(runtime_fingerprint.get("has_otp_flow")),
            "has_sso": has_sso or bool(runtime_fingerprint.get("has_sso")),
            "has_auth_checkpoint": bool(runtime_fingerprint.get("has_auth_checkpoint") or has_otp or has_sso),
            "button_count": len(info.get("buttons", [])),
            "link_count": len(info.get("links", [])),
            "form_count": len(info.get("forms", [])),
            "standalone_control_count": len(standalone_controls),
            "section_count": len(info.get("sections", [])),
            "table_count": len(info.get("tables", [])),
            "iframe_count": int(runtime_fingerprint.get("iframe_count", 0) or 0),
            "shadow_host_count": int(runtime_fingerprint.get("shadow_host_count", 0) or 0),
            "xhr_count": int(runtime_fingerprint.get("xhr_count", 0) or 0),
            "fetch_count": int(runtime_fingerprint.get("fetch_count", 0) or 0),
            "graphql_request_count": int(runtime_fingerprint.get("graphql_request_count", 0) or 0),
            "websocket_count": int(runtime_fingerprint.get("websocket_count", 0) or 0),
        }

    def _extract_visual_components(self, soup: BeautifulSoup) -> list[dict]:
        components = []

        def add_component(component_type: str, label: str = "", details: dict | None = None):
            payload = {"type": component_type, "label": label[:120]}
            if details:
                payload.update(details)
            key = json.dumps(payload, sort_keys=True, ensure_ascii=False)
            if key not in seen:
                components.append(payload)
                seen.add(key)

        seen = set()

        for nav in soup.select('nav[aria-label*="breadcrumb" i], .breadcrumb, [data-testid*="breadcrumb" i]')[:3]:
            text = nav.get_text(" ", strip=True)
            add_component("breadcrumb", text[:120], {"items": [a.get_text(" ", strip=True)[:60] for a in nav.find_all("a", limit=6)]})

        for nav in soup.select('nav, [role="navigation"], [role="menubar"], [role="menu"]')[:6]:
            items = []
            for node in nav.select('a, button, [role="menuitem"]')[:8]:
                label = self._interactive_label(node, soup)
                if label:
                    items.append(label[:60])
            if items:
                add_component(
                    "navigation_menu" if nav.get("role") != "menu" else "menu",
                    self._container_label(nav)[:120] or " | ".join(items[:3]),
                    {"items": items[:8]},
                )

        for tab_container in soup.select('[role="tablist"], .tabs, [data-tabs], .nav-tabs')[:4]:
            tabs = [tab.get_text(" ", strip=True)[:60] for tab in tab_container.select('[role="tab"], button, a')[:8] if tab.get_text(" ", strip=True)]
            if tabs:
                add_component("tabs", " | ".join(tabs[:3]), {"items": tabs[:8]})

        for accordion in soup.select('details, .accordion, [data-accordion], [aria-expanded]')[:6]:
            text = accordion.get_text(" ", strip=True)
            if text:
                add_component("accordion", text[:120], {"expandable": True})

        for modal in soup.select('[role="dialog"], dialog, .modal, [aria-modal="true"]')[:4]:
            text = self._container_label(modal) or modal.get_text(" ", strip=True)
            add_component("modal", text[:120], {"dialog": True})

        for panel in soup.select('[role="dialog"], dialog, [aria-modal="true"]')[:4]:
            label = self._container_label(panel) or panel.get_text(" ", strip=True)
            if label:
                add_component("dialog", label[:120], {"dialog": True})

        for pager in soup.select('.pagination, nav[aria-label*="pagination" i], a[rel="next"], a[rel="prev"]')[:4]:
            text = pager.get_text(" ", strip=True)
            add_component("pagination", text[:120], {"items": [a.get_text(" ", strip=True)[:40] for a in pager.find_all("a", limit=8)]})

        for card in soup.select('article, .card, [class*="card" i], [data-testid*="card" i]')[:8]:
            title = ""
            heading = card.find(["h1", "h2", "h3", "h4"])
            if heading:
                title = heading.get_text(" ", strip=True)
            text = card.get_text(" ", strip=True)
            if title or (10 < len(text) < 220):
                add_component("card", title[:120] or text[:120], {"has_link": bool(card.find("a"))})

        for hero in soup.select('header, .hero, [class*="hero" i], [data-testid*="hero" i], section')[:5]:
            classes = " ".join(hero.get("class", [])).lower()
            if "hero" in classes or hero.get("data-testid", "").lower().find("hero") >= 0:
                text = hero.get_text(" ", strip=True)
                add_component("hero", text[:120], {"visual": True})

        for sort_control in soup.select('select, button, a')[:20]:
            text = sort_control.get_text(" ", strip=True) or sort_control.get("aria-label", "") or sort_control.get("name", "")
            lower = text.lower()
            if any(token in lower for token in ("sort", "urut", "order by")):
                add_component("sort_control", text[:120], {"tag": sort_control.name})
            if any(token in lower for token in ("filter", "refine", "kategori", "category")):
                add_component("filter_control", text[:120], {"tag": sort_control.name})
            if any(token in lower for token in ("search", "cari", "keyword")):
                add_component("search_control", text[:120], {"tag": sort_control.name})

        for control in soup.select('[role="combobox"], [aria-autocomplete], input[list]')[:8]:
            text = control.get("aria-label", "") or control.get("placeholder", "") or control.get("name", "") or control.get("id", "")
            add_component("combobox", text[:120], {"tag": control.name})

        for control in soup.select('[role="listbox"], [aria-haspopup="listbox"]')[:6]:
            text = self._interactive_label(control, soup)
            add_component("listbox", text[:120], {"tag": control.name})

        for picker in soup.select('input[type="date"], input[type="datetime-local"], input[type="month"], input[type="week"]')[:6]:
            text = picker.get("aria-label", "") or picker.get("placeholder", "") or picker.get("name", "") or picker.get("id", "")
            add_component("datepicker", text[:120], {"tag": picker.name})

        for picker in soup.select('input[type="time"]')[:6]:
            text = picker.get("aria-label", "") or picker.get("placeholder", "") or picker.get("name", "") or picker.get("id", "")
            add_component("timepicker", text[:120], {"tag": picker.name})

        for upload in soup.select('input[type="file"], .upload, [data-testid*="upload" i], [data-upload]')[:6]:
            text = upload.get("aria-label", "") or upload.get("name", "") or upload.get("id", "") or upload.get_text(" ", strip=True)
            add_component("file_upload", text[:120], {"tag": upload.name})

        for editor in soup.select('[contenteditable="true"], [role="textbox"][contenteditable], .ql-editor, .ProseMirror, .tox-edit-area, .ck-editor__editable')[:6]:
            text = editor.get("aria-label", "") or editor.get("data-testid", "") or editor.get_text(" ", strip=True)
            add_component("rich_text_editor", text[:120], {"tag": editor.name})

        for toast in soup.select('[role="alert"], [role="status"], .toast, .snackbar, [data-testid*="toast" i]')[:6]:
            text = toast.get_text(" ", strip=True)
            if text:
                add_component("toast", text[:120], {"ephemeral": True})

        for drawer in soup.select('.drawer, .offcanvas, [data-drawer], [class*="drawer" i], [class*="offcanvas" i]')[:6]:
            text = drawer.get_text(" ", strip=True)
            add_component("drawer", text[:120], {"panel": True})

        for zone in soup.select('[draggable="true"], .dropzone, [data-testid*="dropzone" i], [class*="drag" i]')[:6]:
            text = zone.get_text(" ", strip=True) or zone.get("aria-label", "") or zone.get("data-testid", "")
            add_component("drag_drop", text[:120], {"interactive": True})

        for node in soup.select('.swiper, .carousel, [data-testid*="carousel" i], [aria-roledescription="carousel"]')[:6]:
            text = node.get_text(" ", strip=True)
            add_component("carousel", text[:120], {"interactive": True})

        for banner in soup.select('[id*="cookie" i], [class*="cookie" i], [data-testid*="cookie" i], [aria-label*="cookie" i]')[:4]:
            text = banner.get_text(" ", strip=True)
            add_component("consent_banner", text[:120], {"dismissible": True})

        for captcha in soup.select('.g-recaptcha, [data-sitekey], iframe[src*="captcha" i], [id*="captcha" i]')[:4]:
            text = captcha.get("aria-label", "") or captcha.get("id", "") or captcha.get("title", "") or captcha.get("src", "")
            add_component("captcha", text[:120], {"verification": True})

        for otp in soup.select('input[autocomplete="one-time-code"], input[name*="otp" i], input[id*="otp" i], input[name*="verification" i]')[:4]:
            text = otp.get("aria-label", "") or otp.get("placeholder", "") or otp.get("name", "") or otp.get("id", "")
            add_component("otp_verification", text[:120], {"verification": True})

        for sso in soup.find_all(["button", "a"], limit=24):
            text = sso.get_text(" ", strip=True)
            if text and re.search(r"continue with|sign in with|single sign-on|sso", text, flags=re.IGNORECASE):
                add_component("sso_login", text[:120], {"provider": True})

        for live in soup.select('[data-live], [data-testid*="live" i], [aria-live], [class*="live" i], [class*="ticker" i]')[:6]:
            text = live.get_text(" ", strip=True) or live.get("aria-label", "") or live.get("data-testid", "")
            add_component("live_feed", text[:120], {"dynamic": True})

        for frame in soup.select("iframe")[:6]:
            text = frame.get("title", "") or frame.get("name", "") or frame.get("src", "")
            add_component("iframe", text[:120], {"src": (frame.get("src") or "")[:160]})

        if soup.select_one("canvas, .echarts-for-react, .recharts-wrapper, [data-testid*='chart' i]"):
            add_component("chart", "chart", {"visual": True})
        if soup.select_one("iframe[src*='maps' i], .leaflet-container, .mapboxgl-map, [data-testid*='map' i]"):
            add_component("map", "map", {"visual": True})

        return components[:40]

    def _merge_component_lists(self, *component_lists: list[dict]) -> list[dict]:
        merged = []
        seen = set()
        for component_list in component_lists:
            for component in component_list or []:
                key = json.dumps(component, sort_keys=True, ensure_ascii=False)
                if key not in seen:
                    merged.append(component)
                    seen.add(key)
        return merged[:60]

    def _stateful_probe_configs(self) -> list[dict]:
        return [
            {"type": "tabs", "selector": '[role="tab"]', "limit": 2, "delayMs": 250},
            {"type": "accordion", "selector": 'summary, [aria-expanded]', "limit": 2, "delayMs": 250},
            {"type": "drawer", "selector": '[data-drawer-toggle], [aria-controls], .drawer-toggle, .offcanvas-toggle', "limit": 2, "delayMs": 320},
            {"type": "async_drawer", "selector": '[data-drawer-toggle], [aria-controls], .drawer-toggle, .offcanvas-toggle', "limit": 1, "delayMs": 520},
            {"type": "modal", "selector": '[aria-haspopup="dialog"], [data-modal-open], [data-open-modal]', "limit": 2, "delayMs": 300},
            {"type": "combobox", "selector": '[role="combobox"], [aria-autocomplete], input[list]', "limit": 2, "delayMs": 220},
            {"type": "menu", "selector": '[aria-haspopup="menu"], [role="menuitem"], nav button, [data-menu-toggle]', "limit": 2, "delayMs": 250},
            {"type": "datepicker", "selector": 'input[type="date"], input[type="datetime-local"], [data-datepicker], [aria-haspopup="dialog"][aria-controls*="date" i]', "limit": 2, "delayMs": 260},
            {"type": "carousel", "selector": '.swiper-button-next, .swiper-button-prev, [data-carousel-next], [data-carousel-prev], [aria-roledescription="carousel"] button', "limit": 2, "delayMs": 260},
            {"type": "consent_banner", "selector": '[id*="cookie" i] button, [class*="cookie" i] button, [data-testid*="cookie" i] button', "limit": 1, "delayMs": 200},
        ]

    def _discover_stateful_interactions(self, page) -> dict:
        try:
            payload = page.evaluate(
                """
                async (probeConfigs) => {
                    const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
                    const textOf = (el) => (el?.innerText || el?.textContent || '').replace(/\\s+/g, ' ').trim().slice(0, 120);
                    const attrText = (el) => [el?.getAttribute?.('aria-label'), el?.getAttribute?.('title'), el?.getAttribute?.('name'), el?.getAttribute?.('id')].filter(Boolean).join(' ').slice(0, 120);
                    const pickLabel = (el) => textOf(el) || attrText(el);
                    const isVisible = (el) => {
                        if (!el) return false;
                        const style = window.getComputedStyle(el);
                        const rect = el.getBoundingClientRect();
                        return style.visibility !== 'hidden' && style.display !== 'none' && rect.width > 0 && rect.height > 0;
                    };
                    const safeAnchor = (el) => {
                        if (!el || el.tagName !== 'A') return true;
                        const href = (el.getAttribute('href') || '').trim();
                        return !href || href.startsWith('#') || href.startsWith('javascript:');
                    };
                    const safeButton = (el) => {
                        if (!el) return false;
                        const type = (el.getAttribute('type') || '').toLowerCase();
                        return type !== 'submit' && type !== 'reset';
                    };
                    const captureState = () => ({
                        url: location.href,
                        activeTabs: Array.from(document.querySelectorAll('[role="tab"][aria-selected="true"], [role="tab"].active')).map((el) => pickLabel(el)).slice(0, 4),
                        openDetails: document.querySelectorAll('details[open]').length,
                        dialogs: Array.from(document.querySelectorAll('[role="dialog"], dialog, .modal, [aria-modal="true"]')).filter(isVisible).length,
                        drawers: Array.from(document.querySelectorAll('.drawer, .offcanvas, [data-drawer], [class*="drawer" i], [class*="offcanvas" i]')).filter(isVisible).length,
                        expanded: document.querySelectorAll('[aria-expanded="true"]').length,
                    });

                    const probes = [];
                    const states = [];
                    const components = [];
                    const seenStateIds = new Set();

                    for (const config of probeConfigs) {
                        const nodes = Array.from(document.querySelectorAll(config.selector))
                            .filter((el) => isVisible(el))
                            .filter((el) => safeAnchor(el))
                            .filter((el) => el.tagName === 'A' || safeButton(el) || el.tagName === 'SUMMARY' || ['tab', 'menuitem', 'combobox'].includes(el.getAttribute('role')) || el.tagName === 'INPUT')
                            .slice(0, config.limit);
                        for (const node of nodes) {
                            const label = pickLabel(node);
                            if (!label) continue;
                            const before = captureState();
                            const beforeExpanded = node.getAttribute('aria-expanded');
                            try {
                                if (node.tagName === 'INPUT' && ['date', 'datetime-local', 'month', 'week'].includes((node.getAttribute('type') || '').toLowerCase())) {
                                    node.focus();
                                } else {
                                    node.click();
                                }
                                await sleep(config.delayMs || 250);
                            } catch (error) {
                                probes.push({ type: config.type, label, changed: false, error: String(error).slice(0, 120) });
                                continue;
                            }
                            const after = captureState();
                            const afterExpanded = node.getAttribute('aria-expanded');
                            const changed = JSON.stringify(before) !== JSON.stringify(after) || beforeExpanded !== afterExpanded;
                            probes.push({
                                type: config.type,
                                label,
                                changed,
                                before,
                                after,
                            });
                            if (changed) {
                                const stateId = `${config.type}_${label.toLowerCase().replace(/[^a-z0-9]+/g, '_').replace(/^_+|_+$/g, '')}`.slice(0, 60);
                                if (stateId && !seenStateIds.has(stateId)) {
                                    states.push({
                                        state_id: stateId,
                                        label: `After ${config.type} interaction: ${label}`.slice(0, 180),
                                        trigger_action: 'click',
                                        trigger_label: label,
                                        source_type: config.type,
                                        before,
                                        after,
                                    });
                                    seenStateIds.add(stateId);
                                }
                                components.push({ type: config.type, label });
                            }
                            if (config.type !== 'consent_banner') {
                                try {
                                    if (node.tagName !== 'INPUT') node.click();
                                    await sleep(120);
                                } catch (error) {}
                            }
                        }
                    }

                    const scrollBefore = {
                        state: captureState(),
                        scrollTop: window.scrollY,
                        scrollHeight: document.documentElement.scrollHeight,
                    };
                    try {
                        window.scrollTo({ top: document.documentElement.scrollHeight, behavior: 'instant' });
                        await sleep(360);
                    } catch (error) {}
                    const scrollAfter = {
                        state: captureState(),
                        scrollTop: window.scrollY,
                        scrollHeight: document.documentElement.scrollHeight,
                    };
                    const scrollChanged = scrollAfter.scrollHeight > scrollBefore.scrollHeight || Math.abs(scrollAfter.scrollTop - scrollBefore.scrollTop) > 80;
                    probes.push({
                        type: 'infinite_scroll',
                        label: 'Scroll page',
                        changed: scrollChanged,
                        before: scrollBefore.state,
                        after: scrollAfter.state,
                    });
                    if (scrollChanged && !seenStateIds.has('infinite_scroll_more_content')) {
                        states.push({
                            state_id: 'infinite_scroll_more_content',
                            label: 'After infinite scroll interaction',
                            trigger_action: 'scroll',
                            trigger_label: 'Page scroll',
                            source_type: 'infinite_scroll',
                            before: scrollBefore.state,
                            after: scrollAfter.state,
                        });
                        seenStateIds.add('infinite_scroll_more_content');
                        components.push({ type: 'infinite_scroll', label: 'Page scroll' });
                    }

                    return {
                        states: states.slice(0, 12),
                        probes: probes.slice(0, 18),
                        visual_components: components.slice(0, 18),
                    };
                }
                """,
                self._stateful_probe_configs(),
            )
        except Exception as exc:
            console.print(f"[yellow]  Failed to perform state discovery: {exc}[/yellow]")
            return {"states": [], "probes": [], "visual_components": []}
        return payload or {"states": [], "probes": [], "visual_components": []}

    def _collect_runtime_signals(self, page) -> dict:
        try:
            payload = page.evaluate(
                """
                () => {
                    const textOf = (el) => (el?.innerText || el?.textContent || '').replace(/\\s+/g, ' ').trim().slice(0, 120);
                    const attrText = (el) => [el?.getAttribute?.('aria-label'), el?.getAttribute?.('placeholder'), el?.getAttribute?.('name'), el?.getAttribute?.('id')].filter(Boolean).join(' ').slice(0, 120);
                    const pickLabel = (el) => textOf(el) || attrText(el);
                    const components = [];
                    const seen = new Set();
                    const addComponent = (type, label = '', details = {}) => {
                        const payload = { type, label: (label || '').slice(0, 120), ...details };
                        const key = JSON.stringify(payload);
                        if (!seen.has(key)) {
                            components.push(payload);
                            seen.add(key);
                        }
                    };

                    const selectors = {
                        combobox: '[role="combobox"], [aria-autocomplete], input[list]',
                        datepicker: 'input[type="date"], input[type="datetime-local"], input[type="month"], input[type="week"]',
                        timepicker: 'input[type="time"]',
                        toast: '[role="alert"], [role="status"], .toast, .snackbar, [data-testid*="toast" i]',
                        drawer: '.drawer, .offcanvas, [data-drawer], [class*="drawer" i], [class*="offcanvas" i]',
                        upload: 'input[type="file"], .upload, [data-testid*="upload" i], [data-upload]',
                        dragDrop: '[draggable="true"], .dropzone, [data-testid*="dropzone" i], [class*="drag" i]',
                        richText: '[contenteditable="true"], [role="textbox"][contenteditable], .ql-editor, .ProseMirror, .tox-edit-area, .ck-editor__editable',
                        infiniteScroll: '.infinite-scroll, [data-testid*="infinite" i], [class*="virtual" i], [data-virtualized]',
                        carousel: '.swiper, .carousel, [data-testid*="carousel" i], [aria-roledescription="carousel"]',
                        chart: 'canvas, .echarts-for-react, .recharts-wrapper, [data-testid*="chart" i]',
                        map: 'iframe[src*="maps" i], .leaflet-container, .mapboxgl-map, [data-testid*="map" i]',
                        cookieBanner: '[id*="cookie" i], [class*="cookie" i], [data-testid*="cookie" i], [aria-label*="cookie" i]',
                        captcha: '.g-recaptcha, [data-sitekey], iframe[src*="captcha" i], [id*="captcha" i]',
                        spaShell: '#__next, #__nuxt, [data-reactroot], [ng-version], [data-svelte-h]',
                        otp: 'input[autocomplete="one-time-code"], input[name*="otp" i], input[id*="otp" i], input[name*="verification" i]',
                        sso: 'button, a',
                        liveFeed: '[data-live], [data-testid*="live" i], [aria-live], [class*="live" i], [class*="ticker" i]',
                    };

                    const findCount = (selector) => document.querySelectorAll(selector).length;
                    const hasShadowDom = Array.from(document.querySelectorAll('*')).some((el) => !!el.shadowRoot);
                    const shadowHostCount = Array.from(document.querySelectorAll('*')).filter((el) => !!el.shadowRoot).length;
                    const resources = performance.getEntriesByType('resource') || [];
                    const xhrCount = resources.filter((entry) => entry.initiatorType === 'xmlhttprequest').length;
                    const fetchCount = resources.filter((entry) => entry.initiatorType === 'fetch').length;
                    const websocketCount = resources.filter((entry) => String(entry.name || '').toLowerCase().startsWith('ws')).length;
                    const graphqlCount = resources.filter((entry) => String(entry.name || '').toLowerCase().includes('graphql')).length;
                    const liveFeedCount = findCount(selectors.liveFeed);
                    const ssoNodes = Array.from(document.querySelectorAll(selectors.sso)).filter((el) => /continue with|sign in with|single sign-on|sso/i.test(textOf(el) || attrText(el))).slice(0, 6);
                    const iframeNodes = Array.from(document.querySelectorAll('iframe')).slice(0, 8);
                    const iframeDetails = iframeNodes.map((frame) => ({
                        title: (frame.getAttribute('title') || frame.getAttribute('name') || '').slice(0, 80),
                        src: (frame.getAttribute('src') || '').slice(0, 160),
                    }));

                    Array.from(document.querySelectorAll(selectors.combobox)).slice(0, 6).forEach((el) => addComponent('combobox', pickLabel(el), { tag: el.tagName.toLowerCase() }));
                    Array.from(document.querySelectorAll(selectors.datepicker)).slice(0, 6).forEach((el) => addComponent('datepicker', pickLabel(el), { tag: el.tagName.toLowerCase() }));
                    Array.from(document.querySelectorAll(selectors.timepicker)).slice(0, 6).forEach((el) => addComponent('timepicker', pickLabel(el), { tag: el.tagName.toLowerCase() }));
                    Array.from(document.querySelectorAll(selectors.toast)).slice(0, 6).forEach((el) => addComponent('toast', pickLabel(el), { ephemeral: true }));
                    Array.from(document.querySelectorAll(selectors.drawer)).slice(0, 6).forEach((el) => addComponent('drawer', pickLabel(el), { panel: true }));
                    Array.from(document.querySelectorAll(selectors.upload)).slice(0, 6).forEach((el) => addComponent('file_upload', pickLabel(el), { tag: el.tagName.toLowerCase() }));
                    Array.from(document.querySelectorAll(selectors.dragDrop)).slice(0, 6).forEach((el) => addComponent('drag_drop', pickLabel(el), { interactive: true }));
                    Array.from(document.querySelectorAll(selectors.richText)).slice(0, 6).forEach((el) => addComponent('rich_text_editor', pickLabel(el), { tag: el.tagName.toLowerCase() }));
                    Array.from(document.querySelectorAll(selectors.infiniteScroll)).slice(0, 6).forEach((el) => addComponent('infinite_scroll', pickLabel(el), { virtualized: true }));
                    Array.from(document.querySelectorAll(selectors.carousel)).slice(0, 6).forEach((el) => addComponent('carousel', pickLabel(el), { interactive: true }));
                    Array.from(document.querySelectorAll(selectors.chart)).slice(0, 4).forEach((el) => addComponent('chart', pickLabel(el) || 'chart', { visual: true }));
                    Array.from(document.querySelectorAll(selectors.map)).slice(0, 4).forEach((el) => addComponent('map', pickLabel(el) || 'map', { visual: true }));
                    Array.from(document.querySelectorAll(selectors.cookieBanner)).slice(0, 4).forEach((el) => addComponent('consent_banner', pickLabel(el), { dismissible: true }));
                    Array.from(document.querySelectorAll(selectors.captcha)).slice(0, 4).forEach((el) => addComponent('captcha', pickLabel(el) || 'captcha', { verification: true }));
                    Array.from(document.querySelectorAll(selectors.otp)).slice(0, 4).forEach((el) => addComponent('otp_verification', pickLabel(el) || 'otp', { verification: true }));
                    ssoNodes.forEach((el) => addComponent('sso_login', pickLabel(el), { provider: true }));
                    Array.from(document.querySelectorAll(selectors.liveFeed)).slice(0, 4).forEach((el) => addComponent('live_feed', pickLabel(el) || 'live feed', { dynamic: true }));
                    iframeDetails.forEach((frame) => addComponent('iframe', frame.title || frame.src, { src: frame.src }));
                    if (hasShadowDom) addComponent('shadow_dom', 'shadow dom', { hosts: shadowHostCount });
                    if (findCount(selectors.spaShell) > 0) addComponent('spa_shell', 'spa shell', { dynamic: true });

                    return {
                        visual_components: components.slice(0, 40),
                        embedded_contexts: iframeDetails,
                        signals: {
                            has_shadow_dom: hasShadowDom,
                            iframe_count: iframeDetails.length,
                            xhr_count: xhrCount,
                            fetch_count: fetchCount,
                            graphql_request_count: graphqlCount,
                            websocket_count: websocketCount,
                            history_length: history.length,
                            route_kind: location.hash ? 'hash' : 'path',
                            local_storage_keys: Object.keys(window.localStorage || {}).slice(0, 10),
                            session_storage_keys: Object.keys(window.sessionStorage || {}).slice(0, 10),
                        },
                        fingerprint: {
                            has_search: findCount('input[type="search"], input[name*="search" i], input[placeholder*="search" i]') > 0,
                            has_filters: findCount('select, [data-filter], .filter, .sort, [role="combobox"]') > 0,
                            has_pagination: findCount('.pagination, nav[aria-label*="pagination" i], a[rel="next"], a[rel="prev"]') > 0,
                            has_form: findCount('form') > 0,
                            has_combobox: findCount(selectors.combobox) > 0,
                            has_datepicker: findCount(selectors.datepicker) > 0,
                            has_timepicker: findCount(selectors.timepicker) > 0,
                            has_toast: findCount(selectors.toast) > 0,
                            has_drawer: findCount(selectors.drawer) > 0,
                            has_upload: findCount(selectors.upload) > 0,
                            has_drag_drop: findCount(selectors.dragDrop) > 0,
                            has_rich_text: findCount(selectors.richText) > 0,
                            has_infinite_scroll: findCount(selectors.infiniteScroll) > 0,
                            has_carousel: findCount(selectors.carousel) > 0,
                            has_iframe: iframeDetails.length > 0,
                            iframe_count: iframeDetails.length,
                            has_shadow_dom: hasShadowDom,
                            shadow_host_count: shadowHostCount,
                            has_chart: findCount(selectors.chart) > 0,
                            has_map: findCount(selectors.map) > 0,
                            has_cookie_banner: findCount(selectors.cookieBanner) > 0,
                            has_captcha: findCount(selectors.captcha) > 0,
                            has_spa_shell: findCount(selectors.spaShell) > 0,
                            has_graphql: graphqlCount > 0,
                            has_websocket: websocketCount > 0,
                            has_live_updates: liveFeedCount > 0 || websocketCount > 0,
                            has_otp_flow: findCount(selectors.otp) > 0,
                            has_sso: ssoNodes.length > 0,
                            has_auth_checkpoint: findCount(selectors.captcha) > 0 || findCount(selectors.otp) > 0 || ssoNodes.length > 0,
                            has_auth_pattern: findCount('input[type="password"], input[name*="login" i], input[name*="email" i]') > 0,
                            xhr_count: xhrCount,
                            fetch_count: fetchCount,
                            graphql_request_count: graphqlCount,
                            websocket_count: websocketCount,
                        },
                    };
                }
                """
            )
        except Exception as exc:
            console.print(f"[yellow]  Failed to read runtime signals: {exc}[/yellow]")
            return {"visual_components": [], "embedded_contexts": [], "signals": {}, "fingerprint": {}}
        return payload or {"visual_components": [], "embedded_contexts": [], "signals": {}, "fingerprint": {}}
