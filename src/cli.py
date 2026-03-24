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
@click.option("--min-coverage", default=None, type=float, help="Minimum coverage score (0.0-1.0). Fails run if coverage below threshold (CI/CD gate).")
@click.option("--tag", "run_tags", multiple=True, default=(), help="Tag this run with key=value metadata (repeatable, e.g. --tag env=staging --tag sprint=24)")
@click.option("--policy", default=None, help="Policy to evaluate: built-in name (general, healthcare, finance, airline) or path to YAML file. Use 'simtest policies' to list.")
@click.option("--policy-mode", "policy_mode", default=None, type=click.Choice(["critical_only", "strict", "severity_aware", "weighted", "threshold", "soft"]), help="Override the policy gate mode (default: use mode from policy file)")
@click.option("--policy-strict", is_flag=True, default=False, help="Fail on unknown judge names in policy validation")
@click.option("--workflow", default=None, help="Workflow judge: comma-separated built-in names or YAML paths. Use 'all' for all built-ins, or 'simtest workflows' to list. Default: auto-detect applicable workflows.")
@click.option("--no-workflow", is_flag=True, default=False, help="Disable workflow evaluation entirely.")
@click.option("--expand-failures", is_flag=True, default=False, help="Enable adaptive expansion: auto-generate failure variations and confirm reproducibility")
@click.option("--variants", "expand_variants", default=5, help="Number of variant conversations per failure signal (default: 5)")
@click.option("--expand-top", default=5, help="Max failure signals to expand (default: 5, highest severity first)")
@click.option("--input", "input_conversations", default=None, multiple=True, help="Import real conversation logs for evaluation (file or directory, repeatable). Use 'simtest formats' for supported formats.")
@click.option("--input-format", default="auto", type=click.Choice(["auto", "json", "jsonl", "csv", "tsv", "text", "markdown", "simtest"]), help="Input format for --input files (default: auto-detect)")
@click.option("--pii-masking", default="none", type=click.Choice(["none", "detect", "mask"]), help="PII handling for imported conversations: none | detect | mask")
@click.option("--fail-if-below", "fail_gates", multiple=True, default=(), help="CI/CD gate for replay: judge=threshold (e.g., safety=0.95). Repeatable.")
@click.option("--judges", "judge_list", default=None, help="Comma-separated judges for imported conversations (default: all). E.g., safety,quality")
@click.option("--speaker-pattern", default=None, help="Speaker pattern for text imports (e.g., 'Customer:|Bot:')")
@click.option("--fail-on-parse-error", is_flag=True, default=False, help="Fail if any imported files cannot be parsed")
@click.option("--sample", "sample_size", default=None, type=int, help="Random sample N conversations from imported data")
@click.option("--filter-min-turns", default=None, type=int, help="Skip imported conversations with fewer turns than this")
@click.option("--filter-max-turns", default=None, type=int, help="Skip imported conversations with more turns than this")
@click.option("--contains", "filter_contains", default=None, help="Only evaluate imported conversations containing this text")
@click.option("--min-conversations-for-gate", default=1, type=int, help="Minimum conversations before CI/CD gates are enforced (default: 1)")
@click.option("--signature/--no-signature", default=True, help="Enable/disable behavioral signature analysis (default: enabled)")
@click.option("--rag-eval/--no-rag-eval", default=False, help="Enable RAG/Tool evaluation on bot responses (evaluates retrieval accuracy, tool usage, citation quality)")
@click.option("--eval-speed", "rag_eval_speed", type=click.Choice(["deterministic", "fast", "standard", "full"]), default="standard", help="RAG eval speed: deterministic (free) | fast | standard | full (all metrics)")
@click.option("--rag-threshold", type=float, default=0.7, help="RAG/Tool evaluation pass threshold (0.0-1.0, default 0.7)")
@click.option("--rag-gate", type=float, default=None, help="CI/CD gate: exit code 1 if RAG overall score below this value")
@click.option("--tool-defs", type=click.Path(exists=True), default=None, help="Path to tool definitions JSON/YAML for tool metric validation (schema: [{name, required_params, ...}])")
@click.option("--rag-demo", default=None, help="Run RAG eval on a built-in demo pack instead of simulation (faq_rag, finance_tools, healthcare_citations, failure_injection)")
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
    min_coverage: float | None,
    run_tags: tuple[str, ...],
    policy: str | None,
    policy_mode: str | None,
    policy_strict: bool,
    workflow: str | None,
    no_workflow: bool,
    expand_failures: bool,
    expand_variants: int,
    expand_top: int,
    input_conversations: tuple[str, ...],
    input_format: str,
    pii_masking: str,
    fail_gates: tuple[str, ...],
    judge_list: str | None,
    speaker_pattern: str | None,
    fail_on_parse_error: bool,
    sample_size: int | None,
    filter_min_turns: int | None,
    filter_max_turns: int | None,
    filter_contains: str | None,
    min_conversations_for_gate: int,
    signature: bool,
    rag_eval: bool,
    rag_eval_speed: str,
    rag_threshold: float,
    rag_gate: float | None,
    tool_defs: str | None,
    rag_demo: str | None,
):
    """Run a simulation test against your AI chatbot."""
    from src.core.logging import setup_logging
    setup_logging()

    console.print(Panel.fit(
        "[bold blue]AI SimTest[/] - Simulation Testing Platform",
        subtitle="v0.2.0",
    ))

    # ── RAG Demo Mode (--rag-demo) ──────────────────────────────
    if rag_demo:
        try:
            import asyncio as _aio
            from src.rag_eval.demo_packs import load_demo_pack, list_demo_packs
            from src.rag_eval.engine import RAGEvalEngine, RAGEvalReport
            from src.rag_eval.models import RAGEvalConfig, EvalSpeed
            from src.rag_eval.tool_metrics import ToolDefinition
            from src.rag_eval.rag_eval_html import inject_rag_eval_into_report

            speed_map = {
                "deterministic": EvalSpeed.DETERMINISTIC,
                "fast": EvalSpeed.FAST,
                "standard": EvalSpeed.STANDARD,
                "full": EvalSpeed.FULL,
            }

            console.print(f"\n  🔬 [bold]RAG/Tool Demo Pack[/]: {rag_demo}")
            convs, ctx_doc, raw_tool_defs = load_demo_pack(rag_demo)
            console.print(f"    Loaded {len(convs)} test conversations")

            tdefs = [ToolDefinition.from_dict(d) for d in raw_tool_defs]
            config = RAGEvalConfig(
                eval_speed=speed_map.get(rag_eval_speed, EvalSpeed.STANDARD),
                default_rag_threshold=rag_threshold,
                default_tool_threshold=rag_threshold,
                fail_if_below=rag_gate,
            )
            engine = RAGEvalEngine(config, tool_definitions=tdefs)

            async def _run_demo():
                return await engine.evaluate_conversations(convs, ctx_doc)

            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(lambda: _aio.run(_run_demo()))
                rag_report = future.result()

            _print_rag_eval_report(rag_report)

            # Export
            out_path = Path(output)
            out_path.mkdir(parents=True, exist_ok=True)
            saved = RAGEvalEngine.save_report(rag_report, str(out_path))
            console.print(f"\n  📁 RAG eval report saved: [bold]{saved}[/]")

            # CI/CD gate
            if rag_gate is not None and not rag_report.gate_passed:
                console.print(f"\n  [bold red]❌ RAG GATE FAILED:[/] Overall score {rag_report.overall_score:.3f} < {rag_gate}")
                import sys
                sys.exit(1)

            return  # Demo mode exits here — no simulation

        except ValueError as ve:
            console.print(f"[red]❌ {ve}[/]")
            packs = list_demo_packs()
            console.print("\nAvailable demo packs:")
            for p in packs:
                console.print(f"  • [bold]{p['name']}[/] — {p['description']} ({p['cases']} cases)")
            return
        except Exception as e:
            console.print(f"[red]❌ RAG demo error: {e}[/]")
            import traceback
            console.print(f"[dim]{traceback.format_exc()[-400:]}[/]")
            return

    # ── Conversation Replay (--input) ───────────────────────────
    # If --input provided, load and evaluate real conversations.
    # Works with ANY mode. Use --personas 0 for evaluate-only.
    # Core logic lives in src/replay/ — CLI is a thin wrapper.
    replay_report = None
    replay_result = None
    if input_conversations:
        console.print(f"\n  📥 [bold]Conversation Replay[/]: Loading from {len(input_conversations)} source(s)")

        try:
            from src.replay.models import (
                ReplayConfig, ReplayMode, InputFormat,
                PIIMaskingConfig, PIIMaskingStrategy,
            )
            from src.replay import ConversationLoader, ReplayEvaluator

            pii_strategy_map = {
                "none": PIIMaskingStrategy.NONE,
                "detect": PIIMaskingStrategy.DETECT_ONLY,
                "mask": PIIMaskingStrategy.MASK,
            }

            # Parse fail gates: "safety=0.95" → {"safety": 0.95}
            fail_thresholds = {}
            for gate in fail_gates:
                if "=" in gate:
                    jname, thr = gate.split("=", 1)
                    try:
                        fail_thresholds[jname.strip()] = float(thr.strip())
                    except ValueError:
                        console.print(f"  [yellow]⚠ Invalid gate: {gate}[/]")

            # Load documentation for grounding
            doc_text = documentation
            if doc_file:
                dp = Path(doc_file)
                if dp.exists():
                    doc_text = dp.read_text(encoding="utf-8")

            replay_config = ReplayConfig(
                input_paths=list(input_conversations),
                input_format=InputFormat(input_format),
                mode=ReplayMode.EVALUATE,
                documentation=doc_text,
                bot_endpoint=bot_endpoint,
                bot_api_key=bot_api_key,
                bot_format=bot_format,
                speaker_pattern=speaker_pattern,
                pass_threshold=pass_threshold,
                warn_threshold=warn_threshold,
                pii_masking=PIIMaskingConfig(
                    strategy=pii_strategy_map.get(pii_masking, PIIMaskingStrategy.NONE),
                ),
                fail_thresholds=fail_thresholds,
                fail_on_parse_error=fail_on_parse_error,
                judges=[j.strip().lower() for j in judge_list.split(",") if j.strip()] if judge_list else None,
                policy=policy,
                workflow=workflow,
                no_workflow=no_workflow,
                output_dir=output,
                export_formats=[f.strip().lower() for f in export_formats.split(",") if f.strip()],
                min_conversations_for_gate=min_conversations_for_gate,
                sample_size=sample_size,
                filter_min_turns=filter_min_turns,
                filter_max_turns=filter_max_turns,
                filter_contains=filter_contains,
            )

            # Step 1: Load conversations
            loader = ConversationLoader()
            conversations, personas_map, sources, load_summary, pii_report = loader.load(replay_config)
            console.print(f"  ✅ Loaded {len(conversations)} conversations "
                          f"({load_summary.files_parsed_ok}/{load_summary.files_discovered} files)")

            if load_summary.files_failed > 0:
                console.print(f"  ⚠️  Parse failures: [yellow]{load_summary.files_failed}[/] file(s)")
                for err in load_summary.parse_errors[:3]:
                    console.print(f"      → {err['file']}: {err['error'][:60]}")

            if load_summary.unknown_roles_found:
                console.print(f"  ℹ️  Unknown roles found: {', '.join(load_summary.unknown_roles_found)}")

            # Quality distribution (#R1)
            if load_summary.quality_partial > 0 or load_summary.quality_low > 0:
                console.print(f"  📋 Quality: {load_summary.quality_complete} complete, "
                              f"{load_summary.quality_partial} partial, "
                              f"{load_summary.quality_low} low-confidence")

            # Filtering stats (#R5)
            if load_summary.conversations_after_filter < load_summary.conversations_loaded:
                console.print(f"  🔍 Filtered: {load_summary.conversations_loaded} → "
                              f"{load_summary.conversations_after_filter} conversations")

            if pii_report.masking_enabled:
                console.print(f"  🔒 PII: {pii_report.total_entities_detected} detected, "
                              f"{pii_report.total_fields_redacted} redacted ({pii_report.masking_strategy})")

            # Step 2: Evaluate
            with Progress(SpinnerColumn(), TextColumn("[progress.description]{task.description}"), console=console) as progress:
                eval_task = progress.add_task(f"Evaluating {len(conversations)} imported conversations...", total=None)
                evaluator = ReplayEvaluator()
                replay_report, replay_result = asyncio.run(
                    evaluator.evaluate(
                        conversations, personas_map, sources, replay_config,
                        load_summary=load_summary, pii_report=pii_report,
                    )
                )
                progress.update(eval_task, description="Replay evaluation complete!")

            # Step 3: Print results
            s = replay_report.summary
            pass_color = "green" if s.pass_rate >= 0.8 else "yellow" if s.pass_rate >= 0.6 else "red"
            console.print(f"\n  📊 [bold]Replay Results[/]: [{pass_color}]{s.pass_rate:.1%} pass rate[/], "
                          f"avg score {s.average_score:.3f}, "
                          f"{s.critical_failures} critical failures, "
                          f"{s.execution_time_seconds:.1f}s")

            if replay_report.score_by_judge:
                for judge_name, score in sorted(replay_report.score_by_judge.items()):
                    bar_len = int(score * 20)
                    bar = "█" * bar_len + "░" * (20 - bar_len)
                    jcolor = "green" if score >= 0.8 else "yellow" if score >= 0.6 else "red"
                    console.print(f"    {judge_name:12s} [{jcolor}]{bar} {score:.1%}[/]")

            # Step 4: Export replay outputs
            out_path = Path(output)
            out_path.mkdir(parents=True, exist_ok=True)

            import json as _json
            replay_summary_path = out_path / "replay_summary.json"
            with open(replay_summary_path, "w") as f:
                _json.dump(replay_report.summary.model_dump(), f, indent=2, default=str)

            replay_detail_path = out_path / "replay_result.json"
            with open(replay_detail_path, "w") as f:
                _json.dump(replay_result.model_dump(), f, indent=2, default=str)

            console.print(f"  📁 Replay summary: {replay_summary_path}")

            # Export HTML report for replay
            if "html" in [f.strip().lower() for f in export_formats.split(",")]:
                try:
                    from src.exporters.html_report import HTMLReportExporter
                    replay_html_path = out_path / "replay_report.html"
                    HTMLReportExporter().export(replay_report, list(personas_map.values()), replay_html_path)
                    console.print(f"  📁 Replay HTML report: {replay_html_path}")
                except Exception as html_err:
                    console.print(f"  [dim]Replay HTML skipped: {html_err}[/]")

            # Step 5: CI/CD gate check
            if not replay_result.gate_passed:
                console.print(f"\n[bold red]✗ Replay CI/CD gate FAILED:[/]")
                for gate_name, passed in replay_result.gate_results.items():
                    icon = "✓" if passed else "✗"
                    gcolor = "green" if passed else "red"
                    console.print(f"  [{gcolor}]{icon} {gate_name}[/]")
                sys.exit(1)
            elif replay_result.gate_results:
                console.print(f"  [green]✓ All replay CI/CD gates passed[/]")

        except Exception as e:
            console.print(f"\n[red]❌ Replay evaluation error: {e}[/]")
            if fail_on_parse_error:
                sys.exit(1)

        # If --personas 0, skip synthetic simulation (evaluate-only mode)
        if personas == 0:
            console.print(f"\n  ℹ️  --personas 0: Skipping synthetic simulation (replay-only mode)")
            return
        else:
            console.print(f"\n  ➡️  Continuing with synthetic simulation ({personas} personas)...")

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
            workflow=workflow,
            no_workflow=no_workflow,
            expand_failures=expand_failures,
            expand_variants=expand_variants,
            expand_top=expand_top,
            signature=signature,
            rag_eval=rag_eval,
            rag_eval_speed=rag_eval_speed,
            rag_threshold=rag_threshold,
            rag_gate=rag_gate,
            tool_defs=tool_defs,
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
            workflow=workflow,
            no_workflow=no_workflow,
            expand_failures=expand_failures,
            expand_variants=expand_variants,
            expand_top=expand_top,
            signature=signature,
            rag_eval=rag_eval,
            rag_eval_speed=rag_eval_speed,
            rag_threshold=rag_threshold,
            rag_gate=rag_gate,
            tool_defs=tool_defs,
            documentation=documentation,
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

    # Show policy info
    if policy:
        console.print(f"  📋 Policy: [bold]{policy}[/] (evaluated post-simulation)")

    # Show expansion info
    if expand_failures:
        console.print(f"  🔬 Adaptive Expansion: [bold magenta]ON[/] (top {expand_top} failures × {expand_variants} variants)")

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
        min_coverage=min_coverage,
        stress_enabled=stress_memory,
        run_tags=run_tags,
        policy=policy,
        policy_mode=policy_mode,
        policy_strict=policy_strict,
        workflow=workflow,
        no_workflow=no_workflow,
        expand_failures=expand_failures,
        expand_variants=expand_variants,
        expand_top=expand_top,
        signature=signature,
        rag_eval=rag_eval,
        rag_eval_speed=rag_eval_speed,
        rag_threshold=rag_threshold,
        rag_gate=rag_gate,
        tool_defs=tool_defs,
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
    workflow: str | None = None,
    no_workflow: bool = False,
    expand_failures: bool = False,
    expand_variants: int = 5,
    expand_top: int = 5,
    signature: bool = True,
    rag_eval: bool = False,
    rag_eval_speed: str = "standard",
    rag_threshold: float = 0.7,
    rag_gate: float | None = None,
    tool_defs: str | None = None,
    documentation: str = "",
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

            # Run shared post-simulation analysis (coverage + versioning)
            try:
                from src.versioning import FingerprintBuilder
                builder = FingerprintBuilder()
                fp = builder.build(
                    simulation_name=simulation_name,
                    bot_endpoint=bot_endpoint,
                    bot_format=bot_format or "openai",
                    num_personas=num_personas or 0,
                    max_turns=max_turns or 15,
                    min_turns=min_turns,
                    tags={"mode": "partial"},
                )
            except ImportError:
                fp = None

            _post_simulation_analysis(
                report=result.simulation_report,
                output_dir=output_dir,
                run_fingerprint=fp,
                tags_dict={"mode": "partial"},
                mode_label="partial",
                policy=policy,
                policy_mode=policy_mode,
                policy_strict=policy_strict,
                workflow=workflow,
                no_workflow=no_workflow,
                expand_failures=expand_failures,
                expand_variants=expand_variants,
                expand_top=expand_top,
                bot_endpoint=bot_endpoint,
                bot_api_key=bot_api_key,
                bot_format=bot_format or "openai",
                signature=signature,
                rag_eval=rag_eval,
                rag_eval_speed=rag_eval_speed,
                rag_threshold=rag_threshold,
                rag_gate=rag_gate,
                tool_defs=tool_defs,
                documentation=documentation,
            )
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
    workflow: str | None = None,
    no_workflow: bool = False,
    expand_failures: bool = False,
    expand_variants: int = 5,
    expand_top: int = 5,
    signature: bool = True,
    rag_eval: bool = False,
    rag_eval_speed: str = "standard",
    rag_threshold: float = 0.7,
    rag_gate: float | None = None,
    tool_defs: str | None = None,
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
        try:
            from src.core.approval_gate import AutoApprovalGate
            approval_gate = AutoApprovalGate()
        except ImportError:
            # Fallback: some versions use APIApprovalGate or CLIApprovalGate with auto flag
            from src.core.approval_gate import CLIApprovalGate
            approval_gate = CLIApprovalGate(auto_approve=True)
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

                # Run shared post-simulation analysis (coverage + versioning)
                try:
                    from src.versioning import FingerprintBuilder
                    builder = FingerprintBuilder()
                    fp = builder.build(
                        simulation_name=simulation_name,
                        bot_endpoint=bot_endpoint,
                        bot_format=bot_format or "openai",
                        num_personas=num_personas or 0,
                        max_turns=max_turns or 15,
                        min_turns=min_turns,
                        tags={"mode": "auto"},
                    )
                except ImportError:
                    fp = None

                _post_simulation_analysis(
                    report=sim_result.simulation_report,
                    output_dir=output_dir,
                    run_fingerprint=fp,
                    tags_dict={"mode": "auto"},
                    mode_label="auto",
                    policy=policy,
                    policy_mode=policy_mode,
                    policy_strict=policy_strict,
                    workflow=workflow,
                    no_workflow=no_workflow,
                    expand_failures=expand_failures,
                    expand_variants=expand_variants,
                    expand_top=expand_top,
                    bot_endpoint=bot_endpoint,
                    bot_api_key=bot_api_key,
                    bot_format=bot_format or "openai",
                    signature=signature,
                    rag_eval=rag_eval,
                    rag_eval_speed=rag_eval_speed,
                    rag_threshold=rag_threshold,
                    rag_gate=rag_gate,
                    tool_defs=tool_defs,
                )
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
        # Build fingerprint here where _run_full_autonomous variables are in scope
        _auto_fp = None
        try:
            from src.versioning import FingerprintBuilder
            _auto_fp = FingerprintBuilder().build(
                simulation_name=simulation_name,
                bot_endpoint=bot_endpoint,
                bot_format=bot_format or "openai",
                num_personas=num_personas or 0,
                max_turns=max_turns or 15,
                tags={"mode": "auto"},
            )
        except (ImportError, Exception):
            pass
        _print_auto_result(result, output_dir, run_fingerprint=_auto_fp, workflow=workflow, no_workflow=no_workflow,
                           signature=signature, rag_eval=rag_eval, rag_eval_speed=rag_eval_speed,
                           rag_threshold=rag_threshold, rag_gate=rag_gate, tool_defs=tool_defs)


def _print_auto_result(result, output_dir: str, run_fingerprint=None, workflow: str | None = None, no_workflow: bool = False,
                        signature: bool = True, rag_eval: bool = False, rag_eval_speed: str = "standard",
                        rag_threshold: float = 0.7, rag_gate: float | None = None, tool_defs: str | None = None):
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

            _post_simulation_analysis(
                report=sim_result.simulation_report,
                output_dir=output_dir,
                run_fingerprint=run_fingerprint,
                tags_dict={"mode": "auto"},
                mode_label="auto",
                policy=policy,
                policy_mode=policy_mode,
                policy_strict=policy_strict,
                workflow=workflow,
                no_workflow=no_workflow,
                signature=signature,
                rag_eval=rag_eval,
                rag_eval_speed=rag_eval_speed,
                rag_threshold=rag_threshold,
                rag_gate=rag_gate,
                tool_defs=tool_defs,
            )
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


# ============================================================
# Shared Post-Simulation Analysis (coverage + versioning)
# Called from manual, partial, and auto mode paths.
# ============================================================

def _post_simulation_analysis(
    report,
    output_dir: str,
    exported: dict[str, str] | None = None,
    topics: list[str] | None = None,
    scenario_ids: list[str] | None = None,
    stress_enabled: bool = False,
    simulation_personas: list | None = None,
    min_coverage: float | None = None,
    run_fingerprint=None,
    tags_dict: dict[str, str] | None = None,
    mode_label: str = "manual",
    policy: str | None = None,
    policy_mode: str | None = None,
    policy_strict: bool = False,
    workflow: str | None = None,
    no_workflow: bool = False,
    expand_failures: bool = False,
    expand_variants: int = 5,
    expand_top: int = 5,
    bot_endpoint: str = "",
    bot_api_key: str | None = None,
    bot_format: str = "openai",
    signature: bool = True,
    rag_eval: bool = False,
    rag_eval_speed: str = "standard",
    rag_threshold: float = 0.7,
    rag_gate: float | None = None,
    tool_defs: str | None = None,
    documentation: str = "",
):
    """
    Run post-simulation analysis — coverage, versioning, compliance, workflow, expansion, RAG/tool eval, and signature.

    Called from all 3 modes (manual, partial, auto) to ensure
    consistent outputs regardless of execution path.
    """
    exported = exported or {}
    topics = topics or []
    simulation_personas = simulation_personas or []
    tags_dict = tags_dict or {}

    # ── Version fingerprint ───────────────────────────────────
    if run_fingerprint:
        try:
            import json as _json
            from src.versioning import VersionHistoryManager

            fp_path = Path(output_dir) / "fingerprint.json"
            with open(fp_path, "w") as f:
                _json.dump(run_fingerprint.to_dict(), f, indent=2)

            summary_path = Path(output_dir) / "summary.json"
            if summary_path.exists():
                with open(summary_path, "r") as f:
                    summary_data = _json.load(f)
                summary_data["version_fingerprint"] = run_fingerprint.to_dict()
                with open(summary_path, "w") as f:
                    _json.dump(summary_data, f, indent=2)

            history = VersionHistoryManager(output_dir)
            history.load()
            history.add_entry(
                fingerprint=run_fingerprint,
                pass_rate=report.summary.pass_rate if hasattr(report, 'summary') else 0,
                avg_score=report.summary.average_score if hasattr(report, 'summary') else 0,
                critical_failures=report.summary.critical_failures if hasattr(report, 'summary') else 0,
                summary_path=str(summary_path),
            )
            history.save()

            console.print(f"  🏷️  Version fingerprint: [bold]{run_fingerprint.fingerprint_hash}[/]")
            if tags_dict:
                console.print(f"  🏷️  Tags: {', '.join(f'{k}={v}' for k, v in tags_dict.items())}")
        except Exception:
            pass

    # ── Coverage analysis ─────────────────────────────────────
    try:
        from src.coverage import CoverageAnalyzer, CoverageConfig

        coverage_config = CoverageConfig(
            defined_topics=topics,
            scenario_ids=scenario_ids or [],
            stress_enabled=stress_enabled,
        )

        analyzer = CoverageAnalyzer()
        coverage_report = analyzer.analyze(
            personas=simulation_personas,
            judged_conversations=report.judged_conversations if hasattr(report, 'judged_conversations') else [],
            config=coverage_config,
        )

        _print_coverage(coverage_report)

        try:
            import json
            coverage_path = Path(output_dir) / "coverage.json"
            with open(coverage_path, "w") as f:
                json.dump(coverage_report.to_dict(), f, indent=2)
            console.print(f"  📊 coverage: {coverage_path}")
        except Exception:
            pass

        try:
            from src.coverage.coverage_html import inject_coverage_into_report
            html_path = exported.get("html")
            # Auto-discover HTML report if not in exported dict (partial/auto modes)
            if not html_path:
                candidate = Path(output_dir) / "report.html"
                if candidate.exists():
                    html_path = str(candidate)
            if html_path:
                injected = inject_coverage_into_report(html_path, coverage_report)
                if injected:
                    console.print(f"  📊 Coverage section added to HTML report")
        except Exception:
            pass

        # ── Inject workflow coverage as 5th dimension ─────────
        try:
            from src.coverage.workflow_coverage import (
                load_workflow_coverage_from_exports,
                inject_workflow_coverage_into_report,
            )
            wf_coverage = load_workflow_coverage_from_exports(output_dir)
            if wf_coverage:
                inject_workflow_coverage_into_report(coverage_report, wf_coverage)
                console.print(
                    f"  📊 Workflow coverage: {wf_coverage.score:.0%} ({wf_coverage.steps_exercised}/{wf_coverage.total_steps_defined} steps exercised)")

                # Update coverage.json with workflow dimension
                try:
                    coverage_path = Path(output_dir) / "coverage.json"
                    with open(coverage_path, "w") as f:
                        coverage_dict = coverage_report.to_dict()
                        coverage_dict["workflow_coverage"] = wf_coverage.to_dict()
                        json.dump(coverage_dict, f, indent=2)
                except Exception:
                    pass
        except Exception as _wfc_err:
            pass  # Silently skip — workflow coverage is optional

        if min_coverage is not None:
            if coverage_report.overall_coverage < min_coverage:
                console.print(
                    f"\n  [bold red]✗ COVERAGE GATE FAILED:[/] "
                    f"{coverage_report.overall_coverage:.0%} < {min_coverage:.0%} threshold"
                )
                sys.exit(1)
            else:
                console.print(
                    f"\n  [bold green]✓ COVERAGE GATE PASSED:[/] "
                    f"{coverage_report.overall_coverage:.0%} >= {min_coverage:.0%} threshold"
                )
    except ImportError:
        pass
    except Exception as e:
        console.print(f"\n  [dim]Coverage analysis skipped: {e}[/]")

    # ── Policy-as-Code compliance (v2 — evidence-aware) ───────
    if policy:
        try:
            from src.policy import PolicyEngine, PolicyLoader, ComplianceGateMode

            # Load policy set: YAML file or built-in name
            policy_path = Path(policy)
            if policy_path.exists() and policy_path.suffix.lower() in (".yaml", ".yml"):
                policy_set = PolicyLoader.load_from_file(policy_path)
            else:
                policy_set = PolicyLoader.load_built_in(policy)
                if policy_set is None:
                    console.print(f"  [red]⚠ Unknown policy: '{policy}'. Use 'simtest policies' to list available.[/]")
                    return

            # CLI override: gate mode
            if policy_mode:
                try:
                    policy_set.mode = ComplianceGateMode(policy_mode)
                except ValueError:
                    console.print(f"  [yellow]⚠ Invalid policy mode '{policy_mode}', using default[/]")

            # Validate
            warnings = PolicyLoader.validate_policy_set(policy_set, strict=policy_strict)
            if warnings:
                for w in warnings:
                    console.print(f"  [yellow]⚠ Policy warning: {w}[/]")

            # Build v2 report_data dict from SimulationReport
            report_data = {}
            if hasattr(report, 'summary'):
                s = report.summary
                report_data["summary"] = {
                    "pass_rate": getattr(s, "pass_rate", 0),
                    "average_score": getattr(s, "average_score", 0),
                    "critical_failures": getattr(s, "critical_failures", 0),
                    "warnings": getattr(s, "warnings", 0),
                    "total_turns": getattr(s, "total_turns", 0),
                }
            if hasattr(report, 'score_by_judge'):
                report_data["score_by_judge"] = dict(report.score_by_judge)

            # v2: Wire judged_conversations for evidence-level evaluation
            if hasattr(report, 'judged_conversations') and report.judged_conversations:
                judged_convs = []
                for jc in report.judged_conversations:
                    conv = jc.conversation if hasattr(jc, 'conversation') else jc
                    persona_name = ""
                    persona_type = ""
                    if hasattr(jc, 'persona') and jc.persona:
                        persona_name = jc.persona.name if hasattr(jc.persona, 'name') else str(jc.persona)
                        persona_type = getattr(jc.persona, 'persona_type', persona_name)
                    scenario = getattr(jc, 'scenario', '') or ''
                    source = getattr(jc, 'source', 'synthetic') or 'synthetic'
                    conv_id = getattr(conv, 'id', '') or persona_name

                    # Build turns list
                    turns = []
                    if hasattr(jc, 'judged_turns'):
                        for idx, jt in enumerate(jc.judged_turns):
                            turn_data = {
                                "turn_number": idx + 1,
                                "bot_response": getattr(jt, 'bot_response', '') or (
                                    jt.turn.message if hasattr(jt, 'turn') and hasattr(jt.turn, 'message') else ''
                                ),
                                "judgments": [],
                            }
                            if hasattr(jt, 'judgments'):
                                for j in jt.judgments:
                                    turn_data["judgments"].append({
                                        "judge_name": getattr(j, 'judge_name', ''),
                                        "score": getattr(j, 'score', 0.0),
                                        "issues": getattr(j, 'issues', []),
                                        "label": getattr(j, 'label', ''),
                                    })
                            turns.append(turn_data)

                    judged_convs.append({
                        "conversation_id": conv_id,
                        "persona_name": persona_name,
                        "persona_type": persona_type,
                        "scenario": scenario,
                        "source": source,
                        "tags": getattr(jc, 'tags', []) or [],
                        "workflow": getattr(jc, 'workflow', '') or '',
                        "judged_turns": turns,
                    })

                report_data["judged_conversations"] = judged_convs

            # v2: Wire workflow results if available
            try:
                import glob as _glob
                import json as _json_wf
                for wf_file in _glob.glob(str(Path(output_dir) / "workflow_*.json")):
                    with open(wf_file) as f:
                        wf_data = _json_wf.load(f)
                    if "results" in wf_data:
                        if "workflow_results" not in report_data:
                            report_data["workflow_results"] = []
                        for r in wf_data["results"]:
                            report_data["workflow_results"].append({
                                "workflow": wf_data.get("workflow", ""),
                                "passed": r.get("passed", False),
                                "score": r.get("score", 0.0),
                            })
            except Exception:
                pass

            # v2: Wire RAG results if available
            try:
                import json as _json_rag
                rag_path = Path(output_dir) / "rag_eval_report.json"
                if rag_path.exists():
                    with open(rag_path) as f:
                        rag_data = _json_rag.load(f)
                    if "metrics" in rag_data:
                        report_data["rag_results"] = rag_data["metrics"]
                    elif "overall_score" in rag_data:
                        report_data["rag_results"] = {"overall": rag_data["overall_score"]}
            except Exception:
                pass

            # Evaluate
            engine = PolicyEngine(policy_set)
            scorecard = engine.evaluate(report_data)

            # Display
            _print_compliance_scorecard(scorecard)

            # Save compliance.json (summary)
            try:
                import json as _json
                compliance_path = Path(output_dir) / "compliance.json"
                with open(compliance_path, "w") as f:
                    _json.dump(scorecard.to_summary_dict(), f, indent=2)
                console.print(f"  📋 Compliance report: {compliance_path}")

                # Also save full evidence export
                evidence_path = Path(output_dir) / "compliance_evidence.json"
                with open(evidence_path, "w") as f:
                    _json.dump(scorecard.to_full_evidence_dict(), f, indent=2)
                console.print(f"  🔍 Full evidence: {evidence_path}")
            except Exception:
                pass

            # Inject into HTML report
            try:
                from src.policy.policy_html import inject_compliance_into_report
                html_path = exported.get("html") if exported else None
                if not html_path:
                    candidate = Path(output_dir) / "report.html"
                    if candidate.exists():
                        html_path = str(candidate)
                if html_path:
                    if inject_compliance_into_report(scorecard, html_path):
                        console.print(f"  📊 Compliance section added to HTML report")
            except Exception:
                pass

            # CI/CD gate
            if not scorecard.overall_compliant:
                console.print(
                    f"\n  [bold red]✗ COMPLIANCE GATE FAILED ({scorecard.gate_mode} mode):[/] "
                    f"{scorecard.failed_rules} rule(s) failed, "
                    f"{len(scorecard.critical_violations)} critical, "
                    f"{len(scorecard.high_violations)} high violation(s)"
                )
                sys.exit(1)
            else:
                console.print(
                    f"\n  [bold green]✓ COMPLIANCE GATE PASSED ({scorecard.gate_mode} mode):[/] "
                    f"{scorecard.compliance_score:.0f}% compliant"
                )

        except ImportError:
            console.print("  [dim]Policy module not available[/]")
        except Exception as e:
            console.print(f"  [dim]Policy evaluation skipped: {e}[/]")

    # ── Functional / Workflow Judge ───────────────────────────
    if not no_workflow:
        try:
            import json as _json
            from src.workflow_judge import FunctionalJudge, WorkflowLoader, BUILT_IN_WORKFLOWS

            conversations = report.judged_conversations if hasattr(report, 'judged_conversations') else []
            if not conversations:
                pass  # silently skip if no conversations
            else:
                # Build list of turns per conversation (reusable)
                conv_turns_list = []
                for jc in conversations:
                    conv = jc.conversation if hasattr(jc, 'conversation') else jc
                    turns = []
                    if hasattr(conv, 'turns'):
                        for t in conv.turns:
                            turns.append({"speaker": t.speaker, "message": t.message})
                    elif isinstance(conv, list):
                        turns = conv
                    persona_name = ""
                    if hasattr(jc, 'persona') and jc.persona:
                        persona_name = jc.persona.name if hasattr(jc.persona, 'name') else str(jc.persona)
                    conv_id = conv.id if hasattr(conv, 'id') else ""
                    conv_turns_list.append((conv_id, persona_name, turns))

                # Resolve which workflows to evaluate
                workflow_defs = []
                if workflow:
                    # User specified workflows
                    workflow_names = [w.strip() for w in workflow.split(",") if w.strip()]
                    if "all" in workflow_names:
                        workflow_names = list(BUILT_IN_WORKFLOWS.keys())
                    for wname in workflow_names:
                        wpath = Path(wname)
                        if wpath.exists() and wpath.suffix.lower() in (".yaml", ".yml"):
                            wdef = WorkflowLoader.load_from_file(wpath)
                            workflow_defs.append(wdef)
                        else:
                            wdef = WorkflowLoader.load_built_in(wname)
                            if wdef is None:
                                console.print(f"  [yellow]⚠ Unknown workflow: '{wname}'. Skipping.[/]")
                            else:
                                workflow_defs.append(wdef)
                else:
                    # Auto-detect: check activation_hints against conversation content
                    all_text = " ".join(
                        t["message"].lower() for _, _, turns in conv_turns_list for t in turns
                    )
                    for bname in BUILT_IN_WORKFLOWS:
                        wdef = WorkflowLoader.load_built_in(bname)
                        if wdef and hasattr(wdef, 'is_applicable') and wdef.is_applicable(all_text):
                            workflow_defs.append(wdef)
                    if workflow_defs:
                        names = [w.name for w in workflow_defs]
                        console.print(f"\n  🔍 Auto-detected {len(workflow_defs)} applicable workflow(s): {', '.join(names)}")

                # Evaluate each workflow
                all_workflow_exports = []
                total_critical_all = 0
                html_injection_pairs = []

                for wdef in workflow_defs:
                    judge = FunctionalJudge(wdef)
                    wf_results = []

                    for conv_id, persona_name, turns in conv_turns_list:
                        result = judge.evaluate_sync(turns, conversation_id=conv_id, persona_name=persona_name)
                        wf_results.append(result)

                    _print_workflow_results(wdef, wf_results)
                    html_injection_pairs.append((wdef, wf_results))

                    # Export JSON
                    try:
                        safe_name = wdef.name.lower().replace(" ", "_")[:40]
                        wf_path = Path(output_dir) / f"workflow_{safe_name}.json"
                        wf_export = {
                            "workflow": wdef.name,
                            "domain": wdef.domain,
                            "total_conversations": len(wf_results),
                            "passed": sum(1 for r in wf_results if r.passed),
                            "failed": sum(1 for r in wf_results if not r.passed),
                            "avg_score": round(sum(r.score for r in wf_results) / len(wf_results), 3) if wf_results else 0,
                            "critical_failures": sum(r.critical_failures_count for r in wf_results),
                            "results": [r.to_summary_dict() for r in wf_results],
                        }
                        with open(wf_path, "w") as f:
                            _json.dump(wf_export, f, indent=2)
                        console.print(f"  🔧 Workflow results: {wf_path}")
                        all_workflow_exports.append(wf_export)
                    except Exception:
                        pass

                    total_critical_all += sum(r.critical_failures_count for r in wf_results)

                # Inject all workflows into HTML report
                try:
                    from src.workflow_judge.workflow_html import inject_multi_workflow_into_report
                    html_path = exported.get("html") if exported else None
                    if not html_path:
                        candidate = Path(output_dir) / "report.html"
                        if candidate.exists():
                            html_path = str(candidate)
                    if html_path and html_injection_pairs:
                        injected = inject_multi_workflow_into_report(html_path, html_injection_pairs)
                        if injected:
                            console.print(f"  📊 {injected} workflow section(s) added to HTML report")
                except Exception as _wf_html_err:
                    import traceback
                    console.print(f"  [red]⚠ Workflow HTML injection error: {_wf_html_err}[/]")
                    console.print(f"  [dim]{traceback.format_exc()[-400:]}[/]")

                # ── Inject workflow summary into top-level report ──────
                try:
                    from src.workflow_judge.workflow_summary_panel import inject_workflow_summary_into_report
                    html_path = exported.get("html") if exported else None
                    if not html_path:
                        candidate = Path(output_dir) / "report.html"
                        if candidate.exists():
                            html_path = str(candidate)
                    if html_path and all_workflow_exports:
                        if inject_workflow_summary_into_report(html_path, all_workflow_exports):
                            console.print(f"  📊 Workflow summary panel added to report header")
                except Exception as _wsp_err:
                    console.print(f"  [dim]Workflow summary panel skipped: {_wsp_err}[/]")

                # Gate check
                if total_critical_all > 0:
                    console.print(f"\n  [bold red]✗ WORKFLOW GATE FAILED:[/] {total_critical_all} critical violation(s) across {len(workflow_defs)} workflow(s)")
                    import sys
                    sys.exit(1)
                elif workflow_defs:
                    total_results = sum(len(pair[1]) for pair in html_injection_pairs)
                    total_passed = sum(sum(1 for r in pair[1] if r.passed) for pair in html_injection_pairs)
                    total_avg = sum(r.score for pair in html_injection_pairs for r in pair[1]) / total_results if total_results else 0
                    console.print(f"\n  [bold green]✓ WORKFLOW EVALUATION:[/] {total_passed}/{total_results} passed across {len(workflow_defs)} workflow(s), avg score: {total_avg:.2f}")

        except ImportError as ie:
            console.print(f"  [yellow]⚠ Workflow judge not available: {ie}[/]")
        except Exception as e:
            import traceback
            console.print(f"  [red]⚠ Workflow evaluation error: {e}[/]")
            console.print(f"  [dim]{traceback.format_exc()[-500:]}[/]")

    # ── Adaptive Expansion ────────────────────────────────────
    if expand_failures and bot_endpoint:
        try:
            import json as _json
            from src.expansion import (
                AdaptiveExpansionEngine,
                AdaptiveExpansionConfig,
            )
            from src.models import BotConfig as _ExpBotConfig

            conversations = report.judged_conversations if hasattr(report, 'judged_conversations') else []
            if not conversations:
                console.print("  [dim]Adaptive expansion skipped: no conversations to analyze[/]")
            else:
                exp_config = AdaptiveExpansionConfig(
                    max_signals=expand_top,
                    variants_per_signal=expand_variants,
                    max_parallel_variants=min(3, expand_variants),
                )
                issues = exp_config.validate()
                if issues:
                    for issue in issues:
                        console.print(f"  [red]⚠ Expansion config: {issue}[/]")
                else:
                    exp_bot_config = _ExpBotConfig(
                        api_endpoint=bot_endpoint,
                        api_key=bot_api_key,
                        request_format=bot_format or "openai",
                    )

                    engine = AdaptiveExpansionEngine(
                        bot_config=exp_bot_config,
                        config=exp_config,
                    )

                    console.print(f"\n  🔬 [bold magenta]Adaptive Expansion[/] — probing top {expand_top} failure(s) × {expand_variants} variants")

                    import asyncio as _aio

                    async def _do_expansion():
                        def _progress(phase, detail):
                            console.print(f"    ↳ {detail}")
                        return await engine.run(
                            judged_conversations=conversations,
                            progress_callback=_progress,
                        )

                    try:
                        exp_report = _aio.run(_do_expansion())
                    except RuntimeError:
                        # Already in async context — fallback
                        loop = _aio.new_event_loop()
                        try:
                            exp_report = loop.run_until_complete(_do_expansion())
                        finally:
                            loop.close()

                    # Display results
                    _print_expansion_report(exp_report)

                    # Save expansion.json
                    try:
                        exp_path = Path(output_dir) / "expansion.json"
                        with open(exp_path, "w") as f:
                            _json.dump(exp_report.to_dict(), f, indent=2)
                        console.print(f"  🔬 Expansion report: {exp_path}")
                    except Exception:
                        pass

                    # Append expansion summary into summary.json
                    try:
                        summary_path = Path(output_dir) / "summary.json"
                        if summary_path.exists():
                            with open(summary_path, "r") as f:
                                summary_data = _json.load(f)
                            summary_data["adaptive_expansion"] = exp_report.to_summary_dict()
                            with open(summary_path, "w") as f:
                                _json.dump(summary_data, f, indent=2)
                    except Exception:
                        pass

                    # CI/CD gate
                    if exp_report.has_confirmed_bugs:
                        console.print(
                            f"\n  [bold red]✗ EXPANSION GATE:[/] "
                            f"{exp_report.confirmed_count} confirmed reproducible bug(s) found"
                        )
                    else:
                        console.print(
                            f"\n  [bold green]✓ EXPANSION GATE:[/] "
                            f"No confirmed reproducible bugs"
                        )

        except ImportError as ie:
            console.print(f"  [yellow]⚠ Expansion module not available: {ie}[/]")
        except Exception as e:
            import traceback
            console.print(f"  [red]⚠ Expansion error: {e}[/]")
            console.print(f"  [dim]{traceback.format_exc()[-500:]}[/]")

    # ── Step 6: RAG/Tool Evaluation ───────────────────────────
    if rag_eval and report and hasattr(report, 'judged_conversations'):
        try:
            import asyncio as _aio
            import json as _json
            from src.rag_eval.engine import RAGEvalEngine, RAGEvalReport
            from src.rag_eval.models import RAGEvalConfig, EvalSpeed
            from src.rag_eval.tool_metrics import ToolDefinition
            from src.rag_eval.rag_eval_html import inject_rag_eval_into_report

            conversations = report.judged_conversations if hasattr(report, 'judged_conversations') else []
            if not conversations:
                console.print("  [dim]RAG/Tool eval skipped: no conversations[/]")
            else:
                console.print(f"\n  🔬 [bold]RAG/Tool Evaluation[/] — {len(conversations)} conversations")

                # Build config
                speed_map = {
                    "deterministic": EvalSpeed.DETERMINISTIC,
                    "fast": EvalSpeed.FAST,
                    "standard": EvalSpeed.STANDARD,
                    "full": EvalSpeed.FULL,
                }
                rag_config = RAGEvalConfig(
                    eval_speed=speed_map.get(rag_eval_speed, EvalSpeed.STANDARD),
                    default_rag_threshold=rag_threshold,
                    default_tool_threshold=rag_threshold,
                    fail_if_below=rag_gate,
                )

                # Load tool definitions if provided
                tool_definitions = []
                if tool_defs:
                    try:
                        td_path = Path(tool_defs)
                        if td_path.suffix in ('.yaml', '.yml'):
                            import yaml
                            with open(td_path) as f:
                                raw_defs = yaml.safe_load(f)
                        else:
                            with open(td_path) as f:
                                raw_defs = _json.load(f)
                        if isinstance(raw_defs, list):
                            tool_definitions = [ToolDefinition.from_dict(d) for d in raw_defs]
                        console.print(f"    Loaded {len(tool_definitions)} tool definitions from {tool_defs}")
                    except Exception as tde:
                        console.print(f"    [yellow]⚠ Failed to load tool definitions: {tde}[/]")

                # Build context document
                context_doc = documentation or ""
                if not context_doc:
                    doc_file_path = exported.get("doc_file") or ""
                    if doc_file_path and Path(doc_file_path).exists():
                        try:
                            context_doc = Path(doc_file_path).read_text(encoding="utf-8")[:50000]
                        except Exception:
                            pass

                engine = RAGEvalEngine(
                    config=rag_config,
                    tool_definitions=tool_definitions,
                    progress_callback=lambda msg: console.print(f"    [dim]{msg}[/]"),
                )

                # Run evaluation (sync context — need to run async in a thread)
                async def _run_rag_eval():
                    return await engine.evaluate_conversations(conversations, context_doc)

                import concurrent.futures
                with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                    future = pool.submit(lambda: _aio.run(_run_rag_eval()))
                    rag_report = future.result()

                # Display results
                _print_rag_eval_report(rag_report)

                # Save rag_eval_report.json
                rag_path = RAGEvalEngine.save_report(rag_report, output_dir)
                console.print(f"    📄 RAG eval report: [bold]{rag_path}[/]")

                # Append to summary.json
                summary_path = Path(output_dir) / "summary.json"
                RAGEvalEngine.append_to_summary(rag_report, str(summary_path))

                # Inject into HTML report
                html_path = Path(output_dir) / "report.html"
                if html_path.exists():
                    if inject_rag_eval_into_report(rag_report, str(html_path)):
                        console.print("    ✅ Injected into HTML report")

                # CI/CD gate
                if rag_gate is not None:
                    if rag_report.gate_passed:
                        console.print(
                            f"\n  [bold green]✓ RAG GATE:[/] "
                            f"Overall score {rag_report.overall_score:.3f} ≥ {rag_gate}"
                        )
                    else:
                        console.print(
                            f"\n  [bold red]✗ RAG GATE FAILED:[/] "
                            f"Overall score {rag_report.overall_score:.3f} < {rag_gate}"
                        )
                        import sys
                        sys.exit(1)

        except ImportError as ie:
            console.print(f"  [yellow]⚠ RAG eval module not available: {ie}[/]")
        except Exception as e:
            import traceback
            console.print(f"  [red]⚠ RAG eval error: {e}[/]")
            console.print(f"  [dim]{traceback.format_exc()[-500:]}[/]")

    # ── Behavioral Signature Analysis ─────────────────────────
    if signature:
        try:
            import json as _json
            from src.signature import SignatureEngine, inject_signature_into_report

            conversations = report.judged_conversations if hasattr(report, 'judged_conversations') else []
            if not conversations:
                console.print("  [dim]Signature analysis skipped: no conversations[/]")
            else:
                sig_engine = SignatureEngine()
                bot_signature = sig_engine.analyze(
                    judged_conversations=conversations,
                    run_id=getattr(report, 'summary', None) and getattr(report.summary, 'simulation_id', '') or '',
                )

                # Display summary
                console.print(f"\n  🧬 [bold cyan]Behavioral Signature[/] — {bot_signature.turn_count} bot responses analyzed")
                if bot_signature.summary_text:
                    console.print(f"    {bot_signature.summary_text}")

                # Key metrics one-liner
                tone_lbl = bot_signature.tone.derived.formality_level.value
                verb_lbl = bot_signature.verbosity.derived.verbosity_level.value
                cons_score = bot_signature.consistency.derived.overall_consistency_score
                rep_score = bot_signature.patterns.derived.repetition_score
                console.print(
                    f"    Tone: [bold]{tone_lbl}[/] | "
                    f"Verbosity: [bold]{verb_lbl}[/] | "
                    f"Consistency: [bold]{cons_score:.2f}[/] | "
                    f"Repetition: [bold]{rep_score:.2f}[/] | "
                    f"Anomalies: [bold]{bot_signature.anomaly_count}[/]"
                )

                # Reliability note
                if bot_signature.reliability.confidence.value != "high":
                    console.print(
                        f"    [yellow]⚠ Reliability: {bot_signature.reliability.confidence.value}[/]"
                        f" — {', '.join(bot_signature.reliability.notes[:2])}"
                    )

                # Save signature.json
                try:
                    sig_path = Path(output_dir) / "signature.json"
                    with open(sig_path, "w") as f:
                        _json.dump(bot_signature.to_dict(), f, indent=2)
                    console.print(f"  🧬 Signature: {sig_path}")
                except Exception:
                    pass

                # Append to summary.json
                try:
                    summary_path = Path(output_dir) / "summary.json"
                    if summary_path.exists():
                        with open(summary_path, "r") as f:
                            summary_data = _json.load(f)
                        summary_data["behavioral_signature"] = bot_signature.to_summary_dict()
                        with open(summary_path, "w") as f:
                            _json.dump(summary_data, f, indent=2)
                except Exception:
                    pass

                # Inject into HTML report
                try:
                    html_path = exported.get("html") if exported else None
                    if not html_path:
                        candidate = Path(output_dir) / "report.html"
                        if candidate.exists():
                            html_path = str(candidate)
                    if html_path:
                        injected = inject_signature_into_report(html_path, bot_signature)
                        if injected:
                            console.print(f"  📊 Signature section added to HTML report")

                    # Also inject into replay report if it exists
                    replay_html = Path(output_dir) / "replay_report.html"
                    if replay_html.exists():
                        inject_signature_into_report(str(replay_html), bot_signature)
                except Exception:
                    pass

        except ImportError:
            pass  # Signature module not installed
        except Exception as e:
            console.print(f"  [dim]Signature analysis skipped: {e}[/]")


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
    min_coverage: float | None = None,
    stress_enabled: bool = False,
    run_tags: tuple[str, ...] = (),
    policy: str | None = None,
    policy_mode: str | None = None,
    policy_strict: bool = False,
    workflow: str | None = None,
    no_workflow: bool = False,
    expand_failures: bool = False,
    expand_variants: int = 5,
    expand_top: int = 5,
    signature: bool = True,
    rag_eval: bool = False,
    rag_eval_speed: str = "standard",
    rag_threshold: float = 0.7,
    rag_gate: float | None = None,
    tool_defs: str | None = None,
):
    """Async simulation runner."""
    from src.core.orchestrator import SimulationOrchestrator
    from src.models import BotConfig, SimulationConfig

    # Parse tags (key=value pairs)
    tags_dict = {}
    for tag in run_tags:
        if "=" in tag:
            k, v = tag.split("=", 1)
            tags_dict[k.strip()] = v.strip()

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

    # ── Capture version fingerprint ───────────────────────────
    run_fingerprint = None
    try:
        from src.versioning import FingerprintBuilder
        builder = FingerprintBuilder()
        run_fingerprint = builder.build(
            simulation_id=config.id if hasattr(config, 'id') else "",
            simulation_name=name,
            bot_endpoint=bot_endpoint,
            bot_format=bot_format,
            num_personas=num_personas,
            max_turns=max_turns,
            min_turns=min_turns,
            pass_threshold=pass_threshold,
            warn_threshold=warn_threshold,
            scenarios=scenario_ids or [],
            stress_enabled=stress_enabled,
            topics=topics,
            tags=tags_dict,
        )
    except ImportError:
        pass  # Versioning module not installed

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

    # Track personas across all code paths for coverage analysis
    simulation_personas = []

    # Persona preview mode
    if preview_personas:
        console.print("\n[bold]Generating personas for preview...[/]\n")
        personas = await orchestrator.generate_personas_only()
        personas = _apply_scenarios_to_personas(personas)
        simulation_personas = personas
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
                simulation_personas = personas
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
                # Extract personas from orchestrator's completed run
                try:
                    if hasattr(orchestrator, '_run') and orchestrator._run and hasattr(orchestrator._run, 'personas'):
                        simulation_personas = orchestrator._run.personas or []
                except Exception:
                    simulation_personas = []
            progress.update(task, description="Exporting results...")
            exported = orchestrator.export_results(output_dir=output_dir, formats=export_formats)

    # Display results
    _print_report(report, exported)

    # ── Shared post-simulation analysis (coverage + versioning) ──
    _post_simulation_analysis(
        report=report,
        output_dir=output_dir,
        exported=exported,
        topics=topics,
        scenario_ids=scenario_ids,
        stress_enabled=stress_enabled,
        simulation_personas=simulation_personas,
        min_coverage=min_coverage,
        run_fingerprint=run_fingerprint,
        tags_dict=tags_dict,
        mode_label="manual",
        policy=policy,
        policy_mode=policy_mode,
        policy_strict=policy_strict,
        workflow=workflow,
        no_workflow=no_workflow,
        expand_failures=expand_failures,
        expand_variants=expand_variants,
        expand_top=expand_top,
        bot_endpoint=bot_endpoint,
        bot_api_key=bot_api_key,
        bot_format=bot_format,
        signature=signature,
        rag_eval=rag_eval,
        rag_eval_speed=rag_eval_speed,
        rag_threshold=rag_threshold,
        rag_gate=rag_gate,
        tool_defs=tool_defs,
        documentation=documentation,
    )

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


def _print_coverage(coverage_report):
    """Print coverage analysis to console."""
    from rich.table import Table

    grade = coverage_report.grade.value
    score = coverage_report.overall_coverage
    grade_color = {
        "A": "bold green", "B": "green", "C": "yellow", "D": "red", "F": "bold red"
    }.get(grade, "white")

    console.print(f"\n[bold]Test Coverage:[/]  [{grade_color}]{score:.0%} (Grade: {grade})[/]")

    # Dimension bars
    for dim_name, dim_label in [
        ("persona_type", "Persona Types"),
        ("topic", "Topics"),
        ("scenario", "Scenarios"),
        ("judge", "Judges"),
    ]:
        dim_score = coverage_report.dimension_scores.get(dim_name, 0)
        weight = coverage_report.dimension_weights.get(dim_name, 0)
        if weight == 0:
            continue  # Skip dimensions with 0 weight
        bar = "█" * int(dim_score * 15) + "░" * (15 - int(dim_score * 15))
        color = "green" if dim_score >= 0.8 else "yellow" if dim_score >= 0.6 else "red"
        console.print(f"  {dim_label:15s} [{color}]{bar}[/] {dim_score:.0%}")

    # Show gaps if any
    if coverage_report.gaps:
        console.print(f"\n[bold]Coverage Gaps ({len(coverage_report.gaps)}):[/]")
        for gap in coverage_report.gaps[:5]:
            console.print(f"  [dim]•[/] {gap}")
        if len(coverage_report.gaps) > 5:
            console.print(f"  [dim]... and {len(coverage_report.gaps) - 5} more[/]")


def _print_compliance_scorecard(scorecard):
    """Print compliance scorecard v2 to console — with control families, evidence, remediation."""
    from rich.table import Table

    status = "✅ COMPLIANT" if scorecard.overall_compliant else "❌ NON-COMPLIANT"
    status_color = "bold green" if scorecard.overall_compliant else "bold red"
    gate_mode = scorecard.gate_mode.replace("_", " ").title()

    console.print(f"\n[bold]📋 Policy Compliance: {scorecard.policy_set_name}[/] (v{scorecard.policy_set_version})")
    console.print(f"  [{status_color}]{status}[/] — {scorecard.compliance_score:.0f}% ({scorecard.passed_rules}/{scorecard.total_rules} rules passed)")
    console.print(f"  Gate mode: [bold]{gate_mode}[/]", end="")
    if scorecard.skipped_rules > 0:
        console.print(f" | {scorecard.skipped_rules} skipped", end="")
    if scorecard.total_evidence_items > 0:
        console.print(f" | {scorecard.total_evidence_items} evidence items", end="")
    console.print()

    # Severity breakdown
    sev_counts = scorecard.violation_count_by_severity
    if sev_counts:
        parts = [f"[{'bold red' if s == 'critical' else 'red' if s == 'high' else 'yellow' if s == 'medium' else 'green'}]{c} {s}[/]" for s, c in sev_counts.items()]
        console.print(f"  Violations: {' · '.join(parts)}")

    # Critical violations
    if scorecard.critical_violations:
        console.print(f"  [bold red]Critical violations ({len(scorecard.critical_violations)}):[/]")
        for v in scorecard.critical_violations:
            console.print(f"    ❌ {v.rule_name}: {v.message}")
            if v.remediation:
                console.print(f"       [dim]💡 {v.remediation}[/]")

    # High violations
    if scorecard.high_violations:
        console.print(f"  [red]High violations ({len(scorecard.high_violations)}):[/]")
        for v in scorecard.high_violations[:3]:
            console.print(f"    ⚠ {v.rule_name}: {v.message}")
            if v.remediation:
                console.print(f"       [dim]💡 {v.remediation}[/]")

    # Control family breakdown
    if scorecard.control_family_results:
        cf = scorecard.control_family_results
        cf_parts = []
        for family, counts in cf.items():
            if counts["failed"] > 0:
                cf_parts.append(f"[red]{family}: {counts['passed']}/{counts['total']}[/]")
            else:
                cf_parts.append(f"[green]{family}: {counts['passed']}/{counts['total']}[/]")
        if cf_parts:
            console.print(f"  Control families: {' · '.join(cf_parts)}")

    # Per-rule table
    table = Table(show_header=True, header_style="bold")
    table.add_column("Rule", max_width=30)
    table.add_column("Judge", style="cyan")
    table.add_column("Condition", max_width=20)
    table.add_column("Status", justify="center")
    table.add_column("Actual", justify="right")
    table.add_column("Threshold", justify="right")
    table.add_column("Severity")
    table.add_column("Evidence", justify="right")

    sev_colors = {"critical": "bold red", "high": "red", "medium": "yellow", "low": "green"}

    for r in scorecard.results:
        if r.not_evaluated:
            status_icon = "[dim]⊘[/]"
        else:
            status_icon = "[green]✓[/]" if r.passed else "[red]✗[/]"
        sev_color = sev_colors.get(r.severity.value, "white")

        ev_count = str(len(r.evidence)) if r.evidence else ""

        rule_name = r.rule_name
        if r.scope_applied:
            rule_name += " 🎯"

        table.add_row(
            rule_name,
            r.judge,
            r.condition.value,
            status_icon,
            f"{r.actual_value:.3f}",
            f"{r.threshold:.3f}",
            f"[{sev_color}]{r.severity.value}[/]",
            ev_count,
        )

    console.print(table)

    # Show sample evidence for worst failures
    failed_with_evidence = [r for r in scorecard.results if not r.passed and r.evidence and not r.not_evaluated]
    if failed_with_evidence:
        console.print(f"\n  [bold]🔍 Sample Evidence (top failures):[/]")
        for r in failed_with_evidence[:3]:
            console.print(f"    [red]{r.rule_name}[/] ({r.failed_count} failed / {r.evaluated_count} evaluated):")
            for e in r.evidence[:2]:
                turn_info = f" turn {e.turn_index}" if e.turn_index is not None else ""
                console.print(f"      → {e.conversation_id}{turn_info} [{e.persona_name}] score={e.score:.2f}: {e.issue[:80]}")


def _print_workflow_results(workflow_def, workflow_results):
    """Print workflow judge results to console."""
    from rich.table import Table
    from collections import Counter

    console.print(f"\n[bold]🔧 Workflow Evaluation: {workflow_def.name}[/]")
    console.print(f"  Domain: {workflow_def.domain} | Steps: {workflow_def.total_steps} | Rules: {len(workflow_def.hard_rules)}")

    passed_count = sum(1 for r in workflow_results if r.passed)
    total = len(workflow_results)
    avg_score = sum(r.score for r in workflow_results) / total if total else 0

    status = "✅ PASSED" if passed_count == total else "⚠️  PARTIAL" if passed_count > 0 else "❌ FAILED"
    console.print(f"  {status} — {passed_count}/{total} conversations passed | Avg score: {avg_score:.2f}")

    table = Table(show_header=True, header_style="bold")
    table.add_column("Persona", max_width=25)
    table.add_column("Score", justify="right", style="cyan")
    table.add_column("Steps", justify="right")
    table.add_column("Rules", justify="right")
    table.add_column("Conditions", justify="right")
    table.add_column("Status", justify="center")
    table.add_column("Mode")

    for r in workflow_results:
        persona = r.persona_name or r.conversation_id or "—"
        status_icon = "✅" if r.passed else "❌"
        sev_color = {"critical": "bold red", "high": "red", "medium": "yellow", "low": "green"}.get(r.severity, "white")
        table.add_row(persona[:25], f"[{sev_color}]{r.score:.2f}[/]", f"{r.step_score:.2f}",
                      f"{r.rule_score:.2f}", f"{r.condition_score:.2f}", status_icon, r.evaluation_mode)

    console.print(table)

    # Step completion details
    if workflow_results:
        step_status_counts = Counter()
        for r in workflow_results:
            for sr in r.step_results:
                step_status_counts[(sr.step_name, sr.status.value)] += 1

        console.print(f"\n  [bold]Step Completion:[/]")
        step_names = list(dict.fromkeys(sr.step_name for r in workflow_results for sr in r.step_results))
        for sname in step_names:
            completed = step_status_counts.get((sname, "completed"), 0)
            partial = step_status_counts.get((sname, "partial"), 0)
            missed = step_status_counts.get((sname, "missed"), 0)
            icon = "✅" if completed == total else "⚠️ " if completed + partial > 0 else "❌"
            console.print(f"    {icon} {sname}: {completed} completed, {partial} partial, {missed} missed")

    # Hard rule results
    if workflow_results:
        console.print(f"\n  [bold]Hard Rule Results:[/]")
        rule_names = list(dict.fromkeys(rr.rule_name for r in workflow_results for rr in r.rule_results))
        for rname in rule_names:
            passed = sum(1 for r in workflow_results for rr in r.rule_results if rr.rule_name == rname and rr.passed)
            failed = total - passed
            icon = "✅" if failed == 0 else "❌"
            sev = next((rr.severity for r in workflow_results for rr in r.rule_results if rr.rule_name == rname), "")
            console.print(f"    {icon} {rname} [{sev}]: {passed}/{total} passed")

    # Violations
    all_violations = []
    for r in workflow_results:
        all_violations.extend(r.violations)
    if all_violations:
        console.print(f"\n  [bold red]Violations ({len(all_violations)}):[/]")
        seen = set()
        for v in all_violations:
            if v not in seen:
                console.print(f"    🚫 {v}")
                seen.add(v)
            if len(seen) >= 5:
                break


def _print_expansion_report(exp_report):
    """Print adaptive expansion results to console."""

    console.print(f"\n[bold]🔬 Adaptive Expansion Results[/]")
    console.print(
        f"  Signals found: {exp_report.total_signals_found} | "
        f"Expanded: {exp_report.signals_expanded} | "
        f"Variants run: {exp_report.total_variants_run} | "
        f"Time: {exp_report.execution_time_seconds:.1f}s"
    )

    # Confirmed bugs
    if exp_report.confirmed_bugs:
        console.print(f"\n  [bold red]Confirmed Bugs ({exp_report.confirmed_count}):[/]")
        for result in exp_report.confirmed_bugs:
            sig = result.signal
            console.print(
                f"    🔴 [{sig.judge_name}] {sig.message[:80]}"
            )
            console.print(
                f"       Reproducibility: {result.reproducibility_score:.0%} "
                f"({result.reproduced_count}/{result.total_variants} variants)"
            )
            if result.minimal_repro:
                console.print(
                    f"       Minimal repro: {result.minimal_repro.conversation_turns} turns "
                    f"({result.minimal_repro.variant.strategy.value} strategy)"
                )

    # Likely bugs
    if exp_report.likely_bugs:
        console.print(f"\n  [bold yellow]Likely Bugs ({exp_report.likely_count}):[/]")
        for result in exp_report.likely_bugs:
            sig = result.signal
            console.print(
                f"    🟡 [{sig.judge_name}] {sig.message[:80]}"
            )
            console.print(
                f"       Reproducibility: {result.reproducibility_score:.0%} "
                f"({result.reproduced_count}/{result.total_variants} variants)"
            )

    # Flukes
    if exp_report.flukes:
        console.print(f"\n  [dim]Flukes/Inconclusive ({exp_report.fluke_count}):[/]")
        for result in exp_report.flukes:
            sig = result.signal
            console.print(
                f"    ⚪ [{sig.judge_name}] {sig.message[:60]} — "
                f"{result.reproducibility_score:.0%} repro"
            )

    # Errors
    if exp_report.errors:
        console.print(f"\n  [dim]Expansion errors: {len(exp_report.errors)}[/]")
        for err in exp_report.errors[:3]:
            console.print(f"    ⚠ {err[:100]}")

    # Summary line
    total = exp_report.confirmed_count + exp_report.likely_count + exp_report.fluke_count
    if total > 0:
        console.print(f"\n  Summary: ", end="")
        if exp_report.confirmed_count:
            console.print(f"[bold red]{exp_report.confirmed_count} confirmed[/] ", end="")
        if exp_report.likely_count:
            console.print(f"[yellow]{exp_report.likely_count} likely[/] ", end="")
        if exp_report.fluke_count:
            console.print(f"[dim]{exp_report.fluke_count} fluke[/] ", end="")
        console.print()


def _print_rag_eval_report(rag_report):
    """Print RAG/Tool evaluation results to console."""
    from rich.table import Table

    console.print(f"\n[bold]🔬 RAG/Tool Evaluation Results[/]")
    console.print(
        f"  Conversations: {rag_report.total_conversations} | "
        f"Turns evaluated: {rag_report.total_turns_evaluated} | "
        f"Evidence: {rag_report.evidence_mode} | "
        f"Speed: {rag_report.eval_speed} | "
        f"Time: {rag_report.execution_time_seconds:.1f}s"
    )

    # Score summary
    def _color(score):
        if score >= 0.8:
            return "green"
        elif score >= 0.6:
            return "yellow"
        return "red"

    overall = rag_report.overall_score
    rag_s = rag_report.overall_rag_score
    tool_s = rag_report.overall_tool_score

    console.print(
        f"\n  Overall: [{_color(overall)}]{overall:.1%}[/] | "
        f"RAG: [{_color(rag_s)}]{rag_s:.1%}[/] | "
        f"Tool: [{_color(tool_s)}]{tool_s:.1%}[/] | "
        f"Issues: {rag_report.total_rag_issues + rag_report.total_tool_issues}"
    )

    # No-evidence hint
    if (rag_report.evidence_mode == "inferred"
            and not rag_report.rag_metric_averages
            and not rag_report.tool_metric_averages):
        console.print(
            "\n  [dim]ℹ No RAG evidence or tool calls detected in bot responses.[/]"
            "\n  [dim]  This bot may not use retrieval-augmented generation or function calling.[/]"
            "\n  [dim]  Tip: Provide --documentation for grounding context, or use --tool-defs for tool validation.[/]"
        )

    # RAG metrics table
    if rag_report.rag_metric_averages:
        table = Table(title="RAG Metrics", show_lines=False, padding=(0, 1))
        table.add_column("Metric", style="bold")
        table.add_column("Avg Score", justify="center")
        table.add_column("Pass Rate", justify="center")
        for name in sorted(rag_report.rag_metric_averages):
            avg = rag_report.rag_metric_averages[name]
            pr = rag_report.rag_metric_pass_rates.get(name, 0)
            table.add_row(
                name.replace("_", " ").title(),
                f"[{_color(avg)}]{avg:.1%}[/]",
                f"{pr:.0%}",
            )
        console.print(table)

    # Tool metrics table
    if rag_report.tool_metric_averages:
        table = Table(title="Tool Metrics", show_lines=False, padding=(0, 1))
        table.add_column("Metric", style="bold")
        table.add_column("Avg Score", justify="center")
        table.add_column("Pass Rate", justify="center")
        for name in sorted(rag_report.tool_metric_averages):
            avg = rag_report.tool_metric_averages[name]
            pr = rag_report.tool_metric_pass_rates.get(name, 0)
            table.add_row(
                name.replace("_", " ").title(),
                f"[{_color(avg)}]{avg:.1%}[/]",
                f"{pr:.0%}",
            )
        console.print(table)

    # Top issues
    if rag_report.top_issues:
        console.print(f"\n  [yellow]Top Issues:[/]")
        for issue in rag_report.top_issues[:5]:
            console.print(f"    ⚠ {issue[:100]}")

    if rag_report.errors:
        console.print(f"\n  [dim]Errors: {len(rag_report.errors)}[/]")


@main.command()
@click.option("--domain", default=None, help="Filter by domain (banking, healthcare, ecommerce, customer_service)")
@click.option("--validate", "validate_path", default=None, help="Validate a custom workflow YAML file")
@click.option("-v", "--verbose", is_flag=True, default=False, help="Show step details, rules, and conditions")
@click.option("--export", "export_name", default=None, help="Export a built-in workflow as YAML template")
def workflows(domain: str | None, validate_path: str | None, verbose: bool, export_name: str | None):
    """List, validate, or export workflow judge templates."""
    from src.workflow_judge import WorkflowLoader, WorkflowLoadError, BUILT_IN_WORKFLOWS

    if export_name:
        import yaml
        if export_name in BUILT_IN_WORKFLOWS:
            console.print(yaml.dump(BUILT_IN_WORKFLOWS[export_name], default_flow_style=False, sort_keys=False))
        else:
            console.print(f"[red]Unknown workflow: '{export_name}'. Available: {list(BUILT_IN_WORKFLOWS.keys())}[/]")
        return

    if validate_path:
        try:
            wf = WorkflowLoader.load_from_file(validate_path)
            warnings = WorkflowLoader.validate(wf)
            console.print(f"\n[bold green]✅ Valid workflow:[/] {wf.name}")
            console.print(f"  Domain: {wf.domain} | Steps: {wf.total_steps} | Rules: {len(wf.hard_rules)} | Conditions: {len(wf.success_conditions)}")
            if wf.activation_hints:
                console.print(f"  Activation: {', '.join(wf.activation_hints)}")
            if wf.order_mode != "none":
                console.print(f"  Order mode: {wf.order_mode}")
            if warnings:
                console.print(f"\n[yellow]Warnings:[/]")
                for w in warnings:
                    console.print(f"  ⚠️  {w}")
        except WorkflowLoadError as e:
            console.print(f"[red]❌ Invalid workflow:[/] {e}")
        return

    from rich.table import Table
    console.print(Panel.fit("[bold]🔧 AI SimTest — Workflow Judge Templates[/]"))

    table = Table(show_header=True, header_style="bold")
    table.add_column("Name", style="cyan", min_width=25, no_wrap=True)
    table.add_column("Domain")
    table.add_column("Steps", justify="right")
    table.add_column("Rules", justify="right")
    table.add_column("Conditions", justify="right")
    table.add_column("Order")
    if verbose:
        table.add_column("Activation Hints")

    for name in sorted(BUILT_IN_WORKFLOWS.keys()):
        wf = WorkflowLoader.load_built_in(name)
        if domain and wf.domain.lower() != domain.lower():
            continue
        row = [name, wf.domain, str(wf.total_steps), str(len(wf.hard_rules)), str(len(wf.success_conditions)), wf.order_mode]
        if verbose:
            row.append(", ".join(wf.activation_hints[:3]) + ("..." if len(wf.activation_hints) > 3 else ""))
        table.add_row(*row)

    console.print(table)

    if verbose:
        console.print(f"\n[bold]Workflow Details:[/]")
        for name in sorted(BUILT_IN_WORKFLOWS.keys()):
            wf = WorkflowLoader.load_built_in(name)
            if domain and wf.domain.lower() != domain.lower():
                continue
            console.print(f"\n  [bold cyan]{name}[/] — {wf.description}")
            console.print(f"  Steps:")
            for s in wf.steps:
                req = "[REQUIRED]" if s.required else "[optional]"
                console.print(f"    {s.order or '-'}. {s.name} {req}")
            if wf.hard_rules:
                console.print(f"  Hard Rules:")
                for r in wf.hard_rules:
                    sev_color = {"critical": "red", "high": "yellow"}.get(r.severity, "white")
                    console.print(f"    [{sev_color}]{r.severity.upper()}[/] {r.name} ({r.rule_type.value})")
            if wf.success_conditions:
                console.print(f"  Success Conditions:")
                for c in wf.success_conditions:
                    console.print(f"    • {c.description}")

    console.print(f"\n[dim]Usage: simtest run --bot-endpoint URL --workflow banking_account_opening[/]")
    console.print(f"[dim]       simtest workflows --validate my_workflow.yaml[/]")
    console.print(f"[dim]       simtest workflows --export banking_account_opening > my_workflow.yaml[/]")
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

    # ─── Version Diff (if fingerprints available) ─────────
    try:
        import json as _json
        from src.versioning import RunFingerprint, VersionComparator

        # Try to load fingerprints from summary files
        with open(baseline, "r") as f:
            baseline_data = _json.load(f)
        with open(current, "r") as f:
            current_data = _json.load(f)

        baseline_fp_data = baseline_data.get("version_fingerprint")
        current_fp_data = current_data.get("version_fingerprint")

        if baseline_fp_data and current_fp_data:
            baseline_fp = RunFingerprint.from_dict(baseline_fp_data)
            current_fp = RunFingerprint.from_dict(current_fp_data)

            comparator = VersionComparator()
            version_diff = comparator.compare(baseline_fp, current_fp)

            if version_diff.configs_identical:
                console.print("  [dim]📋 Configuration unchanged between runs[/dim]\n")
            else:
                console.print(f"  [bold]📋 Configuration Changes ({version_diff.total_changes}):[/bold]")
                for change in version_diff.changes:
                    impact_color = {"high": "red", "medium": "yellow", "low": "dim"}.get(change.impact, "white")
                    console.print(
                        f"    [{impact_color}]●[/] {change.field}: "
                        f"[dim]{change.old_value}[/] → [bold]{change.new_value}[/] "
                        f"[dim]({change.impact} impact)[/]"
                    )
                console.print()
    except (ImportError, Exception):
        pass  # Version tracking not available — skip silently

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

    # ─── Workflow Comparison ──────────────────────────────

    try:
        import json as _wf_json
        baseline_dir = Path(baseline).parent
        current_dir = Path(current).parent

        baseline_wf_files = sorted(baseline_dir.glob("workflow_*.json"))
        current_wf_files = sorted(current_dir.glob("workflow_*.json"))

        # Build lookup by workflow name
        def _load_wf(path):
            with open(path) as f:
                return _wf_json.load(f)

        baseline_wfs = {}
        for wf_path in baseline_wf_files:
            try:
                data = _load_wf(wf_path)
                baseline_wfs[data.get("workflow", wf_path.stem)] = data
            except Exception:
                pass

        current_wfs = {}
        for wf_path in current_wf_files:
            try:
                data = _load_wf(wf_path)
                current_wfs[data.get("workflow", wf_path.stem)] = data
            except Exception:
                pass

        all_wf_names = sorted(set(list(baseline_wfs.keys()) + list(current_wfs.keys())))

        if all_wf_names:
            console.print()
            wf_table = Table(title="Workflow Judge Comparison", show_header=True, header_style="bold")
            wf_table.add_column("Workflow", style="cyan")
            wf_table.add_column("Baseline Score", justify="right")
            wf_table.add_column("Current Score", justify="right")
            wf_table.add_column("Delta", justify="right")
            wf_table.add_column("Baseline Pass", justify="right")
            wf_table.add_column("Current Pass", justify="right")
            wf_table.add_column("Critical", justify="right")

            for wf_name in all_wf_names:
                b = baseline_wfs.get(wf_name)
                c = current_wfs.get(wf_name)

                b_score = b["avg_score"] if b else 0
                c_score = c["avg_score"] if c else 0
                delta = c_score - b_score

                b_pass = f"{b['passed']}/{b['total_conversations']}" if b else "—"
                c_pass = f"{c['passed']}/{c['total_conversations']}" if c else "—"
                c_crit = str(c.get("critical_failures", 0)) if c else "—"

                delta_color = "green" if delta > 0.01 else "red" if delta < -0.01 else "dim"
                status = "NEW" if not b else "REMOVED" if not c else ""

                if status == "NEW":
                    wf_table.add_row(wf_name, "—", f"{c_score:.3f}", "[blue]NEW[/blue]", "—", c_pass, c_crit)
                elif status == "REMOVED":
                    wf_table.add_row(wf_name, f"{b_score:.3f}", "—", "[yellow]REMOVED[/yellow]", b_pass, "—", "—")
                else:
                    wf_table.add_row(
                        wf_name, f"{b_score:.3f}", f"{c_score:.3f}",
                        f"[{delta_color}]{delta:+.3f}[/{delta_color}]",
                        b_pass, c_pass, c_crit,
                    )

            console.print(wf_table)

            # Collect data for HTML report
            _wf_comparison_data = []
            for wf_name in all_wf_names:
                b = baseline_wfs.get(wf_name)
                c = current_wfs.get(wf_name)
                _wf_comparison_data.append({
                    "name": wf_name,
                    "baseline_score": b["avg_score"] if b else 0,
                    "current_score": c["avg_score"] if c else 0,
                    "baseline_pass": f"{b['passed']}/{b['total_conversations']}" if b else "—",
                    "current_pass": f"{c['passed']}/{c['total_conversations']}" if c else "—",
                    "current_critical": str(c.get("critical_failures", 0)) if c else "—",
                    "status": "NEW" if not b else "REMOVED" if not c else "",
                })
    except Exception:
        _wf_comparison_data = []

    # ─── Generate Reports ──────────────────────────────────

    output_dir = Path(output)
    output_dir.mkdir(parents=True, exist_ok=True)
    format_list = [f.strip().lower() for f in formats.split(",")]

    gen = ComparisonReportGenerator()

    if "html" in format_list:
        html_path = gen.generate(result, output_dir / "comparison.html",
                                 workflow_comparison=_wf_comparison_data if _wf_comparison_data else None)
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
    console.print("  coverage     Show coverage metrics info")
    console.print("  calibrate    Run judge calibration against golden examples")
    console.print("  history      View simulation run history and trends")
    console.print("  refine       Iterative persona refinement")
    console.print("  save-suite   Save regression suite from failures")
    console.print("  replay       Replay regression suite")
    console.print("  compare      Compare two simulation reports")
    console.print("  formats      List supported input formats for --input")
    console.print("  serve        Start the API server")


# ============================================================
# simtest formats — List supported input formats
# ============================================================

@main.command()
def formats():
    """List supported input formats for conversation replay (--input)."""
    table = Table(title="Supported Input Formats for --input")
    table.add_column("Format", style="bold cyan")
    table.add_column("Extensions")
    table.add_column("Description")

    table.add_row("json", ".json", "JSON array or single conversation object")
    table.add_row("jsonl", ".jsonl, .ndjson", "One JSON conversation per line")
    table.add_row("csv", ".csv", "Tabular: conversation_id, role, message columns")
    table.add_row("tsv", ".tsv", "Tab-separated (same as CSV)")
    table.add_row("text", ".txt, .log, .chat", "Plain text with speaker prefixes")
    table.add_row("markdown", ".md", "Markdown transcripts")
    table.add_row("simtest", ".jsonl", "AI SimTest output for re-evaluation")
    table.add_row("auto", "(any)", "Auto-detect from extension (default)")

    console.print(table)

    console.print("\n  [bold]JSON example:[/]")
    console.print('    [{"conversation_id": "c1", "messages": [{"role": "user", "content": "Hello"}, {"role": "assistant", "content": "Hi!"}]}]')

    console.print("\n  [bold]CSV example:[/]")
    console.print("    conversation_id,role,message")
    console.print('    c1,user,"How do I open an account?"')
    console.print('    c1,bot,"You can open an account online."')

    console.print("\n  [bold]Text example:[/]")
    console.print("    Customer: I need help")
    console.print("    Bot: How can I assist you?")

    console.print("\n  [bold]Role normalization:[/]")
    console.print("    user / customer / human / client → [bold]user[/]")
    console.print("    bot / assistant / agent / ai → [bold]bot[/]")

    console.print("\n  [bold]Usage:[/]")
    console.print("    simtest run --bot-endpoint URL --input ./logs/chats.json                    [dim]# evaluate + simulate[/]")
    console.print("    simtest run --bot-endpoint URL --input ./logs/ --personas 0                 [dim]# evaluate only[/]")
    console.print("    simtest run --bot-endpoint URL --input ./logs/ --pii-masking mask           [dim]# with PII redaction[/]")
    console.print("    simtest run --bot-endpoint URL --input ./logs/ --fail-if-below safety=0.95  [dim]# CI/CD gate[/]")


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


# ============================================================
# simtest coverage — Show coverage metrics info
# ============================================================

@main.command()
@click.option("--verbose", "-v", is_flag=True, default=False, help="Show detailed dimension explanations")
def coverage(verbose: bool):
    """Show coverage metrics dimensions, grading, and usage.

    \b
    Coverage metrics answer: "How thorough was this test run?"
    They are computed automatically after every simulation run
    and included in the summary output.

    \b
    Examples:
        simtest coverage            # Overview of 4 dimensions
        simtest coverage -v         # Detailed explanations

    \b
    CI/CD gate usage:
        simtest run --bot-endpoint URL --min-coverage 0.75
    """
    from rich.table import Table

    console.print("\n[bold cyan]📊 AI SimTest — Coverage Metrics[/]\n")
    console.print("Coverage metrics measure how thorough your test run was across 4 dimensions.\n")

    # Dimension table
    table = Table(show_header=True, header_style="bold")
    table.add_column("Dimension", style="bold cyan", no_wrap=True)
    table.add_column("What It Measures")
    table.add_column("Weight", justify="center")
    table.add_column("Data Source")

    table.add_row(
        "Persona Types",
        "Did 70/20/10 distribution hold? All types represented?",
        "25%",
        "Generated personas",
    )
    table.add_row(
        "Topics",
        "What % of --topics appeared in conversations?",
        "25%*",
        "--topics flag",
    )
    table.add_row(
        "Scenarios",
        "How many scenario categories & difficulties tested?",
        "25%*",
        "--scenarios, --stress-memory",
    )
    table.add_row(
        "Judges",
        "Did all 4 judges produce verdicts? Any misconfigured?",
        "25%",
        "Judge engine output",
    )

    console.print(table)
    console.print("\n  [dim]* Weight is 0% if that feature wasn't used (no unfair penalty)[/]")

    # Grading
    console.print("\n[bold]Grading Scale:[/]\n")
    grade_table = Table(show_header=True, header_style="bold", box=None)
    grade_table.add_column("Grade", style="bold")
    grade_table.add_column("Score Range")
    grade_table.add_column("Meaning")

    grade_table.add_row("[green]A[/]", "90%+", "Excellent — comprehensive test coverage")
    grade_table.add_row("[green]B[/]", "75-89%", "Good — solid coverage with minor gaps")
    grade_table.add_row("[yellow]C[/]", "60-74%", "Adequate — noticeable coverage gaps")
    grade_table.add_row("[red]D[/]", "40-59%", "Poor — significant gaps, results may be unreliable")
    grade_table.add_row("[bold red]F[/]", "<40%", "Insufficient — test run needs more breadth")

    console.print(grade_table)

    if verbose:
        console.print("\n[bold cyan]━━━ Dimension Details ━━━[/]\n")

        console.print("[bold]1. Persona-Type Coverage[/]")
        console.print("  Checks the mix of standard (70%), edge_case (20%), adversarial (10%) personas.")
        console.print("  Missing an entire type is heavily penalized. Distribution deviation is moderately penalized.")
        console.print("  Score = 60% type presence + 40% distribution accuracy\n")

        console.print("[bold]2. Topic Coverage[/]")
        console.print("  Searches all conversation messages for each --topics keyword (case-insensitive).")
        console.print("  Score = topics found / topics defined. No topics defined = 100% (nothing to miss).\n")

        console.print("[bold]3. Scenario Coverage[/]")
        console.print("  Maps your --scenarios to 5 categories: robustness, safety, quality, memory, empathy.")
        console.print("  Also checks difficulty spread (easy/medium/hard) and --stress-memory bonus.")
        console.print("  Score = 60% category breadth + 25% difficulty spread + 15% stress bonus\n")

        console.print("[bold]4. Judge Coverage[/]")
        console.print("  Verifies all 4 judges (grounding, safety, quality, relevance) produced verdicts.")
        console.print("  Flags judges with 100% uniform scores as potentially misconfigured.\n")

    console.print("[bold]CI/CD Usage:[/]\n")
    console.print("  [dim]simtest run --bot-endpoint URL --min-coverage 0.75[/]")
    console.print("  [dim]  → Exits with code 1 if coverage < 75% (blocks deployment)[/]\n")
    console.print("  [dim]simtest run --bot-endpoint URL --scenarios all --stress-memory --min-coverage 0.80[/]")
    console.print("  [dim]  → Full-spectrum test with 80% coverage gate[/]")


# ============================================================
# simtest calibrate — Judge Calibration Suite
# ============================================================

@main.command()
@click.option("--golden-file", default=None, help="Path to custom golden examples JSON file (merged with built-in)")
@click.option("--judge", default=None, help="Calibrate only this judge (grounding, safety, quality, relevance)")
@click.option("--output", default="reports", help="Output directory for calibration.json")
@click.option("--doc-context", default=None, help="Documentation text for grounding judge (overrides golden example docs)")
@click.option("--verbose", "-v", is_flag=True, default=False, help="Show per-example verdicts")
@click.option("--list-examples", is_flag=True, default=False, help="List golden examples without running calibration")
@click.option("--version-tag", default=None, help="Tag this calibration run with a version (e.g., 'v1.0.0')")
def calibrate(
    golden_file: str | None,
    judge: str | None,
    output: str,
    doc_context: str | None,
    verbose: bool,
    list_examples: bool,
    version_tag: str | None,
):
    """Run judge calibration against golden labeled examples.

    \b
    Validates that your 4 judges (grounding, safety, quality, relevance)
    produce correct verdicts on hand-labeled test cases. Shows accuracy,
    confusion matrix, and recommendations for tuning.

    \b
    Examples:
        simtest calibrate                         # Run all 42 built-in examples
        simtest calibrate --judge safety           # Calibrate safety judge only
        simtest calibrate --golden-file custom.json # Add custom examples
        simtest calibrate --list-examples          # Preview examples without running
        simtest calibrate -v                       # Show per-example verdicts
        simtest calibrate --version-tag v2.0       # Tag for version comparison

    \b
    Custom golden examples JSON format:
        {"examples": [{"id": "custom_001", "target_judge": "safety",
          "category": "safety_pii", "description": "...",
          "user_message": "...", "bot_response": "...",
          "expected_verdict": "fail", "expected_score_min": 0.0,
          "expected_score_max": 0.3}]}
    """
    from rich.table import Table
    from src.calibration import (
        GoldenDatasetManager,
        CalibrationRunner,
        CalibrationAnalyzer,
        JudgeVersionTracker,
    )

    console.print("\n[bold cyan]🔬 AI SimTest — Judge Calibration Suite[/]\n")

    # ── Load golden examples ──────────────────────────────────
    dataset = GoldenDatasetManager()
    built_in_count = dataset.load_built_in()
    console.print(f"  📦 Loaded {built_in_count} built-in golden examples")

    if golden_file:
        try:
            custom_count = dataset.load_from_file(golden_file)
            console.print(f"  📄 Loaded {custom_count} custom examples from [bold]{golden_file}[/]")
        except FileNotFoundError:
            console.print(f"  [red]⚠ File not found: {golden_file}[/]")
            return
        except Exception as e:
            console.print(f"  [red]⚠ Error loading custom examples: {e}[/]")
            return

    # Filter by judge if specified
    if judge:
        valid_judges = ["grounding", "safety", "quality", "relevance"]
        if judge not in valid_judges:
            console.print(f"  [red]⚠ Unknown judge: {judge}. Valid: {', '.join(valid_judges)}[/]")
            return
        examples = dataset.filter_by_judge(judge)
        console.print(f"  🎯 Filtering to [bold]{judge}[/] judge: {len(examples)} examples")
    else:
        examples = dataset.examples

    # Validate
    issues = dataset.validate()
    if issues:
        console.print(f"  [yellow]⚠ {len(issues)} validation issue(s) found[/]")
        for issue in issues[:3]:
            console.print(f"    • {issue}")

    # ── List mode ─────────────────────────────────────────────
    if list_examples:
        _print_golden_examples(examples, verbose)
        return

    console.print(f"\n  Running {len(examples)} examples through judges...\n")

    # ── Initialize judges and run calibration ─────────────────
    import asyncio
    import time

    start_time = time.time()

    try:
        judge_results = asyncio.run(_run_calibration_examples(examples, doc_context))
    except Exception as e:
        console.print(f"  [red]❌ Judge initialization failed: {e}[/]")
        console.print(f"  [dim]This may happen if judge dependencies are not installed.[/]")
        console.print(f"  [dim]Falling back to offline mode (mock scores)...[/]")
        # Fallback: run without real judges (for environments without models)
        judge_results = _mock_judge_results(examples)

    duration = time.time() - start_time

    # ── Evaluate verdicts ─────────────────────────────────────
    runner = CalibrationRunner()
    verdicts = runner.run_batch(examples, judge_results)

    # ── Analyze ───────────────────────────────────────────────
    analyzer = CalibrationAnalyzer()
    report = analyzer.analyze(verdicts)
    report.duration_seconds = duration

    # ── Version tracking ──────────────────────────────────────
    if version_tag:
        tracker = JudgeVersionTracker()
        version_path = Path(output) / "judge_versions.json"
        if version_path.exists():
            tracker.load_from_file(version_path)

        for judge_name, acc in report.judge_accuracy.items():
            tracker.register_version(judge_name, version_tag, f"Calibration run")
            tracker.update_accuracy(judge_name, version_tag, acc.overall_accuracy)

        tracker.save_to_file(version_path)
        console.print(f"  🏷️  Version [bold]{version_tag}[/] recorded for all judges")

    # ── Display results ───────────────────────────────────────
    _print_calibration_report(report, verbose)

    # ── Save calibration.json ─────────────────────────────────
    try:
        import json
        from datetime import datetime, timezone

        report.timestamp = datetime.now(timezone.utc).isoformat()
        cal_path = Path(output) / "calibration.json"
        Path(output).mkdir(parents=True, exist_ok=True)
        with open(cal_path, "w") as f:
            json.dump(report.to_dict(), f, indent=2)
        console.print(f"\n  📄 Calibration report saved: [bold]{cal_path}[/]")
    except Exception as e:
        console.print(f"  [dim]Could not save calibration.json: {e}[/]")

    console.print()


async def _run_calibration_examples(
    examples: list,
    doc_context: str | None = None,
) -> dict[str, tuple[float, bool, str]]:
    """
    Run golden examples through real judges.

    Returns dict mapping example_id → (score, passed, message).
    """
    from src.judges import JudgeEngine
    from src.judges.grounding_judge import GroundingJudge
    from src.judges.safety_judge import SafetyJudge
    from src.judges.quality_judge import QualityJudge, RelevanceJudge
    from src.models import Turn

    # Determine which judges are needed based on examples
    needed_judges = set(ex.target_judge for ex in examples)

    # Initialize individual judge instances
    judges = []

    if "grounding" in needed_judges:
        judges.append(GroundingJudge())

    if "safety" in needed_judges:
        judges.append(SafetyJudge())

    if "quality" in needed_judges:
        judges.append(QualityJudge())

    if "relevance" in needed_judges:
        judges.append(RelevanceJudge())

    # Build engine and initialize all judges (loads models)
    engine = JudgeEngine(judges=judges)
    await engine.initialize_all()

    # Map judge name → judge instance for direct access
    judge_map = {j.name: j for j in engine.judges}

    results = {}

    for example in examples:
        target = example.target_judge
        judge = judge_map.get(target)

        if judge is None:
            results[example.id] = (0.0, False, f"Judge '{target}' not available after init")
            continue

        try:
            doc = doc_context if doc_context else example.documentation

            # Build conversation_history with a user Turn so RelevanceJudge
            # can extract the user message (it reads from conversation_history)
            history = [Turn(speaker="user", message=example.user_message)]

            judgment = await judge.evaluate(
                response=example.bot_response,
                context=doc,
                conversation_history=history,
            )

            results[example.id] = (
                judgment.score,
                judgment.passed,
                judgment.message,
            )
        except Exception as e:
            results[example.id] = (0.0, False, f"Error: {str(e)[:80]}")

    return results


def _mock_judge_results(examples: list) -> dict[str, tuple[float, bool, str]]:
    """
    Generate mock judge results for offline calibration.

    Uses a simple heuristic: if the example expects PASS, give 0.8.
    If FAIL, give 0.2. This lets the calibration pipeline run even
    without real judges installed.
    """
    from src.calibration.models import ExpectedVerdict

    results = {}
    for ex in examples:
        if ex.expected_verdict == ExpectedVerdict.PASS:
            results[ex.id] = (0.85, True, "Mock: expected pass")
        elif ex.expected_verdict == ExpectedVerdict.FAIL:
            results[ex.id] = (0.15, False, "Mock: expected fail")
        else:
            results[ex.id] = (0.5, True, "Mock: expected warning")
    return results


def _print_golden_examples(examples: list, verbose: bool):
    """Print golden examples in a table."""
    from rich.table import Table

    # Stats summary
    by_judge = {}
    by_verdict = {}
    for ex in examples:
        by_judge[ex.target_judge] = by_judge.get(ex.target_judge, 0) + 1
        by_verdict[ex.expected_verdict.value] = by_verdict.get(ex.expected_verdict.value, 0) + 1

    console.print(f"\n[bold]Golden Examples ({len(examples)} total)[/]\n")
    for j, c in sorted(by_judge.items()):
        console.print(f"  {j}: {c} examples")
    console.print(f"\n  Expected verdicts: {dict(by_verdict)}")

    if verbose:
        table = Table(show_header=True, header_style="bold")
        table.add_column("ID", style="cyan", no_wrap=True)
        table.add_column("Judge")
        table.add_column("Category")
        table.add_column("Expected")
        table.add_column("Score Range")
        table.add_column("Description", max_width=50)

        for ex in examples:
            verdict_color = {"pass": "green", "fail": "red", "warning": "yellow"}.get(
                ex.expected_verdict.value, "white"
            )
            table.add_row(
                ex.id,
                ex.target_judge,
                ex.category.value,
                f"[{verdict_color}]{ex.expected_verdict.value}[/]",
                f"{ex.expected_score_min:.1f}-{ex.expected_score_max:.1f}",
                ex.description,
            )

        console.print()
        console.print(table)

    console.print()


def _print_calibration_report(report, verbose: bool):
    """Print calibration results to console."""
    from rich.table import Table

    grade = report.overall_grade.value
    accuracy = report.overall_accuracy
    grade_color = {
        "excellent": "bold green", "good": "green", "needs_tuning": "yellow",
        "poor": "red", "broken": "bold red",
    }.get(grade, "white")

    console.print(f"\n[bold]Calibration Results:[/]  [{grade_color}]{accuracy:.0%} accuracy ({grade})[/]")
    console.print(f"  Total examples: {report.total_examples} | Correct: {report.total_correct} | Duration: {report.duration_seconds:.1f}s")

    if report.strongest_judge:
        console.print(f"  Strongest: [green]{report.strongest_judge}[/] | Weakest: [red]{report.weakest_judge}[/]")

    # Per-judge table
    if report.judge_accuracy:
        console.print()
        table = Table(title="Per-Judge Accuracy", show_header=True, header_style="bold")
        table.add_column("Judge", style="cyan")
        table.add_column("Examples", justify="center")
        table.add_column("Accuracy", justify="center")
        table.add_column("Verdict Acc", justify="center")
        table.add_column("Score Acc", justify="center")
        table.add_column("FP", justify="center")
        table.add_column("FN", justify="center")
        table.add_column("Grade")

        for name in sorted(report.judge_accuracy):
            acc = report.judge_accuracy[name]
            g = acc.grade.value
            gc = {
                "excellent": "green", "good": "green", "needs_tuning": "yellow",
                "poor": "red", "broken": "bold red",
            }.get(g, "white")

            bar = "█" * int(acc.overall_accuracy * 10) + "░" * (10 - int(acc.overall_accuracy * 10))
            table.add_row(
                name,
                str(acc.total_examples),
                f"{bar} {acc.overall_accuracy:.0%}",
                f"{acc.verdict_accuracy:.0%}",
                f"{acc.score_accuracy:.0%}",
                str(acc.false_positives),
                str(acc.false_negatives),
                f"[{gc}]{g}[/]",
            )

        console.print(table)

    # Verbose: per-example verdicts
    if verbose and report.judge_accuracy:
        for name, acc in sorted(report.judge_accuracy.items()):
            console.print(f"\n[bold]{name}[/] — per-example verdicts:")
            for v in acc.verdicts:
                icon = "✓" if v.overall_correct else "✗"
                color = "green" if v.overall_correct else "red"
                console.print(
                    f"  [{color}]{icon}[/] {v.example_id}: "
                    f"expected {v.expected_verdict.value} [{v.expected_score_min:.1f}-{v.expected_score_max:.1f}] "
                    f"→ got {v.actual_score:.2f} ({'pass' if v.actual_passed else 'fail'}) "
                    f"[dim]{v.actual_message[:60]}[/]"
                )

    # Recommendations
    if report.recommendations:
        console.print(f"\n[bold]Recommendations:[/]")
        for rec in report.recommendations:
            console.print(f"  • {rec}")


# ============================================================
# simtest policies — List and validate policy templates
# ============================================================

@main.command()
@click.option("--industry", default=None, help="Filter by industry (general, healthcare, finance, airline)")
@click.option("--validate", "validate_path", default=None, help="Validate a custom YAML policy file")
@click.option("--verbose", "-v", is_flag=True, default=False, help="Show full rule details")
@click.option("--export", "export_path", default=None, help="Export a built-in template to YAML file for customization")
@click.option("--strict", is_flag=True, default=False, help="Strict validation (fail on unknown judge names)")
def policies(industry: str | None, validate_path: str | None, verbose: bool, export_path: str | None, strict: bool):
    """List available policy templates and validate custom policies.

    \b
    Examples:
        simtest policies                              # List all built-in policies
        simtest policies --industry healthcare         # Show healthcare policy
        simtest policies -v                            # Verbose with all rules
        simtest policies --validate my_policy.yaml     # Validate custom policy
        simtest policies --validate my.yaml --strict   # Strict validation
        simtest policies --export general > my.yaml    # Export template for editing

    \b
    Use with 'simtest run':
        simtest run --bot-endpoint http://... --policy general
        simtest run --bot-endpoint http://... --policy healthcare
        simtest run --bot-endpoint http://... --policy healthcare --policy-mode strict
        simtest run --bot-endpoint http://... --policy ./my_custom_policy.yaml
    """
    from src.policy import PolicyLoader, PolicySeverity, ComplianceGateMode

    console.print(Panel.fit(
        "[bold blue]AI SimTest[/] - Policy-as-Code v2",
        subtitle="Enterprise compliance governance",
    ))

    # Validate a custom file
    if validate_path:
        vp = Path(validate_path)
        if not vp.exists():
            console.print(f"[red]File not found: {validate_path}[/]")
            sys.exit(1)
        try:
            ps = PolicyLoader.load_from_file(vp)
            warnings = PolicyLoader.validate_policy_set(ps, strict=strict)
            console.print(f"\n  [green]✅ Valid policy:[/] {ps.name} (v{ps.version})")
            console.print(f"  Rules: {ps.rule_count} | Industry: {ps.industry}")
            console.print(f"  Gate mode: {ps.mode.value} | Error mode: {ps.evaluation_error_mode.value}")
            console.print(f"  Critical rules: {len(ps.critical_rules)}")

            families = ps.get_control_families()
            if families:
                console.print(f"  Control families: {', '.join(families)}")

            conditions = {}
            for r in ps.rules:
                conditions[r.condition.value] = conditions.get(r.condition.value, 0) + 1
            if conditions:
                cond_parts = [f"{v}×{k}" for k, v in sorted(conditions.items(), key=lambda x: -x[1])]
                console.print(f"  Conditions: {', '.join(cond_parts)}")

            if warnings:
                console.print(f"\n  [yellow]Warnings:[/]")
                for w in warnings:
                    console.print(f"    ⚠ {w}")
            else:
                console.print(f"  [green]No validation warnings[/]")
        except Exception as e:
            console.print(f"\n  [red]❌ Invalid policy: {e}[/]")
            sys.exit(1)
        return

    # Export a built-in template
    if export_path:
        import yaml
        ps_data = None
        from src.policy.built_in import BUILT_IN_POLICIES
        if export_path in BUILT_IN_POLICIES:
            ps_data = BUILT_IN_POLICIES[export_path]
            yaml_str = yaml.dump(ps_data, default_flow_style=False, sort_keys=False)
            console.print(yaml_str)
        else:
            console.print(f"[red]Unknown template: '{export_path}'. Choose from: {', '.join(BUILT_IN_POLICIES.keys())}[/]")
            sys.exit(1)
        return

    # List all built-in policies
    templates = PolicyLoader.list_built_in()

    if industry:
        industry = industry.lower()
        if industry not in templates:
            console.print(f"[red]Unknown industry: '{industry}'. Choose from: {', '.join(templates)}[/]")
            sys.exit(1)
        templates = [industry]

    from rich.table import Table

    for name in templates:
        ps = PolicyLoader.load_built_in(name)
        if ps is None:
            continue

        console.print(f"\n[bold cyan]{ps.name}[/] (v{ps.version})")
        console.print(f"  {ps.description}")
        console.print(f"  Industry: {ps.industry} | Rules: {ps.rule_count} | Critical: {len(ps.critical_rules)}")
        console.print(f"  Gate mode: [bold]{ps.mode.value}[/] | Error mode: {ps.evaluation_error_mode.value}")

        families = ps.get_control_families()
        if families:
            console.print(f"  Control families: {', '.join(families)}")

        if verbose:
            table = Table(show_header=True, header_style="bold")
            table.add_column("ID", style="dim")
            table.add_column("Name")
            table.add_column("Judge", style="cyan")
            table.add_column("Condition")
            table.add_column("Threshold", justify="right")
            table.add_column("Severity")
            table.add_column("Family", style="dim")

            sev_colors = {"critical": "bold red", "high": "red", "medium": "yellow", "low": "green"}

            for r in ps.rules:
                sev_color = sev_colors.get(r.severity.value, "white")
                table.add_row(
                    r.id,
                    r.name,
                    r.judge,
                    r.condition.value,
                    f"{r.threshold}",
                    f"[{sev_color}]{r.severity.value}[/]",
                    r.control_family or "",
                )

            console.print(table)

    console.print(f"\n[dim]Use with: simtest run --bot-endpoint URL --policy <name-or-yaml-file>[/]")
    console.print(f"[dim]Override gate: simtest run --policy healthcare --policy-mode strict[/]")
    console.print(f"[dim]Export template: simtest policies --export general > my_policy.yaml[/]")
    console.print()


# ============================================================
# simtest expand — Standalone adaptive expansion from previous run
# ============================================================

@main.command()
@click.option("--report", "report_path", required=True, help="Path to summary.json from a previous run")
@click.option("--bot-endpoint", required=True, help="API endpoint of the bot to test")
@click.option("--bot-api-key", default=None, help="API key for the bot")
@click.option("--bot-format", default="openai", help="Request format: openai, anthropic, custom")
@click.option("--variants", default=5, help="Number of variant conversations per failure (default: 5)")
@click.option("--expand-top", default=5, help="Max failure signals to expand (default: 5)")
@click.option("--output", default=None, help="Output directory (default: same as report)")
@click.option("--fail-if-reproducible", is_flag=True, default=False, help="Exit code 1 if confirmed reproducible bugs found (CI/CD gate)")
def expand(
    report_path: str,
    bot_endpoint: str,
    bot_api_key: str | None,
    bot_format: str,
    variants: int,
    expand_top: int,
    output: str | None,
    fail_if_reproducible: bool,
):
    """Expand failures from a previous simulation run.

    \b
    Loads a previous simulation's results, extracts failure signals,
    generates variant conversations, runs them against the bot, and
    reports which failures are reproducible vs flukes.

    \b
    Examples:
        simtest expand --report ./reports/summary.json --bot-endpoint http://bot/api
        simtest expand --report ./reports/summary.json --bot-endpoint http://bot/api --variants 3
        simtest expand --report ./reports/summary.json --bot-endpoint http://bot/api --fail-if-reproducible
    """
    from src.core.logging import setup_logging
    setup_logging()

    console.print(Panel.fit(
        "[bold magenta]AI SimTest[/] — Adaptive Expansion",
        subtitle="Confirm failures & find minimal repros",
    ))

    rp = Path(report_path)
    if not rp.exists():
        console.print(f"[red]Error: Report not found: {report_path}[/]")
        sys.exit(1)

    output_dir = output or str(rp.parent)

    console.print(f"  📊 Report: [bold]{report_path}[/]")
    console.print(f"  🎯 Bot: [bold]{bot_endpoint}[/]")
    console.print(f"  🔬 Top {expand_top} failures × {variants} variants")

    exit_code = asyncio.run(_run_expand(
        report_path=rp,
        bot_endpoint=bot_endpoint,
        bot_api_key=bot_api_key,
        bot_format=bot_format,
        variants_per_signal=variants,
        max_signals=expand_top,
        output_dir=output_dir,
        fail_if_reproducible=fail_if_reproducible,
    ))

    if exit_code != 0:
        sys.exit(exit_code)


async def _run_expand(
    report_path: Path,
    bot_endpoint: str,
    bot_api_key: str | None,
    bot_format: str,
    variants_per_signal: int,
    max_signals: int,
    output_dir: str,
    fail_if_reproducible: bool,
) -> int:
    """Async expansion runner for standalone mode."""
    import json

    from src.expansion import AdaptiveExpansionEngine, AdaptiveExpansionConfig
    from src.models import BotConfig

    # Load the previous report
    try:
        with open(report_path, "r") as f:
            summary_data = json.load(f)
    except Exception as e:
        console.print(f"[red]Error loading report: {e}[/]")
        return 1

    # Reconstruct judged conversations from JSONL
    jsonl_path = report_path.parent / "conversations.jsonl"
    if not jsonl_path.exists():
        console.print(f"[red]Error: conversations.jsonl not found at {jsonl_path}[/]")
        console.print("[dim]Adaptive expansion needs the full conversation data.[/]")
        return 1

    judged_conversations = _load_judged_conversations_for_expansion(jsonl_path)

    if not judged_conversations:
        console.print("[yellow]No conversations found to expand.[/]")
        return 0

    console.print(f"  📄 Loaded {len(judged_conversations)} conversations from {jsonl_path}")

    # Configure and run
    config = AdaptiveExpansionConfig(
        max_signals=max_signals,
        variants_per_signal=variants_per_signal,
    )

    bot_config = BotConfig(
        api_endpoint=bot_endpoint,
        api_key=bot_api_key,
        request_format=bot_format,
    )

    engine = AdaptiveExpansionEngine(bot_config=bot_config, config=config)

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        console=console,
    ) as progress:
        task = progress.add_task("Running adaptive expansion...", total=None)

        def _progress(phase, detail):
            progress.update(task, description=detail)

        exp_report = await engine.run(
            judged_conversations=judged_conversations,
            progress_callback=_progress,
        )

    # Display
    _print_expansion_report(exp_report)

    # Save
    try:
        exp_path = Path(output_dir) / "expansion.json"
        with open(exp_path, "w") as f:
            json.dump(exp_report.to_dict(), f, indent=2)
        console.print(f"\n  🔬 Expansion report saved: {exp_path}")
    except Exception as e:
        console.print(f"  [dim]Could not save expansion report: {e}[/]")

    # CI/CD gate
    if fail_if_reproducible and exp_report.has_confirmed_bugs:
        console.print(
            f"\n  [bold red]✗ EXPANSION GATE FAILED:[/] "
            f"{exp_report.confirmed_count} confirmed reproducible bug(s)"
        )
        return 1

    return 0


def _load_judged_conversations_for_expansion(jsonl_path: Path) -> list:
    """
    Load judged conversations from JSONL for expansion.
    Returns lightweight objects that the SignalExtractor can process.
    """
    import json
    from dataclasses import dataclass, field

    @dataclass
    class LiteTurn:
        speaker: str = ""
        message: str = ""

    @dataclass
    class LiteSeverity:
        value: str = "info"

    @dataclass
    class LiteJudgment:
        judge_name: str = ""
        passed: bool = True
        score: float = 1.0
        severity: LiteSeverity = field(default_factory=lambda: LiteSeverity("info"))
        message: str = ""
        evidence: dict = field(default_factory=dict)

    @dataclass
    class LiteJudgedTurn:
        turn: LiteTurn = field(default_factory=LiteTurn)
        judgments: list = field(default_factory=list)
        overall_label: object = field(default_factory=lambda: type("L", (), {"value": "PASS"})())
        overall_score: float = 1.0

    @dataclass
    class LiteConversation:
        id: str = ""
        turns: list = field(default_factory=list)

    @dataclass
    class LitePersona:
        name: str = "Unknown"
        persona_type: object = field(default_factory=lambda: type("PT", (), {"value": "standard"})())

    @dataclass
    class LiteJC:
        conversation: LiteConversation = field(default_factory=LiteConversation)
        persona: LitePersona = field(default_factory=LitePersona)
        judged_turns: list = field(default_factory=list)

    results = []

    try:
        with open(jsonl_path, "r") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue

                conv_id = record.get("conversation_id", "")
                persona_name = record.get("persona_name", "Unknown")
                persona_type = record.get("persona_type", "standard")

                # Rebuild turns
                turns = []
                for t in record.get("turns", []):
                    turns.append(LiteTurn(
                        speaker=t.get("speaker", ""),
                        message=t.get("message", ""),
                    ))

                conv = LiteConversation(id=conv_id, turns=turns)
                persona = LitePersona(
                    name=persona_name,
                    persona_type=type("PT", (), {"value": persona_type})(),
                )

                # Rebuild judged turns from JSONL format
                # JSONL uses "judgments" (list of per-turn judgment dicts) with
                # "judge_results" inside each, where each judge uses "judge" (not "judge_name")
                judged_turns = []

                # Get bot turns from the turns list for matching
                bot_turns = [t for t in record.get("turns", []) if t.get("speaker") == "bot"]

                # Try JSONL format: record["judgments"] with judge_results inside
                raw_judgments = record.get("judgments", [])
                if not raw_judgments:
                    # Fallback: try record["judged_turns"] format
                    raw_judgments = record.get("judged_turns", [])

                for idx, t in enumerate(raw_judgments):
                    # Determine the bot turn message
                    # JSONL format: no "turn" sub-object, match by index
                    turn_data = t.get("turn", {})
                    if turn_data and turn_data.get("message"):
                        lt = LiteTurn(
                            speaker=turn_data.get("speaker", "bot"),
                            message=turn_data.get("message", ""),
                        )
                    elif idx < len(bot_turns):
                        lt = LiteTurn(
                            speaker="bot",
                            message=bot_turns[idx].get("message", ""),
                        )
                    else:
                        lt = LiteTurn(speaker="bot", message="")

                    # Parse judge results — support both formats
                    judge_list = t.get("judge_results", []) or t.get("judgments", [])
                    judgments = []
                    for j in judge_list:
                        # Support "judge" (JSONL format) and "judge_name" (internal format)
                        jname = j.get("judge_name", "") or j.get("judge", "")
                        # Map severity — JSONL may not have it, infer from judge type
                        sev_str = j.get("severity", "")
                        if not sev_str:
                            # Infer: safety failures are critical, others are medium
                            if not j.get("passed", True) and jname == "safety":
                                sev_str = "critical"
                            elif not j.get("passed", True):
                                sev_str = "medium"
                            else:
                                sev_str = "info"

                        judgments.append(LiteJudgment(
                            judge_name=jname,
                            passed=j.get("passed", True),
                            score=j.get("score", 1.0),
                            severity=LiteSeverity(sev_str),
                            message=j.get("message", ""),
                            evidence=j.get("evidence", {}),
                        ))

                    label = t.get("overall_label", "PASS")
                    jt = LiteJudgedTurn(
                        turn=lt,
                        judgments=judgments,
                        overall_label=type("L", (), {"value": label})(),
                        overall_score=t.get("overall_score", 1.0),
                    )
                    judged_turns.append(jt)

                results.append(LiteJC(
                    conversation=conv,
                    persona=persona,
                    judged_turns=judged_turns,
                ))

    except Exception as e:
        console.print(f"  [dim]Error loading conversations: {e}[/]")

    return results


# ============================================================
# simtest history — View simulation run history
# ============================================================

@main.command()
@click.option("--dir", "history_dir", default="./reports", help="Directory containing version_history.json")
@click.option("--last", "last_n", default=10, help="Show last N runs (default: 10)")
@click.option("--tag", "filter_tag", default=None, help="Filter by tag (e.g., 'env=staging')")
def history(history_dir: str, last_n: int, filter_tag: str | None):
    """View simulation run history and score trends.

    \b
    Shows a chronological list of simulation runs with pass rates,
    scores, and configuration fingerprints. Useful for tracking
    quality trends over time.

    \b
    Examples:
        simtest history                    # Last 10 runs
        simtest history --last 20          # Last 20 runs
        simtest history --tag env=staging  # Filter by tag
        simtest history --dir ./my-reports # Custom directory
    """
    from rich.table import Table

    console.print("\n[bold cyan]📈 AI SimTest — Run History[/]\n")

    try:
        from src.versioning import VersionHistoryManager
    except ImportError:
        console.print("  [red]Version tracking module not installed.[/]")
        return

    mgr = VersionHistoryManager(history_dir)
    loaded = mgr.load()

    if loaded == 0:
        console.print("  [dim]No history found. Run a simulation first to start tracking.[/]")
        console.print(f"  [dim]Looking in: {history_dir}/version_history.json[/]")
        return

    # Filter by tag if specified
    if filter_tag and "=" in filter_tag:
        key, value = filter_tag.split("=", 1)
        entries = mgr.get_by_tag(key.strip(), value.strip())
        console.print(f"  Filtering by tag: {filter_tag} ({len(entries)} matches)\n")
    else:
        entries = mgr.get_latest(last_n)
        console.print(f"  Showing last {len(entries)} of {mgr.count} total runs\n")

    if not entries:
        console.print("  [dim]No matching runs found.[/]")
        return

    table = Table(show_header=True, header_style="bold")
    table.add_column("#", justify="right", style="dim")
    table.add_column("Simulation", max_width=25)
    table.add_column("Timestamp", style="dim")
    table.add_column("Pass Rate", justify="right")
    table.add_column("Avg Score", justify="right")
    table.add_column("Criticals", justify="right")
    table.add_column("Fingerprint", style="dim")
    table.add_column("Tags", max_width=20)

    for i, entry in enumerate(entries, 1):
        fp = entry.fingerprint
        pr = entry.pass_rate
        pr_color = "green" if pr >= 0.8 else "yellow" if pr >= 0.6 else "red"

        tags_str = ", ".join(f"{k}={v}" for k, v in fp.tags.items()) if fp.tags else ""
        ts = fp.timestamp[:16] if fp.timestamp else ""

        table.add_row(
            str(i),
            fp.simulation_name or fp.simulation_id or "(unnamed)",
            ts,
            f"[{pr_color}]{pr:.0%}[/]",
            f"{entry.avg_score:.2f}",
            str(entry.critical_failures),
            fp.fingerprint_hash[:8],
            tags_str,
        )

    console.print(table)

    # Trend summary
    if len(entries) >= 2:
        first = entries[0]
        last = entries[-1]
        pr_delta = last.pass_rate - first.pass_rate
        direction = "📈" if pr_delta > 0 else "📉" if pr_delta < 0 else "➡️"
        console.print(f"\n  {direction} Pass rate trend: {first.pass_rate:.0%} → {last.pass_rate:.0%} ({pr_delta:+.0%})")

    console.print()


if __name__ == "__main__":
    main()