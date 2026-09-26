"use client";

import { CheckCircle2, CircleAlert, XCircle } from "lucide-react";
import { Button } from "@/components/ui/button";
import { pct, plural } from "@/lib/engine/report";
import type { MemoryResult, ScenarioResult } from "@/lib/engine/types";
import { categoryLabel } from "@/components/setup/test-focus";
import { ScoreBars } from "./charts";
import { EmptyNote, Panel } from "./parts";

const TARGET = 0.8;

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
      description={`${plural(memory.conversations, "conversation")} of ${memory.turns} messages. Read from the transcripts: a detail counts as remembered when the bot repeats it back when asked.`}
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
