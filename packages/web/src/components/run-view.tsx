"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import Link from "next/link";
import { AlertOctagon, Ban, Loader2, RotateCcw, ServerCrash, StopCircle } from "lucide-react";
import { ReportView } from "@/components/report/report-view";
import { GeneratingPanel, SimulationLoader } from "@/components/loaders";
import { ReviewGate } from "@/components/review-gate";
import { Button } from "@/components/ui/button";
import { WizardLayout } from "@/components/wizard-layout";
import { engine, EngineError } from "@/lib/engine/client";
import { REPORT_STEP, stepForStatus } from "@/lib/engine/stages";
import {
  TERMINAL_STATUSES,
  type PendingGate,
  type ReportResponse,
  type SimulationStatus,
} from "@/lib/engine/types";

const POLL_MS = 1500;

function Notice({
  icon,
  title,
  children,
  actions,
}: {
  icon: React.ReactNode;
  title: string;
  children?: React.ReactNode;
  actions?: React.ReactNode;
}) {
  return (
    <div className="flex flex-col items-center justify-center min-h-[50vh] text-center gap-4 max-w-xl mx-auto" role="status">
      <div className="p-4 rounded-full bg-muted">{icon}</div>
      <h2 className="text-2xl font-bold tracking-tight">{title}</h2>
      {children && <div className="text-muted-foreground">{children}</div>}
      <div className="flex flex-wrap justify-center gap-3 pt-2">
        {actions}
        <Button asChild>
          <Link href="/new">
            <RotateCcw /> Start New Test
          </Link>
        </Button>
      </div>
    </div>
  );
}

function errorNotice(error: EngineError) {
  if (error.isUnreachable) {
    return (
      <Notice icon={<ServerCrash className="w-8 h-8 text-fail" />} title="Engine not reachable">
        {error.message}
      </Notice>
    );
  }
  if (error.status === 404) {
    return (
      <Notice icon={<Ban className="w-8 h-8 text-muted-foreground" />} title="Simulation not found">
        The engine keeps runs in memory, so runs are lost when it restarts.
      </Notice>
    );
  }
  return (
    <Notice icon={<AlertOctagon className="w-8 h-8 text-fail" />} title="Something went wrong">
      {error.message}
    </Notice>
  );
}

/**
 * Drives one simulation: polls engine status and shows the matching screen —
 * a review gate, a loader, the report, or a terminal notice.
 */
export function RunView({ id, detailed = false }: { id: string; detailed?: boolean }) {
  const [status, setStatus] = useState<SimulationStatus | null>(null);
  const [gate, setGate] = useState<PendingGate | null>(null);
  const [report, setReport] = useState<ReportResponse | null>(null);
  const [error, setError] = useState<EngineError | null>(null);
  const [cancelling, setCancelling] = useState(false);
  const gateKeyRef = useRef<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      const next = await engine.getSimulation(id);
      setStatus(next);
      setError(null);

      const pendingKey = next.pending_gate?.gate_key ?? null;
      if (pendingKey && pendingKey !== gateKeyRef.current) {
        gateKeyRef.current = pendingKey;
        try {
          setGate(await engine.getGate(id));
        } catch {
          // Gate moved on between the two calls — pick it up on the next poll
          gateKeyRef.current = null;
        }
      } else if (!pendingKey) {
        gateKeyRef.current = null;
        setGate(null);
      }
      return next;
    } catch (err) {
      setError(err instanceof EngineError ? err : new EngineError(0, "Unexpected error"));
      return null;
    }
  }, [id]);

  // Poll until the run reaches a terminal state
  useEffect(() => {
    let timer: ReturnType<typeof setTimeout>;
    let stopped = false;
    const tick = async () => {
      const next = await refresh();
      if (stopped) return;
      if (!next || !TERMINAL_STATUSES.includes(next.status)) {
        timer = setTimeout(tick, POLL_MS);
      }
    };
    tick();
    return () => {
      stopped = true;
      clearTimeout(timer);
    };
  }, [refresh]);

  // Fetch the report once complete
  useEffect(() => {
    if (status?.status !== "completed" || report) return;
    engine
      .getReport(id)
      .then(setReport)
      .catch((err) => setError(err instanceof EngineError ? err : new EngineError(0, "Could not load report")));
  }, [id, status?.status, report]);

  const cancel = async () => {
    if (!confirm("Stop this simulation? Progress will be lost.")) return;
    setCancelling(true);
    try {
      setStatus(await engine.cancel(id));
    } catch (err) {
      if (err instanceof EngineError) setError(err);
    } finally {
      setCancelling(false);
    }
  };

  const step = status ? stepForStatus(status) : detailed ? REPORT_STEP : 1;
  const active = status && !TERMINAL_STATUSES.includes(status.status);

  let body: React.ReactNode;
  if (error && !status) {
    body = errorNotice(error);
  } else if (!status) {
    body = (
      <div className="flex justify-center py-24">
        <Loader2 className="w-8 h-8 animate-spin text-muted-foreground" />
      </div>
    );
  } else if (status.status === "completed") {
    body = report ? (
      <ReportView data={report} status={status} initialTab={detailed ? "failures" : "overview"} />
    ) : error ? (
      errorNotice(error)
    ) : (
      <div className="flex justify-center py-24">
        <Loader2 className="w-8 h-8 animate-spin text-muted-foreground" />
      </div>
    );
  } else if (status.status === "failed") {
    body = (
      <Notice icon={<AlertOctagon className="w-8 h-8 text-fail" />} title="Simulation failed">
        <p className="font-mono text-sm break-words">{status.error}</p>
      </Notice>
    );
  } else if (status.status === "aborted") {
    body = (
      <Notice icon={<Ban className="w-8 h-8 text-muted-foreground" />} title="Simulation stopped">
        {status.error}
      </Notice>
    );
  } else if (status.status === "cancelled") {
    body = <Notice icon={<StopCircle className="w-8 h-8 text-muted-foreground" />} title="Simulation cancelled" />;
  } else if (status.status === "awaiting_approval" && gate && gate.gate_key === status.pending_gate?.gate_key) {
    body = (
      <ReviewGate
        key={gate.gate_key}
        simulationId={id}
        gate={gate}
        onDecided={(next) => {
          setStatus(next);
          refresh();
        }}
      />
    );
  } else if (status.stage_number >= 6) {
    body = <SimulationLoader status={status} />;
  } else {
    body = <GeneratingPanel status={status} />;
  }

  return (
    <WizardLayout stepIndex={step}>
      {status && status.status !== "completed" && (
        <div className="flex flex-wrap items-center justify-between gap-2 text-sm text-muted-foreground">
          <span className="truncate">
            <span className="font-medium text-foreground">{status.name}</span> ·{" "}
            <span className="font-mono text-xs">{status.config.bot_endpoint}</span>
          </span>
          {active && (
            <Button variant="ghost" size="sm" onClick={cancel} disabled={cancelling}>
              {cancelling ? <Loader2 className="animate-spin" /> : <StopCircle />} Stop simulation
            </Button>
          )}
        </div>
      )}
      {error && status && (
        <p role="alert" className="mt-2 text-sm text-destructive">
          {error.message} — retrying…
        </p>
      )}
      {body}
    </WizardLayout>
  );
}
