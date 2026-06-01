"""
CLI Tool - Command-line interface for AI SimTest.
Run simulations directly from the terminal.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import click
from rich.console import Console
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn
from rich.table import Table

from src.core.comparison_engine import ComparisonEngine
from src.core.comparison_models import ComparisonConfig, RegressionVerdict
from src.exporters.comparison_report import ComparisonReportGenerator

console = Console()


@click.group()
@click.version_option(version="0.2.0")
def main():
    """AI SimTest - Open-source AI simulation testing platform."""
    pass


# ============================================================
# simtest run
# ============================================================

@main.command()
@click.option("--bot-endpoint", required=True, help="API endpoint of the bot to test")
@click.option("--bot-api-key", default=None, help="API key for the bot")
@click.option("--bot-format", default="openai", help="Request format: openai, anthropic, custom")
@click.option("--mode", default="manual", type=click.Choice(["manual", "partial", "auto"]), help="Mode: manual | partial (AI derives from docs) | auto (endpoint only)")
@click.option("--doc-dir", default=None, help="Directory of bot documentation (for partial mode: txt, md, pdf, docx, html, json)")
@click.option("--documentation", default="", help="Inline documentation text for grounding")
@click.option("--doc-file", default=None, help="Path to documentation file (txt, md, json) for grounding judge")
@click.option("--success-criteria", default=None, multiple=True, help="Success criteria (can repeat: --success-criteria 'rule1' --success-criteria 'rule2')")
@click.option("--topics", default=None, help="Comma-separated test topics (e.g., 'refunds,shipping,billing')")
@click.option("--name", default="CLI Simulation Run", help="Name for this simulation run")
@click.option("--personas", default=20, help="Number of personas to generate")
@click.option("--max-turns", default=15, help="Max conversation turns")
@click.option("--min-turns", default=1, help="Min conversation turns before allowing early exit (default: 1)")
@click.option("--parallel", default=10, help="Max parallel conversations (lower = safer for rate-limited bots, e.g., 1-3)")
@click.option("--output", default="./reports", help="Output directory for reports")
@click.option("--export-formats", default="jsonl,csv,summary,html", help="Comma-separated export formats (jsonl,csv,summary,html,dpo)")
@click.option("--config-file", default=None, help="Path to JSON config file (overrides other options)")
@click.option("--preview-personas", is_flag=True, default=False, help="Generate and preview personas before running simulation")
@click.option("--pass-threshold", default=0.7, help="Score threshold for PASS (0.0-1.0, default 0.7)")
@click.option("--warn-threshold", default=0.5, help="Score threshold for WARNING vs FAIL (0.0-1.0, default 0.5)")
@click.option("--save-suite", "auto_save_suite", is_flag=True, default=False, help="Auto-save a regression suite from failures")
@click.option("--auto-approve", is_flag=True, default=False, help="Auto-approve all gates in partial mode (for CI/CD)")
@click.option("--analysis-only", is_flag=True, default=False, help="Run analysis stages only, skip simulation (partial mode)")
@click.option("--scenarios", default=None, help="Scenario templates to run (comma-separated IDs, category name, or 'all'). Use 'simtest scenarios' to list.")
@click.option("--stress-memory", is_flag=True, default=False, help="Enable context endurance / memory stress testing (long conversations with fact seeding, contradiction injection, progressive complexity)")
@click.option("--stress-turns", default=30, help="Target conversation turns for stress test (default: 30, min: 10)")
@click.option("--stress-patterns", default="", help="Comma-separated stress patterns: fact_seeding,contradiction,progressive_complexity (default: all)")
@click.option("--stress-facts", default=5, help="Number of facts to seed for recall testing (default: 5, max: 10)")
@click.option("--stress-contradictions", default=3, help="Number of contradiction pairs to inject (default: 3, max: 5)")
def run(
    bot_endpoint: str,
    bot_api_key: str | None,
    bot_format: str,
    mode: str,
    doc_dir: str | None,
    documentation: str,
    doc_file: str | None,
    success_criteria: tuple[str, ...],
    topics: str | None,
    name: str,
    personas: int,
    max_turns: int,
    min_turns: int,
    parallel: int,
    output: str,
    export_formats: str,
    config_file: str | None,
    preview_personas: bool,
    pass_threshold: float,
    warn_threshold: float,
    auto_save_suite: bool,
    auto_approve: bool,
    analysis_only: bool,
    scenarios: str | None,
    stress_memory: bool,
    stress_turns: int,
    stress_patterns: str,
    stress_facts: int,
    stress_contradictions: int,
):
    """Run a simulation test against your AI chatbot."""
    from src.core.logging import setup_logging
    setup_logging()

    console.print(Panel.fit(
        "[bold blue]AI SimTest[/] - Simulation Testing Platform",
        subtitle="v0.2.0",
    ))

    # ── Fully Autonomous Mode ─────────────────────────────────
    if mode == "auto":
        console.print(f"  🤖 Mode: [bold magenta]Fully Autonomous[/]")
        console.print(f"  🔍 Will discover bot purpose via exploratory conversations")
        console.print(f"  🎯 Bot endpoint: [bold]{bot_endpoint}[/]")
        console.print(f"  🔄 Max parallel conversations: [bold]{parallel}[/]")
        if auto_approve:
            console.print(f"  ⚡ Auto-approve: [bold green]ON[/] (CI/CD mode)")

        sim_name = name if name != "CLI Simulation Run" else "Fully Autonomous Simulation"

        asyncio.run(_run_full_autonomous(
            bot_endpoint=bot_endpoint,
            bot_api_key=bot_api_key,
            bot_format=bot_format,
            auto_approve=auto_approve,
            output_dir=output,
            export_formats=export_formats.split(","),
            simulation_name=sim_name,
            num_personas=personas if personas != 20 else None,
            max_turns=max_turns if max_turns != 15 else None,
            min_turns=min_turns,
            max_parallel=parallel,
            pass_threshold=pass_threshold,
            warn_threshold=warn_threshold,
        ))
        return

    # ── Partial Autonomous Mode ──────────────────────────────
    if mode == "partial":
        if not doc_dir and not doc_file:
            console.print("[red]Error: Partial mode requires --doc-dir or --doc-file[/]")
            console.print("  Usage: simtest run --mode partial --doc-dir ./docs/ --bot-endpoint http://bot/api")
            sys.exit(1)

        console.print(f"  🤖 Mode: [bold cyan]Partial Autonomous[/]")
        if doc_dir:
            console.print(f"  📁 Documentation directory: [bold]{doc_dir}[/]")
        if doc_file:
            console.print(f"  📄 Documentation file: [bold]{doc_file}[/]")
        if auto_approve:
            console.print(f"  ⚡ Auto-approve: [bold green]ON[/] (CI/CD mode)")
        if analysis_only:
            console.print(f"  🔍 Analysis only: [bold yellow]ON[/] (no simulation)")
        console.print(f"  🔄 Max parallel conversations: [bold]{parallel}[/]")

        doc_files_list = [doc_file] if doc_file else None
        sim_name = name if name != "CLI Simulation Run" else "Partial Autonomous Simulation"

        asyncio.run(_run_partial_autonomous(
            bot_endpoint=bot_endpoint,
            bot_api_key=bot_api_key,
            bot_format=bot_format,
            doc_dir=doc_dir,
            doc_files=doc_files_list,
            auto_approve=auto_approve,
            analysis_only=analysis_only,
            output_dir=output,
            export_formats=export_formats.split(","),
            simulation_name=sim_name,
            num_personas=personas,
            max_turns=max_turns,
            min_turns=min_turns,
            max_parallel=parallel,
        ))
        return

    # ── Manual Mode (existing behavior) ──────────────────────
    console.print(f"  🤖 Mode: [bold]Manual[/]")

    # Load documentation from file if path provided
    doc_text = documentation
    if doc_file:
        doc_path = Path(doc_file)
        if doc_path.exists():
            doc_text = doc_path.read_text(encoding="utf-8")
            console.print(f"  📄 Loaded documentation from [bold]{doc_file}[/] ({len(doc_text)} chars)")
        else:
            console.print(f"  [red]⚠ Documentation file not found: {doc_file}[/]")
    elif documentation and Path(documentation).exists():
        doc_text = Path(documentation).read_text(encoding="utf-8")

    # Parse success criteria
    criteria_list = list(success_criteria) if success_criteria else []

    # Parse topics
    topics_list = [t.strip() for t in topics.split(",") if t.strip()] if topics else []

    # Load config from file if provided
    if config_file:
        config_path = Path(config_file)
        if not config_path.exists():
            console.print(f"[red]Error: Config file not found: {config_file}[/]")
            sys.exit(1)

        # Strip comments (lines starting with # or //)
        raw = config_path.read_text(encoding="utf-8")
        clean_lines = [
            line for line in raw.splitlines()
            if not line.strip().startswith("#") and not line.strip().startswith("//")
        ]
        config_data = json.loads("\n".join(clean_lines))

        # Config file values as defaults, CLI takes precedence
        bot_endpoint = bot_endpoint or config_data.get("bot_endpoint", "")
        bot_api_key = bot_api_key or config_data.get("bot_api_key")
        bot_format = bot_format or config_data.get("bot_request_format", "openai")
        doc_text = doc_text or config_data.get("documentation", "")
        name = name if name != "CLI Simulation Run" else config_data.get("name", name)
        personas = personas if personas != 20 else config_data.get("num_personas", 20)
        max_turns = max_turns if max_turns != 15 else config_data.get("max_turns", 15)

        if not criteria_list:
            criteria_list = config_data.get("success_criteria", [])
        if not topics_list:
            topics_list = config_data.get("topics", [])

        console.print(f"  📋 Loaded config from [bold]{config_file}[/]")

    # Show what we're testing with
    if criteria_list:
        console.print(f"  ✅ Success criteria: {len(criteria_list)} rules")
    if topics_list:
        console.print(f"  🎯 Test topics: {', '.join(topics_list)}")
    if doc_text:
        console.print(f"  📖 Documentation: {len(doc_text)} chars loaded")

    # Resolve scenario templates
    scenario_ids = []
    if scenarios:
        from src.scenarios import ScenarioLibrary
        lib = ScenarioLibrary()
        lib.load_built_in()
        try:
            scenario_ids = lib.resolve_scenario_ids(scenarios)
            console.print(f"  🎭 Scenarios: [bold]{', '.join(scenario_ids)}[/] ({len(scenario_ids)} templates)")
        except ValueError as e:
            console.print(f"  [red]⚠ {e}[/]")
            console.print("  [dim]Use 'simtest scenarios' to list available templates[/]")
            sys.exit(1)

    # Build endurance / memory stress config
    endurance_config = None
    if stress_memory:
        from src.endurance import EnduranceConfig, StressPattern

        # Parse patterns
        patterns = list(StressPattern)
        if stress_patterns:
            pattern_map = {
                "fact_seeding": StressPattern.FACT_SEEDING,
                "contradiction": StressPattern.CONTRADICTION,
                "progressive_complexity": StressPattern.PROGRESSIVE_COMPLEXITY,
            }
            parsed = [
                pattern_map[p.strip().lower()]
                for p in stress_patterns.split(",")
                if p.strip().lower() in pattern_map
            ]
            if parsed:
                patterns = parsed

        # Calculate windows based on target turns
        seed_end = max(3, stress_turns // 6)
        recall_start = max(seed_end + 3, stress_turns // 2)
        recall_end = min(stress_turns - 2, int(stress_turns * 0.85))

        endurance_config = EnduranceConfig(
            patterns=patterns,
            target_turns=stress_turns,
            num_facts=min(stress_facts, 10),
            seed_window=(1, seed_end),
            recall_window=(recall_start, recall_end),
            num_contradictions=min(stress_contradictions, 5),
        )

        # Validate
        issues = endurance_config.validate()
        if issues:
            for issue in issues:
                console.print(f"  [red]⚠ Stress config: {issue}[/]")
            sys.exit(1)

        pattern_names = ", ".join(p.value for p in patterns)
        console.print(f"  🧠 Memory stress: [bold magenta]ON[/] ({stress_turns} turns, patterns: {pattern_names})")
        console.print(f"     Facts: {endurance_config.num_facts} | Contradictions: {endurance_config.num_contradictions}")

    asyncio.run(_run_simulation(
        bot_endpoint=bot_endpoint,
        bot_api_key=bot_api_key,
        bot_format=bot_format,
        documentation=doc_text,
        success_criteria=criteria_list,
        topics=topics_list,
        name=name,
        num_personas=personas,
        max_turns=max_turns,
        min_turns=min_turns,
        max_parallel=parallel,
        output_dir=output,
        export_formats=export_formats.split(","),
        preview_personas=preview_personas,
        pass_threshold=pass_threshold,
        warn_threshold=warn_threshold,
        auto_save_suite=auto_save_suite,
        scenario_ids=scenario_ids,
        endurance_config=endurance_config,
    ))


# ============================================================
# Partial Autonomous Mode Runner
# ============================================================

async def _run_partial_autonomous(
    bot_endpoint: str,
    bot_api_key: str | None,
    bot_format: str,
    doc_dir: str | None,
    doc_files: list[str] | None,
    auto_approve: bool,
    analysis_only: bool,
    output_dir: str,
    export_formats: list[str],
    simulation_name: str,
    num_personas: int | None = None,
    max_turns: int | None = None,
    min_turns: int = 1,
    max_parallel: int = 3,
):
    """Run the partial autonomous pipeline."""
    from src.core.autonomous_orchestrator import AutonomousOrchestrator

    orch = AutonomousOrchestrator(
        bot_endpoint=bot_endpoint,
        doc_dir=doc_dir,
        doc_files=doc_files,
        bot_api_key=bot_api_key,
        bot_format=bot_format,
        auto_approve=auto_approve,
        output_dir=output_dir,
        export_formats=export_formats,
        simulation_name=simulation_name,
        num_personas=num_personas,
        max_turns=max_turns,
        min_turns=min_turns,
        max_parallel=max_parallel,
    )

    if analysis_only:
        console.print("\n[bold]Running analysis pipeline (no simulation)...[/]\n")
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            console=console,
        ) as progress:
            task = progress.add_task("Analyzing documents...", total=None)
            result = await orch.run_analysis_only()

        if result.aborted:
            console.print(f"\n[red]❌ Pipeline aborted: {result.abort_reason}[/]")
            return

        _print_analysis_results(result)
    else:
        console.print("\n[bold]Running full partial autonomous pipeline...[/]\n")
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            console=console,
        ) as progress:
            task = progress.add_task("Running pipeline...", total=None)
            result = await orch.run()

        if result.aborted:
            console.print(f"\n[red]❌ Pipeline aborted: {result.abort_reason}[/]")
            return

        _print_analysis_results(result)

        if result.simulation_report:
            _print_report(result.simulation_report, {})
            console.print(f"\n  📁 Results exported to: [bold]{output_dir}[/]")


# ============================================================
# Fully Autonomous Mode Runner
# ============================================================

async def _run_full_autonomous(
    bot_endpoint: str,
    bot_api_key: str | None,
    bot_format: str,
    auto_approve: bool,
    output_dir: str,
    export_formats: list[str],
    simulation_name: str,
    num_personas: int | None = None,
    max_turns: int | None = None,
    min_turns: int = 1,
    max_parallel: int = 3,
    pass_threshold: float = 0.7,
    warn_threshold: float = 0.5,
):
    """Run the fully autonomous pipeline: discovery → approval → partial pipeline."""
    from src.core.llm_client import LLMClientFactory
    from src.simulators.conversation_simulator import TargetBotClient
    from src.models import BotConfig
    from src.core.full_auto_orchestrator import FullAutoOrchestrator

    # Build bot client
    bot_config = BotConfig(
        api_endpoint=bot_endpoint,
        api_key=bot_api_key,
        request_format=bot_format or "openai",
    )
    bot_client = TargetBotClient(bot_config)

    # Build LLM client for discovery (creative role, temp=0.9)
    llm_client = LLMClientFactory.persona_generator()

    # Build approval gate
    if auto_approve:
        from src.core.approval_gate import AutoApprovalGate
        approval_gate = AutoApprovalGate()
    else:
        from src.core.approval_gate import CLIApprovalGate
        approval_gate = CLIApprovalGate()

    # Create the orchestrator with FullAutoOrchestrator's actual API
    orch = FullAutoOrchestrator(
        bot_client=bot_client,
        llm_client=llm_client,
        approval_gate=approval_gate,
        auto_approve=auto_approve,
        max_discovery_turns=12,
        num_personas=num_personas,
        max_turns=max_turns,
        output_dir=output_dir,
        export_formats=",".join(export_formats) if isinstance(export_formats, list) else export_formats,
        pass_threshold=pass_threshold,
        warn_threshold=warn_threshold,
    )

    console.print("\n[bold]Running fully autonomous pipeline...[/]")
    console.print("  Stage 0: Bot Discovery (exploratory conversation)")
    console.print("  Stage 1-5: Partial Autonomous Pipeline (if discovery succeeds)\n")

    # Stage 0: Discovery + Approval (handled by FullAutoOrchestrator)
    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        console=console,
    ) as progress:
        task = progress.add_task("Discovering bot capabilities...", total=None)
        result = await orch.run()

    # Stage 1-5: If discovery succeeded, wire the handoff to partial pipeline
    if result.success and result.final_context:
        ctx = result.final_context
        console.print("\n[bold green]✅ Bot discovery succeeded![/]")
        console.print(f"  Bot: {ctx.bot_name} | Domain: {ctx.domain}")
        if ctx.capabilities:
            console.print(f"  Capabilities: {', '.join(ctx.capabilities[:5])}")
        confidence = ctx.overall_confidence.score if hasattr(ctx.overall_confidence, 'score') else 0
        console.print(f"  Confidence: {confidence:.0%} ({getattr(ctx, 'quality_level', 'unknown')})")
        console.print(f"\n[bold]Handing off to simulation pipeline...[/]\n")

        documentation = ctx.to_documentation()

        try:
            import tempfile
            import os
            from src.core.autonomous_orchestrator import AutonomousOrchestrator

            # Write discovered documentation to a temp file so AutonomousOrchestrator
            # can process it through its normal doc analysis pipeline
            os.makedirs(output_dir, exist_ok=True)
            doc_path = os.path.join(output_dir, "discovered_documentation.md")
            with open(doc_path, "w", encoding="utf-8") as f:
                f.write(documentation)
            console.print(f"  📄 Discovered docs saved to: [dim]{doc_path}[/]")

            partial_orch = AutonomousOrchestrator(
                bot_endpoint=bot_endpoint,
                doc_files=[doc_path],
                bot_api_key=bot_api_key,
                bot_format=bot_format,
                auto_approve=auto_approve,
                output_dir=output_dir,
                export_formats=export_formats,
                simulation_name=simulation_name,
                num_personas=num_personas,
                max_turns=max_turns,
                min_turns=min_turns,
                max_parallel=max_parallel,
            )

            with Progress(
                SpinnerColumn(),
                TextColumn("[progress.description]{task.description}"),
                console=console,
            ) as progress:
                task = progress.add_task("Running simulation pipeline...", total=None)
                sim_result = await partial_orch.run()

            # Store simulation result back into the discovery result for unified printing
            result.simulation_report = sim_result

            if hasattr(sim_result, 'aborted') and sim_result.aborted:
                console.print(f"\n[yellow]⚠ Simulation aborted: {sim_result.abort_reason}[/]")
            elif hasattr(sim_result, 'simulation_report') and sim_result.simulation_report:
                _print_report(sim_result.simulation_report, {})
                console.print(f"\n  📁 Results exported to: [bold]{output_dir}[/]")
            else:
                console.print(f"\n[green]✅ Simulation pipeline completed.[/]")
                console.print(f"  📁 Results exported to: [bold]{output_dir}[/]")

        except Exception as e:
            console.print(f"\n[red]❌ Simulation pipeline error: {e}[/]")
            console.print("[yellow]Discovery was successful. You can retry with partial mode:[/]")
            console.print(f"  simtest run --mode partial --doc-dir <docs> --bot-endpoint {bot_endpoint}")

        console.print(f"\n  ⏱ Total execution time: {result.total_execution_time:.1f}s")
        console.print("=" * 60)
    else:
        # Discovery failed or was rejected — use existing result printer
        _print_auto_result(result, output_dir)


def _print_auto_result(result, output_dir: str):
    """Display the fully autonomous mode results."""
    console.print("\n" + "=" * 60)
    console.print("[bold magenta]🤖 Fully Autonomous Mode Results[/]")
    console.print("=" * 60)

    status = getattr(result, "status", "unknown")
    execution_time = getattr(result, "execution_time", 0.0) or getattr(result, "total_execution_time", 0.0)

    # Discovery summary
    attempt1 = getattr(result, "discovery_attempt1", None) or getattr(result, "discovery_result", None)
    attempt2 = getattr(result, "discovery_attempt2", None) or getattr(result, "retry_result", None)
    context = getattr(result, "discovered_context", None) or getattr(result, "final_context", None)

    if attempt1 and hasattr(attempt1, "context"):
        ctx = attempt1.context
        console.print(f"\n[bold]Discovery Attempt 1:[/]")
        console.print(f"  Bot Name: {ctx.bot_name}")
        console.print(f"  Domain: {ctx.domain}")
        if ctx.bot_description:
            desc = ctx.bot_description[:120] + "..." if len(ctx.bot_description) > 120 else ctx.bot_description
            console.print(f"  Description: {desc}")
        if ctx.capabilities:
            console.print(f"  Capabilities: {', '.join(ctx.capabilities[:5])}")
        confidence = ctx.overall_confidence.score if hasattr(ctx.overall_confidence, "score") else 0
        quality = getattr(ctx, "quality_level", "unknown")
        color = "green" if confidence >= 0.6 else "yellow" if confidence >= 0.3 else "red"
        console.print(f"  Confidence: [{color}]{confidence:.0%} ({quality})[/]")

    if attempt2 and hasattr(attempt2, "context"):
        ctx2 = attempt2.context
        console.print(f"\n[bold]Discovery Attempt 2 (Cross-examination):[/]")
        console.print(f"  Bot Name: {ctx2.bot_name}")
        console.print(f"  Domain: {ctx2.domain}")
        confidence2 = ctx2.overall_confidence.score if hasattr(ctx2.overall_confidence, "score") else 0
        quality2 = getattr(ctx2, "quality_level", "unknown")
        color2 = "green" if confidence2 >= 0.6 else "yellow" if confidence2 >= 0.3 else "red"
        console.print(f"  Confidence: [{color2}]{confidence2:.0%} ({quality2})[/]")

    # Mismatch report
    mismatch = getattr(result, "mismatch_report", None)
    if mismatch and hasattr(mismatch, "has_mismatches") and mismatch.has_mismatches:
        console.print(f"\n[bold red]⚠ Mismatches Detected:[/]")
        if hasattr(mismatch, "severity"):
            console.print(f"  Severity: {mismatch.severity}")
        if hasattr(mismatch, "to_report_section"):
            for line in mismatch.to_report_section().split("\n")[:8]:
                console.print(f"  {line}")

    # Final outcome
    if status == "completed":
        console.print(f"\n[bold green]✅ Pipeline completed successfully![/]")

        sim_result = getattr(result, "simulation_result", None)
        if sim_result and hasattr(sim_result, "simulation_report") and sim_result.simulation_report:
            _print_report(sim_result.simulation_report, {})
        elif sim_result and hasattr(sim_result, "aborted") and sim_result.aborted:
            console.print(f"[yellow]  Pipeline was aborted: {sim_result.abort_reason}[/]")

        console.print(f"\n  📁 Results exported to: [bold]{output_dir}[/]")

    elif status == "failed":
        failure_type = getattr(result, "failure_type", "unknown")
        console.print(f"\n[bold red]❌ Pipeline failed[/]")
        console.print(f"  Failure type: {failure_type}")

        error_msg = getattr(result, "error_message", "")
        if error_msg:
            console.print(f"  Error: {error_msg}")

        diag_path = getattr(result, "diagnostic_report_path", "")
        if diag_path:
            console.print(f"  📋 Diagnostic report: [bold]{diag_path}[/]")

        console.print("\n[bold yellow]Suggested actions:[/]")
        if failure_type == "no_response":
            console.print("  • Check the bot endpoint URL is correct and accessible")
            console.print("  • Verify there are no firewall or SSL issues (try --no-verify-ssl)")
        elif failure_type in ("confused", "inconsistent", "evasive"):
            console.print("  • Try --mode partial with --doc-dir to provide documentation")
            console.print("  • The bot may not expose enough about itself for auto-discovery")
        else:
            console.print("  • Try --mode partial with documentation for more control")
            console.print("  • Check the diagnostic report for details")

    elif status == "stopped":
        stopped_reason = getattr(result, "stopped_reason", "")
        console.print(f"\n[bold yellow]⚠ Pipeline stopped[/]")
        console.print(f"  Reason: {stopped_reason}")
        console.print("\n[bold yellow]Suggested actions:[/]")
        console.print("  • Try --mode partial with --doc-dir to provide documentation")
        console.print("  • Review the diagnostic report for what was discovered")

    else:
        # Handle FullAutoResult style (success field instead of status)
        success = getattr(result, "success", False)
        stopped_reason = getattr(result, "stopped_reason", "")
        diagnostic = getattr(result, "diagnostic_report", "")

        if success and context:
            console.print(f"\n[bold green]✅ Discovery succeeded — ready for testing![/]")
            console.print(f"\n  To run the simulation with discovered context:")
            console.print(f"  [dim]The partial pipeline would run automatically here.[/]")
        elif stopped_reason:
            console.print(f"\n[bold yellow]⚠ Pipeline stopped: {stopped_reason}[/]")
            if diagnostic:
                console.print(f"\n[bold]Diagnostic Report:[/]")
                for line in diagnostic.split("\n")[:15]:
                    console.print(f"  {line}")
            console.print("\n[bold yellow]Suggested actions:[/]")
            console.print("  • Try --mode partial with --doc-dir to provide documentation")
        else:
            console.print(f"\n[yellow]No clear outcome. Check logs for details.[/]")

    console.print(f"\n  ⏱ Total execution time: {execution_time:.1f}s")
    console.print("=" * 60)


def _print_analysis_results(result):
    """Display the analysis pipeline results."""
    from src.core.autonomous_orchestrator import AnalysisPipelineResult

    console.print("\n" + "=" * 60)
    console.print("[bold cyan]📊 Analysis Pipeline Results[/]")
    console.print("=" * 60)

    # Bot Context
    if result.bot_context:
        ctx = result.bot_context
        table = Table(title="Stage 1: Bot Context", show_header=False, box=None)
        table.add_column("Field", style="bold")
        table.add_column("Value")
        table.add_row("Bot Name", ctx.bot_name)
        table.add_row("Domain", ctx.domain)
        table.add_row("Purpose", ctx.purpose[:80] + "..." if len(ctx.purpose) > 80 else ctx.purpose)
        table.add_row("Capabilities", ", ".join(ctx.capabilities[:5]))
        table.add_row("Limitations", ", ".join(ctx.limitations[:3]))
        table.add_row("Confidence", f"[{'green' if ctx.confidence == 'high' else 'yellow'}]{ctx.confidence}[/]")
        console.print(table)

    # Success Criteria
    if result.criteria_set:
        console.print(f"\n[bold]Stage 2: Success Criteria[/] ({result.criteria_set.count} criteria)")
        for c in result.criteria_set.criteria[:5]:
            severity_color = "red" if c.importance == "high" else "yellow" if c.importance == "medium" else "white"
            console.print(f"  [{severity_color}]●[/] [{c.category}] {c.criterion}")
        if result.criteria_set.count > 5:
            console.print(f"  ... and {result.criteria_set.count - 5} more")

    # Guardrails
    if result.guardrail_set:
        console.print(f"\n[bold]Stage 3: Guardrail Rules[/] ({result.guardrail_set.count} rules, "
                      f"{len(result.guardrail_set.critical_rules)} critical)")
        for r in result.guardrail_set.rules[:5]:
            sev_color = "red" if r.severity == "critical" else "yellow" if r.severity == "high" else "white"
            console.print(f"  [{sev_color}]●[/] [{r.category}] {r.rule}")

    # Test Plan
    if result.test_plan:
        plan = result.test_plan
        console.print(f"\n[bold]Stage 4: Test Plan[/]")
        console.print(f"  Topics: {plan.topic_count} ({len(plan.high_risk_topics)} high-risk)")
        console.print(f"  Personas: {plan.persona_strategy.total_personas} "
                      f"({plan.persona_strategy.standard_pct}% standard, "
                      f"{plan.persona_strategy.edge_case_pct}% edge, "
                      f"{plan.persona_strategy.adversarial_pct}% adversarial)")
        console.print(f"  Turns: {plan.conversation_config.min_turns}-{plan.conversation_config.max_turns}")

        if plan.topics:
            topic_table = Table(title="Test Topics", show_lines=False)
            topic_table.add_column("Topic", style="bold")
            topic_table.add_column("Priority")
            topic_table.add_column("Risk")
            topic_table.add_column("Personas")
            for t in plan.topics:
                risk_color = "red" if t.risk_level == "high" else "yellow" if t.risk_level == "medium" else "green"
                topic_table.add_row(t.name, t.priority, f"[{risk_color}]{t.risk_level}[/]", str(t.estimated_personas))
            console.print(topic_table)

    # Gate summary
    if result.gate_manager:
        console.print(f"\n[bold]Approval Gates:[/] {result.gate_manager.summary}")

    console.print(f"\n  ⏱ Analysis time: {result.execution_time_seconds:.1f}s")
    console.print("=" * 60)


async def _run_simulation(
    bot_endpoint: str,
    bot_api_key: str | None,
    bot_format: str,
    documentation: str,
    success_criteria: list[str],
    topics: list[str],
    name: str,
    num_personas: int,
    max_turns: int,
    min_turns: int,
    max_parallel: int,
    output_dir: str,
    export_formats: list[str],
    preview_personas: bool = False,
    pass_threshold: float = 0.7,
    warn_threshold: float = 0.5,
    auto_save_suite: bool = False,
    scenario_ids: list[str] | None = None,
    endurance_config=None,
):
    """Async simulation runner."""
    from src.core.orchestrator import SimulationOrchestrator
    from src.models import BotConfig, SimulationConfig

    # Build documentation with topics if provided
    full_doc = documentation
    if topics:
        topics_text = "\n\nTest Focus Topics:\n" + "\n".join(f"- {t}" for t in topics)
        full_doc = (documentation + topics_text) if documentation else topics_text

    config = SimulationConfig(
        name=name,
        bot=BotConfig(
            api_endpoint=bot_endpoint,
            api_key=bot_api_key,
            request_format=bot_format,
        ),
        documentation=full_doc,
        success_criteria=success_criteria,
        num_personas=num_personas,
        max_turns_per_conversation=max_turns,
        min_turns_per_conversation=min_turns,
        max_parallel_conversations=max_parallel,
        pass_threshold=pass_threshold,
        warn_threshold=warn_threshold,
    )

    # ── Endurance / memory stress setup ───────────────────────
    # Must adjust config turns BEFORE creating orchestrator
    endurance_runner = None
    if endurance_config:
        from src.endurance import EnduranceRunner
        endurance_runner = EnduranceRunner(endurance_config)
        # Override min turns to ensure stress test has enough turns
        config.min_turns_per_conversation = max(
            config.min_turns_per_conversation,
            endurance_runner.get_min_turns(),
        )
        # Also raise max_turns if stress needs more
        if config.max_turns_per_conversation < endurance_config.target_turns:
            config.max_turns_per_conversation = endurance_config.target_turns

    orchestrator = SimulationOrchestrator(config)

    # ── Scenario template setup ────────────────────────────────
    scenario_runner = None
    if scenario_ids:
        from src.scenarios import ScenarioLibrary, ScenarioRunner
        lib = ScenarioLibrary()
        lib.load_built_in()
        scenario_runner = ScenarioRunner(lib)

    def _apply_scenarios_to_personas(personas_list):
        """Inject scenario and/or stress instructions into persona system prompts.

        If there are N scenarios and M personas, we distribute scenarios
        round-robin across personas so each scenario gets tested.
        Memory stress instructions are applied to ALL personas when enabled.
        """
        if not scenario_runner and not endurance_runner:
            return personas_list

        for i, persona in enumerate(personas_list):
            # Apply scenario (round-robin distribution)
            if scenario_runner and scenario_ids:
                scenario_id = scenario_ids[i % len(scenario_ids)]
                scenario = scenario_runner.library.get(scenario_id)

                if hasattr(persona, 'system_prompt') and persona.system_prompt:
                    persona.system_prompt = scenario_runner.apply_scenario_to_prompt(
                        persona.system_prompt, scenario
                    )

                if hasattr(config, 'min_turns_per_conversation'):
                    config.min_turns_per_conversation = max(
                        config.min_turns_per_conversation,
                        scenario.min_turns,
                    )

            # Apply memory stress instructions (all personas)
            if endurance_runner:
                if hasattr(persona, 'system_prompt') and persona.system_prompt:
                    persona.system_prompt = endurance_runner.apply_to_persona_prompt(
                        persona.system_prompt
                    )

        # Display what was applied
        if scenario_runner and scenario_ids:
            console.print(f"\n  🎭 Applied {len(scenario_ids)} scenario(s) to {len(personas_list)} personas")
            from collections import Counter
            dist = Counter(scenario_ids[i % len(scenario_ids)] for i in range(len(personas_list)))
            for sid, count in dist.most_common():
                console.print(f"     • {sid}: {count} persona(s)")

        if endurance_runner:
            pattern_names = ", ".join(p.value for p in endurance_config.patterns)
            console.print(f"\n  🧠 Applied memory stress to {len(personas_list)} personas")
            console.print(f"     Patterns: {pattern_names}")
            console.print(f"     Target turns: {endurance_config.target_turns} | Facts: {endurance_config.num_facts} | Contradictions: {endurance_config.num_contradictions}")

        return personas_list

    # Persona preview mode
    if preview_personas:
        console.print("\n[bold]Generating personas for preview...[/]\n")
        personas = await orchestrator.generate_personas_only()
        personas = _apply_scenarios_to_personas(personas)
        _print_persona_preview(personas)

        if not click.confirm("\nProceed with simulation?", default=True):
            console.print("[yellow]Simulation cancelled.[/]")
            return

        # Run with approved personas
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            console=console,
        ) as progress:
            task = progress.add_task("Running simulation...", total=None)
            report = await orchestrator.run_simulation(personas=personas)
            progress.update(task, description="Exporting results...")
            exported = orchestrator.export_results(output_dir=output_dir, formats=export_formats)
    else:
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            console=console,
        ) as progress:
            task = progress.add_task("Running simulation...", total=None)
            if scenario_ids or endurance_runner:
                # Generate personas first, apply scenarios/stress, then run with modified personas
                progress.update(task, description="Generating personas...")
                personas = await orchestrator.generate_personas_only()
                personas = _apply_scenarios_to_personas(personas)
                desc = "Running simulation with"
                parts = []
                if scenario_ids:
                    parts.append("scenarios")
                if endurance_runner:
                    parts.append("memory stress")
                progress.update(task, description=f"{desc} {' + '.join(parts)}...")
                report = await orchestrator.run_simulation(personas=personas)
            else:
                report = await orchestrator.run_simulation()
            progress.update(task, description="Exporting results...")
            exported = orchestrator.export_results(output_dir=output_dir, formats=export_formats)

    # Display results
    _print_report(report, exported)

    # Auto-save regression suite from failures
    if auto_save_suite:
        from src.regression.suite_manager import RegressionSuiteManager
        mgr = RegressionSuiteManager()
        suite = mgr.create_from_report(
            report=report,
            name=f"Regression: {name}",
            include_warnings=True,
        )
        if suite.total_cases > 0:
            suite_path = mgr.save_suite(suite, Path(output_dir) / "regression_suite.json")
            console.print(f"\n  🔁 Regression suite saved: [bold]{suite_path}[/] ({suite.total_cases} test cases)")
        else:
            console.print("\n  ✅ No failures found — no regression suite needed")


# ============================================================
# simtest refine — Iterative Persona Refinement
# ============================================================

@main.command()
@click.option("--report", "report_path", required=True, help="Path to summary.json from a previous run")
@click.option("--bot-endpoint", required=True, help="API endpoint of the bot to test")
@click.option("--bot-api-key", default=None, help="API key for the bot")
@click.option("--bot-format", default="openai", help="Request format: openai, anthropic, custom")
@click.option("--documentation", default="", help="Documentation text for grounding")
@click.option("--doc-file", default=None, help="Path to documentation file")
@click.option("--personas", default=10, help="Number of refined personas to generate")
@click.option("--max-turns", default=15, help="Max conversation turns")
@click.option("--output", default="./reports/refined", help="Output directory")
@click.option("--export-formats", default="jsonl,csv,summary,html", help="Export formats")
def refine(
    report_path: str,
    bot_endpoint: str,
    bot_api_key: str | None,
    bot_format: str,
    documentation: str,
    doc_file: str | None,
    personas: int,
    max_turns: int,
    output: str,
    export_formats: str,
):
    """Run iterative persona refinement based on a previous simulation's failures.

    Analyzes the previous report to find high-risk persona types, then generates
    new personas that drill deeper into those weak spots and re-runs the simulation.
    """
    from src.core.logging import setup_logging
    setup_logging()

    console.print(Panel.fit(
        "[bold magenta]AI SimTest[/] - Iterative Persona Refinement",
        subtitle="Drill deeper into failures",
    ))

    # Load previous report
    rp = Path(report_path)
    if not rp.exists():
        console.print(f"[red]Error: Report file not found: {report_path}[/]")
        sys.exit(1)

    # Load documentation
    doc_text = documentation
    if doc_file:
        dp = Path(doc_file)
        if dp.exists():
            doc_text = dp.read_text(encoding="utf-8")

    asyncio.run(_run_refinement(
        report_path=rp,
        bot_endpoint=bot_endpoint,
        bot_api_key=bot_api_key,
        bot_format=bot_format,
        documentation=doc_text,
        num_personas=personas,
        max_turns=max_turns,
        output_dir=output,
        export_formats=export_formats.split(","),
    ))


async def _run_refinement(
    report_path: Path,
    bot_endpoint: str,
    bot_api_key: str | None,
    bot_format: str,
    documentation: str,
    num_personas: int,
    max_turns: int,
    output_dir: str,
    export_formats: list[str],
):
    """Async refinement runner."""
    from src.core.orchestrator import SimulationOrchestrator
    from src.generators.persona_refiner import PersonaRefiner
    from src.models import BotConfig, SimulationConfig

    # Load the report files
    report_dir = report_path.parent
    jsonl_path = report_dir / "conversations.jsonl"

    console.print(f"  📊 Loaded previous report: {report_path}")

    # Reconstruct report from files
    report = _reconstruct_report_from_files(report_path, jsonl_path)

    # Analyze failures
    refiner = PersonaRefiner()
    analysis = refiner.analyze_failures(report)

    # Display analysis
    _print_refinement_analysis(analysis)

    if not analysis["high_risk_personas"]:
        console.print("\n[green]No high-risk personas found — your bot is doing well![/]")
        return

    if not click.confirm("\nGenerate refined personas and run simulation?", default=True):
        console.print("[yellow]Refinement cancelled.[/]")
        return

    # Generate refined personas
    with Progress(SpinnerColumn(), TextColumn("[progress.description]{task.description}"), console=console) as progress:
        task = progress.add_task("Generating refined personas...", total=None)
        refined_personas, _ = await refiner.generate_refined_personas(
            report=report,
            num_personas=num_personas,
            bot_description=f"Bot endpoint: {bot_endpoint}",
            documentation=documentation,
        )
        progress.update(task, description=f"Generated {len(refined_personas)} refined personas")

    if not refined_personas:
        console.print("[yellow]Could not generate refined personas.[/]")
        return

    # Preview refined personas
    _print_persona_preview(refined_personas)

    # Run simulation with refined personas
    config = SimulationConfig(
        name="Refinement Run",
        bot=BotConfig(
            api_endpoint=bot_endpoint,
            api_key=bot_api_key,
            request_format=bot_format,
        ),
        documentation=documentation,
        num_personas=len(refined_personas),
        max_turns_per_conversation=max_turns,
    )

    orchestrator = SimulationOrchestrator(config)

    with Progress(SpinnerColumn(), TextColumn("[progress.description]{task.description}"), console=console) as progress:
        task = progress.add_task("Running refined simulation...", total=None)
        refined_report = await orchestrator.run_simulation(personas=refined_personas)
        progress.update(task, description="Exporting results...")
        exported = orchestrator.export_results(output_dir=output_dir, formats=export_formats)

    _print_report(refined_report, exported)
    console.print("\n[bold magenta]Refinement complete![/] Compare with original using: simtest compare")


# ============================================================
# simtest save-suite — Save regression suite from report
# ============================================================

@main.command("save-suite")
@click.option("--report", "report_path", required=True, help="Path to summary.json from a simulation run")
@click.option("--conversations", "jsonl_path", default=None, help="Path to conversations.jsonl (auto-detected if in same dir)")
@click.option("--name", default="Regression Suite", help="Name for the suite")
@click.option("--output", default="./reports/regression_suite.json", help="Output file path")
@click.option("--include-passing", is_flag=True, default=False, help="Also include passing edge cases")
@click.option("--include-warnings/--no-include-warnings", default=True, help="Include WARNING cases (default: True)")
@click.option("--max-cases", default=100, help="Maximum test cases to save")
def save_suite(
    report_path: str,
    jsonl_path: str | None,
    name: str,
    output: str,
    include_passing: bool,
    include_warnings: bool,
    max_cases: int,
):
    """Save a regression test suite from simulation failures.

    Extracts failing conversations as frozen test cases that can be
    replayed later to check if issues got fixed or regressed.
    """
    console.print(Panel.fit(
        "[bold cyan]AI SimTest[/] - Save Regression Suite",
    ))

    rp = Path(report_path)
    if not rp.exists():
        console.print(f"[red]Error: Report not found: {report_path}[/]")
        sys.exit(1)

    # Auto-detect JSONL path
    jp = Path(jsonl_path) if jsonl_path else rp.parent / "conversations.jsonl"

    report = _reconstruct_report_from_files(rp, jp)

    from src.regression.suite_manager import RegressionSuiteManager
    mgr = RegressionSuiteManager()
    suite = mgr.create_from_report(
        report=report,
        name=name,
        include_passing=include_passing,
        include_warnings=include_warnings,
        max_cases=max_cases,
    )

    if suite.total_cases == 0:
        console.print("[green]No failing test cases found — your bot passed everything![/]")
        return

    path = mgr.save_suite(suite, output)

    # Display summary
    table = Table(title=f"Regression Suite: {name}")
    table.add_column("Metric", style="bold")
    table.add_column("Value", justify="right")
    table.add_row("Total test cases", str(suite.total_cases))
    table.add_row("FAIL cases", str(sum(1 for tc in suite.test_cases if tc.original_label == "FAIL")))
    table.add_row("WARNING cases", str(sum(1 for tc in suite.test_cases if tc.original_label == "WARNING")))
    table.add_row("PASS (edge) cases", str(sum(1 for tc in suite.test_cases if tc.original_label == "PASS")))
    table.add_row("Saved to", str(path))
    console.print(table)

    # Show tags breakdown
    all_tags: dict[str, int] = {}
    for tc in suite.test_cases:
        for tag in tc.tags:
            all_tags[tag] = all_tags.get(tag, 0) + 1
    if all_tags:
        console.print("\n  Failure types in suite:")
        for tag, count in sorted(all_tags.items(), key=lambda x: x[1], reverse=True):
            console.print(f"    {tag}: {count} cases")

    console.print(f"\n  Replay later with: [bold]simtest replay --suite {path} --bot-endpoint <url>[/]")


# ============================================================
# simtest replay — Replay regression suite
# ============================================================

@main.command()
@click.option("--suite", "suite_path", required=True, help="Path to regression_suite.json")
@click.option("--bot-endpoint", required=True, help="API endpoint of the bot to test")
@click.option("--bot-api-key", default=None, help="API key for the bot")
@click.option("--bot-format", default="openai", help="Request format: openai, anthropic, custom")
@click.option("--documentation", default="", help="Documentation text for grounding")
@click.option("--doc-file", default=None, help="Path to documentation file")
@click.option("--max-turns", default=15, help="Max conversation turns per test case")
@click.option("--output", default="./reports/replay", help="Output directory for replay results")
@click.option("--fail-on-regression", is_flag=True, default=False, help="Exit with code 1 if regressions found (for CI/CD)")
def replay(
    suite_path: str,
    bot_endpoint: str,
    bot_api_key: str | None,
    bot_format: str,
    documentation: str,
    doc_file: str | None,
    max_turns: int,
    output: str,
    fail_on_regression: bool,
):
    """Replay a regression suite against the bot and check for fixes/regressions.

    Sends the same user messages from saved test cases and judges the new
    responses, reporting what got fixed and what regressed.
    """
    from src.core.logging import setup_logging
    setup_logging()

    console.print(Panel.fit(
        "[bold yellow]AI SimTest[/] - Regression Replay",
        subtitle="Check fixes & regressions",
    ))

    sp = Path(suite_path)
    if not sp.exists():
        console.print(f"[red]Error: Suite not found: {suite_path}[/]")
        sys.exit(1)

    # Load documentation
    doc_text = documentation
    if doc_file:
        dp = Path(doc_file)
        if dp.exists():
            doc_text = dp.read_text(encoding="utf-8")

    exit_code = asyncio.run(_run_replay(
        suite_path=sp,
        bot_endpoint=bot_endpoint,
        bot_api_key=bot_api_key,
        bot_format=bot_format,
        documentation=doc_text,
        max_turns=max_turns,
        output_dir=output,
        fail_on_regression=fail_on_regression,
    ))

    if exit_code != 0:
        sys.exit(exit_code)


async def _run_replay(
    suite_path: Path,
    bot_endpoint: str,
    bot_api_key: str | None,
    bot_format: str,
    documentation: str,
    max_turns: int,
    output_dir: str,
    fail_on_regression: bool,
) -> int:
    """Async replay runner. Returns exit code."""
    from src.models import BotConfig
    from src.regression.suite_manager import RegressionSuiteManager

    mgr = RegressionSuiteManager()
    suite = mgr.load_suite(suite_path)

    console.print(f"  📋 Loaded suite: [bold]{suite.name}[/] ({suite.total_cases} test cases)")

    bot_config = BotConfig(
        api_endpoint=bot_endpoint,
        api_key=bot_api_key,
        request_format=bot_format,
    )

    with Progress(SpinnerColumn(), TextColumn("[progress.description]{task.description}"), console=console) as progress:
        task = progress.add_task(f"Replaying {suite.total_cases} test cases...", total=None)
        summary = await mgr.replay_suite(
            suite=suite,
            bot_config=bot_config,
            documentation=documentation,
            max_turns=max_turns,
        )
        progress.update(task, description="Replay complete!")

    # Save results
    out_path = Path(output_dir)
    out_path.mkdir(parents=True, exist_ok=True)
    mgr.save_replay_summary(summary, out_path / "replay_summary.json")

    # Display results
    _print_replay_summary(summary)

    # CI/CD exit code
    if fail_on_regression and summary.regressed > 0:
        console.print(f"\n[bold red]CI FAILED: {summary.regressed} regression(s) detected![/]")
        return 1

    return 0


def _print_replay_summary(summary):
    """Print a formatted replay summary."""
    table = Table(title=f"Replay Results: {summary.suite_name}")
    table.add_column("Status", style="bold")
    table.add_column("Count", justify="right")
    table.add_column("Icon")

    table.add_row("Fixed", str(summary.fixed), "✅")
    table.add_row("Still Failing", str(summary.still_failing), "🔴")
    table.add_row("Regressed", str(summary.regressed), "❌")
    table.add_row("Still Passing", str(summary.still_passing), "🟢")
    table.add_row("Improved", str(summary.improved), "📈")
    table.add_row("Total", str(summary.total_cases), "")

    console.print(table)

    if summary.avg_score_change != 0:
        direction = "↑" if summary.avg_score_change > 0 else "↓"
        color = "green" if summary.avg_score_change > 0 else "red"
        console.print(f"\n  Average score change: [{color}]{direction} {abs(summary.avg_score_change):.3f}[/]")

    # Show details for regressions and fixes
    regressions = [r for r in summary.results if r.status == "regressed"]
    if regressions:
        console.print("\n[bold red]Regressions:[/]")
        for r in regressions:
            console.print(f"  ❌ {r.persona_name}: {r.original_label}→{r.new_label} (score: {r.original_score:.2f}→{r.new_score:.2f})")
            for f in r.new_failures[:2]:
                console.print(f"     → {f[:80]}")

    fixes = [r for r in summary.results if r.status == "fixed"]
    if fixes:
        console.print("\n[bold green]Fixed:[/]")
        for r in fixes:
            console.print(f"  ✅ {r.persona_name}: {r.original_label}→{r.new_label} (score: {r.original_score:.2f}→{r.new_score:.2f})")


## Old compare command removed — replaced by full implementation below (with regression detection, CI/CD gates, HTML reports)


# ============================================================
# Shared helpers
# ============================================================

def _reconstruct_report_from_files(summary_path: Path, jsonl_path: Path):
    """Reconstruct a SimulationReport from saved files (best effort)."""
    from src.models import (
        Conversation, FailurePattern, JudgedConversation, JudgedTurn,
        JudgmentLabel, JudgmentResult, Persona, PersonaType,
        ReportSummary, Severity, SimulationReport, Turn,
    )

    with open(summary_path) as f:
        summary_data = json.load(f)

    judged_conversations = []

    # Try to load full conversations from JSONL
    if jsonl_path.exists():
        with open(jsonl_path) as f:
            for line in f:
                try:
                    record = json.loads(line.strip())
                    turns = [
                        Turn(speaker=t["speaker"], message=t["message"], latency_ms=t.get("latency_ms"))
                        for t in record.get("turns", [])
                    ]
                    conv = Conversation(
                        id=record.get("conversation_id", "unknown"),
                        persona_id=record.get("persona_id", "unknown"),
                        turns=turns,
                    )
                    judged_turns = []
                    for jt_data in record.get("judgments", []):
                        judgments = [
                            JudgmentResult(
                                judge_name=jr["judge"],
                                passed=jr["passed"],
                                score=jr["score"],
                                message=jr.get("message", ""),
                            )
                            for jr in jt_data.get("judge_results", [])
                        ]
                        label = JudgmentLabel(jt_data.get("overall_label", "PASS"))
                        judged_turns.append(JudgedTurn(
                            turn=Turn(speaker="bot", message=""),
                            judgments=judgments,
                            overall_label=label,
                            overall_score=jt_data.get("overall_score", 0.0),
                            issues=jt_data.get("issues", []),
                        ))

                    persona = Persona(
                        name=record.get("persona_name", "Unknown"),
                        role="reconstructed",
                        goals=["reconstructed"],
                    )

                    jc = JudgedConversation(
                        conversation=conv,
                        persona=persona,
                        judged_turns=judged_turns,
                        overall_score=record.get("overall_score", 0.0),
                        failure_modes=record.get("failure_modes", []) if isinstance(record.get("failure_modes"), list) else [],
                    )
                    judged_conversations.append(jc)
                except Exception:
                    continue

    s = summary_data.get("summary", {})
    report = SimulationReport(
        summary=ReportSummary(
            simulation_id=s.get("simulation_id", "unknown"),
            simulation_name=s.get("simulation_name", "unknown"),
            total_personas=s.get("total_personas", 0),
            total_conversations=s.get("total_conversations", 0),
            total_turns=s.get("total_turns", 0),
            pass_rate=s.get("pass_rate", 0.0),
            average_score=s.get("average_score", 0.0),
            critical_failures=s.get("critical_failures", 0),
            warnings=s.get("warnings", 0),
            execution_time_seconds=s.get("execution_time_seconds", 0.0),
        ),
        failure_patterns=[
            FailurePattern(
                pattern_name=fp.get("pattern_name", ""),
                description=fp.get("description", ""),
                frequency=fp.get("frequency", 0),
                severity=Severity(fp.get("severity", "medium")),
            )
            for fp in summary_data.get("failure_patterns", [])
        ],
        score_by_judge=summary_data.get("score_by_judge", {}),
        score_by_persona_type=summary_data.get("score_by_persona_type", {}),
        recommendations=summary_data.get("recommendations", []),
        judged_conversations=judged_conversations,
    )
    return report


def _print_refinement_analysis(analysis: dict):
    """Print refinement analysis results."""
    console.print("\n[bold]Failure Analysis:[/]")

    by_type = analysis.get("failure_by_type", {})
    if by_type:
        console.print("\n  Failure rates by persona type:")
        for t, rate in sorted(by_type.items(), key=lambda x: x[1], reverse=True):
            bar = "█" * int(rate * 20) + "░" * (20 - int(rate * 20))
            color = "red" if rate > 0.5 else "yellow" if rate > 0.3 else "green"
            console.print(f"    {t:15s} [{color}]{bar}[/] {rate:.0%}")

    by_topic = analysis.get("failure_by_topic", {})
    if by_topic:
        console.print("\n  High-failure topics:")
        for topic, count in sorted(by_topic.items(), key=lambda x: x[1], reverse=True)[:5]:
            console.print(f"    🔴 {topic}: {count} failures")

    strategy = analysis.get("refinement_strategy", [])
    if strategy:
        console.print("\n  [bold]Refinement strategy:[/]")
        for s in strategy:
            console.print(f"    → {s}")

    high_risk = analysis.get("high_risk_personas", [])
    console.print(f"\n  Found [bold red]{len(high_risk)}[/] high-risk persona profiles (>50% failure rate)")


def _print_persona_preview(personas):
    """Print a persona preview table for user review."""
    table = Table(title=f"Generated Personas ({len(personas)})", show_header=True, show_lines=True)
    table.add_column("#", style="dim", width=3)
    table.add_column("Name", style="bold", max_width=30)
    table.add_column("Type", width=12)
    table.add_column("Tone", width=12)
    table.add_column("Tech Level", width=12)
    table.add_column("Goals", max_width=40)
    table.add_column("Topics", max_width=30)

    for i, p in enumerate(personas, 1):
        type_color = {"standard": "blue", "edge_case": "yellow", "adversarial": "red"}.get(
            p.persona_type.value, "white"
        )
        goals_str = ", ".join(p.goals[:2])
        topics_str = ", ".join(p.topics[:3]) if p.topics else "-"
        tactics = ""
        if p.adversarial_tactics:
            tactics = f"\n[dim]Tactics: {', '.join(p.adversarial_tactics[:2])}[/]"

        table.add_row(
            str(i),
            p.name,
            f"[{type_color}]{p.persona_type.value}[/]",
            p.tone,
            p.technical_level.value,
            goals_str + tactics,
            topics_str,
        )

    console.print(table)


def _print_report(report, exported: dict[str, str]):
    """Print a formatted report to the console."""

    s = report.summary

    table = Table(title="Simulation Results", show_header=True)
    table.add_column("Metric", style="bold")
    table.add_column("Value", justify="right")

    table.add_row("Total Personas", str(s.total_personas))
    table.add_row("Total Conversations", str(s.total_conversations))
    table.add_row("Total Turns", str(s.total_turns))
    table.add_row("Pass Rate", f"{s.pass_rate:.1%}")
    table.add_row("Average Score", f"{s.average_score:.2f}")
    table.add_row("Critical Failures", str(s.critical_failures))
    table.add_row("Warnings", str(s.warnings))
    table.add_row("Execution Time", f"{s.execution_time_seconds:.1f}s")

    console.print(table)

    if report.score_by_judge:
        console.print("\n[bold]Scores by Judge:[/]")
        for judge, score in report.score_by_judge.items():
            bar = "█" * int(score * 20) + "░" * (20 - int(score * 20))
            color = "green" if score >= 0.8 else "yellow" if score >= 0.6 else "red"
            console.print(f"  {judge:20s} [{color}]{bar}[/] {score:.1%}")

    if report.recommendations:
        console.print("\n[bold]Recommendations:[/]")
        for rec in report.recommendations:
            console.print(f"  {rec}")

    if exported:
        console.print("\n[bold]Exported Files:[/]")
        for fmt, path in exported.items():
            icon = "📊" if fmt == "html" else "📄"
            console.print(f"  {icon} {fmt}: {path}")
        if "html" in exported:
            console.print(f"\n  [bold green]→ Open the HTML report in your browser: file://{Path(exported['html']).resolve()}[/]")

    console.print()


@main.command()
def serve():
    """Start the API server."""
    import uvicorn
    from src.core.config import settings

    console.print(Panel.fit(
        f"[bold blue]AI SimTest API Server[/]\nhttp://{settings.api_host}:{settings.api_port}",
    ))

    uvicorn.run(
        "src.api.app:app",
        host=settings.api_host,
        port=settings.api_port,
        reload=settings.app_env == "development",
    )

@main.command()
@click.argument("baseline", type=click.Path(exists=True))
@click.argument("current", type=click.Path(exists=True))
@click.option("--fail-if-regression", is_flag=True, default=False,
              help="Exit with code 1 if regressions detected (for CI/CD)")
@click.option("--threshold", type=float, default=0.05,
              help="Minimum delta percentage to count as regression [default: 0.05 = 5%]")
@click.option("--critical-tolerance", type=int, default=0,
              help="Max new critical failures before FAIL verdict [default: 0]")
@click.option("--pass-rate-floor", type=float, default=0.0,
              help="Minimum acceptable pass rate (0.0-1.0) [default: 0.0]")
@click.option("--output", type=click.Path(), default="./comparison",
              help="Output directory for comparison report [default: ./comparison]")
@click.option("--format", "formats", type=str, default="html,json",
              help="Output formats: html,json [default: html,json]")
def compare(baseline, current, fail_if_regression, threshold, critical_tolerance,
            pass_rate_floor, output, formats):
    """Compare two simulation reports for regression detection.

    BASELINE is the path to the reference summary JSON (e.g., from the last known-good run).
    CURRENT is the path to the new summary JSON (e.g., from the latest run).

    Examples:

        simtest compare reports/v1/summary.json reports/v2/summary.json

        simtest compare baseline.json current.json --fail-if-regression

        simtest compare old.json new.json --threshold 0.03 --output ./diff/
    """
    from rich.console import Console
    from rich.panel import Panel
    from rich.table import Table

    from src.core.comparison_engine import ComparisonEngine
    from src.core.comparison_models import ComparisonConfig, RegressionVerdict
    from src.exporters.comparison_report import ComparisonReportGenerator

    console = Console()
    console.print("\n[bold]🧪 AI SimTest — Compare Mode[/bold]\n")

    # Configure
    config = ComparisonConfig(
        regression_threshold=threshold,
        critical_failure_tolerance=critical_tolerance,
        pass_rate_floor=pass_rate_floor,
        fail_if_regression=fail_if_regression,
    )

    # Run comparison
    engine = ComparisonEngine(config)
    try:
        result = engine.compare(baseline, current)
    except FileNotFoundError as e:
        console.print(f"[red]Error: {e}[/red]")
        raise SystemExit(1)
    except Exception as e:
        console.print(f"[red]Error loading reports: {e}[/red]")
        raise SystemExit(1)

    s = result.summary

    # ─── Display Verdict ───────────────────────────────────

    verdict_styles = {
        RegressionVerdict.PASS: ("✅ NO REGRESSIONS", "green"),
        RegressionVerdict.IMPROVED: ("🎉 IMPROVED", "blue"),
        RegressionVerdict.WARNING: ("⚠️  MINOR REGRESSIONS", "yellow"),
        RegressionVerdict.FAIL: ("🚨 REGRESSIONS DETECTED", "red"),
    }
    label, color = verdict_styles[s.verdict]

    console.print(Panel(
        f"[bold {color}]{label}[/bold {color}]\n"
        f"[dim]{s.total_regressions} regression(s), {s.total_improvements} improvement(s)[/dim]",
        title=f"[bold]{s.baseline_name} → {s.current_name}[/bold]",
        border_style=color,
    ))

    # Verdict reasons
    if s.verdict_reasons:
        for reason in s.verdict_reasons:
            console.print(f"  [yellow]• {reason}[/yellow]")
        console.print()

    # ─── Metrics Table ─────────────────────────────────────

    table = Table(title="Core Metrics", show_header=True, header_style="bold")
    table.add_column("Metric", style="cyan")
    table.add_column("Baseline", justify="right")
    table.add_column("Current", justify="right")
    table.add_column("Delta", justify="right")

    def fmt_metric(m, fmt="score"):
        if fmt == "pct":
            return f"{m.baseline_value:.1%}", f"{m.current_value:.1%}", f"{m.delta:+.1%}"
        elif fmt == "int":
            return f"{int(m.baseline_value)}", f"{int(m.current_value)}", f"{int(m.delta):+d}"
        return f"{m.baseline_value:.3f}", f"{m.current_value:.3f}", f"{m.delta:+.3f}"

    def delta_style(direction):
        if direction.value == "improved":
            return "green"
        elif direction.value == "regressed":
            return "red"
        return "dim"

    for label_text, metric, fmt in [
        ("Pass Rate", s.pass_rate, "pct"),
        ("Avg Score", s.average_score, "score"),
        ("Critical Failures", s.critical_failures, "int"),
        ("Conversations", s.total_conversations, "int"),
        ("Total Turns", s.total_turns, "int"),
    ]:
        b, c, d = fmt_metric(metric, fmt)
        style = delta_style(metric.direction)
        table.add_row(label_text, b, c, f"[{style}]{d}[/{style}]")

    console.print(table)

    # ─── Judge Comparison ──────────────────────────────────

    if s.judge_comparisons:
        console.print()
        judge_table = Table(title="Judge Scores", show_header=True, header_style="bold")
        judge_table.add_column("Judge", style="cyan")
        judge_table.add_column("Baseline", justify="right")
        judge_table.add_column("Current", justify="right")
        judge_table.add_column("Delta", justify="right")

        for j in s.judge_comparisons:
            style = delta_style(j.direction)
            judge_table.add_row(
                j.judge_name,
                f"{j.baseline_score:.3f}",
                f"{j.current_score:.3f}",
                f"[{style}]{j.delta:+.3f} ({j.delta_percent:+.1f}%)[/{style}]",
            )

        console.print(judge_table)

    # ─── Failure Patterns ──────────────────────────────────

    all_patterns = (s.new_failures + s.resolved_failures + s.worsened_failures +
                    s.improved_failures + s.persistent_failures)
    if all_patterns:
        console.print()
        fp_table = Table(title="Failure Pattern Changes", show_header=True, header_style="bold")
        fp_table.add_column("Pattern")
        fp_table.add_column("Status")
        fp_table.add_column("Δ", justify="right")

        status_styles = {
            "new": "[red]NEW[/red]",
            "resolved": "[green]RESOLVED[/green]",
            "worsened": "[red]WORSENED[/red]",
            "improved": "[blue]IMPROVED[/blue]",
            "persistent": "[dim]PERSISTENT[/dim]",
        }

        for p in sorted(all_patterns, key=lambda x: x.status):
            fp_table.add_row(
                p.pattern_name,
                status_styles.get(p.status, p.status),
                f"{p.delta:+d}",
            )

        console.print(fp_table)

    # ─── Generate Reports ──────────────────────────────────

    output_dir = Path(output)
    output_dir.mkdir(parents=True, exist_ok=True)
    format_list = [f.strip().lower() for f in formats.split(",")]

    gen = ComparisonReportGenerator()

    if "html" in format_list:
        html_path = gen.generate(result, output_dir / "comparison.html")
        console.print(f"\n📄 HTML report: [link={html_path}]{html_path}[/link]")

    if "json" in format_list:
        json_path = gen.generate_json(result, output_dir / "comparison.json")
        console.print(f"📄 JSON report: [link={json_path}]{json_path}[/link]")

    # ─── CI/CD Gate ────────────────────────────────────────

    console.print()
    if fail_if_regression:
        if result.exit_code == 0:
            console.print("[bold green]✅ CI/CD Gate: PASSED[/bold green]")
        else:
            console.print("[bold red]❌ CI/CD Gate: FAILED — Regressions detected[/bold red]")
            console.print(f"[dim]Exit code: {result.exit_code}[/dim]")

    console.print()
    raise SystemExit(result.exit_code)


@main.command()
def version():
    """Show version info."""
    console.print("[bold]AI SimTest[/] v0.2.0")
    console.print("Open-source AI simulation testing platform")
    console.print("\nModes:")
    console.print("  manual     You provide everything (default)")
    console.print("  partial    AI analyzes docs, you approve")
    console.print("  auto       Just the endpoint — AI discovers everything")
    console.print("\nCommands:")
    console.print("  run          Run a simulation test")
    console.print("  scenarios    List available scenario templates")
    console.print("  stress       List memory stress test patterns")
    console.print("  refine       Iterative persona refinement")
    console.print("  save-suite   Save regression suite from failures")
    console.print("  replay       Replay regression suite")
    console.print("  compare      Compare two simulation reports")
    console.print("  serve        Start the API server")


# ============================================================
# simtest scenarios — List available scenario templates
# ============================================================

@main.command()
@click.option("--category", default=None, help="Filter by category (robustness, safety, quality, memory, empathy)")
@click.option("--difficulty", default=None, help="Filter by difficulty (easy, medium, hard)")
@click.option("--verbose", "-v", is_flag=True, default=False, help="Show full descriptions and turn instructions")
def scenarios(category: str | None, difficulty: str | None, verbose: bool):
    """List available scenario templates for structured testing.

    \b
    Examples:
        simtest scenarios                    # List all scenarios
        simtest scenarios --category safety  # Safety scenarios only
        simtest scenarios -v                 # Verbose with details

    \b
    Use with 'simtest run':
        simtest run --bot-endpoint http://... --scenarios goal_shift,prompt_injection
        simtest run --bot-endpoint http://... --scenarios all
        simtest run --bot-endpoint http://... --scenarios safety
    """
    from src.scenarios import ScenarioCategory, ScenarioDifficulty, ScenarioLibrary

    lib = ScenarioLibrary()
    lib.load_built_in()

    console.print(Panel.fit(
        "[bold blue]AI SimTest[/] - Scenario Templates",
        subtitle=f"{lib.count} available",
    ))

    # Filter
    scenario_list = lib.list_all()
    if category:
        try:
            cat = ScenarioCategory(category.lower())
            scenario_list = [s for s in scenario_list if s.category == cat]
        except ValueError:
            cats = ", ".join(c.value for c in ScenarioCategory)
            console.print(f"[red]Unknown category: '{category}'. Choose from: {cats}[/]")
            sys.exit(1)

    if difficulty:
        try:
            diff = ScenarioDifficulty(difficulty.lower())
            scenario_list = [s for s in scenario_list if s.difficulty == diff]
        except ValueError:
            diffs = ", ".join(d.value for d in ScenarioDifficulty)
            console.print(f"[red]Unknown difficulty: '{difficulty}'. Choose from: {diffs}[/]")
            sys.exit(1)

    if not scenario_list:
        console.print("[yellow]No scenarios match the filter.[/]")
        return

    # Display
    table = Table(title="Scenario Templates")
    table.add_column("ID", style="bold cyan", no_wrap=True)
    table.add_column("Name", style="bold")
    table.add_column("Category", style="green")
    table.add_column("Difficulty")
    table.add_column("Turns", justify="center")
    table.add_column("Tags", style="dim")

    diff_colors = {"easy": "green", "medium": "yellow", "hard": "red"}

    for s in scenario_list:
        diff_color = diff_colors.get(s.difficulty.value, "white")
        table.add_row(
            s.id,
            s.name,
            s.category.value,
            f"[{diff_color}]{s.difficulty.value}[/{diff_color}]",
            f"{s.min_turns}-{s.max_turns}",
            ", ".join(s.tags[:3]),
        )

    console.print(table)

    if verbose:
        for s in scenario_list:
            console.print(f"\n[bold cyan]━━━ {s.name} ({s.id}) ━━━[/]")
            console.print(f"  {s.description}\n")
            if s.turn_instructions:
                console.print("  [bold]Turn Instructions:[/]")
                for ti in s.turn_instructions:
                    turn_label = f"Turn {ti.turn_number}" if ti.turn_number else "General"
                    console.print(f"    [{turn_label}] {ti.instruction}")
            if s.success_criteria:
                console.print("\n  [bold green]Success Criteria:[/]")
                for sc in s.success_criteria:
                    console.print(f"    ✅ {sc}")
            if s.failure_indicators:
                console.print("\n  [bold red]Failure Indicators:[/]")
                for fi in s.failure_indicators:
                    console.print(f"    ❌ {fi}")

    console.print(f"\n[dim]Usage: simtest run --bot-endpoint URL --scenarios {scenario_list[0].id}[/]")
    console.print(f"[dim]       simtest run --bot-endpoint URL --scenarios all[/]")


# ============================================================
# simtest stress — List memory stress test patterns
# ============================================================

@main.command()
@click.option("--verbose", "-v", is_flag=True, default=False, help="Show full details including built-in facts and contradictions")
def stress(verbose: bool):
    """List available memory stress test patterns and configuration.

    \b
    Examples:
        simtest stress            # Overview of stress patterns
        simtest stress -v         # Verbose with built-in facts & contradictions

    \b
    Use with 'simtest run':
        simtest run --bot-endpoint http://... --stress-memory
        simtest run --bot-endpoint http://... --stress-memory --stress-turns 40
        simtest run --bot-endpoint http://... --stress-memory --stress-patterns fact_seeding,contradiction
        simtest run --bot-endpoint http://... --scenarios safety --stress-memory   # Combine both!
    """
    from src.endurance import (
        BUILT_IN_CONTRADICTIONS,
        BUILT_IN_COMPLEXITY_LEVELS,
        BUILT_IN_FACTS,
        StressPattern,
    )

    console.print(Panel.fit(
        "[bold blue]AI SimTest[/] - Memory Stress Testing (P2 #10)",
        subtitle="Context Endurance & Memory Stress",
    ))

    console.print("\n[bold]Three Stress Patterns:[/]\n")

    # Pattern table
    table = Table(show_header=True, header_style="bold")
    table.add_column("Pattern", style="bold cyan", no_wrap=True)
    table.add_column("Description")
    table.add_column("Built-in Data", justify="center")
    table.add_column("What It Tests")

    table.add_row(
        "fact_seeding",
        "Plant facts early, verify recall later",
        f"{len(BUILT_IN_FACTS)} facts",
        "Does the bot remember user details over long conversations?",
    )
    table.add_row(
        "contradiction",
        "State opposing facts, check if bot notices",
        f"{len(BUILT_IN_CONTRADICTIONS)} pairs",
        "Does the bot catch when users contradict themselves?",
    )
    table.add_row(
        "progressive_complexity",
        "Escalate question complexity over time",
        f"{len(BUILT_IN_COMPLEXITY_LEVELS)} levels",
        "Does bot quality degrade as context window fills?",
    )

    console.print(table)

    # Default config
    console.print("\n[bold]Default Configuration:[/]\n")
    defaults_table = Table(show_header=True, header_style="bold", box=None)
    defaults_table.add_column("Parameter", style="cyan")
    defaults_table.add_column("Default")
    defaults_table.add_column("CLI Flag")

    defaults_table.add_row("Target turns", "30", "--stress-turns")
    defaults_table.add_row("Facts to seed", "5", "--stress-facts")
    defaults_table.add_row("Contradictions", "3", "--stress-contradictions")
    defaults_table.add_row("Patterns", "all three", "--stress-patterns")
    defaults_table.add_row("Seed window", "turns 1-5", "(auto-calculated)")
    defaults_table.add_row("Recall window", "turns 15-25", "(auto-calculated)")

    console.print(defaults_table)

    if verbose:
        # Show built-in facts
        console.print(f"\n[bold cyan]━━━ Built-in Facts ({len(BUILT_IN_FACTS)}) ━━━[/]")
        for fact in BUILT_IN_FACTS:
            console.print(f"\n  [bold]{fact.fact_id}[/] ({fact.category})")
            console.print(f"    Seed: \"{fact.seed_text}\"")
            console.print(f"    Recall: \"{fact.recall_prompt}\"")
            console.print(f"    Expected: {', '.join(fact.expected_in_response)}")

        # Show contradiction pairs
        console.print(f"\n[bold cyan]━━━ Built-in Contradictions ({len(BUILT_IN_CONTRADICTIONS)}) ━━━[/]")
        for pair in BUILT_IN_CONTRADICTIONS:
            console.print(f"\n  [bold]{pair.pair_id}[/]")
            console.print(f"    Original: \"{pair.original_statement}\"")
            console.print(f"    Contradiction: \"{pair.contradicting_statement}\"")
            console.print(f"    Expected: {pair.expected_bot_behavior}")

        # Show complexity levels
        console.print(f"\n[bold cyan]━━━ Complexity Levels ({len(BUILT_IN_COMPLEXITY_LEVELS)}) ━━━[/]")
        for level in BUILT_IN_COMPLEXITY_LEVELS:
            console.print(f"  Level {level.level}: {level.description}")

    # Scoring
    console.print("\n[bold]Endurance Scoring:[/]\n")
    console.print("  Overall endurance score = weighted combination of:")
    console.print("    • [bold]50%[/] Fact recall rate (how many seeded facts the bot remembers)")
    console.print("    • [bold]30%[/] Contradiction detection rate (does the bot catch contradictions)")
    console.print("    • [bold]20%[/] Memory stability (decay curve — when does memory degrade)")
    console.print("\n  The [bold]degradation point[/] shows exactly which turn the bot starts forgetting.")

    console.print(f"\n[dim]Usage: simtest run --bot-endpoint URL --stress-memory[/]")
    console.print(f"[dim]       simtest run --bot-endpoint URL --stress-memory --stress-turns 40 --stress-patterns fact_seeding[/]")
    console.print(f"[dim]       simtest run --bot-endpoint URL --scenarios goal_shift --stress-memory   # Combine![/]")


if __name__ == "__main__":
    main()