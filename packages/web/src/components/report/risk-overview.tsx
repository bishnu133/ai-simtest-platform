"use client";

import { BookX, CheckCircle2, MessageCircleOff, Repeat, ShieldAlert, ThumbsDown, Workflow } from "lucide-react";
import { riskSummary, type RiskId } from "@/lib/engine/risks";
import type { JudgedConversation } from "@/lib/engine/types";

const ICONS: Record<RiskId, typeof BookX> = {
  grounding: BookX,
  safety: ShieldAlert,
  quality: ThumbsDown,
  relevance: MessageCircleOff,
  workflow: Workflow,
  stuck: Repeat,
};

/** Where the bot is weak, at a glance: one tile per risk, a click shows those conversations. */
export function RiskOverview({
  conversations,
  onPick,
}: {
  conversations: JudgedConversation[];
  onPick: (risk: RiskId) => void;
}) {
  const rows = riskSummary(conversations);
  const total = conversations.length;
  if (!total || !rows.length) return null;
  return (
    <section aria-labelledby="risk-heading" className="space-y-3">
      <div>
        <h3 id="risk-heading" className="text-base font-semibold text-foreground">
          Where your bot is weak
        </h3>
        <p className="text-sm text-muted-foreground">Conversations with at least one reply failing each check. Click one to read them.</p>
      </div>
      <ul className="grid grid-cols-2 gap-3 sm:grid-cols-[repeat(auto-fit,minmax(10rem,1fr))]">
        {rows.map(({ risk, hits }) => {
          const Icon = ICONS[risk.id];
          const share = hits / total;
          const tone = hits === 0 ? "pass" : share >= 0.25 ? "fail" : "warn";
          return (
            <li key={risk.id}>
              <button
                type="button"
                onClick={() => onPick(risk.id)}
                disabled={hits === 0}
                title={risk.about}
                className={`group flex h-full w-full flex-col gap-2 rounded-xl border bg-card p-4 text-left shadow-xs transition-all enabled:hover:-translate-y-0.5 enabled:hover:shadow-md disabled:cursor-default ${
                  tone === "fail" ? "border-fail/30" : tone === "warn" ? "border-warn/30" : ""
                }`}
              >
                <span className="flex items-center justify-between gap-2">
                  <span
                    className={`flex h-8 w-8 items-center justify-center rounded-lg ${
                      tone === "fail" ? "bg-fail/10 text-fail" : tone === "warn" ? "bg-warn/10 text-warn" : "bg-pass/10 text-pass"
                    }`}
                  >
                    <Icon className="h-4 w-4" aria-hidden />
                  </span>
                  {hits === 0 && <CheckCircle2 className="h-4 w-4 text-pass" aria-hidden />}
                </span>
                <span className="text-sm font-medium text-foreground">{risk.label}</span>
                <span className="flex items-baseline gap-1.5">
                  <span
                    className={`text-2xl font-bold tabular-nums ${tone === "fail" ? "text-fail" : tone === "warn" ? "text-warn" : "text-foreground"}`}
                  >
                    {hits}
                  </span>
                  <span className="text-xs text-muted-foreground">{hits === 0 ? "none found" : `of ${total} conversations`}</span>
                </span>
                <span className="h-1 w-full overflow-hidden rounded-full bg-muted" aria-hidden>
                  <span
                    className={`block h-full rounded-full ${tone === "fail" ? "bg-fail" : tone === "warn" ? "bg-warn" : "bg-pass"}`}
                    style={{ width: `${Math.max(share * 100, hits ? 4 : 0)}%` }}
                  />
                </span>
              </button>
            </li>
          );
        })}
      </ul>
    </section>
  );
}
