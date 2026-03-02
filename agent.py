"""
QA AI Agent - Page Scope Analyzer and Test Scenario Generator
1. Input URL
2. Optional Session Auth
3. Optional Instructions
4. Scan Page
5. Analyze Page Scope
6. Generate Test Scenarios
"""

import argparse
import json
import os
import sys
from pathlib import Path

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from dotenv import load_dotenv
from rich import box
from rich.console import Console
from rich.panel import Panel
from rich.rule import Rule

from core.ai_engine import AIEngine
from core.artifacts import execution_debug_path, execution_learning_path, execution_results_path, json_artifact_path
from core.confidence import compute_composite_confidence
from core.executor import CodeGenerator
from core.guardrails import validate_execution_plan
from core.planner import build_execution_plan, build_normalized_page_model, save_json_artifact
from core.result_analyzer import analyze_execution_results, save_execution_summary
from core.run_context import merge_recrawl_project_info
from core.scanner import Scanner
from core.site_profiles import enrich_site_profile_with_clusters, load_site_profile, merge_execution_learning

load_dotenv()

console = Console(force_terminal=True)
ai: AIEngine | None = None
runner = Scanner()

Path("instructions").mkdir(exist_ok=True)

auth_dir = Path("auth")
auth_dir.mkdir(exist_ok=True)
sample_auth_file = auth_dir / "sample_auth_state.json"
if not sample_auth_file.exists():
    sample_auth_file.write_text(
        '{ "cookies": [ { "name": "session_id", "value": "xxxx", "domain": ".namadomain.com", "path": "/" } ] }',
        encoding="utf-8",
    )


def process_single_url(
    url: str,
    instruction: str,
    csv_sep: str,
    use_auth: bool,
    crawl_limit: int,
    adaptive_recrawl: bool,
    execute_now: bool | None = None,
    executor_headless: bool = True,
):
    engine = get_ai()
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    site_profile = load_site_profile(url)

    engine.last_scope_validation = {}
    engine.last_scenario_validation = {}

    console.print(Rule(f"[bold]Scan Halaman: {url}[/bold]"))
    with console.status(
        f"[bold yellow]Menjalankan browser headless, memindai halaman, dan membaca beberapa internal link relevan dari {url}...[/bold yellow]",
        spinner="dots",
    ):
        project_info, page_info, _ = runner.scan_website(url, use_auth, crawl_limit=crawl_limit, site_profile=site_profile)

    try:
        page_scope = analyze_page_scope_with_retry(
            url=url,
            project_info=project_info,
            page_info=page_info,
            instruction=instruction,
            use_auth=use_auth,
            crawl_limit=crawl_limit,
            adaptive_recrawl=adaptive_recrawl,
        )
        page_model = build_normalized_page_model(page_info)
        site_profile = enrich_site_profile_with_clusters(site_profile, page_model=page_model, page_scope=page_scope)
        page_info["site_profile"] = site_profile
        page_model["site_profile"] = site_profile
        page_model_path = save_json_artifact(
            page_model,
            json_artifact_path(
                project_info["run_dir"],
                f"Normalized_Page_Model_{project_info['safe_name']}_{project_info['timestamp']}.json",
            ),
        )
        site_profile_path = save_json_artifact(
            site_profile,
            json_artifact_path(
                project_info["run_dir"],
                f"Site_Profile_{project_info['safe_name']}_{project_info['timestamp']}.json",
            ),
        )
        if engine.last_scope_validation:
            scope_validation_path = save_json_artifact(
                {
                    "issues": engine.last_scope_validation.get("issues", []),
                    "allowed_vocabulary": engine.last_scope_validation.get("allowed_vocabulary", {}),
                },
                json_artifact_path(
                    project_info["run_dir"],
                    f"Page_Scope_Validation_{project_info['safe_name']}_{project_info['timestamp']}.json",
                ),
            )
        else:
            scope_validation_path = None

        console.print(f"  [green][OK][/green] Page type: [bold]{page_scope.get('page_type', '-') }[/bold]")
        console.print(f"  [dim]Primary goal: {page_scope.get('primary_goal', '-') }[/dim]")
        console.print(f"  [dim]Confidence: {page_scope.get('confidence', 0)}[/dim]")
        if page_scope.get("key_modules"):
            console.print(f"  [dim]Key modules: {', '.join(page_scope['key_modules'][:6])}[/dim]")
        if page_scope.get("scope_summary"):
            console.print(f"  [dim]Scope summary: {page_scope['scope_summary']}[/dim]")
        if page_info.get("crawled_pages"):
            console.print(f"  [dim]Linked pages sampled: {len(page_info['crawled_pages'])}[/dim]")
            for item in page_info["crawled_pages"][:5]:
                console.print(f"  [dim]- {item.get('url', '')}[/dim]")
        scope_json_path = runner.save_page_scope(page_scope, project_info)
        crawled_json_path = runner.save_crawled_pages(page_info, project_info)
        raw_scan_json_path = runner.save_raw_scan(page_info, project_info)
        console.print(f"  [green][OK][/green] Scope analysis disimpan: [dim]{scope_json_path}[/dim]")
        console.print(f"  [green][OK][/green] Daftar linked pages disimpan: [dim]{crawled_json_path}[/dim]")
        console.print(f"  [green][OK][/green] Raw scan disimpan: [dim]{raw_scan_json_path}[/dim]")
        console.print(f"  [green][OK][/green] Normalized page model disimpan: [dim]{page_model_path}[/dim]")
        console.print(f"  [green][OK][/green] Site profile disimpan: [dim]{site_profile_path}[/dim]")
        if scope_validation_path:
            console.print(f"  [green][OK][/green] Validasi scope disimpan: [dim]{scope_validation_path}[/dim]")

        console.print(Rule(f"[bold]Generate Test Scenarios (CSV): {url}[/bold]"))
        with console.status(
            f"[bold green]AI ({engine.current_model}) sedang menyusun test scenario berdasarkan scope halaman...[/bold green]"
        ):
            parsed_data = engine.generate_test_scenarios(
                url=url,
                website_title=project_info["title"],
                page_info=page_info,
                page_model=page_model,
                page_scope=page_scope,
                custom_instruction=instruction,
                csv_sep=csv_sep,
            )
            saved_csv_path = runner.save_csv_scenarios(parsed_data, project_info, csv_sep)
            console.print(f"  [green][OK][/green] CSV berhasil dibuat: [dim]{saved_csv_path}[/dim]")
            if engine.last_scenario_validation:
                scenario_validation_path = save_json_artifact(
                    {
                        "issues": engine.last_scenario_validation.get("issues", []),
                        "rejected_cases": engine.last_scenario_validation.get("rejected_cases", []),
                        "allowed_vocabulary": engine.last_scenario_validation.get("allowed_vocabulary", {}),
                    },
                    json_artifact_path(
                        project_info["run_dir"],
                        f"Scenario_Validation_{project_info['safe_name']}_{project_info['timestamp']}.json",
                    ),
                )
                console.print(f"  [green][OK][/green] Validasi scenario disimpan: [dim]{scenario_validation_path}[/dim]")

            execution_plan = build_execution_plan(parsed_data, page_model, page_info.get("url", url), site_profile=site_profile)
            execution_plan_validation = validate_execution_plan(execution_plan, page_model, page_info)
            validation_path = save_json_artifact(
                {
                    "issues": execution_plan_validation["issues"],
                    "rejected_plans": execution_plan_validation["rejected_plans"],
                    "valid_plan_count": len(execution_plan_validation["valid_plan"].get("plans", [])),
                },
                json_artifact_path(
                    project_info["run_dir"],
                    f"Execution_Plan_Validation_{project_info['safe_name']}_{project_info['timestamp']}.json",
                ),
            )
            execution_plan = execution_plan_validation["valid_plan"]
            if not execution_plan.get("plans"):
                raise ValueError(
                    "Semua execution plan ditolak guardrail. Periksa JSON/Execution_Plan_Validation_*.json untuk detail."
                )
            composite_confidence = compute_composite_confidence(
                page_scope=page_scope,
                page_info=page_info,
                page_model=page_model,
                scope_validation=engine.last_scope_validation,
                scenario_validation=engine.last_scenario_validation,
                execution_plan_validation=execution_plan_validation,
            )
            page_scope["confidence"] = composite_confidence["score"]
            page_scope["confidence_breakdown"] = composite_confidence["breakdown"]
            runner.save_page_scope(page_scope, project_info)
            execution_plan_path = save_json_artifact(
                execution_plan,
                json_artifact_path(
                    project_info["run_dir"],
                    f"Execution_Plan_{project_info['safe_name']}_{project_info['timestamp']}.json",
                ),
            )
            console.print(f"  [green][OK][/green] Execution plan dibuat: [dim]{execution_plan_path}[/dim]")
            console.print(f"  [green][OK][/green] Validation plan disimpan: [dim]{validation_path}[/dim]")

            console.print("  [dim][>] Menyusun ringkasan eksekutif...[/dim]")
            md_summary = engine.generate_executive_summary(url, project_info["title"], page_info, parsed_data)
            saved_md_path = runner.save_executive_summary(md_summary, project_info)
            console.print(f"  [green][OK][/green] Ringkasan eksekutif dibuat: [dim]{saved_md_path}[/dim]")

            executor = CodeGenerator(engine)
            pom_script = executor.generate_pom_script(project_info, execution_plan_path, headless=executor_headless)

        if pom_script:
            console.print(f"\n[cyan]  [!] Script executor berhasil dibuat di:[/cyan] [bold]{pom_script}[/bold]")
            if execute_now is None:
                console.print(
                    "\n[bold yellow]Apakah anda ingin menjalankan executor sekarang untuk mengumpulkan video evidence?[/bold yellow]"
                )
                console.print("  [1] Ya, jalankan sekarang")
                console.print("  [2] Tidak, simpan script saja")
                execute_choice = console.input("  [cyan]Pilih [1/2] (Default: 2): [/cyan]").strip()
                should_execute = execute_choice == "1"
            else:
                should_execute = execute_now

            if should_execute:
                console.print("[bold green][>] Menjalankan script executor...[/bold green]")
                import subprocess

                try:
                    subprocess.run([sys.executable, pom_script.name], cwd=pom_script.parent, check=True)
                    console.print("\n[bold green][OK] Eksekusi otomatisasi selesai.[/bold green]")
                    results_path = execution_results_path(project_info["run_dir"])
                    if results_path.exists():
                        summary = analyze_execution_results(results_path)
                        summary_path = save_execution_summary(results_path, summary)
                        runner.update_csv_with_execution_results(saved_csv_path, results_path, csv_sep)
                        debug_path = execution_debug_path(project_info["run_dir"])
                        learning_path = execution_learning_path(project_info["run_dir"])
                        console.print(f"[green][OK][/green] Ringkasan eksekusi dibuat: [dim]{summary_path}[/dim]")
                        console.print(f"[green][OK][/green] CSV diperbarui dengan hasil eksekusi: [dim]{saved_csv_path}[/dim]")
                        if debug_path.exists():
                            console.print(f"[green][OK][/green] Debug eksekusi disimpan: [dim]{debug_path}[/dim]")
                        if learning_path.exists():
                            learned_profile_info = merge_execution_learning(
                                url,
                                json.loads(learning_path.read_text(encoding="utf-8")),
                                knowledge_context={"page_model": page_model, "page_scope": page_scope},
                            )
                            if learned_profile_info:
                                console.print(
                                    f"[green][OK][/green] Global knowledge bank diperbarui: "
                                    f"[dim]{learned_profile_info['global_path']}[/dim]"
                                )
                                console.print(
                                    f"[green][OK][/green] Domain learning diperbarui: "
                                    f"[dim]{learned_profile_info['domain_path']}[/dim]"
                                )
                                if learned_profile_info.get("cluster_paths"):
                                    console.print(
                                        f"[green][OK][/green] Cluster learning diperbarui: "
                                        f"[dim]{', '.join(learned_profile_info['cluster_paths'][:3])}[/dim]"
                                    )
                except subprocess.CalledProcessError as exc:
                    console.print(f"\n[bold red][ERR] Executor gagal dijalankan: {exc}[/bold red]")
            else:
                console.print("[dim]  Eksekusi dilewati. Script bisa dijalankan manual nanti.[/dim]")

    except Exception as exc:
        import traceback

        console.print(f"\n[bold red][ERR] Error pada proses AI/generasi untuk {url}:[/bold red] {exc}")
        console.print(f"[red]{traceback.format_exc()}[/red]")


def analyze_page_scope_with_retry(
    url: str,
    project_info: dict,
    page_info: dict,
    instruction: str,
    use_auth: bool,
    crawl_limit: int,
    adaptive_recrawl: bool,
) -> dict:
    engine = get_ai()
    console.print(Rule(f"[bold]AI Analisa Scope Halaman: {url}[/bold]"))
    with console.status(
        f"[bold green]AI ({engine.current_model}) sedang membaca konteks halaman dan menentukan scope testing...[/bold green]"
    ):
        page_scope = engine.analyze_page_scope(
            url=url,
            website_title=project_info["title"],
            page_info=page_info,
            page_model=build_normalized_page_model(page_info),
            custom_instruction=instruction,
        )

    current_confidence = float(page_scope.get("confidence", 0) or 0)
    if not adaptive_recrawl or current_confidence >= 0.55 or crawl_limit >= 5:
        return page_scope

    save_json_artifact(
        page_scope,
        json_artifact_path(
            project_info["run_dir"],
            f"Page_Scope_Before_Recrawl_{project_info['safe_name']}_{project_info['timestamp']}.json",
        ),
    )

    expanded_limit = min(max(crawl_limit + 2, 3), 5)
    console.print(
        f"  [yellow][i][/yellow] Confidence rendah ({current_confidence}). Mencoba scan ulang dengan linked page lebih banyak ({expanded_limit})."
    )
    with console.status(
        f"[bold yellow]Menjalankan adaptive recrawl untuk memperkaya konteks halaman...[/bold yellow]",
        spinner="dots",
    ):
        refreshed_project_info, refreshed_page_info, _ = runner.scan_website(
            url,
            use_auth,
            crawl_limit=expanded_limit,
            site_profile=project_info.get("site_profile"),
        )

    merge_recrawl_project_info(project_info, refreshed_project_info)
    page_info.clear()
    page_info.update(refreshed_page_info)

    with console.status(
        f"[bold green]AI ({engine.current_model}) sedang menganalisa ulang scope dari konteks yang diperluas...[/bold green]"
    ):
        page_scope = engine.analyze_page_scope(
            url=url,
            website_title=project_info["title"],
            page_info=page_info,
            page_model=build_normalized_page_model(page_info),
            custom_instruction=instruction,
        )
    save_json_artifact(
        page_scope,
        json_artifact_path(
            project_info["run_dir"],
            f"Page_Scope_After_Recrawl_{project_info['safe_name']}_{project_info['timestamp']}.json",
        ),
    )
    return page_scope


def parse_args():
    parser = argparse.ArgumentParser(add_help=True)
    parser.add_argument("--url")
    parser.add_argument("--batch-file")
    parser.add_argument("--instruction", default="")
    parser.add_argument("--instruction-file")
    parser.add_argument("--csv-sep", choices=[",", ";"], default=",")
    parser.add_argument("--use-auth", action="store_true")
    parser.add_argument("--crawl-limit", type=int, choices=[1, 3, 5], default=3)
    parser.add_argument("--disable-adaptive-recrawl", action="store_true")
    parser.add_argument("--run-executor", action="store_true")
    parser.add_argument("--executor-headed", action="store_true")
    return parser.parse_args()


def _load_instruction(instruction: str, instruction_file: str | None) -> str:
    manual_instruction = instruction.strip()
    if instruction_file:
        file_instruction = Path(instruction_file).read_text(encoding="utf-8").strip()
        if manual_instruction:
            return f"{file_instruction}\n\n{manual_instruction}".strip()
        return file_instruction
    return manual_instruction


def get_ai() -> AIEngine:
    global ai
    if ai is None:
        ai = AIEngine()
    return ai


def main():
    args = parse_args()
    engine = get_ai()
    console.print(
        Panel(
            f"[bold cyan]QA Agent - Page Scope Analyzer & Test Scenario Generator[/bold cyan]\n"
            f"[dim]Model aktif: {engine.current_model} ({engine.current_rpm} RPM)[/dim]",
            border_style="cyan",
            box=box.DOUBLE_EDGE,
        )
    )

    if args.url or args.batch_file:
        if args.url and args.batch_file:
            raise ValueError("Gunakan salah satu: --url atau --batch-file, jangan keduanya sekaligus.")
        if args.batch_file:
            batch_path = Path(args.batch_file)
            if not batch_path.exists():
                raise FileNotFoundError(f"Batch file tidak ditemukan: {batch_path}")
            urls_to_process = [
                line.strip()
                for line in batch_path.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
        else:
            urls_to_process = [args.url]
        instruction = _load_instruction(args.instruction, args.instruction_file)
        csv_sep = ";" if args.csv_sep == ";" else ","
        use_auth = args.use_auth
        crawl_limit = args.crawl_limit
        adaptive_recrawl = not args.disable_adaptive_recrawl
        execute_now = args.run_executor
        executor_headless = not args.executor_headed
        console.print(
            f"[dim]Mode non-interaktif aktif untuk "
            f"{args.url or args.batch_file}[/dim]"
        )
    else:
        console.print("\n[bold]Step 1: Input URL / File Batch (.txt)[/bold]")
        url_input = console.input("  [cyan]Masukan URL target atau file .txt batch: [/cyan]").strip()

        if url_input.lower() in ["", "exit", "quit", "keluar"]:
            console.print("[dim]Sampai jumpa![/dim]")
            return

        urls_to_process = []
        if url_input.endswith(".txt") and os.path.exists(url_input):
            with open(url_input, "r", encoding="utf-8") as handle:
                urls_to_process = [line.strip() for line in handle if line.strip()]
            console.print(f"[bold green]Batch mode aktif: ditemukan {len(urls_to_process)} URL.[/bold green]")
        else:
            urls_to_process.append(url_input)

        console.print("\n[bold]Step 2: Format CSV[/bold]")
        console.print("  [1] Koma (,) - standar umum")
        console.print("  [2] Titik koma (;) - cocok untuk Excel regional tertentu")
        sep_choice = console.input("  [cyan]Pilih [1/2] (Default: 1): [/cyan]").strip()
        csv_sep = ";" if sep_choice == "2" else ","

        console.print("\n[bold]Step 3: Session Mode (Optional)[/bold]")
        console.print("  [1] Standard scan tanpa session")
        console.print("  [2] Gunakan session auth Playwright")
        auth_choice = console.input("  [cyan]Pilih [1/2] (Default: 1): [/cyan]").strip()
        use_auth = auth_choice == "2"

        if use_auth:
            console.print("\n[bold yellow][i] Cara memakai session auth Playwright:[/bold yellow]")
            console.print("  1. Simpan file session bernama [bold green]auth_state.json[/bold green] di folder [bold cyan]auth/[/bold cyan].")
            console.print("  2. Gunakan [dim]auth/sample_auth_state.json[/dim] sebagai template struktur file.")
            console.print("  3. Session ini akan dipakai saat scan dan saat executor dijalankan.")
            console.input("  [green]Tekan Enter jika file session sudah siap...[/green]")

        console.print("\n[bold]Step 4: Additional Instructions (Optional)[/bold]")
        console.print("[dim]Ketik /profile_name.txt untuk load profile dari folder instructions/[/dim]")
        instruction = ""
        instruction_lines = []

        try:
            first_line = input("  > ").strip()
            if first_line.startswith("/"):
                profile_name = first_line.lstrip("/")
                if not profile_name.endswith(".txt"):
                    profile_name += ".txt"
                profile_path = Path("instructions") / profile_name
                if profile_path.exists():
                    instruction = profile_path.read_text(encoding="utf-8")
                    console.print(f"[green][OK] Profil instruksi dimuat dari {profile_path}[/green]")
                else:
                    console.print(f"[red][ERR] Profil instruksi {profile_path} tidak ditemukan. Menggunakan instruksi kosong.[/red]")
            else:
                if first_line.lower() != "selesai" and first_line != "":
                    instruction_lines.append(first_line)
                    while True:
                        line = input("  > ")
                        if line.strip().lower() == "selesai":
                            break
                        if not line.strip() and not instruction_lines:
                            break
                        if not line.strip() and instruction_lines and not instruction_lines[-1].strip():
                            break
                        instruction_lines.append(line)
                    instruction = "\n".join(instruction_lines).strip()
        except EOFError:
            pass

        console.print("\n[bold]Step 5: Mini Crawl Depth[/bold]")
        console.print("  [1] Ringan - 1 linked page")
        console.print("  [2] Normal - 3 linked pages")
        console.print("  [3] Lebih luas - 5 linked pages")
        crawl_choice = console.input("  [cyan]Pilih [1/2/3] (Default: 2): [/cyan]").strip()
        crawl_limit = {"1": 1, "2": 3, "3": 5}.get(crawl_choice, 3)

        console.print("\n[bold]Step 6: Adaptive Recrawl[/bold]")
        console.print("  [1] Aktif - jika confidence rendah, agent akan scan ulang dengan linked pages lebih banyak")
        console.print("  [2] Nonaktif - gunakan hasil scan awal saja")
        adaptive_choice = console.input("  [cyan]Pilih [1/2] (Default: 1): [/cyan]").strip()
        adaptive_recrawl = adaptive_choice != "2"
        execute_now = None
        executor_headless = True

    console.print("\n[bold green]=== MEMULAI SCAN, ANALISA SCOPE, DAN GENERASI TEST SCENARIOS ===[/bold green]\n")
    for url in urls_to_process:
        process_single_url(
            url,
            instruction,
            csv_sep,
            use_auth,
            crawl_limit,
            adaptive_recrawl,
            execute_now=execute_now,
            executor_headless=executor_headless,
        )
        console.print("")

    console.print("[bold green]Semua proses selesai.[/bold green]")


if __name__ == "__main__":
    main()
