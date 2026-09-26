"use client";

import { CheckCircle2, CircleAlert, ShieldAlert, ShieldCheck, XCircle } from "lucide-react";
import { Button } from "@/components/ui/button";
import { pct, plural } from "@/lib/engine/report";
import type { MemoryResult, ReplayLoad, ScenarioResult } from "@/lib/engine/types";
import { categoryLabel } from "@/components/setup/test-focus";
import { ScoreBars } from "./charts";
import { EmptyNote, Panel } from "./parts";

const TARGET = 0.8;

const MISS_LABELS: Record<string, string> = {
  deflected: "Deflected",
  wrong_value: "Wrong detail",
  ignored: "Answered something else",
};

const humanise = (id: string) => {
  const text = id.replace(/_/g, " ");
  return text[0]?.toUpperCase() + text.slice(1);
};

/** Pass / at risk / fail against the 80% target: icon + label, never colour alone. */
function Outcome({ value }: { value: number | null }) {
  if (value == null) return <span className="text-xs text-muted-foreground">n/a</span>;
  const [Icon, label, tone] =
    value >= TARGET
      ? [CheckCircle2, "Pass", "text-pass"]
      : value >= 0.6
        ? [CircleAlert, "At risk", "text-warn"]
        : [XCircle, "Fail", "text-fail"];
  return (
    <span className={`inline-flex items-center gap-1 text-xs font-semibold ${tone}`}>
      <Icon className="h-3.5 w-3.5" aria-hidden /> {label}
    </span>
  );
}

export function ScenarioResultsPanel({
  scenarios,
  onOpen,
}: {
  scenarios: ScenarioResult[];
  onOpen: (conversationId: string) => void;
}) {
  const failing = scenarios.filter((s) => s.pass_rate < TARGET).length;
  const thin = scenarios.filter((s) => s.conversations < 3).length;
  return (
    <Panel
      title="Scenario results"
      description={
        failing
          ? `${plural(failing, "scenario")} below the ${pct(TARGET)} target, weakest first.`
          : `Every scenario met the ${pct(TARGET)} target.`
      }
    >
      <div className="relative -mx-5 overflow-x-auto px-5">
        <table className="w-full min-w-[34rem] text-sm">
          <caption className="sr-only">Pass rate of each scenario&apos;s replies, weakest first</caption>
          <thead>
            <tr className="border-b text-left text-xs text-muted-foreground">
              <th scope="col" className="py-2 pr-3 font-medium">Scenario</th>
              <th scope="col" className="px-3 py-2 font-medium">Pass rate</th>
              <th scope="col" className="px-3 py-2 text-right font-medium">Chats</th>
              <th scope="col" className="px-3 py-2 text-right font-medium">Stuck</th>
              <th scope="col" className="py-2 pl-3 font-medium"><span className="sr-only">Transcript</span></th>
            </tr>
          </thead>
          <tbody className="divide-y">
            {scenarios.map((s) => (
              <tr key={s.id} className="align-top">
                <th scope="row" className="py-3 pr-3 text-left font-normal">
                  <span className="block font-medium text-foreground">{s.name}</span>
                  <span className="block text-xs text-muted-foreground">
                    {categoryLabel(s.category)} · {s.difficulty}
                  </span>
                </th>
                <td className="px-3 py-3">
                  <div className="flex items-center gap-2">
                    <div className="relative h-2.5 w-24 shrink-0 rounded-r-[4px] bg-muted" aria-hidden>
                      <div className="h-full rounded-r-[4px] bg-primary" style={{ width: `${Math.max(0, Math.min(1, s.pass_rate)) * 100}%` }} />
                      <div className="absolute -top-0.5 -bottom-0.5 w-px bg-foreground/40" style={{ left: `${TARGET * 100}%` }} />
                    </div>
                    <span className="w-10 font-semibold tabular-nums text-foreground">{pct(s.pass_rate)}</span>
                    <Outcome value={s.pass_rate} />
                  </div>
                </td>
                <td className="px-3 py-3 text-right tabular-nums">{s.conversations}</td>
                <td className="px-3 py-3 text-right tabular-nums">{s.stuck || "—"}</td>
                <td className="py-3 pl-3 text-right">
                  {s.conversation_ids[0] && (
                    <Button variant="ghost" size="xs" onClick={() => onOpen(s.conversation_ids[0])}>
                      View chat
                    </Button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="mt-3 flex items-center gap-2 text-xs text-muted-foreground">
        <span className="inline-block h-3 w-px bg-foreground/40" aria-hidden /> Target {pct(TARGET)} of replies passing
      </p>
      {thin > 0 && (
        <p role="note" className="mt-2 text-xs text-muted-foreground">
          {thin === scenarios.length ? "Every scenario" : plural(thin, "scenario")} ran in fewer than 3 conversations, so
          one bad chat moves its pass rate a lot. Run a bigger test before drawing conclusions from a single scenario.
        </p>
      )}
    </Panel>
  );
}

function Headline({ label, value, sub }: { label: string; value: string; sub: string }) {
  return (
    <div className="rounded-lg border p-3">
      <div className="text-xs font-medium text-muted-foreground">{label}</div>
      <div className="mt-1 text-2xl font-bold tabular-nums text-foreground">{value}</div>
      <div className="mt-0.5 text-xs text-muted-foreground">{sub}</div>
    </div>
  );
}

export function MemoryPanel({ memory, onOpen }: { memory: MemoryResult; onOpen: (conversationId: string) => void }) {
  const byGap = memory.by_gap.filter((g) => g.asked > 0);
  const byFact = memory.by_fact.filter((f) => f.asked > 0);
  const checksContradictions = memory.patterns.includes("contradiction");
  return (
    <Panel
      title="Memory & context"
      description={`${plural(memory.conversations, "conversation")} of ${memory.turns} messages. Read from the transcripts: a detail counts as remembered when the bot repeats it back when asked. These replies are scored by exact match and kept out of the pass rate.`}
    >
      <div className="grid gap-3 sm:grid-cols-3">
        <Headline
          label="Details remembered"
          value={memory.recall_rate == null ? "—" : pct(memory.recall_rate)}
          sub={
            memory.facts_asked
              ? `${memory.facts_recalled} of ${memory.facts_asked} asked for`
              : "The customers never asked for a detail back"
          }
        />
        {checksContradictions && (
          <Headline
            label="Contradictions noticed"
            value={memory.noticed_rate == null ? "—" : pct(memory.noticed_rate)}
            sub={memory.contradictions_injected ? `${memory.contradictions_noticed} of ${memory.contradictions_injected}` : "None made it into the chats"}
          />
        )}
        <Headline
          label="Never asked back"
          value={String(memory.facts_not_asked)}
          sub={`of ${plural(memory.facts_shared, "detail")} shared; not counted either way`}
        />
      </div>

      {(byGap.length > 0 || byFact.length > 0) && (
        <div className="mt-5 grid gap-5 lg:grid-cols-2">
          {byGap.length > 0 && (
            <div>
              <h4 className="mb-2 text-sm font-semibold text-foreground">Remembered, by how long ago it was said</h4>
              <ScoreBars
                caption="Recall rate by messages between sharing and asking"
                target={TARGET}
                rows={byGap.map((g) => ({ label: `${g.gap} messages`, value: g.recalled / g.asked, hint: `${g.recalled}/${g.asked}` }))}
              />
            </div>
          )}
          {byFact.length > 0 && (
            <div>
              <h4 className="mb-2 text-sm font-semibold text-foreground">Remembered, by detail</h4>
              <ScoreBars
                caption="Recall rate by detail"
                target={TARGET}
                rows={byFact.map((f) => ({ label: humanise(f.fact_id), value: f.recalled / f.asked, hint: `${f.recalled}/${f.asked}` }))}
              />
            </div>
          )}
        </div>
      )}

      {!!memory.by_reason?.length && (
        <div className="mt-5">
          <h4 className="mb-1 text-sm font-semibold text-foreground">Why it forgot</h4>
          <p className="mb-2 text-xs text-muted-foreground">
            Different failures: not trying, remembering wrong and losing the thread need different fixes.
          </p>
          <ScoreBars
            caption="Share of missed recall questions, by reason"
            rows={memory.by_reason.map((r) => ({
              label: MISS_LABELS[r.reason] ?? r.label,
              value: r.count / memory.by_reason!.reduce((n, x) => n + x.count, 0),
              hint: `${r.count} of ${memory.facts_asked - memory.facts_recalled} — ${r.label}`,
            }))}
          />
        </div>
      )}

      <div className="mt-5">
        <h4 className="mb-2 text-sm font-semibold text-foreground">What it forgot</h4>
        {memory.misses.length ? (
          <ul className="divide-y rounded-lg border">
            {memory.misses.map((m, i) => (
              <li key={i} className="space-y-1.5 p-3 text-sm">
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <span className="font-medium text-foreground">
                    {humanise(m.fact_id)}{" "}
                    <span className="font-normal text-muted-foreground">
                      · shared at message {m.seeded_at}, asked at {m.asked_at} · {m.persona}
                    </span>
                    {m.reason && (
                      <span className="ml-2 rounded bg-muted px-1.5 py-0.5 text-[11px] font-medium text-foreground">
                        {MISS_LABELS[m.reason] ?? m.reason}
                      </span>
                    )}
                  </span>
                  <Button variant="ghost" size="xs" onClick={() => onOpen(m.conversation_id)}>
                    View chat
                  </Button>
                </div>
                <p className="text-muted-foreground">
                  <span className="font-medium text-foreground">Customer:</span> {m.question}
                </p>
                <p className="text-muted-foreground">
                  <span className="font-medium text-foreground">Bot:</span> {m.reply || "(no reply)"}
                </p>
                {!!m.expected?.length && (
                  <p className="text-xs text-muted-foreground">Expected: {m.expected.join(" or ")}</p>
                )}
              </li>
            ))}
          </ul>
        ) : (
          <EmptyNote>{memory.facts_asked ? "Nothing — every detail asked for came back." : "No details were asked for, so nothing could be forgotten."}</EmptyNote>
        )}
      </div>
    </Panel>
  );
}

const ENTITY_LABELS: Record<string, string> = {
  PERSON: "Names",
  EMAIL_ADDRESS: "Email addresses",
  PHONE_NUMBER: "Phone numbers",
  CREDIT_CARD: "Card numbers",
  ACCOUNT_NUMBER: "Account numbers",
  NATIONAL_ID: "National ID numbers",
  US_SSN: "Social security numbers",
  DATE_OF_BIRTH: "Dates of birth",
  IBAN_CODE: "IBANs",
  IP_ADDRESS: "IP addresses",
  LOCATION: "Places",
};

/** What the replay read, what it judged, and what personal data it found. */
export function ReplayPanel({ replay }: { replay: ReplayLoad }) {
  const pii = replay.pii;
  const masked = pii.strategy === "mask";
  const types = Object.entries(pii.by_type);
  const skipped = replay.conversations_loaded - replay.conversations_judged;
  return (
    <Panel
      title="Production replay"
      description={`${replay.filename} · ${replay.resend ? "customer messages re-sent to your bot; today's replies judged" : "the replies in the file judged"}.`}
    >
      <div className="grid gap-3 sm:grid-cols-3">
        <Headline
          label="Conversations judged"
          value={replay.conversations_judged.toLocaleString()}
          sub={
            skipped > 0
              ? `of ${replay.conversations_loaded.toLocaleString()} in the file (${skipped.toLocaleString()} filtered or sampled out)`
              : `every conversation in the file`
          }
        />
        <Headline
          label={masked ? "Personal data masked" : "Personal data found"}
          value={(masked ? pii.masked : pii.detected).toLocaleString()}
          sub={`in ${plural(pii.conversations_with_pii, "conversation")}; ${pii.conversations_clean.toLocaleString()} had none`}
        />
        <Headline
          label="Transcript quality"
          value={`${replay.quality.complete.toLocaleString()} complete`}
          sub={
            replay.quality.partial + replay.quality.low
              ? `${replay.quality.partial} partial, ${replay.quality.low} hard to read`
              : "every conversation had both sides"
          }
        />
      </div>

      <div
        className={`mt-4 flex gap-3 rounded-lg border p-3 text-sm ${masked ? "border-pass/30 bg-pass/5" : "border-warn/30 bg-warn/5"}`}
      >
        {masked ? (
          <ShieldCheck className="mt-0.5 h-4 w-4 shrink-0 text-pass" aria-hidden />
        ) : (
          <ShieldAlert className="mt-0.5 h-4 w-4 shrink-0 text-warn" aria-hidden />
        )}
        <div className="space-y-1">
          <p className="font-medium text-foreground">
            {masked
              ? "Personal data was masked before the judges, the report or the exports saw it."
              : "Personal data was only reported: the transcripts, report and exports contain it as uploaded."}
          </p>
          {types.length > 0 && (
            <p className="text-muted-foreground">
              {types.map(([t, n]) => `${ENTITY_LABELS[t] ?? t} ${n}`).join(" · ")}
            </p>
          )}
          <p className="text-xs text-muted-foreground">
            Found with {pii.engine === "presidio+patterns" ? "Presidio and pattern matching" : "pattern matching"}. The uploaded file
            was not kept.
          </p>
          {pii.warnings.map((w) => (
            <p key={w} className="text-xs font-medium text-warn">
              {w}
            </p>
          ))}
        </div>
      </div>

      {(replay.parse_errors.length > 0 || replay.unknown_roles.length > 0) && (
        <ul className="mt-3 list-disc space-y-1 pl-5 text-xs text-muted-foreground">
          {replay.unknown_roles.length > 0 && (
            <li>Messages from other roles were kept aside and not judged: {replay.unknown_roles.join(", ")}.</li>
          )}
          {replay.parse_errors.map((e) => (
            <li key={e}>{e}</li>
          ))}
        </ul>
      )}
    </Panel>
  );
}
