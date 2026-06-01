"""
Approval Gate Framework — The keystone component for Partial and Fully Autonomous modes.

Every autonomous stage produces output that requires tester approval before proceeding.
This module provides a consistent, reusable interface for approval workflows across
CLI (Rich interactive prompts) and API (REST endpoints) interfaces.

Design Principles:
  - AI proposes, human disposes
  - Partial approval: approve some items, modify others
  - Auto-approve flag for CI/CD (--auto-approve)
  - Audit trail for every decision
  - Fail gracefully: ask for human input rather than proceeding with garbage

Classes exported (all used by autonomous_orchestrator.py):
  - GateDecision, ConfidenceLevel (enums)
  - ProposalItem, GateProposal, GateResult, AuditEntry, AuditTrail (models)
  - ApprovalGate (abstract base)
  - CLIApprovalGate (interactive CLI with improved UX)
  - APIApprovalGate (REST-based)
  - ProgrammaticApprovalGate (for testing/automation)
  - GateManager (coordinates multi-stage pipelines)
"""

from __future__ import annotations

import json
import time
import uuid
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Callable

from pydantic import BaseModel, Field

from src.core.logging import get_logger

logger = get_logger(__name__)


# ============================================================
# Enums
# ============================================================

class GateDecision(str, Enum):
    """Possible outcomes of an approval gate."""
    APPROVED = "approved"
    MODIFIED = "modified"
    REJECTED = "rejected"
    AUTO_APPROVED = "auto_approved"
    TIMED_OUT = "timed_out"


class ConfidenceLevel(str, Enum):
    """AI's confidence in its proposal."""
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


# ============================================================
# Data Models
# ============================================================

class ProposalItem(BaseModel):
    """A single item in an AI proposal that can be individually reviewed."""
    id: str = Field(default_factory=lambda: uuid.uuid4().hex[:8])
    content: Any
    explanation: str = ""
    confidence: ConfidenceLevel = ConfidenceLevel.MEDIUM
    approved: bool | None = None  # None = not yet reviewed


class GateProposal(BaseModel):
    """Structured output from an AI stage, presented for approval."""
    gate_name: str
    stage_number: int = 0
    title: str
    description: str = ""
    items: list[ProposalItem] = Field(default_factory=list)
    raw_data: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @property
    def item_count(self) -> int:
        return len(self.items)


class GateResult(BaseModel):
    """The outcome of an approval gate, including any modifications."""
    gate_name: str
    decision: GateDecision
    original_proposal: GateProposal
    modified_data: Any = None
    modifications: list[str] = Field(default_factory=list)
    reviewer_notes: str = ""
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    duration_seconds: float = 0.0


class AuditEntry(BaseModel):
    """A single entry in the approval audit trail."""
    id: str = Field(default_factory=lambda: uuid.uuid4().hex[:8])
    gate_name: str
    stage_number: int = 0
    decision: GateDecision
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    proposal_summary: str = ""
    modifications: list[str] = Field(default_factory=list)
    reviewer_notes: str = ""
    item_count: int = 0
    approved_count: int = 0
    modified_count: int = 0
    rejected_count: int = 0


class AuditTrail(BaseModel):
    """Complete audit trail for all gates in a simulation run."""
    simulation_id: str = ""
    entries: list[AuditEntry] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    def add_entry(self, result: GateResult) -> None:
        entry = AuditEntry(
            gate_name=result.gate_name,
            stage_number=result.original_proposal.stage_number,
            decision=result.decision,
            timestamp=result.timestamp,
            proposal_summary=result.original_proposal.title,
            modifications=result.modifications,
            reviewer_notes=result.reviewer_notes,
            item_count=result.original_proposal.item_count,
        )
        self.entries.append(entry)

    def save(self, path: str | Path) -> Path:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "w") as f:
            json.dump(self.model_dump(mode="json"), f, indent=2, default=str)
        return p

    @classmethod
    def load(cls, path: str | Path) -> "AuditTrail":
        with open(Path(path)) as f:
            return cls(**json.load(f))


# ============================================================
# Base Approval Gate (Abstract)
# ============================================================

class ApprovalGate(ABC):
    """
    Abstract base class for approval gates.
    """

    def __init__(
        self,
        auto_approve: bool = False,
        timeout_seconds: float | None = None,
        timeout_action: str = "pause",
    ):
        self.auto_approve = auto_approve
        self.timeout_seconds = timeout_seconds
        self.timeout_action = timeout_action

    async def submit(self, proposal: GateProposal) -> GateResult:
        """Submit a proposal for approval."""
        start = time.time()

        if self.auto_approve:
            logger.info(
                "gate_auto_approved",
                gate=proposal.gate_name,
                items=proposal.item_count,
            )
            return GateResult(
                gate_name=proposal.gate_name,
                decision=GateDecision.AUTO_APPROVED,
                original_proposal=proposal,
                modified_data=self._extract_data(proposal),
                duration_seconds=time.time() - start,
            )

        result = await self._present_and_collect(proposal)
        result.duration_seconds = time.time() - start

        logger.info(
            "gate_decision",
            gate=proposal.gate_name,
            decision=result.decision.value,
            modifications=len(result.modifications),
            duration=f"{result.duration_seconds:.1f}s",
        )

        return result

    @abstractmethod
    async def _present_and_collect(self, proposal: GateProposal) -> GateResult:
        ...

    def _extract_data(self, proposal: GateProposal) -> Any:
        if proposal.items:
            return [item.content for item in proposal.items]
        return proposal.raw_data


# ============================================================
# CLI Approval Gate (Rich interactive prompts) — IMPROVED UX
# ============================================================

class CLIApprovalGate(ApprovalGate):
    """
    CLI-based approval gate using Rich for interactive prompts.

    Improved UX:
      - Numbered items, clear help text
      - Field-level editing for dict items
      - Change summary before confirmation
      - Undo last change
    """

    def __init__(
        self,
        auto_approve: bool = False,
        timeout_seconds: float | None = None,
        timeout_action: str = "pause",
        show_confidence: bool = True,
        show_explanations: bool = True,
    ):
        super().__init__(auto_approve, timeout_seconds, timeout_action)
        self.show_confidence = show_confidence
        self.show_explanations = show_explanations

    async def _present_and_collect(self, proposal: GateProposal) -> GateResult:
        from rich.console import Console
        from rich.panel import Panel
        import click

        console = Console()

        stage_label = f"Stage {proposal.stage_number}" if proposal.stage_number else ""
        console.print(Panel.fit(
            f"[bold cyan]Approval Gate: {proposal.title}[/]\n"
            f"{stage_label}  •  {proposal.item_count} items for review",
            title=f"🔒 {proposal.gate_name}",
        ))

        if proposal.description:
            console.print(f"\n  {proposal.description}\n")

        if proposal.items:
            self._display_items_table(console, proposal)
        elif proposal.raw_data:
            self._display_raw_data(console, proposal)

        console.print()
        decision = self._prompt_decision(console, proposal)

        if decision == "approve":
            return GateResult(
                gate_name=proposal.gate_name,
                decision=GateDecision.APPROVED,
                original_proposal=proposal,
                modified_data=self._extract_data(proposal),
            )
        elif decision == "modify":
            modified_data, modifications = self._prompt_modifications(console, proposal)
            return GateResult(
                gate_name=proposal.gate_name,
                decision=GateDecision.MODIFIED,
                original_proposal=proposal,
                modified_data=modified_data,
                modifications=modifications,
            )
        else:
            notes = click.prompt("  Reason for rejection (optional)", default="", show_default=False)
            return GateResult(
                gate_name=proposal.gate_name,
                decision=GateDecision.REJECTED,
                original_proposal=proposal,
                reviewer_notes=notes,
            )

    def _display_items_table(self, console, proposal: GateProposal) -> None:
        from rich.table import Table

        table = Table(show_header=True, show_lines=True, title="Proposed Items", expand=True)
        table.add_column("#", style="dim", width=4)
        table.add_column("Item", ratio=3, no_wrap=False)

        if self.show_confidence:
            table.add_column("Confidence", width=12)
        if self.show_explanations:
            table.add_column("Reasoning", ratio=2, no_wrap=False)

        for i, item in enumerate(proposal.items, 1):
            row = [str(i)]

            if isinstance(item.content, str):
                row.append(item.content)
            elif isinstance(item.content, dict):
                key_parts = []
                for k, v in list(item.content.items())[:5]:
                    val_str = str(v)
                    key_parts.append(f"[bold]{k}[/]: {val_str}")
                row.append("\n".join(key_parts))
            else:
                row.append(str(item.content)[:200])

            if self.show_confidence:
                conf_color = {"high": "green", "medium": "yellow", "low": "red"}.get(item.confidence.value, "white")
                row.append(f"[{conf_color}]{item.confidence.value}[/]")

            if self.show_explanations and item.explanation:
                row.append(f"[dim]{item.explanation}[/]")
            elif self.show_explanations:
                row.append("")

            table.add_row(*row)

        console.print(table)

    def _display_raw_data(self, console, proposal: GateProposal) -> None:
        from rich.syntax import Syntax

        json_str = json.dumps(proposal.raw_data, indent=2, default=str)
        syntax = Syntax(json_str, "json", theme="monokai", word_wrap=True)
        console.print(syntax)

    def _prompt_decision(self, console, proposal: GateProposal) -> str:
        import click

        console.print("  [bold]Options:[/]")
        console.print("    [green]approve[/]  — Accept as-is, proceed to next stage")
        console.print("    [yellow]modify[/]   — Edit items, then proceed")
        console.print("    [red]reject[/]   — Stop execution")

        decision = click.prompt(
            "\n  Your decision",
            type=click.Choice(["approve", "modify", "reject"], case_sensitive=False),
            default="approve",
        )
        return decision.lower()

    def _prompt_modifications(self, console, proposal: GateProposal) -> tuple[Any, list[str]]:
        modifications: list[str] = []
        if proposal.items:
            return self._modify_items_interactive(console, proposal, modifications)
        else:
            return self._modify_raw_data(console, proposal, modifications)

    def _modify_items_interactive(
        self, console, proposal: GateProposal, modifications: list[str]
    ) -> tuple[list, list[str]]:
        """Interactive item modification with help, undo, field-level editing."""
        import click

        modified_items = [item.content for item in proposal.items]
        undo_stack: list[tuple[list, str]] = []

        self._show_modify_help(console)

        while True:
            console.print(f"\n  [dim]({len(modified_items)} items | {len(modifications)} changes)[/]")
            action = click.prompt("  Command", default="done").strip()
            action_lower = action.lower()

            if action_lower == "done":
                if modifications:
                    console.print("\n  [bold]📋 Change Summary:[/]")
                    for i, mod in enumerate(modifications, 1):
                        console.print(f"    {i}. {mod}")
                    confirm = click.confirm("  Apply these changes?", default=True)
                    if not confirm:
                        continue
                break

            elif action_lower == "help":
                self._show_modify_help(console)

            elif action_lower == "list":
                self._show_current_items(console, modified_items)

            elif action_lower == "undo":
                if undo_stack:
                    modified_items, undone_desc = undo_stack.pop()
                    modifications.append(f"Undo: {undone_desc}")
                    console.print(f"    [yellow]↩ Undone: {undone_desc}[/]")
                else:
                    console.print("    [dim]Nothing to undo[/]")

            elif action_lower == "add":
                undo_stack.append((list(modified_items), "before adding new item"))
                new_item = self._prompt_new_item(console, modified_items)
                if new_item is not None:
                    modified_items.append(new_item)
                    modifications.append(f"Added: {self._item_summary(new_item)}")
                    console.print(f"    [green]+ Added item #{len(modified_items)}[/]")
                else:
                    undo_stack.pop()

            elif action_lower.startswith("remove ") or action_lower.startswith("rm "):
                parts = action_lower.split(maxsplit=1)
                if len(parts) == 2:
                    try:
                        idx = int(parts[1]) - 1
                        if 0 <= idx < len(modified_items):
                            undo_stack.append((list(modified_items), f"before removing item {idx+1}"))
                            removed = modified_items.pop(idx)
                            modifications.append(f"Removed #{idx+1}: {self._item_summary(removed)}")
                            console.print(f"    [red]- Removed item #{idx+1}[/]")
                        else:
                            console.print(f"    [red]Invalid number. Range: 1-{len(modified_items)}[/]")
                    except ValueError:
                        console.print("    [red]Usage: remove <number>[/]")

            elif action_lower.startswith("edit "):
                parts = action_lower.split(maxsplit=1)
                if len(parts) == 2:
                    try:
                        idx = int(parts[1]) - 1
                        if 0 <= idx < len(modified_items):
                            undo_stack.append((list(modified_items), f"before editing item {idx+1}"))
                            edited = self._edit_item(console, modified_items[idx], idx + 1)
                            if edited is not None:
                                old_str = self._item_summary(modified_items[idx])
                                modified_items[idx] = edited
                                modifications.append(f"Edited #{idx+1}: {old_str} → {self._item_summary(edited)}")
                                console.print(f"    [yellow]~ Updated item #{idx+1}[/]")
                            else:
                                undo_stack.pop()
                        else:
                            console.print(f"    [red]Invalid number. Range: 1-{len(modified_items)}[/]")
                    except ValueError:
                        console.print("    [red]Usage: edit <number>[/]")

            else:
                try:
                    idx = int(action) - 1
                    if 0 <= idx < len(modified_items):
                        undo_stack.append((list(modified_items), f"before editing item {idx+1}"))
                        edited = self._edit_item(console, modified_items[idx], idx + 1)
                        if edited is not None:
                            old_str = self._item_summary(modified_items[idx])
                            modified_items[idx] = edited
                            modifications.append(f"Edited #{idx+1}: {old_str} → {self._item_summary(edited)}")
                            console.print(f"    [yellow]~ Updated item #{idx+1}[/]")
                        else:
                            undo_stack.pop()
                    else:
                        console.print(f"    [red]Invalid number. Range: 1-{len(modified_items)}[/]")
                except ValueError:
                    console.print("    [dim]Unknown command. Type 'help' for options.[/]")

        return modified_items, modifications

    def _show_modify_help(self, console) -> None:
        from rich.panel import Panel
        help_text = (
            "[bold]Commands:[/]\n"
            "  [cyan]<number>[/]       — Edit item by number (e.g., [cyan]3[/])\n"
            "  [cyan]edit <n>[/]       — Edit item by number\n"
            "  [cyan]add[/]            — Add a new item\n"
            "  [cyan]remove <n>[/]     — Remove item by number (or [cyan]rm <n>[/])\n"
            "  [cyan]list[/]           — Show all current items\n"
            "  [cyan]undo[/]           — Undo last change\n"
            "  [cyan]done[/]           — Finish editing (shows summary)\n"
            "  [cyan]help[/]           — Show this help"
        )
        console.print(Panel(help_text, title="✏️  Modify Mode", border_style="yellow"))

    def _show_current_items(self, console, items: list) -> None:
        console.print("\n  [bold]Current Items:[/]")
        for i, item in enumerate(items, 1):
            console.print(f"    [dim]{i:3d}.[/] {self._item_display(item)}")

    def _edit_item(self, console, item: Any, item_num: int) -> Any | None:
        import click
        if isinstance(item, str):
            console.print(f"\n  [bold]Editing item #{item_num}:[/]")
            console.print(f"  Current: [dim]{item}[/]")
            new_val = click.prompt("  New value (or Enter to keep)", default=item, show_default=False)
            return new_val if new_val != item else None
        elif isinstance(item, dict):
            console.print(f"\n  [bold]Editing item #{item_num}:[/]")
            for k, v in item.items():
                console.print(f"    [bold]{k}[/]: {v}")
            edited = dict(item)
            any_change = False
            console.print("\n  [dim]Edit fields (press Enter to keep, 'skip' to stop):[/]")
            for key in item:
                current = str(item[key])
                new_val = click.prompt(f"  {key}", default=current, show_default=True)
                if new_val.lower() == "skip":
                    break
                if new_val != current:
                    edited[key] = new_val
                    any_change = True
            return edited if any_change else None
        else:
            import click
            console.print(f"\n  [bold]Editing item #{item_num}:[/]")
            new_val = click.prompt("  New value", default=str(item), show_default=False)
            return new_val if new_val != str(item) else None

    def _prompt_new_item(self, console, existing_items: list) -> Any | None:
        import click
        if existing_items and isinstance(existing_items[0], dict):
            sample = existing_items[0]
            console.print(f"\n  [bold]Add new item[/] (fields: {', '.join(sample.keys())})")
            new_item = {}
            for key in sample:
                val = click.prompt(f"  {key}", default="")
                if val.lower() == "cancel":
                    return None
                new_item[key] = val
            return new_item
        else:
            console.print(f"\n  [bold]Add new item[/]")
            val = click.prompt("  Value (or 'cancel')", default="")
            return val if val.lower() != "cancel" and val else None

    def _item_summary(self, item: Any, max_len: int = 80) -> str:
        if isinstance(item, str):
            return item[:max_len] + ("..." if len(item) > max_len else "")
        elif isinstance(item, dict):
            parts = [f"{k}={str(v)[:30]}" for k, v in list(item.items())[:2]]
            return ", ".join(parts)
        return str(item)[:max_len]

    def _item_display(self, item: Any) -> str:
        if isinstance(item, str):
            return item
        elif isinstance(item, dict):
            parts = [f"[bold]{k}[/]={str(v)}" for k, v in item.items()]
            return " | ".join(parts)
        return str(item)

    def _modify_raw_data(self, console, proposal: GateProposal, modifications: list[str]) -> tuple[dict, list[str]]:
        import click
        console.print("\n  [bold]Modify data[/] (enter key=value pairs, 'done' when finished):")
        modified = dict(proposal.raw_data)
        while True:
            entry = click.prompt("  Key=Value (or 'done')", default="done")
            if entry.lower() == "done":
                break
            if "=" in entry:
                key, value = entry.split("=", 1)
                key, value = key.strip(), value.strip()
                old_val = modified.get(key, "<new>")
                modified[key] = value
                modifications.append(f"Set {key}: '{old_val}' → '{value}'")
                console.print(f"    [yellow]~ {key} updated[/]")
            else:
                console.print("    [dim]Format: key=value[/]")
        return modified, modifications


# ============================================================
# API Approval Gate (REST-based for web UI)
# ============================================================

class APIApprovalGate(ApprovalGate):
    """
    API-based approval gate for web UI integration.
    Stores proposals and waits for API calls with decisions.
    """

    _pending_gates: dict[str, GateProposal] = {}
    _gate_results: dict[str, GateResult] = {}

    def __init__(
        self,
        auto_approve: bool = False,
        timeout_seconds: float | None = 300,
        timeout_action: str = "pause",
        poll_interval: float = 2.0,
    ):
        super().__init__(auto_approve, timeout_seconds, timeout_action)
        self.poll_interval = poll_interval

    async def _present_and_collect(self, proposal: GateProposal) -> GateResult:
        import asyncio

        gate_key = f"{proposal.gate_name}_{int(time.time())}"
        APIApprovalGate._pending_gates[gate_key] = proposal

        logger.info("gate_awaiting_api", gate_key=gate_key, gate_name=proposal.gate_name)

        start = time.time()
        while True:
            if gate_key in APIApprovalGate._gate_results:
                result = APIApprovalGate._gate_results.pop(gate_key)
                del APIApprovalGate._pending_gates[gate_key]
                return result

            elapsed = time.time() - start
            if self.timeout_seconds and elapsed > self.timeout_seconds:
                if gate_key in APIApprovalGate._pending_gates:
                    del APIApprovalGate._pending_gates[gate_key]

                if self.timeout_action == "approve":
                    return GateResult(
                        gate_name=proposal.gate_name,
                        decision=GateDecision.TIMED_OUT,
                        original_proposal=proposal,
                        modified_data=self._extract_data(proposal),
                        reviewer_notes="Auto-approved due to timeout",
                    )
                else:
                    return GateResult(
                        gate_name=proposal.gate_name,
                        decision=GateDecision.TIMED_OUT,
                        original_proposal=proposal,
                        reviewer_notes="Timed out waiting for decision",
                    )

            await asyncio.sleep(self.poll_interval)

    @classmethod
    def submit_decision(
        cls,
        gate_key: str,
        decision_or_result: GateResult | str | GateDecision = GateDecision.APPROVED,
        modified_data: Any = None,
    ) -> bool:
        """
        Submit a decision for a pending gate.

        Can be called with:
          - submit_decision(key, GateResult(...))
          - submit_decision(key, "approved", modified_data=[...])
          - submit_decision(key, GateDecision.APPROVED)

        Returns False if gate_key not found in pending gates.
        """
        if gate_key not in cls._pending_gates:
            return False

        if isinstance(decision_or_result, GateResult):
            cls._gate_results[gate_key] = decision_or_result
        else:
            # Build a GateResult from the string/enum decision
            if isinstance(decision_or_result, str):
                decision_or_result = GateDecision(decision_or_result)

            proposal = cls._pending_gates[gate_key]
            cls._gate_results[gate_key] = GateResult(
                gate_name=proposal.gate_name,
                decision=decision_or_result,
                original_proposal=proposal,
                modified_data=modified_data,
            )

        return True

    @classmethod
    def get_pending_gates(cls) -> dict[str, Any]:
        return {
            gate_id: {
                "gate_name": proposal.gate_name,
                "title": proposal.title,
                "description": proposal.description,
                "item_count": proposal.item_count,
                "items": [
                    {
                        "id": item.id,
                        "content": item.content,
                        "explanation": item.explanation,
                        "confidence": item.confidence.value,
                    }
                    for item in proposal.items
                ],
                "raw_data": proposal.raw_data,
            }
            for gate_id, proposal in cls._pending_gates.items()
        }


# ============================================================
# Programmatic Approval Gate (for testing & automation)
# ============================================================

class ProgrammaticApprovalGate(ApprovalGate):
    """
    Programmatic approval gate for testing and automation.
    Uses a callback function to make decisions.
    """

    def __init__(
        self,
        decision_fn: Callable[[GateProposal], GateResult] | None = None,
        default_decision: GateDecision = GateDecision.APPROVED,
        auto_approve: bool = False,
    ):
        super().__init__(auto_approve=auto_approve)
        self.decision_fn = decision_fn
        self.default_decision = default_decision

    async def _present_and_collect(self, proposal: GateProposal) -> GateResult:
        if self.decision_fn:
            return self.decision_fn(proposal)

        return GateResult(
            gate_name=proposal.gate_name,
            decision=self.default_decision,
            original_proposal=proposal,
            modified_data=self._extract_data(proposal),
        )


# ============================================================
# Gate Manager — Coordinates gates across a pipeline
# ============================================================

class GateManager:
    """
    Manages a sequence of approval gates for a simulation pipeline.
    Tracks the audit trail and supports rollback.
    """

    def __init__(
        self,
        gate: ApprovalGate,
        simulation_id: str = "",
    ):
        self.gate = gate
        self.audit_trail = AuditTrail(simulation_id=simulation_id)
        self._results: dict[str, GateResult] = {}

    async def submit_gate(
        self,
        gate_name: str,
        title: str,
        items: list[ProposalItem] | None = None,
        raw_data: dict[str, Any] | None = None,
        description: str = "",
        stage_number: int = 0,
        metadata: dict[str, Any] | None = None,
    ) -> GateResult:
        """Submit a proposal through the configured gate."""
        proposal = GateProposal(
            gate_name=gate_name,
            stage_number=stage_number,
            title=title,
            description=description,
            items=items or [],
            raw_data=raw_data or {},
            metadata=metadata or {},
        )

        result = await self.gate.submit(proposal)
        self.audit_trail.add_entry(result)
        self._results[gate_name] = result
        return result

    def get_result(self, gate_name: str) -> GateResult | None:
        return self._results.get(gate_name)

    def was_approved(self, gate_name: str) -> bool:
        result = self._results.get(gate_name)
        if not result:
            return False
        return result.decision in (
            GateDecision.APPROVED,
            GateDecision.MODIFIED,
            GateDecision.AUTO_APPROVED,
        )

    def was_rejected(self, gate_name: str) -> bool:
        result = self._results.get(gate_name)
        if not result:
            return False
        return result.decision == GateDecision.REJECTED

    def get_approved_data(self, gate_name: str) -> Any:
        result = self._results.get(gate_name)
        if not result:
            return None
        return result.modified_data

    def save_audit_trail(self, path: str | Path) -> Path:
        return self.audit_trail.save(path)

    @property
    def total_gates(self) -> int:
        return len(self._results)

    @property
    def all_approved(self) -> bool:
        return all(
            r.decision in (GateDecision.APPROVED, GateDecision.MODIFIED, GateDecision.AUTO_APPROVED)
            for r in self._results.values()
        )

    @property
    def summary(self) -> dict:
        return {
            "total_gates": self.total_gates,
            "approved": sum(1 for r in self._results.values() if r.decision == GateDecision.APPROVED),
            "modified": sum(1 for r in self._results.values() if r.decision == GateDecision.MODIFIED),
            "rejected": sum(1 for r in self._results.values() if r.decision == GateDecision.REJECTED),
            "auto_approved": sum(1 for r in self._results.values() if r.decision == GateDecision.AUTO_APPROVED),
            "timed_out": sum(1 for r in self._results.values() if r.decision == GateDecision.TIMED_OUT),
        }