"use client";

import { useMemo, useState } from "react";
import { ArrowRight, CheckCircle2, Edit2, Loader2, RotateCcw, Sparkles } from "lucide-react";
import { EditableTable } from "@/components/editable-table";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { engine, EngineError } from "@/lib/engine/client";
import { GATE_ADAPTERS, testPlanStrategy, type Row } from "@/lib/engine/gates";
import type { GateDecision, PendingGate, SimulationStatus } from "@/lib/engine/types";

type ReviewGateProps = {
  simulationId: string;
  gate: PendingGate;
  /** Called with the fresh status once the engine accepted the decision */
  onDecided: (status: SimulationStatus) => void;
};

/**
 * One AI-generated proposal awaiting human approval (Replit's ReviewTableScreen).
 *   Modify  → inline edits (or include/exclude for personas)
 *   Reject  → engine regenerates the proposal
 *   Approve → "approved", or "modified" with the edited rows
 */
export function ReviewGate({ simulationId, gate, onDecided }: ReviewGateProps) {
  const adapter = GATE_ADAPTERS[gate.gate_name];
  const initialRows = useMemo(() => adapter.toRows(gate), [adapter, gate]);

  const [rows, setRows] = useState<Row[]>(initialRows);
  const [excluded, setExcluded] = useState<Set<string>>(new Set());
  const [isEditing, setIsEditing] = useState(false);
  const [busy, setBusy] = useState<GateDecision | null>(null);
  const [error, setError] = useState("");

  const isDirty =
    adapter.mode === "remove" ? excluded.size > 0 : JSON.stringify(rows) !== JSON.stringify(initialRows);
  const keptRows = adapter.mode === "remove" ? rows.filter((r) => !excluded.has(r.id)) : rows;
  const strategy = gate.gate_name === "test_plan" ? testPlanStrategy(gate) : null;

  const submit = async (decision: GateDecision) => {
    let modified: unknown;
    if (decision === "approved" && isDirty) {
      if (keptRows.length === 0) {
        setError(adapter.mode === "remove" ? "Keep at least one persona." : "Add at least one row before approving.");
        return;
      }
      decision = "modified";
      modified = adapter.toModified(keptRows, gate);
      if (Array.isArray(modified) && modified.length === 0) {
        setError("Every row is empty — fill one in or reject to regenerate.");
        return;
      }
    }

    setBusy(decision);
    setError("");
    try {
      onDecided(await engine.decide(simulationId, gate.gate_key, decision, modified));
    } catch (err) {
      setError(err instanceof EngineError ? err.message : "Could not submit your decision.");
      setBusy(null);
    }
  };

  const toggleExcluded = (id: string) =>
    setExcluded((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });

  return (
    <div className="py-6 space-y-6 animate-in fade-in slide-in-from-bottom-4 duration-500">
      <div className="flex flex-col lg:flex-row lg:items-start justify-between gap-4 border-b pb-6">
        <div className="space-y-2">
          <div className="flex flex-wrap items-center gap-3">
            <h1 className="text-2xl md:text-3xl font-bold tracking-tight text-foreground">{adapter.title}</h1>
            <Badge variant="secondary" className="bg-primary/10 text-primary border-primary/20 gap-1.5 py-1">
              <Sparkles className="w-3.5 h-3.5" /> AI Generated · Awaiting Human Approval
            </Badge>
          </div>
          <p className="text-base md:text-lg text-muted-foreground max-w-3xl">{adapter.description}</p>
          {gate.description && <p className="text-sm text-muted-foreground">{gate.description}</p>}
        </div>

        <div className="flex flex-wrap items-center gap-3 lg:self-auto shrink-0">
          <Button
            variant={isEditing ? "default" : "outline"}
            onClick={() => setIsEditing((v) => !v)}
            disabled={!!busy}
          >
            {isEditing ? <CheckCircle2 /> : <Edit2 />}
            {isEditing ? "Done Editing" : "Modify"}
          </Button>
          <Button
            variant="outline"
            onClick={() => submit("regenerate")}
            disabled={!!busy}
            className="text-destructive border-destructive/30 hover:bg-destructive/10 hover:text-destructive"
            title="Discard this proposal and let the AI generate a new one"
          >
            <RotateCcw className={busy === "regenerate" ? "animate-spin" : ""} />
            Reject
          </Button>
          <Button onClick={() => submit("approved")} disabled={!!busy} className="shadow-md shadow-primary/20">
            {busy === "approved" || busy === "modified" ? <Loader2 className="animate-spin" /> : null}
            {isDirty ? "Approve Changes" : "Approve"} <ArrowRight />
          </Button>
        </div>
      </div>

      {error && (
        <p role="alert" className="text-sm font-medium text-destructive">
          {error}
        </p>
      )}

      {isEditing && (
        <p className="text-sm text-muted-foreground">
          {adapter.mode === "remove"
            ? "Untick personas to leave them out of the simulation."
            : "Edit cells inline. Changes are sent to the engine when you approve."}
        </p>
      )}

      {strategy && (
        <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
          {[
            ["Standard users", strategy.standard],
            ["Edge cases", strategy.edgeCase],
            ["Adversarial", strategy.adversarial],
          ].map(([label, pct]) => (
            <div key={label} className="rounded-lg border bg-card px-4 py-3">
              <div className="text-xs font-medium uppercase tracking-wider text-muted-foreground">{label}</div>
              <div className="text-2xl font-bold tabular-nums">{pct}%</div>
            </div>
          ))}
        </div>
      )}

      <div className={`transition-opacity duration-300 ${busy === "regenerate" ? "opacity-40" : ""}`}>
        <EditableTable
          columns={adapter.columns}
          rows={rows}
          onChange={setRows}
          isEditing={isEditing}
          mode={adapter.mode}
          excluded={excluded}
          onToggleExcluded={toggleExcluded}
          onAddRow={adapter.canAddRows && adapter.newRow ? () => setRows((r) => [...r, adapter.newRow!(r.length)]) : undefined}
        />
      </div>

      {adapter.mode === "remove" && (
        <p className="text-sm text-muted-foreground">
          {keptRows.length} of {rows.length} personas will run.
        </p>
      )}
    </div>
  );
}
