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

    def get_website_info(self, url: str) -> dict:
        """Fetch URL, get title, prep project info."""
        domain = urlparse(url).netloc.replace("www.", "") or "unknown"
        title  = ""

        console.print(f"\n[dim]  Mengambil info website menggunakan requests...[/dim]")
        try:
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.9",
                "Upgrade-Insecure-Requests": "1"
            }
            r = requests.get(url, headers=headers, timeout=15)
            r.raise_for_status()
            soup = BeautifulSoup(r.text, 'html.parser')
            title = soup.title.string.strip() if soup.title and soup.title.string else ""
        except Exception:
            title = ""

        safe_title   = re.sub(r'[<>:"/\\|?*]', '', title).strip()[:40] if title else ""
        project_name = f"{domain} {safe_title}" if safe_title else domain
        safe_folder  = re.sub(r'[^\w\s-]', '_', project_name).strip()

        console.print(f"[green]  Target: [bold]{safe_folder}[/bold][/green]")

        return {
            "title":           title or domain,
            "domain":          domain,
            "project_name":    project_name
        }

    def capture_page_info(self, url: str) -> dict:
        """
        Buka halaman dan ekstrak elemen secara statis (tanpa JS execution).
        """
        console.print(f"[dim]  Scanning halaman (Static HTML)...[/dim]")
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

        try:
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.9",
                "Upgrade-Insecure-Requests": "1"
            }
            r = requests.get(url, headers=headers, timeout=15)
            soup = BeautifulSoup(r.text, 'html.parser')
            
            info["title"] = soup.title.string.strip() if soup.title and soup.title.string else ""
            
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
                
            # Scan API dari file JS (Cari endpoint /api/)
            try:
                for script in soup.find_all('script', src=True)[:3]:
                    js_src = script['src']
                    js_url = js_src if js_src.startswith('http') else url.rstrip('/') + '/' + js_src.lstrip('/')
                    js_r = requests.get(js_url, headers=headers, timeout=5)
                    if js_r.status_code == 200:
                        endpoints = re.findall(r'[\'"](/api/v\d+/[a-zA-Z0-9_/-]+)[\'"]', js_r.text)
                        if endpoints: info["apis"].extend(endpoints)
            except Exception:
                pass
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
        except Exception as e:
            console.print(f"[yellow]  Warning scan: {e}[/yellow]")
            
        return info

    # ─── Data Save ─────────────────────────────────────────────────────────

    def save_csv_scenarios(self, data_list: list, project_info: dict, sep: str = ",") -> Path:
        """Simpan file test scenarios (CSV) ke folder /Result/"""
        import csv, io
        
        output = io.StringIO()
        if data_list and len(data_list) > 0:
            # Pastikan urutan kunci sesuai dengan standar QA
            fieldnames = ["ID", "Module", "Category", "Title", "Precondition", "Steps to Reproduce", "Expected Result", "Actual Result", "Severity", "Priority", "Evidence"]
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
