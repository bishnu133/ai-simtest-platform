"use client";

import { useEffect, useState } from "react";
import { Bot, Loader2, RefreshCcw, Sparkles } from "lucide-react";
import { Progress } from "@/components/ui/progress";
import { GENERATING_LABELS, SIMULATION_PHASES, simulationPhase } from "@/lib/engine/stages";
import type { SimulationStatus } from "@/lib/engine/types";

function useElapsed(since: string) {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(t);
  }, []);
  const secs = Math.max(0, Math.floor((now - new Date(since).getTime()) / 1000));
  return `${Math.floor(secs / 60)}:${String(secs % 60).padStart(2, "0")}`;
}

/** Between approval gates: the engine is drafting the next proposal. */
export function GeneratingPanel({ status }: { status: SimulationStatus }) {
  const label = GENERATING_LABELS[status.stage] ?? "Working…";
  const elapsed = useElapsed(status.updated_at);

  return (
    <div className="flex flex-col items-center justify-center min-h-[50vh] text-center gap-6 animate-in fade-in duration-500" aria-live="polite">
      <div className="relative">
        <div className="absolute inset-0 bg-primary/20 blur-2xl rounded-full scale-150 animate-pulse" />
        <div className="relative bg-card p-5 rounded-2xl shadow-lg border">
          <Sparkles className="w-12 h-12 text-primary" />
        </div>
      </div>
      <div className="space-y-2">
        <h2 className="text-2xl font-bold tracking-tight">{label}</h2>
        <p className="text-muted-foreground">
          The AI is preparing the next step for your review. This usually takes under a minute.
        </p>
      </div>
      <div className="flex items-center gap-2 text-sm text-muted-foreground">
        <Loader2 className="w-4 h-4 animate-spin" /> {elapsed}
      </div>
    </div>
  );
}

/** Stages 6-7: conversations are running against the bot. */
export function SimulationLoader({ status }: { status: SimulationStatus }) {
  const phase = simulationPhase(status);
  const elapsed = useElapsed(status.updated_at);

  return (
    <div className="flex flex-col items-center justify-center min-h-[60vh] max-w-2xl mx-auto text-center space-y-8 animate-in fade-in duration-700">
      <div className="relative">
        <div className="absolute inset-0 bg-primary/20 blur-3xl rounded-full scale-150 animate-pulse" />
        <div
          className="bg-card p-6 rounded-3xl shadow-xl relative z-10 border border-muted/60 animate-bounce"
          style={{ animationDuration: "3s" }}
        >
          <Bot className="w-20 h-20 text-primary" />
        </div>
        <div className="absolute -bottom-2 -right-2 bg-card p-2 rounded-full shadow-lg z-20">
          <RefreshCcw className="w-6 h-6 text-muted-foreground animate-spin" />
        </div>
      </div>

      <div className="space-y-4">
        {status.config.replay ? (
          <>
            <h2 className="text-3xl font-bold tracking-tight">Replay in Progress</h2>
            <p className="text-lg text-muted-foreground leading-relaxed">
              {status.config.replay.resend
                ? "Your real customers' messages are going to your bot again."
                : "Your real conversations are being judged, reply by reply."}
            </p>
          </>
        ) : (
          <>
            <h2 className="text-3xl font-bold tracking-tight">Simulation in Progress</h2>
            <p className="text-lg text-muted-foreground leading-relaxed">
              Your personas are playing with our agents and chatbots.
              <br />
              Hang in there while AI SimTest makes your chatbot sweat.
            </p>
          </>
        )}
      </div>

      <div className="w-full space-y-4 pt-4" aria-live="polite">
        <Progress value={phase.percent} className="h-3 w-full" />
        <div className="flex justify-between gap-4 text-sm font-medium text-muted-foreground px-1">
          <span>
            Phase {phase.index + 1} of {SIMULATION_PHASES.length} · {elapsed}
          </span>
          <span className="text-primary animate-pulse text-right">{phase.label}</span>
        </div>
      </div>
    </div>
  );
}
