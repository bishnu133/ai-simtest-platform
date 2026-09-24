"use client";

import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { judgeLabel, pct, plural, scoreTone } from "@/lib/engine/report";
import type { JudgeBreakdown, JudgedConversation, LoopStats } from "@/lib/engine/types";
import { EmptyNote, Panel } from "./parts";

/** Per-judge pass rate, weight and how many failing turns each decided alone. */
export function JudgeBreakdownPanel({ breakdown }: { breakdown: JudgeBreakdown }) {
  const scored = breakdown.judges.filter((j) => j.kind === "scored");
  const binding = scored[0];
  return (
    <Panel
      title="Judge breakdown"
      description="Which judge decides the pass rate. Detectors report findings rather than scores and are left out of the turn score when they find nothing."
    >
      {binding && breakdown.non_passing_turns > 0 && (
        <p className="mb-4 border-l-2 border-warn pl-3 text-sm text-foreground">
          The <strong>{judgeLabel(binding.name)}</strong> judge passes {pct(binding.pass_rate)} of replies and alone
          decided {binding.sole_failures} of the {breakdown.non_passing_turns} non-passing turns.
        </p>
      )}
      <div className="overflow-x-auto rounded-lg border">
        <Table>
          <TableHeader className="bg-muted/50">
            <TableRow>
              <TableHead className="font-semibold text-foreground">Judge</TableHead>
              <TableHead className="text-right font-semibold text-foreground">Weight</TableHead>
              <TableHead className="text-right font-semibold text-foreground">Pass rate</TableHead>
              <TableHead className="text-right font-semibold text-foreground">Avg score</TableHead>
              <TableHead className="font-semibold text-foreground">Failures</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {breakdown.judges.map((j) => {
              const detector = j.kind === "detector";
              return (
                <TableRow key={j.name}>
                  <TableCell className="whitespace-normal align-top">
                    <div className="font-medium text-foreground">{judgeLabel(j.name)}</div>
                    <div className="text-[11px] uppercase tracking-wide text-muted-foreground">{j.kind}</div>
                    {j.notes.map((n, i) => (
                      <p key={i} className="mt-1 max-w-sm text-xs text-muted-foreground">
                        {n}
                      </p>
                    ))}
                  </TableCell>
                  <TableCell className="text-right align-top tabular-nums">
                    {detector || j.weight === null ? "—" : j.weight.toFixed(2)}
                  </TableCell>
                  <TableCell className={`text-right align-top font-medium tabular-nums ${detector ? "" : scoreTone(j.pass_rate)}`}>
                    {detector ? "—" : pct(j.pass_rate)}
                  </TableCell>
                  <TableCell className="text-right align-top tabular-nums">{j.mean_score.toFixed(2)}</TableCell>
                  <TableCell className="whitespace-normal align-top text-sm">
                    {detector
                      ? `${plural(j.failed_turns, "finding")} in ${j.turns} replies`
                      : `${j.failed_turns} failing · ${j.sole_failures} decided alone`}
                  </TableCell>
                </TableRow>
              );
            })}
          </TableBody>
        </Table>
      </div>
      {breakdown.multi_judge_failures > 0 && (
        <p className="mt-3 text-xs text-muted-foreground">
          {breakdown.multi_judge_failures} non-passing turns failed on two or more judges, so failure-pattern counts
          overlap and can add up to more than the failing turns.
        </p>
      )}
      <PersonaTypeTable breakdown={breakdown} />
      <TurnPositionTable breakdown={breakdown} />
    </Panel>
  );
}

function PersonaTypeTable({ breakdown }: { breakdown: JudgeBreakdown }) {
  const judges = breakdown.judges.filter((j) => j.kind === "scored");
  const types = [...new Set(judges.flatMap((j) => Object.keys(j.pass_rate_by_persona_type)))].sort();
  if (!judges.length || !types.length) return null;
  return (
    <div className="mt-6">
      <h4 className="mb-2 text-sm font-semibold text-foreground">Pass rate by persona type</h4>
      <div className="overflow-x-auto rounded-lg border">
        <Table>
          <TableHeader className="bg-muted/50">
            <TableRow>
              <TableHead className="font-semibold text-foreground">Persona type</TableHead>
              {judges.map((j) => (
                <TableHead key={j.name} className="text-right font-semibold text-foreground">
                  {judgeLabel(j.name)}
                </TableHead>
              ))}
            </TableRow>
          </TableHeader>
          <TableBody>
            {types.map((t) => (
              <TableRow key={t}>
                <TableCell>{judgeLabel(t)}</TableCell>
                {judges.map((j) => {
                  const v = j.pass_rate_by_persona_type[t];
                  return (
                    <TableCell key={j.name} className={`text-right font-medium tabular-nums ${scoreTone(v)}`}>
                      {pct(v)}
                    </TableCell>
                  );
                })}
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </div>
    </div>
  );
}

function TurnPositionTable({ breakdown }: { breakdown: JudgeBreakdown }) {
  const overall = breakdown.pass_rate_by_turn ?? {};
  const bands = Object.keys(overall);
  if (bands.length < 2) return null;
  const rows: [string, Record<string, number>][] = [
    ["All judges (turn label)", overall],
    ...breakdown.judges
      .filter((j) => j.kind === "scored" && j.pass_rate_by_turn)
      .map((j): [string, Record<string, number>] => [judgeLabel(j.name), j.pass_rate_by_turn ?? {}]),
  ];
  const drop = overall[bands[0]] - overall[bands[bands.length - 1]];
  return (
    <div className="mt-6">
      <h4 className="mb-2 text-sm font-semibold text-foreground">Pass rate by position in the conversation</h4>
      <div className="overflow-x-auto rounded-lg border">
        <Table>
          <TableHeader className="bg-muted/50">
            <TableRow>
              <TableHead className="font-semibold text-foreground">Pass rate</TableHead>
              {bands.map((b) => (
                <TableHead key={b} className="text-right font-semibold text-foreground">
                  Replies {b.replace("-", "–")}
                </TableHead>
              ))}
            </TableRow>
          </TableHeader>
          <TableBody>
            {rows.map(([name, rates]) => (
              <TableRow key={name}>
                <TableCell>{name}</TableCell>
                {bands.map((b) => (
                  <TableCell key={b} className={`text-right font-medium tabular-nums ${scoreTone(rates[b])}`}>
                    {pct(rates[b])}
                  </TableCell>
                ))}
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </div>
      {drop >= 0.15 && (
        <p className="mt-3 text-xs text-muted-foreground">
          Replies pass less often later in the conversation ({pct(overall[bands[0]])} → {pct(overall[bands[bands.length - 1]])}).
          If the run used a high minimum turn count, simulated users were told to keep asking after their goal was met,
          so later turns are mostly follow-ups the bot may not be built for.
        </p>
      )}
    </div>
  );
}

/** Conversations where the bot repeated itself or the user said they were not answered. */
export function LoopsPanel({
  loops,
  conversations,
  onOpen,
}: {
  loops: LoopStats;
  conversations: JudgedConversation[];
  onOpen: (id: string) => void;
}) {
  const worst = conversations
    .filter((jc) => (jc.bot_repeats ?? 0) + (jc.user_reasks ?? 0) > 0)
    .sort((a, b) => (b.bot_repeats ?? 0) + (b.user_reasks ?? 0) - ((a.bot_repeats ?? 0) + (a.user_reasks ?? 0)))
    .slice(0, 5);
  return (
    <Panel
      title="Conversation loops"
      description="Stuck means the bot sent the same reply again, or the user said they were not answered, twice or more."
    >
      <p className="text-sm text-foreground">
        <strong className="tabular-nums">
          {loops.stuck_conversations} of {conversations.length}
        </strong>{" "}
        conversations got stuck. The bot repeated itself in {loops.conversations_with_bot_repeats}; users re-asked in{" "}
        {loops.conversations_with_user_reasks}.
      </p>
      {worst.length ? (
        <ul className="mt-3 divide-y">
          {worst.map((jc) => (
            <li key={jc.conversation?.id} className="flex items-center justify-between gap-3 py-2 text-sm">
              <button
                type="button"
                className="min-w-0 truncate text-left font-medium text-foreground underline-offset-4 hover:underline"
                onClick={() => onOpen(jc.conversation?.id ?? "")}
              >
                {jc.persona?.name ?? jc.conversation?.id}
              </button>
              <span className="shrink-0 text-xs text-muted-foreground tabular-nums">
                {plural(jc.bot_repeats ?? 0, "repeat")} · {plural(jc.user_reasks ?? 0, "re-ask")}
              </span>
            </li>
          ))}
        </ul>
      ) : (
        <EmptyNote>No loops detected.</EmptyNote>
      )}
    </Panel>
  );
}
