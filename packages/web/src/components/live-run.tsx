"use client";

import { useEffect, useState } from "react";
import { CheckCircle2, CircleAlert, Loader2, MessageSquare, TriangleAlert, XCircle } from "lucide-react";
import { Progress } from "@/components/ui/progress";
import type { LiveExchange, LivePersona, LiveProgress, SimulationStatus } from "@/lib/engine/types";

const clock = (secs: number) => {
  const s = Math.max(0, Math.floor(secs));
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  return h ? `${h}:${String(m).padStart(2, "0")}:${String(s % 60).padStart(2, "0")}` : `${m}:${String(s % 60).padStart(2, "0")}`;
};

/** The engine's elapsed time, ticking locally between polls so the clock never jumps back. */
function useLiveClock(serverSeconds: number) {
  const [now, setNow] = useState(() => Date.now());
  const [anchor, setAnchor] = useState(() => ({ server: serverSeconds, at: now }));
  // A new reading from the engine re-anchors the local clock (state adjusted while rendering)
  if (serverSeconds !== anchor.server) setAnchor({ server: serverSeconds, at: now });
  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(t);
  }, []);
  return Math.max(serverSeconds, anchor.server + (now - anchor.at) / 1000);
}

function initials(name: string) {
  const parts = name.split(/\s+/).filter(Boolean);
  return ((parts[0]?.[0] ?? "?") + (parts[1]?.[0] ?? "")).toUpperCase();
}

const VERDICT = {
  PASS: { label: "Passed", tone: "text-pass", ring: "ring-pass/40 bg-pass/10", Icon: CheckCircle2 },
  WARNING: { label: "Warning", tone: "text-warn", ring: "ring-warn/40 bg-warn/10", Icon: TriangleAlert },
  FAIL: { label: "Failed", tone: "text-fail", ring: "ring-fail/40 bg-fail/10", Icon: XCircle },
} as const;

function Lane({ p }: { p: LivePersona }) {
  const v = p.verdict ? VERDICT[p.verdict] : null;
  const state =
    p.state === "talking"
      ? p.exchanges
        ? `talking, ${p.exchanges} exchange${p.exchanges === 1 ? "" : "s"}`
        : "starting…"
      : p.state === "judging"
        ? "being judged"
        : p.state === "done"
          ? (v?.label.toLowerCase() ?? "done")
          : p.state === "skipped"
            ? "did not hold the conversation"
            : "waiting";
  return (
    <li className="flex min-w-0 items-center gap-2 rounded-lg border bg-card px-2.5 py-2" title={`${p.name}: ${state}`}>
      <span
        className={`relative flex h-8 w-8 shrink-0 items-center justify-center rounded-full text-[11px] font-semibold ring-2 ${
          p.state === "talking"
            ? "bg-primary/10 text-primary ring-primary/50"
            : v
              ? `${v.ring} ${v.tone}`
              : p.state === "judging"
                ? "bg-muted text-foreground ring-primary/25"
                : "bg-muted text-muted-foreground ring-transparent"
        }`}
        aria-hidden
      >
        {initials(p.name)}
        {p.state === "talking" && (
          <span className="absolute inset-0 rounded-full ring-2 ring-primary/40 motion-safe:animate-ping" />
        )}
      </span>
      <span className="min-w-0 text-xs">
        <span className="block truncate font-medium text-foreground">{p.name}</span>
        <span className={`flex items-center gap-1 ${v ? v.tone : "text-muted-foreground"}`}>
          {p.state === "judging" && <Loader2 className="h-3 w-3 motion-safe:animate-spin" aria-hidden />}
          {v && <v.Icon className="h-3 w-3" aria-hidden />}
          {p.state === "talking" && <MessageSquare className="h-3 w-3 text-primary" aria-hidden />}
          <span className="truncate">{state}</span>
        </span>
      </span>
    </li>
  );
}

function FeedItem({ x }: { x: LiveExchange }) {
  const v = x.label ? VERDICT[x.label] : null;
  return (
    <li className="space-y-1.5 border-b p-3 last:border-b-0 motion-safe:animate-in motion-safe:fade-in motion-safe:slide-in-from-top-1 motion-safe:duration-500">
      <div className="flex items-center justify-between gap-2 text-xs">
        <span className="font-semibold text-foreground">{x.persona}</span>
        {v ? (
          <span className={`inline-flex items-center gap-1 font-semibold ${v.tone}`}>
            <v.Icon className="h-3.5 w-3.5" aria-hidden /> {v.label}
          </span>
        ) : (
          <span className="inline-flex items-center gap-1 text-muted-foreground">
            <Loader2 className="h-3.5 w-3.5 motion-safe:animate-spin" aria-hidden /> Judging after the conversation
          </span>
        )}
      </div>
      <p className="text-sm text-muted-foreground">
        <span className="font-medium text-foreground">Customer: </span>
        {x.customer}
      </p>
      <p className="text-sm text-foreground/90">
        <span className="font-medium text-foreground">Bot: </span>
        {x.bot || <em className="text-muted-foreground">(empty reply)</em>}
      </p>
      {x.issue && (
        <p className={`flex items-start gap-1 text-xs ${v?.tone ?? ""}`}>
          <CircleAlert className="mt-0.5 h-3 w-3 shrink-0" aria-hidden /> {x.issue}
        </p>
      )}
    </li>
  );
}

function title(status: SimulationStatus) {
  if (status.config.regression) return "Replaying the regression suite";
  if (status.config.replay) return status.config.replay.resend ? "Re-sending real conversations" : "Judging real conversations";
  return "Customers are talking to your bot";
}

/** Stages 6–7 with live data: who is talking, what has been judged, the latest exchanges. */
export function LiveRun({ status, live }: { status: SimulationStatus; live: LiveProgress }) {
  const elapsed = useLiveClock(live.elapsed_seconds);
  const { conversations_total: total, conversations_done: done, labels } = live;
  const allTalked = total > 0 && done >= total;
  const percent = total ? Math.round((done / total) * 100) : 0;
  // Rate from the conversations finished so far; only once there is something to go on
  const eta = done >= 2 && !allTalked ? (elapsed / done) * (total - done) : null;
  const judgedTotal = labels.PASS + labels.WARNING + labels.FAIL;

  return (
    <div className="mx-auto w-full max-w-5xl space-y-5 py-4" aria-live="off">
      <div className="space-y-3 rounded-xl border bg-card p-5 shadow-xs">
        <div className="flex flex-wrap items-baseline justify-between gap-2">
          <h2 className="text-xl font-bold tracking-tight text-foreground md:text-2xl">{title(status)}</h2>
          <span className="text-sm tabular-nums text-muted-foreground">
            {clock(elapsed)}
            {eta !== null && ` · about ${eta < 90 ? `${Math.max(1, Math.round(eta / 10) * 10)}s` : `${Math.round(eta / 60)} min`} left`}
          </span>
        </div>
        <Progress value={percent} className="h-2.5" aria-label="Conversations finished" />
        <p className="text-sm text-muted-foreground" role="status" aria-live="polite">
          {allTalked ? (
            <>All {total} conversations finished · judging the last replies</>
          ) : (
            <>
              <span className="font-semibold text-foreground tabular-nums">{done}</span> of {total} conversations finished ·{" "}
              {live.talking} talking now
            </>
          )}
        </p>
        <dl className="grid grid-cols-2 gap-3 pt-1 sm:grid-cols-4">
          <div>
            <dt className="text-xs text-muted-foreground">Messages exchanged</dt>
            <dd className="text-lg font-semibold tabular-nums text-foreground">{live.exchanges}</dd>
          </div>
          {(["PASS", "WARNING", "FAIL"] as const).map((k) => {
            const V = VERDICT[k];
            return (
              <div key={k}>
                <dt className={`flex items-center gap-1 text-xs ${V.tone}`}>
                  <V.Icon className="h-3.5 w-3.5" aria-hidden /> Replies {V.label.toLowerCase()}
                </dt>
                <dd className="text-lg font-semibold tabular-nums text-foreground">
                  {labels[k]}
                  {judgedTotal > 0 && (
                    <span className="ml-1 text-xs font-normal text-muted-foreground">
                      {Math.round((labels[k] / judgedTotal) * 100)}%
                    </span>
                  )}
                </dd>
              </div>
            );
          })}
        </dl>
        <p className="text-xs text-muted-foreground">
          {judgedTotal
            ? `${judgedTotal} repl${judgedTotal === 1 ? "y" : "ies"} judged so far. Each conversation is judged as soon as it ends, so these settle as the run goes on.`
            : "Each conversation is judged as soon as it ends."}
        </p>
      </div>

      <div className="grid gap-5 lg:grid-cols-[minmax(0,5fr)_minmax(0,7fr)]">
        <section className="space-y-2" aria-label="Customers">
          <h3 className="text-sm font-semibold text-foreground">Customers</h3>
          <ul className="grid grid-cols-1 gap-2 sm:grid-cols-2">
            {live.personas.map((p) => (
              <Lane key={p.id} p={p} />
            ))}
          </ul>
        </section>
        <section className="space-y-2" aria-label="Latest exchanges">
          <h3 className="text-sm font-semibold text-foreground">Latest exchanges</h3>
          {live.feed.length ? (
            <ul className="overflow-hidden rounded-xl border bg-card">
              {live.feed.map((x) => (
                <FeedItem key={x.seq} x={x} />
              ))}
            </ul>
          ) : (
            <p className="rounded-xl border bg-card p-4 text-sm text-muted-foreground">
              Waiting for the first reply from your bot…
            </p>
          )}
        </section>
      </div>
    </div>
  );
}
