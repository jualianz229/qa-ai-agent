import os
import re
import requests
from bs4 import BeautifulSoup
from urllib.parse import urlparse
from pathlib import Path
from datetime import datetime
from rich.console import Console

console = Console(force_terminal=True)

class Scanner:
    def __init__(self, reports_dir: str = "Result"):
        self.reports_dir = Path(reports_dir)
        self.reports_dir.mkdir(parents=True, exist_ok=True)

    # ─── Project Setup ──────────────────────────────────────────────────────

    def scan_website(self, url: str, use_auth: bool = False) -> tuple[dict, dict, str]:
        """Buka halaman menggunakan Playwright, tangkap elemen statis/dinamis, dan ambil screenshot."""
        from playwright.sync_api import sync_playwright
        import tempfile
        
        domain = urlparse(url).netloc.replace("www.", "") or "unknown"
        
        info = {
            "title":    "",
            "url":      url,
            "headings": [],
            "texts":    [],
            "buttons":  [],
            "links":    [],
            "forms":    [],
            "images":   [],
            "apis":     [],
        }
        
        console.print(f"[dim]  Membuka browser Playwright ke {url}...[/dim]")
        screenshot_path = ""
        
        with sync_playwright() as p:
            # Pengecekan auth
            browser_args = {"headless": True}
            auth_file = "auth_state.json"
            
            browser = p.chromium.launch(**browser_args)
            video_dir = Path("Result") / "Visual_Evidence"
            video_dir.mkdir(parents=True, exist_ok=True)
            context_kwargs = {
                "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                "viewport": {"width": 1280, "height": 720},
                "record_video_dir": str(video_dir),
                "record_video_size": {"width": 1280, "height": 720}
            }
            if use_auth and os.path.exists(auth_file):
                console.print(f"[green]  Menggunakan bypass login ({auth_file})[/green]")
                context_kwargs["storage_state"] = auth_file
                
            context = browser.new_context(**context_kwargs)
            page = context.new_page()
            
            # API Interception untuk deteksi endpoint
            def handle_route(route):
                request_url = route.request.url
                if "/api/" in request_url and urlparse(request_url).netloc == urlparse(url).netloc:
                    if request_url not in info["apis"]:
                        info["apis"].append(request_url)
                route.continue_()
            
            page.route("**/*", handle_route)
            
            try:
                page.goto(url, wait_until="networkidle", timeout=20000)
            except Exception as e:
                console.print(f"[yellow]  Warning goto: {e}. Melanjutkan analisa DOM.[/yellow]")
                
            # --- Auto-Clicker Spider ---
            console.print(f"[dim]  Memulai Auto-Clicker Spider (Trigger animasi/popup)...[/dim]")
            try:
                page.evaluate("""
                    () => {
                        const buttons = document.querySelectorAll('button, .btn, .dropdown, [role="button"], [aria-haspopup="true"]');
                        let count = 0;
                        for (const b of buttons) {
                            if (count > 15) break; 
                            try { b.click(); count++; } catch (e) {}
                        }
                    }
                """)
                page.wait_for_timeout(2000) # Tunggu animasi render
            except Exception as e:
                console.print(f"[yellow]  Warning Spider: {e}[/yellow]")
            # ---------------------------
                
            title = page.title()
            info["title"] = title
            
            safe_title   = re.sub(r'[<>:"/\\|?*]', '', title).strip()[:40] if title else ""
            project_name = f"{domain} {safe_title}" if safe_title else domain
            
            # Mengambil HTML utuh setelah JS render
            html_content = page.content()
            soup = BeautifulSoup(html_content, 'html.parser')
            
            # Headings
            for tag in ["h1","h2","h3","h4","h5"]:
                for el in soup.find_all(tag, limit=8):
                    t = el.get_text(strip=True)
                    if t: info["headings"].append({"tag": tag, "text": t[:120]})
            
            # Visible texts
            for tag in ["p","label","li","td",".message",".alert",".error",".success"]:
                for el in soup.select(tag, limit=6):
                    t = el.get_text(strip=True)
                    if 5 < len(t) < 250 and t not in info["texts"]:
                        info["texts"].append(t)
                        
            # Buttons
            for el in soup.find_all(['button', 'input'], limit=12):
                if el.name == 'input' and el.get('type') not in ['submit', 'button']:
                    continue
                t = (el.get_text(strip=True) or el.get('value', '') or el.get('aria-label', '')).strip()
                if t and t not in info["buttons"]: 
                    info["buttons"].append(t[:80])
                    
            # Links
            for el in soup.find_all('a', limit=15):
                t = el.get_text(strip=True)
                href = el.get('href', '')
                if t and href and not href.startswith("#"):
                    info["links"].append({"text": t[:60], "href": href[:120]})
                    
            # Forms
            for form in soup.find_all('form', limit=4):
                fields = []
                for inp in form.find_all(['input', 'textarea', 'select'], limit=10):
                    typ = inp.get('type', 'text')
                    name = (inp.get('name') or inp.get('placeholder') or inp.get('id') or '')
                    if typ not in ["hidden","submit","button"]:
                        fields.append({"type": typ, "name": name[:40]})
                if fields: info["forms"].append(fields)
                
            # Images
            for img in soup.find_all('img', limit=6):
                alt = img.get('alt') or img.get('src') or ''
                if alt: info["images"].append(alt[:80])
            
            # Take Screenshot & Save Video path
            tmp_dir = Path(tempfile.gettempdir())
            scr_name = re.sub(r'[^\w]', '_', project_name).lower()[:40] + ".png"
            screenshot_path = str(tmp_dir / scr_name)
            try:
                page.screenshot(path=screenshot_path, full_page=True)
                console.print(f"[dim]  Screenshot berhasil ditangkap.[/dim]")
            except Exception as e:
                console.print(f"[yellow]  Gagal screenshot: {e}[/yellow]")
                screenshot_path = ""
                
            video_path = ""
            if page.video:
                video_path = page.video.path()
            
            browser.close()
            
            if video_path and os.path.exists(video_path):
                import shutil
                # Rename the chaotic playwright video name
                final_v_name = f"{scr_name.replace('.png', '')}_{datetime.now().strftime('%H%M%S')}.webm"
                final_v_path = video_dir / final_v_name
                try:
                    shutil.move(video_path, str(final_v_path))
                    console.print(f"[dim]  Video tersimpan di Result/Visual_Evidence/{final_v_name}[/dim]")
                except Exception as e:
                    console.print(f"[yellow]  Gagal rename video: {e}[/yellow]")

        project_info = {
            "title":           title or domain,
            "domain":          domain,
            "project_name":    project_name
        }
        
        info["apis"] = list(set(info["apis"]))[:10]

        n_h = len(info["headings"])
        n_t = len(info["texts"])
        n_b = len(info["buttons"])
        n_l = len(info["links"])
        n_f = len(info["forms"])
        n_a = len(info["apis"])
        console.print(
            f"[green]  Scan selesai:[/green] "
            f"[cyan]{n_h}[/cyan] heading  "
            f"[cyan]{n_t}[/cyan] teks  "
            f"[cyan]{n_b}[/cyan] tombol  "
            f"[cyan]{n_l}[/cyan] link  "
            f"[cyan]{n_f}[/cyan] form  "
            f"[cyan]{n_a}[/cyan] api"
        )
            
        return project_info, info, screenshot_path

    # ─── Data Save ─────────────────────────────────────────────────────────

    def save_csv_scenarios(self, data_list: list, project_info: dict, sep: str = ",") -> Path:
        """Simpan file test scenarios (CSV) ke folder /Result/"""
        import csv, io
        
        output = io.StringIO()
        if data_list and len(data_list) > 0:
            # Pastikan urutan kunci sesuai dengan standar QA
            fieldnames = ["ID", "Module", "Category", "Test Type", "Title", "Precondition", "Steps to Reproduce", "Expected Result", "Actual Result", "Severity", "Priority", "Evidence"]
            # Fallback jika field AI tidak sengaja beda
            actual_keys = list(data_list[0].keys())
            if not all(field in actual_keys for field in fieldnames[:4]):
                fieldnames = actual_keys
                
            writer = csv.DictWriter(output, fieldnames=fieldnames, delimiter=sep, quoting=csv.QUOTE_MINIMAL)
            writer.writeheader()
            for row in data_list:
                # Hanya tulis kolom yang valid ada di fieldnames untuk menghindari error ValueError jika ada extra keys
                clean_row = {}
                for k in fieldnames:
                    val = str(row.get(k, ""))
                    # Ubah spasi sebelum angka urutan (misal " 2. ") menjadi enter baru otomatis
                    if k in ["Steps to Reproduce", "Expected Result", "Precondition", "Actual Result"]:
                        import re
                        val = re.sub(r'(?<=\S)\s+(?=\d+\.)', '\n', val)
                    clean_row[k] = val
                writer.writerow(clean_row)
                
        final_csv_content = output.getvalue()
        
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        safe_name = re.sub(r'[^\w]', '_', project_info["project_name"].lower())
        csv_path = self.reports_dir / f"{safe_name}_{timestamp}.csv"
        csv_path.write_text(final_csv_content, encoding="utf-8-sig") # Auto BOM for Excel
        return csv_path

    def save_executive_summary(self, md_content: str, project_info: dict) -> Path:
        """Simpan Ringkasan Eksekutif (Markdown) ke folder /Result/"""
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        safe_name = re.sub(r'[^\w]', '_', project_info["project_name"].lower())
        md_path = self.reports_dir / f"Test_Plan_Summary_{safe_name}_{timestamp}.md"
        md_path.write_text(md_content, encoding="utf-8")
        return md_path
