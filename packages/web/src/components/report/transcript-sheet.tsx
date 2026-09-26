"use client";

import { CheckCircle2, XCircle } from "lucide-react";
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from "@/components/ui/sheet";
import { conversationVerdict, formatMs, judgeLabel, pct, scoreTone } from "@/lib/engine/report";
import type { JudgedConversation, JudgedTurn } from "@/lib/engine/types";
import { VERDICT_STYLES } from "./conversations-tab";

function Judgments({ jt }: { jt: JudgedTurn }) {
  const label = (jt.overall_label ?? "").toLowerCase();
  return (
    <div className="mt-2 space-y-2 rounded-md border bg-background/60 p-3">
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

/** A judged conversation's messages, with each bot reply's verdicts. */
export function TranscriptTurns({ conversation, className = "" }: { conversation: JudgedConversation; className?: string }) {
  const judgedById = new Map((conversation.judged_turns ?? []).map((jt) => [jt.turn?.id, jt]));
  return (
    <div className={`space-y-4 ${className}`}>
      {(conversation.conversation?.turns ?? []).map((turn, i) => {
        const isUser = (turn.speaker ?? "").toLowerCase() === "user";
        const judged = turn.id ? judgedById.get(turn.id) : undefined;
        const failed = (judged?.overall_label ?? "").toLowerCase() === "fail";
        return (
          <div key={turn.id ?? i} className={`flex ${isUser ? "justify-start" : "justify-end"}`}>
            <div
              className={`max-w-[90%] rounded-lg p-3 text-sm ${
                isUser ? "rounded-tl-none bg-muted" : failed ? "rounded-tr-none bg-fail/10" : "rounded-tr-none bg-accent"
              }`}
            >
              <div className="mb-1 flex items-center justify-between gap-4 text-xs font-semibold text-muted-foreground">
                <span>{isUser ? "User" : "Bot"}</span>
                {!isUser && turn.latency_ms ? <span className="font-normal">{formatMs(turn.latency_ms)}</span> : null}
              </div>
              <p className="whitespace-pre-wrap text-foreground">{turn.message}</p>
              {judged && <Judgments jt={judged} />}
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
