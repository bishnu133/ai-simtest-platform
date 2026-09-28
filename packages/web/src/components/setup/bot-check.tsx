"use client";

import { CheckCircle2, Loader2, PlugZap, TriangleAlert, XCircle } from "lucide-react";
import { Button } from "@/components/ui/button";
import type { BotCheckResult } from "@/lib/engine/types";

export function BotCheckButton({ checking, disabled, onCheck }: { checking: boolean; disabled: boolean; onCheck: () => void }) {
  return (
    <Button
      type="button"
      variant="outline"
      className="h-11 shrink-0"
      onClick={onCheck}
      disabled={disabled || checking}
      title="Send your bot one message, as the test would"
    >
      {checking ? <Loader2 className="animate-spin" /> : <PlugZap />}
      Test connection
    </Button>
  );
}

/** What the bot health check found, and the fix when it failed. */
export function BotCheckResultPanel({
  result,
  onUsePath,
}: {
  result: BotCheckResult;
  onUsePath: (path: string) => void;
}) {
  if (result.ok) {
    return (
      <div
        role="status"
        className="space-y-1 rounded-lg border border-pass/30 bg-pass/5 px-3.5 py-2.5 text-sm motion-safe:animate-in motion-safe:fade-in"
      >
        <p className="flex items-center gap-1.5 font-semibold text-pass">
          <CheckCircle2 className="h-4 w-4" aria-hidden /> Connected · answered in {((result.latency_ms ?? 0) / 1000).toFixed(1)}s
        </p>
        <p className="text-muted-foreground">
          We said “{result.sent}” and your bot replied: <span className="text-foreground">“{result.reply}”</span>
        </p>
        {result.slow && (
          <p className="flex items-center gap-1.5 text-xs text-warn">
            <TriangleAlert className="h-3.5 w-3.5" aria-hidden /> That&apos;s slow: every turn of the test will wait this long.
          </p>
        )}
      </div>
    );
  }
  return (
    <div
      role="alert"
      className="space-y-1.5 rounded-lg border border-fail/30 bg-fail/5 px-3.5 py-2.5 text-sm motion-safe:animate-in motion-safe:fade-in"
    >
      <p className="flex items-center gap-1.5 font-semibold text-fail">
        <XCircle className="h-4 w-4 shrink-0" aria-hidden /> {result.problem}
      </p>
      <p className="text-muted-foreground">{result.fix}</p>
      {!!result.suggestions?.length && (
        <div className="flex flex-wrap items-center gap-2 pt-0.5">
          <span className="text-xs text-muted-foreground">Reply found at:</span>
          {result.suggestions.map((p) => (
            <Button key={p} type="button" size="sm" variant="outline" className="h-7 font-mono text-xs" onClick={() => onUsePath(p)}>
              Use {p}
            </Button>
          ))}
        </div>
      )}
      {result.body && (
        <p className="truncate font-mono text-xs text-muted-foreground" title={result.body}>
          Bot said: {result.body}
        </p>
      )}
    </div>
  );
}
