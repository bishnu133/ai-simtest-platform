"""
P3 #18: CLI Integration for Conversation Replay Mode.

INSTRUCTIONS:
  1. Copy the two command blocks below into your cli.py
  2. Place them after the existing 'simtest replay' command
  3. Add 'evaluate' and 'formats' to the version command's help listing

The commands are:
  - simtest evaluate  — Import & evaluate real conversations
  - simtest formats   — List supported input formats
"""


# ============================================================
# simtest evaluate — Import & evaluate real conversations
# ============================================================

# Paste this block into cli.py:

"""
@main.command()
@click.option("--input", "input_paths", required=True, multiple=True, help="Input file(s) or directory of conversations (repeatable)")
@click.option("--format", "input_format", default="auto", type=click.Choice(["auto", "json", "jsonl", "csv", "tsv", "text", "markdown", "simtest"]), help="Input format (default: auto-detect from extension)")
@click.option("--mode", "eval_mode", default="evaluate", type=click.Choice(["evaluate", "retest", "hybrid"]), help="evaluate=judge existing | retest=send to bot | hybrid=both+variations")
@click.option("--documentation", default="", help="Inline documentation for grounding judge")
@click.option("--doc-file", default=None, help="Path to documentation file for grounding")
@click.option("--bot-endpoint", default=None, help="Bot API endpoint (required for retest/hybrid modes)")
@click.option("--bot-api-key", default=None, help="API key for the bot")
@click.option("--bot-format", default="openai", help="Request format: openai, anthropic, custom")
@click.option("--speaker-pattern", default=None, help="Speaker pattern for text files (e.g., 'Customer:|Bot:')")
@click.option("--csv-role-col", default="role", help="CSV column name for speaker role")
@click.option("--csv-message-col", default="message", help="CSV column name for message content")
@click.option("--csv-id-col", default="conversation_id", help="CSV column name for conversation ID")
@click.option("--pass-threshold", default=0.7, help="Score threshold for PASS (0.0-1.0)")
@click.option("--warn-threshold", default=0.5, help="Score threshold for WARNING vs FAIL")
@click.option("--pii-masking", default="none", type=click.Choice(["none", "detect", "mask"]), help="PII handling: none | detect (report only) | mask (replace with [TYPE])")
@click.option("--fail-if-below", "fail_gates", multiple=True, help="CI/CD gate: judge=threshold (e.g., safety=0.95). Repeatable. Exits code 1 if below.")
@click.option("--policy", default=None, help="Policy to evaluate (built-in name or YAML path)")
@click.option("--workflow", default=None, help="Workflow judge (comma-separated names, YAML paths, or 'all')")
@click.option("--no-workflow", is_flag=True, default=False, help="Disable workflow evaluation")
@click.option("--variations", default=5, help="Number of variations per conversation in hybrid mode (default: 5)")
@click.option("--compare-with", default=None, help="Path to summary.json to compare replay results against")
@click.option("--output", default="./reports/evaluate", help="Output directory")
@click.option("--export-formats", default="jsonl,csv,summary,html", help="Comma-separated export formats")
@click.option("--no-verify-ssl", is_flag=True, default=False, help="Disable SSL verification (for corporate proxies)")
@click.option("--fail-on-parse-error", is_flag=True, default=False, help="Fail if any input files cannot be parsed (strict mode)")
@click.option("--judges", "judge_list", default=None, help="Comma-separated judge names to run (default: all). E.g., safety,quality")
def evaluate(
    input_paths,
    input_format,
    eval_mode,
    documentation,
    doc_file,
    bot_endpoint,
    bot_api_key,
    bot_format,
    speaker_pattern,
    csv_role_col,
    csv_message_col,
    csv_id_col,
    pass_threshold,
    warn_threshold,
    pii_masking,
    fail_gates,
    policy,
    workflow,
    no_workflow,
    variations,
    compare_with,
    output,
    export_formats,
    no_verify_ssl,
    fail_on_parse_error,
    judge_list,
):
    \"\"\"Evaluate real conversation logs with AI SimTest's judge pipeline.

    Import conversations from JSON, JSONL, CSV, or text files and run
    grounding, safety, quality, relevance, workflow, and policy judges.

    \\b
    Examples:
        # Evaluate JSON conversation logs
        simtest evaluate --input ./logs/chats.json

        # Evaluate CSV with grounding docs
        simtest evaluate --input ./exports/chats.csv --format csv \\
            --doc-file ./docs/bot_spec.md

        # Re-test: send to new bot version
        simtest evaluate --input ./logs/ --format jsonl \\
            --mode retest --bot-endpoint http://bot:9999/v1/chat/completions

        # CI/CD gate
        simtest evaluate --input ./logs/ --format jsonl \\
            --fail-if-below safety=0.95 --fail-if-below quality=0.70

        # With PII masking
        simtest evaluate --input ./logs/ --pii-masking mask
    \"\"\"
    from ai_simtest_engine.core.logging import setup_logging
    setup_logging()

    console.print(Panel.fit(
        "[bold cyan]AI SimTest[/] — Conversation Replay Evaluation",
        subtitle=f"Mode: {eval_mode} | Format: {input_format}",
    ))

    # Load documentation
    doc_text = documentation
    if doc_file:
        dp = Path(doc_file)
        if dp.exists():
            doc_text = dp.read_text(encoding="utf-8")
            console.print(f"  📄 Loaded documentation: {dp.name} ({len(doc_text)} chars)")

    # Parse fail gates: "safety=0.95" → {"safety": 0.95}
    fail_thresholds = {}
    for gate in fail_gates:
        if "=" in gate:
            judge_name, threshold_str = gate.split("=", 1)
            try:
                fail_thresholds[judge_name.strip()] = float(threshold_str.strip())
            except ValueError:
                console.print(f"  [yellow]⚠ Invalid gate: {gate} (expected judge=threshold)[/]")

    # Build config
    from ai_simtest_engine.replay.models import (
        ReplayConfig, ReplayMode, InputFormat,
        PIIMaskingConfig, PIIMaskingStrategy,
    )

    pii_strategy_map = {
        "none": PIIMaskingStrategy.NONE,
        "detect": PIIMaskingStrategy.DETECT_ONLY,
        "mask": PIIMaskingStrategy.MASK,
    }

    config = ReplayConfig(
        input_paths=list(input_paths),
        input_format=InputFormat(input_format),
        mode=ReplayMode(eval_mode),
        documentation=doc_text,
        bot_endpoint=bot_endpoint,
        bot_api_key=bot_api_key,
        bot_format=bot_format,
        speaker_pattern=speaker_pattern,
        csv_role_col=csv_role_col,
        csv_message_col=csv_message_col,
        csv_conversation_id_col=csv_id_col,
        pass_threshold=pass_threshold,
        warn_threshold=warn_threshold,
        pii_masking=PIIMaskingConfig(
            strategy=pii_strategy_map.get(pii_masking, PIIMaskingStrategy.NONE),
        ),
        fail_thresholds=fail_thresholds,
        policy=policy,
        workflow=workflow,
        no_workflow=no_workflow,
        num_variations=variations,
        output_dir=output,
        export_formats=[f.strip().lower() for f in export_formats.split(",") if f.strip()],
        fail_on_parse_error=fail_on_parse_error,
        judges=[j.strip().lower() for j in judge_list.split(",") if j.strip()] if judge_list else None,
    )

    exit_code = asyncio.run(_run_evaluate(config, compare_with))
    if exit_code != 0:
        sys.exit(exit_code)


async def _run_evaluate(config, compare_with=None):
    \"\"\"Async evaluation pipeline. Returns exit code.\"\"\"
    from ai_simtest_engine.replay import ConversationLoader, ReplayEvaluator

    # Step 1: Load conversations
    with Progress(SpinnerColumn(), TextColumn("[progress.description]{task.description}"), console=console) as progress:
        task = progress.add_task("Loading conversations...", total=None)
        try:
            loader = ConversationLoader()
            conversations, personas, sources, load_summary, pii_report = loader.load(config)
            progress.update(task, description=f"Loaded {len(conversations)} conversations")
        except Exception as e:
            progress.update(task, description=f"[red]Load failed[/]")
            console.print(f"\\n[red]Error loading conversations: {e}[/]")
            return 1

    console.print(f"  📥 Imported: [bold]{len(conversations)}[/] conversations from {len(config.input_paths)} source(s)")
    console.print(f"  🔍 Mode: [bold]{config.mode.value}[/]")

    # Load summary (#3)
    if load_summary.files_failed > 0:
        console.print(f"  ⚠️  Parse failures: [yellow]{load_summary.files_failed}[/] of {load_summary.files_discovered} files")
        for err in load_summary.parse_errors[:3]:
            console.print(f"      → {err['file']}: {err['error'][:80]}")
    if load_summary.unknown_roles_found:
        console.print(f"  ℹ️  Unknown roles found: {', '.join(load_summary.unknown_roles_found)}")

    # PII summary (#6)
    if pii_report.masking_enabled:
        console.print(f"  🔒 PII: {pii_report.total_entities_detected} entities detected, "
                       f"{pii_report.total_fields_redacted} redacted ({pii_report.masking_strategy})")
    elif pii_report.total_entities_detected > 0:
        console.print(f"  🔒 PII detections: [yellow]{pii_report.total_entities_detected}[/]")

    # Step 2: Evaluate
    with Progress(SpinnerColumn(), TextColumn("[progress.description]{task.description}"), console=console) as progress:
        task = progress.add_task(f"Evaluating {len(conversations)} conversations...", total=None)
        try:
            evaluator = ReplayEvaluator()
            report, replay_result = await evaluator.evaluate(
                conversations, personas, sources, config,
                load_summary=load_summary,
                pii_report=pii_report,
            )
            progress.update(task, description="Evaluation complete!")
        except Exception as e:
            progress.update(task, description=f"[red]Evaluation failed[/]")
            console.print(f"\\n[red]Error during evaluation: {e}[/]")
            return 1

    # Step 3: Export
    out_path = Path(config.output_dir)
    out_path.mkdir(parents=True, exist_ok=True)

    exported = {}
    if "summary" in config.export_formats:
        import json as _json
        summary_path = out_path / "summary.json"
        with open(summary_path, "w") as f:
            _json.dump(report.summary.model_dump(), f, indent=2, default=str)
        exported["summary"] = str(summary_path)

    if "html" in config.export_formats:
        try:
            from ai_simtest_engine.exporters.html_report import HTMLReportExporter
            html_path = out_path / "replay_report.html"
            exporter = HTMLReportExporter()
            exporter.export(report, list(personas.values()), html_path)
            exported["html"] = str(html_path)
        except Exception as e:
            console.print(f"  [dim]HTML report skipped: {e}[/]")

    if "jsonl" in config.export_formats:
        try:
            from ai_simtest_engine.exporters.dataset_exporter import DatasetExporter
            ds_exp = DatasetExporter()
            jsonl_path = out_path / "conversations.jsonl"
            ds_exp.export_jsonl(report, str(jsonl_path))
            exported["jsonl"] = str(jsonl_path)
        except Exception as e:
            console.print(f"  [dim]JSONL export skipped: {e}[/]")

    if "csv" in config.export_formats:
        try:
            from ai_simtest_engine.exporters.dataset_exporter import DatasetExporter
            ds_exp = DatasetExporter()
            csv_path = out_path / "turns.csv"
            ds_exp.export_csv(report, str(csv_path))
            exported["csv"] = str(csv_path)
        except Exception as e:
            console.print(f"  [dim]CSV export skipped: {e}[/]")

    # Save replay-specific result
    import json as _json
    replay_path = out_path / "replay_result.json"
    with open(replay_path, "w") as f:
        _json.dump(replay_result.model_dump(), f, indent=2, default=str)
    exported["replay_result"] = str(replay_path)

    # Step 4: Print results
    _print_evaluate_results(report, replay_result, exported)

    # Step 5: Post-simulation analysis (policy, workflow, coverage)
    try:
        _post_simulation_analysis(
            report=report,
            output_dir=config.output_dir,
            exported=exported,
            mode_label="evaluate",
            policy=config.policy,
            workflow=config.workflow,
            no_workflow=config.no_workflow,
        )
    except Exception as e:
        console.print(f"  [dim]Post-analysis skipped: {e}[/]")

    # Step 6: Comparison (if requested)
    if compare_with:
        try:
            comp_baseline = Path(compare_with)
            comp_current = out_path / "summary.json"
            if comp_baseline.exists() and comp_current.exists():
                from ai_simtest_engine.core.comparison_engine import ComparisonEngine
                from ai_simtest_engine.core.comparison_models import ComparisonConfig
                engine = ComparisonEngine()
                comp_result = engine.compare(str(comp_baseline), str(comp_current), ComparisonConfig())
                console.print(f"\\n  Comparison verdict: [bold]{comp_result.verdict.value}[/]")

                try:
                    from ai_simtest_engine.exporters.comparison_report import ComparisonReportGenerator
                    gen = ComparisonReportGenerator()
                    comp_html = out_path / "comparison_report.html"
                    gen.generate(comp_result, str(comp_html))
                    console.print(f"  📁 Comparison report: {comp_html}")
                except Exception:
                    pass
        except Exception as e:
            console.print(f"  [dim]Comparison skipped: {e}[/]")

    # Step 7: CI/CD gate check
    if not replay_result.gate_passed:
        console.print(f"\\n[bold red]✗ CI/CD GATE FAILED:[/]")
        for gate_name, passed in replay_result.gate_results.items():
            icon = "✓" if passed else "✗"
            color = "green" if passed else "red"
            console.print(f"  [{color}]{icon} {gate_name}[/]")
        return 1

    if replay_result.gate_results:
        console.print(f"\\n[bold green]✓ All CI/CD gates passed[/]")

    return 0


def _print_evaluate_results(report, replay_result, exported):
    \"\"\"Print formatted evaluation results.\"\"\"
    s = report.summary
    console.print()

    table = Table(title="Conversation Replay Evaluation")
    table.add_column("Metric", style="bold")
    table.add_column("Value", justify="right")

    table.add_row("Conversations", str(s.total_conversations))
    table.add_row("Turns Evaluated", str(s.total_turns))

    pass_color = "green" if s.pass_rate >= 0.8 else "yellow" if s.pass_rate >= 0.6 else "red"
    table.add_row("Pass Rate", f"[{pass_color}]{s.pass_rate:.1%}[/]")
    table.add_row("Average Score", f"{s.average_score:.3f}")
    table.add_row("Critical Failures", f"[red]{s.critical_failures}[/]" if s.critical_failures else "0")
    table.add_row("Warnings", str(s.warnings))
    table.add_row("Execution Time", f"{s.execution_time_seconds:.1f}s")

    console.print(table)

    # Judge scores
    if report.score_by_judge:
        console.print("\\n  Judge Scores:")
        for judge, score in sorted(report.score_by_judge.items()):
            bar_len = int(score * 20)
            bar = "█" * bar_len + "░" * (20 - bar_len)
            color = "green" if score >= 0.8 else "yellow" if score >= 0.6 else "red"
            console.print(f"    {judge:12s} [{color}]{bar} {score:.1%}[/]")

    # PII summary
    if replay_result.pii_summary:
        console.print("\\n  PII Detections:")
        for entity, count in replay_result.pii_summary.items():
            console.print(f"    {entity}: {count}")

    # Exports
    if exported:
        console.print("\\n  Exports:")
        for fmt, path in exported.items():
            console.print(f"    📁 {fmt}: {path}")

    # Failure patterns
    if report.failure_patterns:
        console.print(f"\\n  Top Failure Patterns ({len(report.failure_patterns)}):")
        for fp in report.failure_patterns[:5]:
            console.print(f"    • [{fp.severity.value}] {fp.pattern_name} (×{fp.frequency})")
"""


# ============================================================
# simtest formats — List supported input formats
# ============================================================

"""
@main.command()
def formats():
    \"\"\"List supported input formats for conversation replay.\"\"\"
    table = Table(title="Supported Input Formats")
    table.add_column("Format", style="bold cyan")
    table.add_column("Extensions")
    table.add_column("Description")

    table.add_row("json", ".json", "JSON array of conversations or single conversation object")
    table.add_row("jsonl", ".jsonl, .ndjson", "One JSON conversation per line")
    table.add_row("csv", ".csv", "Tabular: conversation_id, role, message columns")
    table.add_row("tsv", ".tsv", "Tab-separated (same structure as CSV)")
    table.add_row("text", ".txt, .log, .chat", "Plain text with speaker prefixes")
    table.add_row("markdown", ".md", "Markdown transcripts")
    table.add_row("simtest", ".jsonl", "AI SimTest output for re-evaluation")
    table.add_row("auto", "(any)", "Auto-detect from file extension (default)")

    console.print(table)

    console.print("\\n  [bold]JSON example:[/]")
    console.print('    [{"conversation_id": "c1", "messages": [{"role": "user", "content": "Hello"}]}]')

    console.print("\\n  [bold]CSV example:[/]")
    console.print("    conversation_id,role,message")
    console.print('    c1,user,\\"How do I open an account?\\"')

    console.print("\\n  [bold]Text example:[/]")
    console.print("    Customer: I need help")
    console.print("    Bot: How can I assist?")

    console.print("\\n  [bold]Role normalization:[/]")
    console.print("    user/customer/human/client → [bold]user[/]")
    console.print("    bot/assistant/agent/ai → [bold]bot[/]")
"""


# ============================================================
# Update simtest version to include new commands
# ============================================================

# Add these lines to the version command's help listing:
#   console.print("  evaluate    Evaluate real conversation logs")
#   console.print("  formats     List supported input formats")
