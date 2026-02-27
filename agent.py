"""
QA AI Agent — Flow 4 Steps (CSV Test Scenario Generator)
1. Input URL
2. Input Instruksi
3. Scan Halaman Web
4. Generate CSV Test Scenarios
"""

import os
import sys
from pathlib import Path

# Fix encoding Windows
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from dotenv import load_dotenv
load_dotenv()

from rich.console import Console
from rich.panel   import Panel
from rich.rule    import Rule
from rich         import box

from core.ai_engine     import AIEngine
from core.scanner       import Scanner

console  = Console(force_terminal=True)
ai       = AIEngine()
runner   = Scanner()

# Pastikan folder instructions ada
Path("instructions").mkdir(exist_ok=True)

def process_single_url(url: str, instruction: str, csv_sep: str):
    # Auto-Scheme (Bypass URL nyasar tanpa https)
    if not url.startswith("http://") and not url.startswith("https://"):
        url = "https://" + url

    # ── 3. Scan Halaman Web ────────────────────────────────────────────────
    console.print(Rule(f"[bold]Mencerna Halaman Website: {url}[/bold]"))
    
    with console.status(f"[bold yellow]Mengunduh & Memindai Struktur HTML ({url})...[/bold yellow]", spinner="dots"):
        proj = runner.get_website_info(url)
        page_info = runner.capture_page_info(url)
    
    # ── 4. Generate CSV Scenario ───────────────────────────────────────────
    console.print(Rule(f"[bold]Generate Test Scenarios (CSV) untuk {url}[/bold]"))
    
    with console.status(f"[bold green]AI ({ai.current_model}) sedang menganalisa struktur web dan mengkonstruksi Skenario Test...[/bold green]"):
        try:
            parsed_data = ai.generate_test_scenarios(
                url, proj["title"], page_info, instruction, csv_sep
            )
            saved_csv_path = runner.save_csv_scenarios(parsed_data, proj, csv_sep)
            console.print(f"  [green]✓[/green] Format Skenario CSV digenerate: [dim]{saved_csv_path}[/dim]")
            
            # Generate Executive Summary
            console.print(f"  [dim]▶ Menyusun Ringkasan Eksekutif...[/dim]")
            md_summary = ai.generate_executive_summary(url, proj["title"], page_info, parsed_data)
            saved_md_path = runner.save_executive_summary(md_summary, proj)
            console.print(f"  [green]✓[/green] Ringkasan Eksekutif .md digenerate: [dim]{saved_md_path}[/dim]")
            
        except Exception as e:
            console.print(f"\n[bold red]✖ Error AI/Generation pada {url}:[/bold red] {e}")


def main():
    console.print(Panel(
        f"[bold cyan]QA Agent - CSV Test Scenario Generator[/bold cyan]\n"
        f"[dim]Model: {ai.current_model} ({ai.current_rpm} RPM)[/dim]",
        border_style="cyan", box=box.DOUBLE_EDGE,
    ))

    # ── 1. Input URL ───────────────────────────────────────────────────────
    console.print("\n[bold]Step 1: Input URL / Nama File Batch (.txt)[/bold]")
    url_input = console.input("  [cyan]Masukan (misal: saucedemo.com atau urls.txt): [/cyan]").strip()
    
    if url_input.lower() in ['', 'exit', 'quit', 'keluar']:
        console.print("[dim]Sampai jumpa![/dim]")
        return
        
    urls_to_process = []
    if url_input.endswith(".txt") and os.path.exists(url_input):
        with open(url_input, 'r', encoding='utf-8') as f:
            urls_to_process = [line.strip() for line in f if line.strip()]
        console.print(f"[bold green]Batch mode aktif: Ditemukan {len(urls_to_process)} URL(s).[/bold green]")
    else:
        urls_to_process.append(url_input)

    # Pilihan Separator CSV
    console.print("\n[bold]Format CSV (Pemisah Kolom)[/bold]")
    console.print("  [1] Koma (,) - Standar Global\n  [2] Titik Koma (;) - Microsoft Excel Regional Indonesia")
    sep_choice = console.input("  [cyan]Pilih [1/2] (Default: 1): [/cyan]").strip()
    csv_sep = ";" if sep_choice == "2" else ","

    # ── 2. Input Instruksi ─────────────────────────────────────────────────
    console.print("\n[bold]Step 2: Input Instruksi (Opsional) | Ketik /profile_name.txt untuk me-load profil[/bold]")
    instruction = ""
    instruction_lines = []
    
    try:
        first_line = input("  > ").strip()
        if first_line.startswith("/"):
            # Load dari folder instructions
            prof_file = first_line.lstrip("/")
            if not prof_file.endswith(".txt"): prof_file += ".txt"
            prof_path = Path("instructions") / prof_file
            if prof_path.exists():
                instruction = prof_path.read_text(encoding='utf-8')
                console.print(f"[green]✓ Profil instruksi dimuat dari {prof_path}[/green]")
            else:
                console.print(f"[red]✖ Profil instruksi {prof_path} tidak ditemukan. Menggunakan instruksi kosong.[/red]")
        else:
            if first_line.lower() != 'selesai' and first_line != '':
                instruction_lines.append(first_line)
                while True:
                    line = input("  > ")
                    if line.strip().lower() == 'selesai':
                        break
                    if not line.strip() and not instruction_lines:
                        break # Kosong di enter pertama
                    if not line.strip() and instruction_lines and not instruction_lines[-1].strip():
                        break # Dua kali enter kosong berturut-turut
                    instruction_lines.append(line)
                instruction = "\n".join(instruction_lines).strip()
    except EOFError:
        pass

    # ── Eksekusi ──────────────────────────────────────────────────────────
    console.print("\n[bold green]=== MEMULAI GENERASI TEST SCENARIOS ===[/bold green]\n")
    
    for url in urls_to_process:
        process_single_url(url, instruction, csv_sep)
        console.print("\n")

    console.print("[bold green]Semua Tugas Selesai![/bold green]")


if __name__ == "__main__":
    main()
