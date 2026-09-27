"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ArrowDown,
  ArrowUp,
  BookOpenCheck,
  CheckCircle2,
  ChevronDown,
  CircleMinus,
  Download,
  Loader2,
  RefreshCw,
  Scale,
  ThumbsDown,
  ThumbsUp,
  TriangleAlert,
  XCircle,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { EmptyNote, Panel, StatTile } from "@/components/report/parts";
import { saveSuggestedQualityThreshold } from "@/lib/calibration";
import { engine, EngineError } from "@/lib/engine/client";
import { judgeLabel, pct, plural } from "@/lib/engine/report";
import { relativeTime } from "@/lib/engine/runs";
import type {
  CalibrationCheck,
  CalibrationCheckBrief,
  CalibrationJudge,
  CalibrationLean,
  CalibrationOverview,
} from "@/lib/engine/types";
import { useNow } from "./runs-table";

// Fewer labels than this and a suggested pass mark is mostly noise (as in Judge review)
const MIN_LABELS_TO_APPLY = 10;
const COST_PER_REPLY = 0.03;

const LEANS: Record<CalibrationLean, { label: string; tone: string; Icon: typeof CheckCircle2 }> = {
  too_strict: { label: "Too strict", tone: "text-warn", Icon: ArrowUp },
  too_lenient: { label: "Too lenient", tone: "text-fail", Icon: ArrowDown },
  // Disagreements both ways: no lean, which is not the same as agreeing
  balanced: { label: "No clear lean", tone: "text-muted-foreground", Icon: Scale },
  not_enough: { label: "Not enough labels", tone: "text-muted-foreground", Icon: CircleMinus },
};

/** Landis & Koch's reading of kappa, in words. */
function kappaWords(k: number | null): string {
  if (k === null) return "not measurable: one side gave the same verdict every time";
  if (k < 0) return "worse than chance";
  if (k < 0.2) return "slight";
  if (k < 0.4) return "fair";
  if (k < 0.6) return "moderate";
  if (k < 0.8) return "substantial";
  return "almost perfect";
}

const range = (low: number | null, high: number | null) =>
  low === null || high === null ? "" : `${pct(low)}–${pct(high)}`;

function Lean({ lean }: { lean: CalibrationLean }) {
  const l = LEANS[lean];
  return (
    <span className={`inline-flex items-center gap-1 text-xs font-semibold ${l.tone}`}>
      <l.Icon className="h-3.5 w-3.5" aria-hidden /> {l.label}
    </span>
  );
}

interface AgreementRow {
  id: string;
  label: string;
  rate: number;
  low: number | null;
  high: number | null;
  n: number;
  detail?: string;
}

/** Agreement with people, 0–100%: dot for the share, line for its 95% range. One series, so no legend. */
function AgreementChart({ rows, caption }: { rows: AgreementRow[]; caption: string }) {
  const x = (v: number) => `${Math.max(0, Math.min(1, v)) * 100}%`;
  return (
    <figure aria-label={caption} className="space-y-2">
      {rows.map((r) => (
        <div
          key={r.id}
          className="group grid grid-cols-[minmax(0,1fr)_5.5rem] items-center gap-x-3 sm:grid-cols-[minmax(6rem,11rem)_minmax(0,1fr)_6.5rem]"
        >
          <span className="col-span-2 truncate text-sm text-muted-foreground sm:col-span-1" title={r.label}>
            {r.label}
          </span>
          <div className="relative h-8">
            {[0.25, 0.5, 0.75].map((t) => (
              <div key={t} className="absolute inset-y-1 w-px bg-border" style={{ left: x(t) }} aria-hidden />
            ))}
            <div className="absolute inset-x-0 top-1/2 h-px bg-border" aria-hidden />
            {r.low !== null && r.high !== null && (
              <div
                className="absolute top-1/2 h-0.5 -translate-y-1/2 rounded-full bg-primary"
                style={{ left: x(r.low), width: `calc(${x(r.high)} - ${x(r.low)})` }}
                aria-hidden
              />
            )}
            <div
              className="absolute top-1/2 h-2.5 w-2.5 -translate-x-1/2 -translate-y-1/2 rounded-full bg-primary ring-2 ring-card"
              style={{ left: x(r.rate) }}
              aria-hidden
            />
            <div
              role="tooltip"
              className="pointer-events-none absolute -top-10 z-10 hidden whitespace-nowrap rounded-md border bg-popover px-2 py-1 text-xs text-popover-foreground shadow-md group-hover:block"
              style={{ left: `min(${x(r.rate)}, calc(100% - 16rem))` }}
            >
              <span className="font-medium">{r.label}</span> {pct(r.rate)} agree
              {r.low !== null ? ` (range ${range(r.low, r.high)})` : ""} · {plural(r.n, "label")}
              {r.detail ? ` · ${r.detail}` : ""}
            </div>
          </div>
          <span className="flex flex-col items-end">
            <span className="text-sm font-semibold tabular-nums text-foreground">{pct(r.rate)}</span>
            <span className="text-[11px] tabular-nums text-muted-foreground">{plural(r.n, "label")}</span>
          </span>
        </div>
      ))}
      <div
        className="grid grid-cols-[minmax(0,1fr)_5.5rem] gap-x-3 text-[11px] text-muted-foreground sm:grid-cols-[minmax(6rem,11rem)_minmax(0,1fr)_6.5rem]"
        aria-hidden
      >
        <span className="hidden sm:block" />
        <div className="relative h-4 whitespace-nowrap">
          <span className="absolute left-0">0%</span>
          <span className="absolute -translate-x-1/2" style={{ left: "50%" }}>
            50%
          </span>
          <span className="absolute right-0">100%</span>
        </div>
        <span />
      </div>
      <figcaption className="text-xs text-muted-foreground">
        Dot: share of labelled replies where the judge and the person gave the same verdict. Line: the 95% range; with
        few labels it is wide.
      </figcaption>
      <div className="sr-only">
        <table>
          <caption>{caption}</caption>
          <thead>
            <tr>
              <th scope="col">Name</th>
              <th scope="col">Agreement</th>
              <th scope="col">95% range</th>
              <th scope="col">Labels</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.id}>
                <td>{r.label}</td>
                <td>{pct(r.rate)}</td>
                <td>{range(r.low, r.high) || "—"}</td>
                <td>{r.n}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </figure>
  );
}

function Verdict({ pass, who }: { pass: boolean; who: string }) {
  return pass ? (
    <span className="inline-flex items-center gap-1 text-xs font-medium text-pass">
      <ThumbsUp className="h-3.5 w-3.5" aria-hidden /> {who}: pass
    </span>
  ) : (
    <span className="inline-flex items-center gap-1 text-xs font-medium text-fail">
      <ThumbsDown className="h-3.5 w-3.5" aria-hidden /> {who}: fail
    </span>
  );
}

function Exchange({ customer, reply }: { customer: string; reply: string }) {
  return (
    <div className="space-y-1.5 text-sm">
      {customer && (
        <p className="line-clamp-3 text-muted-foreground">
          <span className="font-medium text-foreground">Customer: </span>
          {customer}
        </p>
      )}
      <p className="line-clamp-4 text-foreground/90">
        <span className="font-medium text-foreground">Bot: </span>
        {reply}
      </p>
    </div>
  );
}

function JudgeCard({ judge }: { judge: CalibrationJudge }) {
  const [saved, setSaved] = useState(false);
  const [open, setOpen] = useState(false);
  const enough = judge.labelled >= MIN_LABELS_TO_APPLY;
  const shown = open ? judge.disagreements : judge.disagreements.slice(0, 3);
  return (
    <li className="space-y-5 rounded-xl border bg-card p-5 shadow-xs">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <h3 className="text-base font-semibold text-foreground">{judgeLabel(judge.judge)}</h3>
          <p className="mt-0.5 text-sm text-muted-foreground">{judge.verdict}</p>
        </div>
        <Lean lean={judge.lean} />
      </div>
      {judge.caveat && (
        <p className="flex items-start gap-2 rounded-lg border border-warn/30 bg-warn/5 p-3 text-sm text-foreground">
          <TriangleAlert className="mt-0.5 h-4 w-4 shrink-0 text-warn" aria-hidden />
          {judge.caveat}
        </p>
      )}

      <dl className="grid gap-3 sm:grid-cols-4">
        <div>
          <dt className="text-xs text-muted-foreground">Agrees with people</dt>
          <dd className="text-xl font-semibold tabular-nums text-foreground">
            {judge.agreement_rate === null ? "—" : pct(judge.agreement_rate)}
          </dd>
          <dd className="text-[11px] text-muted-foreground">
            {judge.agree} of {plural(judge.labelled, "label")}
            {judge.low !== null ? ` · range ${range(judge.low, judge.high)}` : ""}
          </dd>
        </div>
        <div>
          <dt className="text-xs text-muted-foreground">Beyond chance (kappa)</dt>
          <dd className="text-xl font-semibold tabular-nums text-foreground">
            {judge.kappa === null ? "—" : judge.kappa.toFixed(2)}
          </dd>
          <dd className="text-[11px] text-muted-foreground">{kappaWords(judge.kappa)}</dd>
        </div>
        <div>
          <dt className="text-xs text-muted-foreground">Failed what a person passed</dt>
          <dd className="text-xl font-semibold tabular-nums text-foreground">{judge.too_strict}</dd>
          <dd className="text-[11px] text-muted-foreground">too strict</dd>
        </div>
        <div>
          <dt className="text-xs text-muted-foreground">Passed what a person failed</dt>
          <dd className="text-xl font-semibold tabular-nums text-foreground">{judge.too_lenient}</dd>
          <dd className="text-[11px] text-muted-foreground">too lenient</dd>
        </div>
      </dl>

      {judge.suggested_threshold && (
        <div className="flex flex-wrap items-center gap-3 rounded-lg border bg-muted/30 p-3 text-sm">
          <p className="min-w-0 flex-1">
            A pass mark of <span className="font-semibold tabular-nums">{judge.suggested_threshold.threshold.toFixed(2)}</span>{" "}
            would agree with people on {pct(judge.suggested_threshold.agreement)} of these labels
            {judge.pass_marks_used.length
              ? `; the runs used ${judge.pass_marks_used.map((m) => m.toFixed(2)).join(", ")}`
              : ""}
            .
          </p>
          {judge.judge === "quality" && (
            <span className="flex flex-col items-start gap-1">
              <Button
                size="sm"
                variant="outline"
                disabled={!enough || saved}
                onClick={() => {
                  saveSuggestedQualityThreshold(judge.suggested_threshold!.threshold);
                  setSaved(true);
                }}
              >
                {saved ? <CheckCircle2 /> : null}
                {saved ? "Offered for the next run" : "Use for new tests"}
              </Button>
              {!enough && (
                <span className="text-[11px] text-muted-foreground">Label at least {MIN_LABELS_TO_APPLY} replies first.</span>
              )}
            </span>
          )}
        </div>
      )}

      {judge.runs.length > 1 && (
        <div className="space-y-2">
          <h4 className="text-sm font-semibold text-foreground">Agreement run by run, oldest first</h4>
          <AgreementChart
            caption={`${judgeLabel(judge.judge)} judge: agreement with people per run`}
            rows={judge.runs.map((r) => ({
              id: r.run_id,
              label: r.run_name || r.run_id,
              rate: r.agreement_rate,
              low: r.low,
              high: r.high,
              n: r.labelled,
              detail: r.pass_mark !== null ? `pass mark ${r.pass_mark.toFixed(2)}` : undefined,
            }))}
          />
        </div>
      )}

      {judge.disagreements.length > 0 && (
        <div className="space-y-2">
          <h4 className="text-sm font-semibold text-foreground">
            Where it disagrees ({judge.disagreements.length}
            {judge.labelled - judge.agree > judge.disagreements.length ? ` of ${judge.labelled - judge.agree}` : ""})
            {judge.score_decided ? ", nearest the pass mark first" : ""}
          </h4>
          {judge.score_decided === false && (
            <p className="text-xs text-muted-foreground">
              This judge fails a reply on what it finds (an unsupported claim, personal data, a broken rule) whatever its
              score, so read its reason rather than the score.
            </p>
          )}
          <ul className="divide-y rounded-lg border">
            {shown.map((d) => (
              <li key={`${d.run_id}:${d.key}`} className="space-y-2 p-3">
                <Exchange customer={d.user_message} reply={d.bot_reply} />
                <div className="flex flex-wrap items-center gap-x-4 gap-y-1">
                  <Verdict pass={d.judge_passed} who={`Judge (${d.judge_score.toFixed(2)})`} />
                  <Verdict pass={d.human_pass} who="Person" />
                  <Link href={`/simulations/${d.run_id}`} className="text-xs font-medium text-primary hover:underline">
                    {d.run_name || "Run"} · {d.persona}, reply {d.turn_index + 1}
                  </Link>
                </div>
                {d.judge_message && <p className="text-xs text-muted-foreground">Judge: {d.judge_message}</p>}
                {d.note && <p className="text-xs italic text-muted-foreground">You: “{d.note}”</p>}
              </li>
            ))}
          </ul>
          {judge.disagreements.length > 3 && (
            <Button variant="ghost" size="sm" onClick={() => setOpen((o) => !o)} aria-expanded={open}>
              <ChevronDown className={open ? "rotate-180" : ""} />
              {open ? "Show fewer" : `Show all ${judge.disagreements.length}`}
            </Button>
          )}
        </div>
      )}
    </li>
  );
}

function CheckStatus({ status }: { status: CalibrationCheckBrief["status"] }) {
  if (status === "running")
    return (
      <span className="inline-flex items-center gap-1 text-xs font-semibold text-muted-foreground">
        <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden /> Running
      </span>
    );
  if (status === "failed")
    return (
      <span className="inline-flex items-center gap-1 text-xs font-semibold text-fail">
        <XCircle className="h-3.5 w-3.5" aria-hidden /> Failed
      </span>
    );
  return (
    <span className="inline-flex items-center gap-1 text-xs font-semibold text-pass">
      <CheckCircle2 className="h-3.5 w-3.5" aria-hidden /> Done
    </span>
  );
}

/** The judge's code changed since the check before this one (history is newest first). */
function codeChanged(checks: CalibrationCheckBrief[], index: number, judge: string): boolean {
  const now = checks[index].judges.find((j) => j.judge === judge)?.fingerprint;
  const older = checks.slice(index + 1).find((c) => c.judges.some((j) => j.judge === judge && j.fingerprint));
  const then = older?.judges.find((j) => j.judge === judge)?.fingerprint;
  return !!now && !!then && now !== then;
}

function CheckDetail({ check }: { check: CalibrationCheck }) {
  if (check.status === "running") {
    const { done, total } = check.progress;
    return (
      <div className="space-y-2" aria-live="polite">
        <p className="text-sm text-muted-foreground">
          Re-judging {done} of {plural(total, "labelled reply", "labelled replies")}…
        </p>
        <div
          className="h-2 overflow-hidden rounded-full bg-muted"
          role="progressbar"
          aria-valuemin={0}
          aria-valuemax={total}
          aria-valuenow={done}
          aria-label="Re-check progress"
        >
          <div className="h-full rounded-full bg-primary transition-all" style={{ width: `${total ? (done / total) * 100 : 0}%` }} />
        </div>
      </div>
    );
  }
  if (check.status === "failed" || !check.summary) {
    return (
      <p role="alert" className="text-sm text-destructive">
        {check.error || "The re-check did not finish."}
      </p>
    );
  }
  const s = check.summary;
  return (
    <div className="space-y-5">
      <p className="text-sm text-muted-foreground">
        {plural(s.checked, "labelled reply", "labelled replies")} put to today&apos;s judges, each with its own run&apos;s
        documentation, guardrails and pass marks: {s.changed ? plural(s.changed, "verdict") + " changed" : "no verdict changed"}
        {s.errors ? `; ${plural(s.errors, "reply", "replies")} could not be re-judged` : ""}. The replies are the same, so a
        change comes from the judge.
      </p>
      <div className="overflow-x-auto">
        <table className="w-full min-w-[34rem] text-sm">
          <caption className="sr-only">Agreement with people when the labels were given and today</caption>
          <thead>
            <tr className="border-b text-left text-xs text-muted-foreground">
              <th scope="col" className="py-2 pr-3 font-medium">Judge</th>
              <th scope="col" className="px-3 py-2 text-right font-medium">Agreed then</th>
              <th scope="col" className="px-3 py-2 text-right font-medium">Agrees now</th>
              <th scope="col" className="px-3 py-2 text-right font-medium">Changed</th>
              <th scope="col" className="py-2 pl-3 font-medium">Reading</th>
            </tr>
          </thead>
          <tbody className="divide-y">
            {s.judges.map((j) => (
              <tr key={j.judge}>
                <th scope="row" className="py-2 pr-3 text-left font-medium text-foreground">
                  {judgeLabel(j.judge)}
                </th>
                <td className="px-3 py-2 text-right tabular-nums">{j.agreement_then === null ? "—" : pct(j.agreement_then)}</td>
                <td className="px-3 py-2 text-right tabular-nums">{j.agreement_now === null ? "—" : pct(j.agreement_now)}</td>
                <td className="px-3 py-2 text-right tabular-nums">
                  {j.changed}
                  {j.errors ? <span className="text-muted-foreground"> · {j.errors} not re-judged</span> : null}
                </td>
                <td className="py-2 pl-3 text-muted-foreground">{j.verdict}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {s.judges.some((j) => j.changes.length) && (
        <div className="space-y-2">
          <h4 className="text-sm font-semibold text-foreground">Verdicts that changed</h4>
          <ul className="divide-y rounded-lg border">
            {s.judges.flatMap((j) =>
              j.changes.map((c) => (
                <li key={`${j.judge}:${c.run_id}:${c.key}`} className="space-y-2 p-3">
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <span className="text-xs font-semibold text-foreground">{judgeLabel(j.judge)}</span>
                    {c.effect === "now_agrees" ? (
                      <span className="inline-flex items-center gap-1 text-xs font-semibold text-pass">
                        <CheckCircle2 className="h-3.5 w-3.5" aria-hidden /> Now agrees with the person
                      </span>
                    ) : (
                      <span className="inline-flex items-center gap-1 text-xs font-semibold text-fail">
                        <TriangleAlert className="h-3.5 w-3.5" aria-hidden /> Now disagrees with the person
                      </span>
                    )}
                  </div>
                  <Exchange customer={c.user_message} reply={c.bot_reply} />
                  <div className="flex flex-wrap items-center gap-x-4 gap-y-1">
                    <Verdict pass={c.before.passed} who={`Then (${c.before.score.toFixed(2)})`} />
                    <Verdict pass={c.after.passed} who={`Now (${c.after.score === null ? "—" : c.after.score.toFixed(2)})`} />
                    <Verdict pass={c.human_pass} who="Person" />
                    <Link href={`/simulations/${c.run_id}`} className="text-xs font-medium text-primary hover:underline">
                      {c.run_name || "Run"}
                    </Link>
                  </div>
                  {c.after.message && <p className="text-xs text-muted-foreground">Judge now: {c.after.message}</p>}
                </li>
              )),
            )}
          </ul>
        </div>
      )}
    </div>
  );
}

function Rechecks({ overview }: { overview: CalibrationOverview }) {
  const queryClient = useQueryClient();
  const now = useNow();
  const checks = overview.checks;
  const [picked, setPicked] = useState<string | null>(null);
  const [confirming, setConfirming] = useState(false);
  const [error, setError] = useState("");
  const [starting, setStarting] = useState(false);
  const selected = picked ?? checks[0]?.id ?? null;
  const running = checks.some((c) => c.status === "running");

  const { data: check } = useQuery({
    queryKey: ["calibration-check", selected],
    queryFn: () => engine.getCalibrationCheck(selected!),
    enabled: !!selected,
    refetchInterval: (q) => (q.state.data?.status === "running" ? 1000 : false),
  });

  // A finished check changes the history: refresh it once
  const finished = check?.status !== "running" ? check?.id : undefined;
  useEffect(() => {
    if (finished && checks.find((c) => c.id === finished)?.status === "running") {
      queryClient.invalidateQueries({ queryKey: ["calibration"] });
    }
  }, [finished, checks, queryClient]);

  const start = async () => {
    setError("");
    setStarting(true);
    try {
      const started = await engine.startCalibrationCheck();
      setPicked(started.id);
      setConfirming(false);
      await queryClient.invalidateQueries({ queryKey: ["calibration"] });
    } catch (e) {
      setError(e instanceof EngineError ? e.message : "Could not start the re-check.");
    } finally {
      setStarting(false);
    }
  };

  return (
    <Panel
      title="Re-check today's judges"
      description="Drift across judge versions: the same labelled replies, judged again by the engine as it is now."
    >
      <div className="space-y-5">
        <div className="flex flex-wrap items-center gap-3">
          {confirming ? (
            <>
              <p className="text-sm text-foreground">
                Re-judge {plural(overview.total_labels, "labelled reply", "labelled replies")}? The AI judges run once per
                reply: about ${(overview.total_labels * COST_PER_REPLY).toFixed(2)}.
              </p>
              <Button size="sm" onClick={start} disabled={starting}>
                {starting ? <Loader2 className="animate-spin" /> : <RefreshCw />} Re-check now
              </Button>
              <Button size="sm" variant="ghost" onClick={() => setConfirming(false)}>
                Cancel
              </Button>
            </>
          ) : (
            <Button size="sm" onClick={() => setConfirming(true)} disabled={running || !overview.total_labels}>
              <RefreshCw /> {running ? "Re-check running…" : "Re-check judges"}
            </Button>
          )}
        </div>
        {error && (
          <p role="alert" className="text-sm font-medium text-destructive">
            {error}
          </p>
        )}

        {checks.length > 0 ? (
          <>
            <div className="overflow-x-auto">
              <table className="w-full min-w-[34rem] text-sm">
                <caption className="sr-only">Re-checks, newest first</caption>
                <thead>
                  <tr className="border-b text-left text-xs text-muted-foreground">
                    <th scope="col" className="py-2 pr-3 font-medium">Re-check</th>
                    <th scope="col" className="px-3 py-2 font-medium">Engine</th>
                    <th scope="col" className="px-3 py-2 font-medium">Judges</th>
                    <th scope="col" className="px-3 py-2 text-right font-medium">Changed</th>
                    <th scope="col" className="py-2 pl-3 text-right font-medium">Status</th>
                  </tr>
                </thead>
                <tbody className="divide-y">
                  {checks.map((c, i) => (
                    <tr key={c.id} className={c.id === selected ? "bg-primary/[0.04]" : undefined}>
                      <td className="py-2 pr-3">
                        <button
                          type="button"
                          onClick={() => setPicked(c.id)}
                          aria-current={c.id === selected}
                          className="font-medium text-primary hover:underline"
                          title={new Date(c.created_at).toLocaleString()}
                        >
                          {relativeTime(c.created_at, now)}
                        </button>
                      </td>
                      <td className="px-3 py-2 font-mono text-xs text-muted-foreground">{c.engine_version ?? "—"}</td>
                      <td className="px-3 py-2 text-xs text-muted-foreground">
                        {c.judges.length
                          ? c.judges.map((j, k) => (
                              <span key={j.judge}>
                                {k > 0 && ", "}
                                {judgeLabel(j.judge)}
                                {codeChanged(checks, i, j.judge) && (
                                  <span className="font-medium text-warn"> (judge changed)</span>
                                )}
                              </span>
                            ))
                          : "—"}
                      </td>
                      <td className="px-3 py-2 text-right tabular-nums">{c.changed ?? "—"}</td>
                      <td className="py-2 pl-3 text-right">
                        <CheckStatus status={c.status} />
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            {check ? (
              <CheckDetail check={check} />
            ) : (
              <div className="flex justify-center py-6">
                <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" aria-label="Loading re-check" />
              </div>
            )}
          </>
        ) : (
          <EmptyNote>
            No re-checks yet. Run one after upgrading the engine or changing a judge, to see whether its verdicts moved.
          </EmptyNote>
        )}
      </div>
    </Panel>
  );
}

function GoldenSetButton({ count }: { count: number }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const download = async () => {
    setBusy(true);
    setError("");
    try {
      const data = await engine.getGoldenSet();
      const url = URL.createObjectURL(new Blob([JSON.stringify(data, null, 2)], { type: "application/json" }));
      const a = document.createElement("a");
      a.href = url;
      a.download = "golden_set.json";
      a.click();
      URL.revokeObjectURL(url);
    } catch (e) {
      setError(e instanceof EngineError ? e.message : "Could not download the golden set.");
    } finally {
      setBusy(false);
    }
  };
  return (
    <span className="flex flex-col items-start gap-1 sm:items-end">
      <Button size="sm" variant="outline" onClick={download} disabled={busy || !count}>
        {busy ? <Loader2 className="animate-spin" /> : <Download />} Golden set ({count})
      </Button>
      {error && (
        <span role="alert" className="text-xs text-destructive">
          {error}
        </span>
      )}
    </span>
  );
}

export function CalibrationPage() {
  const { data, isPending, isError, error } = useQuery({ queryKey: ["calibration"], queryFn: engine.getCalibration });
  const withLabels = data?.judges ?? [];

  return (
    <div className="mx-auto w-full max-w-5xl space-y-6 p-4 md:p-8">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0 max-w-3xl">
          <h2 className="text-2xl font-bold tracking-tight text-foreground md:text-3xl">Judge calibration</h2>
          <p className="text-sm text-muted-foreground">
            Every reply labelled in Judge review, across all runs: how often each judge agrees with a person, which way it
            leans, and whether today&apos;s judges still give the same verdicts.
          </p>
        </div>
        {data && <GoldenSetButton count={data.golden_examples} />}
      </div>

      {isError && (
        <p role="alert" className="text-sm font-medium text-destructive">
          {error instanceof Error ? error.message : "Could not load calibration."}
        </p>
      )}
      {isPending ? (
        <div className="flex justify-center py-16">
          <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" aria-label="Loading" />
        </div>
      ) : data && data.total_labels === 0 ? (
        <div className="space-y-3 rounded-xl border bg-card p-6 text-sm shadow-xs">
          <p className="flex items-center gap-2 font-semibold text-foreground">
            <BookOpenCheck className="h-4 w-4 text-primary" aria-hidden /> No labelled replies yet.
          </p>
          <p className="text-muted-foreground">
            Open a finished run, go to its <span className="font-medium text-foreground">Judge review</span> tab and mark
            whether you would pass each reply. {data.min_labels}+ labels per judge give a first reading; labels from several
            runs add up here.
          </p>
          <Button asChild variant="outline" size="sm">
            <Link href="/runs">Go to runs</Link>
          </Button>
        </div>
      ) : data ? (
        <>
          <div className="grid gap-3 sm:grid-cols-3">
            <StatTile label="Labelled replies" value={data.total_labels} sub={`from ${plural(data.runs_with_labels, "run")}`} />
            <StatTile
              label="Judges with a reading"
              value={withLabels.filter((j) => j.labelled >= data.min_labels).length}
              sub={`of ${withLabels.length} labelled · ${data.min_labels}+ labels each`}
            />
            <StatTile
              label="Leaning one way"
              value={withLabels.filter((j) => j.lean === "too_strict" || j.lean === "too_lenient").length}
              sub="judges clearly too strict or too lenient"
            />
          </div>

          {withLabels.length > 1 && (
            <Panel title="Agreement by judge" description="How often each judge gave the verdict a person gave.">
              <AgreementChart
                caption="Agreement with people by judge"
                rows={withLabels
                  .filter((j) => j.agreement_rate !== null)
                  .map((j) => ({
                    id: j.judge,
                    label: judgeLabel(j.judge),
                    rate: j.agreement_rate!,
                    low: j.low,
                    high: j.high,
                    n: j.labelled,
                  }))}
              />
            </Panel>
          )}

          <ul className="space-y-4">
            {withLabels.map((j) => (
              <JudgeCard key={j.judge} judge={j} />
            ))}
          </ul>

          <Rechecks overview={data} />
        </>
      ) : null}
    </div>
  );
}
