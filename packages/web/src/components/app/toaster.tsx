"use client";

import { useSyncExternalStore } from "react";
import { CheckCircle2, Info, X, XCircle } from "lucide-react";
import { dismissToast, readNoToasts, readToasts, subscribeToasts } from "@/lib/toast";

const ICON = { success: CheckCircle2, error: XCircle, info: Info } as const;
const TONE = { success: "text-pass", error: "text-fail", info: "text-primary" } as const;

export function Toaster() {
  const toasts = useSyncExternalStore(subscribeToasts, readToasts, readNoToasts);
  return (
    <div
      aria-live="polite"
      className="pointer-events-none fixed bottom-4 right-4 z-[60] flex w-[min(24rem,calc(100vw-2rem))] flex-col gap-2"
    >
      {toasts.map((t) => {
        const Icon = ICON[t.tone];
        return (
          <div
            key={t.id}
            role={t.tone === "error" ? "alert" : "status"}
            className="pointer-events-auto flex items-start gap-2.5 rounded-xl border bg-popover px-4 py-3 text-sm text-popover-foreground shadow-lg motion-safe:animate-in motion-safe:fade-in motion-safe:slide-in-from-bottom-2"
          >
            <Icon className={`mt-0.5 h-4 w-4 shrink-0 ${TONE[t.tone]}`} aria-hidden />
            <p className="min-w-0 flex-1">{t.message}</p>
            <button
              type="button"
              className="rounded p-0.5 text-muted-foreground hover:text-foreground"
              aria-label="Dismiss"
              onClick={() => dismissToast(t.id)}
            >
              <X className="h-3.5 w-3.5" />
            </button>
          </div>
        );
      })}
    </div>
  );
}
