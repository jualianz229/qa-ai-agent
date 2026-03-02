import csv
import io
import json
import re
from datetime import datetime
from pathlib import Path
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup
from rich.console import Console

from core.artifacts import json_artifact_path
from core.site_profiles import load_site_profile

console = Console(force_terminal=True)


class Scanner:
    def __init__(self, reports_dir: str = "Result"):
        self.reports_dir = Path(reports_dir)
        self.reports_dir.mkdir(parents=True, exist_ok=True)

    def scan_website(
        self,
        url: str,
        use_auth: bool = False,
        crawl_limit: int = 3,
        site_profile: dict | None = None,
    ) -> tuple[dict, dict, str]:
        """Scan one page, then optionally mini-crawl a few relevant internal links generically."""
        from playwright.sync_api import sync_playwright

        domain = urlparse(url).netloc.replace("www.", "") or "unknown"
        safe_domain = re.sub(r"[^\w]", "_", domain).lower()
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        run_dir = self.reports_dir / f"{safe_domain}_{timestamp}"
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
                console.print(f"[yellow]  File auth tidak ditemukan di {auth_file}. Melanjutkan tanpa session bypass.[/yellow]")

            context = browser.new_context(**context_kwargs)
            try:
                root_info, _, _ = self._scan_single_page(context, url, safe_domain, allow_spider=True)
                root_info["site_profile"] = site_profile
                candidates = self._select_internal_candidates(url, root_info, crawl_limit, site_profile)
                crawled_pages = []
                root_info["crawl_selection"] = candidates
                for index, candidate in enumerate(candidates, start=1):
                    console.print(
                        f"[dim]  Mini-crawl [{index}/{len(candidates)}]: {candidate.get('url', '')}"
                        f" | alasan: {', '.join(candidate.get('reasons', [])[:3])}[/dim]"
                    )
                    child_info, _, _ = self._scan_single_page(
                        context,
                        candidate.get("url", ""),
                        f"{safe_domain}_{index}",
                        allow_spider=False,
                    )
                    crawled_pages.append(child_info)

                merged_info = self._merge_page_infos(root_info, crawled_pages)
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

    def _scan_single_page(self, context, url: str, safe_name: str, allow_spider: bool) -> tuple[dict, str, str]:
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
            console.print(f"[yellow]  Warning goto {url}: {exc}. Melanjutkan analisa DOM.[/yellow]")

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
            "ui_components": [],
            "embedded_contexts": [],
            "metadata": {},
            "page_fingerprint": {},
            "runtime_signals": {},
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

        for el in soup.find_all(["button", "input"], limit=16):
            if el.name == "input" and el.get("type") not in ["submit", "button"]:
                continue
            text = (el.get_text(strip=True) or el.get("value", "") or el.get("aria-label", "")).strip()
            if text and text not in info["buttons"]:
                info["buttons"].append(text[:80])

        for el in soup.find_all("a", limit=30):
            text = el.get_text(strip=True)
            href = el.get("href", "")
            if text and href and not href.startswith("#"):
                info["links"].append({"text": text[:60], "href": href[:160]})

        for nav in soup.find_all(["nav", "header"], limit=4):
            nav_links = []
            for el in nav.find_all("a", limit=10):
                text = el.get_text(strip=True)
                href = el.get("href", "")
                if text:
                    nav_links.append({"text": text[:40], "href": href[:100]})
            if nav_links:
                info["navigation"].append(nav_links)

        for form in soup.find_all("form", limit=6):
            form_info = self._extract_form_info(form, soup)
            if form_info.get("fields"):
                info["forms"].append(form_info)

        info["standalone_controls"] = self._extract_standalone_controls(soup)

        for section in soup.find_all(["section", "article", "main"], limit=10):
            heading = ""
            heading_el = section.find(["h1", "h2", "h3", "h4"])
            if heading_el:
                heading = heading_el.get_text(strip=True)[:100]
            text = section.get_text(" ", strip=True)[:180]
            if heading or text:
                info["sections"].append({"heading": heading, "text": text})

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

        info["ui_components"] = self._merge_component_lists(
            self._extract_ui_components(soup),
            runtime_info.get("ui_components", []),
        )
        info["embedded_contexts"] = runtime_info.get("embedded_contexts", [])
        info["runtime_signals"] = runtime_info.get("signals", {})
        info["page_fingerprint"] = self._build_page_fingerprint(soup, info, runtime_info.get("fingerprint", {}))

    def _extract_form_info(self, form, soup: BeautifulSoup) -> dict:
        fields = []
        submit_texts = []
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
            field_info = self._extract_field_info(inp, form, soup, index)
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
            field_info = self._extract_field_info(control, soup, soup, index)
            if field_info:
                controls.append(field_info)
        return controls

    def _extract_field_info(self, inp, form, soup: BeautifulSoup, index: int) -> dict:
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
            inp.get("autocomplete", ""),
            inp.get("inputmode", ""),
            inp.get("data-testid", ""),
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
            "images", "apis", "sections", "tables", "lists", "ui_components", "embedded_contexts"
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
        merged["metadata"] = root_info.get("metadata", {})
        merged["runtime_signals"] = root_info.get("runtime_signals", {})
        merged["site_profile"] = root_info.get("site_profile", {})
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
            f"[cyan]{len(info.get('ui_components', []))}[/cyan] component  "
            f"[cyan]{len(info.get('embedded_contexts', []))}[/cyan] embedded  "
            f"[cyan]{len(info['apis'])}[/cyan] api  "
            f"[cyan]{len(info.get('crawled_pages', []))}[/cyan] linked page"
        )

    def save_csv_scenarios(self, data_list: list, project_info: dict, sep: str = ",") -> Path:
        output = io.StringIO()
        if data_list:
            fieldnames = [
                "ID", "Module", "Category", "Test Type", "Title", "Precondition",
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
        text = str(value or "").replace('"', "'")
        text = text.replace("\\n", "\n") if numbered else text
        text = re.sub(r"\r\n?", "\n", text)
        text = re.sub(r"[ \t]+\n", "\n", text)
        text = re.sub(r"\n[ \t]+\n", "\n", text)
        if numbered:
            text = re.sub(r"\n\s*\n+", "\n", text)
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

    def _extract_ui_components(self, soup: BeautifulSoup) -> list[dict]:
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

        for tab_container in soup.select('[role="tablist"], .tabs, [data-tabs], .nav-tabs')[:4]:
            tabs = [tab.get_text(" ", strip=True)[:60] for tab in tab_container.select('[role="tab"], button, a')[:8] if tab.get_text(" ", strip=True)]
            if tabs:
                add_component("tabs", " | ".join(tabs[:3]), {"items": tabs[:8]})

        for accordion in soup.select('details, .accordion, [data-accordion], [aria-expanded]')[:6]:
            text = accordion.get_text(" ", strip=True)
            if text:
                add_component("accordion", text[:120], {"expandable": True})

        for modal in soup.select('[role="dialog"], dialog, .modal, [aria-modal="true"]')[:4]:
            text = modal.get_text(" ", strip=True)
            add_component("modal", text[:120], {"dialog": True})

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
                        ui_components: components.slice(0, 40),
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
            console.print(f"[yellow]  Gagal membaca runtime signals: {exc}[/yellow]")
            return {"ui_components": [], "embedded_contexts": [], "signals": {}, "fingerprint": {}}
        return payload or {"ui_components": [], "embedded_contexts": [], "signals": {}, "fingerprint": {}}
