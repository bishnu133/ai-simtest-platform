"use client";

import { useState } from "react";
import { Bot as BotIcon, CheckCircle2, ChevronDown, TriangleAlert, XCircle } from "lucide-react";
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from "@/components/ui/sheet";
import { conversationVerdict, formatMs, judgeLabel, pct, scoreTone } from "@/lib/engine/report";
import type { JudgedConversation, JudgedTurn } from "@/lib/engine/types";
import { VERDICT_STYLES } from "./conversations-tab";

function Judgments({ jt }: { jt: JudgedTurn }) {
  const label = (jt.overall_label ?? "").toLowerCase();
  return (
    <div className="space-y-2 rounded-xl border bg-muted/30 p-3 text-left">
      <div className="flex items-center justify-between text-xs">
        <span className="font-semibold uppercase tracking-wide text-muted-foreground">Judges</span>
        <span className={`font-semibold ${label === "fail" ? "text-fail" : label === "pass" ? "text-pass" : "text-warn"}`}>
          {jt.overall_label} · {pct(jt.overall_score)}
        </span>
      </div>
      <ul className="space-y-2">
        {(jt.judgments ?? []).map((j, i) => (
          <li key={`${j.judge_name}-${i}`} className="flex gap-2 text-xs">
            {j.passed ? (
              <CheckCircle2 className="mt-0.5 h-3.5 w-3.5 shrink-0 text-pass" aria-label="passed" />
            ) : (
              <XCircle className="mt-0.5 h-3.5 w-3.5 shrink-0 text-fail" aria-label="failed" />
            )}
            <div className="min-w-0">
              <span className="font-semibold text-foreground">{judgeLabel(j.judge_name ?? "")}</span>{" "}
              <span className="tabular-nums text-muted-foreground">{pct(j.score)}</span>
              {j.message && <p className="mt-0.5 whitespace-pre-line text-muted-foreground">{j.message}</p>}
            </div>
          </li>
        ))}
      </ul>
      {!!jt.issues?.length && (
        <ul className="list-disc space-y-0.5 pl-5 text-xs text-fail">
          {jt.issues.map((issue, i) => (
            <li key={i}>{issue}</li>
          ))}
        </ul>
      )}
    </div>
  );
}

const CHIP = {
  pass: { label: "Passed", cls: "bg-pass/10 text-pass ring-pass/25", dot: "bg-pass", Icon: CheckCircle2 },
  warning: { label: "Warning", cls: "bg-warn/10 text-warn ring-warn/25", dot: "bg-warn", Icon: TriangleAlert },
  fail: { label: "Failed", cls: "bg-fail/10 text-fail ring-fail/25", dot: "bg-fail", Icon: XCircle },
} as const;

function chipFor(jt: JudgedTurn | undefined) {
  const label = (jt?.overall_label ?? "").toLowerCase();
  return label === "fail" ? CHIP.fail : label === "warning" ? CHIP.warning : label === "pass" ? CHIP.pass : null;
}

function initials(name: string) {
  const parts = name.split(/\s+/).filter(Boolean);
  return ((parts[0]?.[0] ?? "?") + (parts[1]?.[0] ?? "")).toUpperCase();
}

function BotReply({ turnId, judged, children }: { turnId: string; judged: JudgedTurn | undefined; children: React.ReactNode }) {
  const chip = chipFor(judged);
  const failed = chip === CHIP.fail;
  const [open, setOpen] = useState(false);
  return (
    <div className="flex min-w-0 flex-col items-end gap-1.5">
      {children}
      {judged && chip && (
        <div className="flex flex-wrap items-center justify-end gap-2">
          <button
            type="button"
            aria-expanded={open}
            aria-controls={`why-${turnId}`}
            onClick={() => setOpen((o) => !o)}
            className={`inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-xs font-semibold ring-1 transition-colors hover:brightness-95 ${chip.cls}`}
          >
            <chip.Icon className="h-3.5 w-3.5" aria-hidden />
            {chip.label} · {pct(judged.overall_score)}
            <ChevronDown className={`h-3 w-3 transition-transform ${open ? "rotate-180" : ""}`} aria-hidden />
            <span className="sr-only">{open ? "Hide" : "Show"} the judges&apos; reasoning</span>
          </button>
        </div>
      )}
      {judged && !open && failed && !!judged.issues?.length && (
        <p className="max-w-[85%] text-right text-xs text-fail">{judged.issues[0]}</p>
      )}
      {judged && open && (
        <div id={`why-${turnId}`} className="w-full max-w-[85%] motion-safe:animate-in motion-safe:fade-in">
          <Judgments jt={judged} />
        </div>
      )}
    </div>
  );
}

/** A judged conversation as a chat: each bot reply carries its verdict, the reasoning a click away. */
export function TranscriptTurns({ conversation, className = "" }: { conversation: JudgedConversation; className?: string }) {
  const judgedById = new Map((conversation.judged_turns ?? []).map((jt) => [jt.turn?.id, jt]));
  const turns = conversation.conversation?.turns ?? [];
  const persona = conversation.persona?.name ?? "Customer";
  const botTurns = turns
    .map((t, i) => ({ t, i }))
    .filter(({ t }) => (t.speaker ?? "").toLowerCase() !== "user");
  const anchor = (i: number) => `turn-${conversation.conversation?.id ?? "c"}-${i}`;

  return (
    <div className={`space-y-5 ${className}`}>
      {botTurns.some(({ t }) => t.id && judgedById.has(t.id)) && (
        <nav aria-label="Jump to a bot reply" className="flex flex-wrap items-center gap-1.5 rounded-lg border bg-muted/30 px-3 py-2">
          <span className="mr-1 text-xs text-muted-foreground">Replies</span>
          {botTurns.map(({ t, i }, n) => {
            const chip = chipFor(t.id ? judgedById.get(t.id) : undefined);
            return (
              <a
                key={t.id ?? i}
                href={`#${anchor(i)}`}
                title={`Reply ${n + 1}: ${chip?.label ?? "not judged"}`}
                className={`h-3 w-3 rounded-full ring-offset-2 transition-transform hover:scale-125 focus-visible:ring-2 focus-visible:ring-ring ${chip?.dot ?? "bg-muted-foreground/40"}`}
              >
                <span className="sr-only">
                  Reply {n + 1}: {chip?.label ?? "not judged"}
                </span>
              </a>
            );
          })}
        </nav>
      )}
      {turns.map((turn, i) => {
        const isUser = (turn.speaker ?? "").toLowerCase() === "user";
        const judged = turn.id ? judgedById.get(turn.id) : undefined;
        const failed = chipFor(judged) === CHIP.fail;
        const bubble = (
          <div
            className={`max-w-[85%] rounded-2xl px-4 py-2.5 text-sm shadow-xs ${
              isUser
                ? "rounded-tl-md bg-muted text-foreground"
                : `rounded-tr-md border bg-card text-foreground ${failed ? "border-fail/40" : ""}`
            }`}
          >
            <p className="whitespace-pre-wrap">{turn.message}</p>
          </div>
        );
        return (
          <div key={turn.id ?? i} id={anchor(i)} className={`flex scroll-mt-4 gap-2.5 ${isUser ? "" : "flex-row-reverse"}`}>
            <span
              className={`mt-0.5 flex h-8 w-8 shrink-0 items-center justify-center rounded-full text-[11px] font-semibold ${
                isUser ? "bg-muted text-muted-foreground" : "bg-primary/10 text-primary"
              }`}
              aria-hidden
            >
              {isUser ? initials(persona) : <BotIcon className="h-4 w-4" />}
            </span>
            <div className={`flex min-w-0 flex-1 flex-col gap-1 ${isUser ? "items-start" : "items-end"}`}>
              <span className="text-xs text-muted-foreground">
                {isUser ? persona : "Your bot"}
                {!isUser && turn.latency_ms ? ` · ${formatMs(turn.latency_ms)}` : ""}
              </span>
              {isUser ? (
                bubble
              ) : (
                <BotReply turnId={turn.id ?? String(i)} judged={judged}>
                  {bubble}
                </BotReply>
              )}
            </div>
          </div>
        );
      })}
    </div>
  );
}

export function TranscriptSheet({
  conversation,
  onClose,
}: {
  conversation: JudgedConversation | null;
  onClose: () => void;
}) {
  const verdict = conversation ? conversationVerdict(conversation) : "pass";

  return (
    <Sheet open={!!conversation} onOpenChange={(open) => !open && onClose()}>
      <SheetContent side="right" className="w-full overflow-y-auto sm:max-w-2xl">
        {conversation && (
          <>
            <SheetHeader className="space-y-1 pr-6 text-left">
              <SheetTitle className="flex flex-wrap items-center gap-2">
                {conversation.persona?.name ?? "Persona"}
                <span className={`rounded-full px-2 py-0.5 text-xs font-semibold uppercase ${VERDICT_STYLES[verdict]}`}>{verdict}</span>
              </SheetTitle>
              <SheetDescription>
                {judgeLabel(conversation.persona?.persona_type ?? "")} ·{" "}
                <span className={scoreTone(conversation.overall_score)}>score {pct(conversation.overall_score)}</span> ·{" "}
                {conversation.conversation?.turns?.length ?? 0} messages
                {conversation.persona?.goals?.length ? ` · goal: ${conversation.persona.goals[0]}` : ""}
              </SheetDescription>
            </SheetHeader>

            <TranscriptTurns conversation={conversation} className="mt-6" />
          </>
        )}
      </SheetContent>
    </Sheet>
  );
}
