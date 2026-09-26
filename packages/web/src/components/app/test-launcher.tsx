"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";
import { Check, CheckCircle2, Clock, Copy, Terminal } from "lucide-react";
import { Button } from "@/components/ui/button";
import { SetupForm, type TestFocus } from "@/components/setup-form";
import { TEST_TYPES, testType, type TestType } from "@/lib/test-types";

const isFocus = (id: string): id is TestFocus => ["simulation", "scenarios", "stress", "replay"].includes(id);

function CopyButton({ text }: { text: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <Button
      size="xs"
      variant="outline"
      onClick={async () => {
        try {
          await navigator.clipboard.writeText(text);
          setCopied(true);
          setTimeout(() => setCopied(false), 1500);
        } catch {
          /* clipboard unavailable: the command is still selectable */
        }
      }}
    >
      {copied ? <Check /> : <Copy />} {copied ? "Copied" : "Copy"}
    </Button>
  );
}

function ComingSoon({ type }: { type: TestType }) {
  return (
    <div className="space-y-5 rounded-xl border bg-card p-6 shadow-xs">
      <p className="text-sm leading-relaxed text-foreground/85">{type.description}</p>
      <div className="grid gap-5 md:grid-cols-2">
        <div>
          <h3 className="mb-2 text-sm font-semibold text-foreground">What it checks</h3>
          <ul className="space-y-1.5 text-sm text-muted-foreground">
            {type.checks.map((c) => (
              <li key={c} className="flex gap-2">
                <CheckCircle2 className="mt-0.5 h-4 w-4 shrink-0 text-primary" aria-hidden /> {c}
              </li>
            ))}
          </ul>
        </div>
        <div>
          <h3 className="mb-2 text-sm font-semibold text-foreground">What you&apos;ll need</h3>
          <ul className="list-disc space-y-1.5 pl-5 text-sm text-muted-foreground">
            {type.needs.map((n) => (
              <li key={n}>{n}</li>
            ))}
          </ul>
        </div>
      </div>
      {type.cli && (
        <div className="space-y-2 rounded-lg border bg-muted/40 p-4">
          <div className="flex items-center justify-between gap-2">
            <h3 className="flex items-center gap-1.5 text-sm font-semibold text-foreground">
              <Terminal className="h-4 w-4" aria-hidden /> Run it today from the engine CLI
            </h3>
            <CopyButton text={type.cli} />
          </div>
          <pre className="overflow-x-auto whitespace-pre rounded-md bg-background px-3 py-2 font-mono text-xs text-foreground">
            {type.cli}
          </pre>
          <p className="text-xs text-muted-foreground">
            It will get its own guided setup here in {type.roadmap}. Results from the CLI open in the same HTML report.
          </p>
        </div>
      )}
    </div>
  );
}

export function TestLauncher({ initialType }: { initialType?: string }) {
  const router = useRouter();
  const [selected, setSelected] = useState(() => testType(initialType).id);
  const type = testType(selected);

  const choose = (id: string) => {
    setSelected(id as TestType["id"]);
    router.replace(`/new?type=${id}`, { scroll: false });
  };

  return (
    <div className="mx-auto grid w-full max-w-7xl grid-cols-[minmax(0,1fr)] gap-6 p-4 md:p-8 lg:grid-cols-[300px_minmax(0,1fr)]">
      <aside className="min-w-0 space-y-3 lg:sticky lg:top-0 lg:self-start">
        <div>
          <h2 className="text-lg font-semibold text-foreground">What do you want to test?</h2>
          <p className="text-sm text-muted-foreground">Pick a test type to see what it checks and set it up.</p>
        </div>
        <div
          role="radiogroup"
          aria-label="Test type"
          className="-mx-4 flex gap-2 overflow-x-auto px-4 pb-1 lg:mx-0 lg:flex-col lg:overflow-visible lg:px-0"
        >
          {TEST_TYPES.map((t) => {
            const active = t.id === selected;
            return (
              <button
                key={t.id}
                type="button"
                role="radio"
                aria-checked={active}
                onClick={() => choose(t.id)}
                className={`flex min-w-[220px] shrink-0 items-start gap-3 rounded-xl border p-3 text-left transition-colors lg:min-w-0 ${
                  active ? "border-primary bg-primary/[0.06] ring-1 ring-primary/30" : "bg-card hover:border-primary/40 hover:bg-muted/50"
                }`}
              >
                <span
                  className={`flex h-8 w-8 shrink-0 items-center justify-center rounded-lg ${active ? "bg-primary text-primary-foreground" : "bg-primary/10 text-primary"}`}
                >
                  <t.icon className="h-4 w-4" aria-hidden />
                </span>
                <span className="min-w-0 flex-1">
                  <span className="flex items-center justify-between gap-2 text-sm font-medium text-foreground">
                    {t.name}
                    {!t.available && (
                      <span className="rounded bg-muted px-1.5 py-0.5 text-[10px] font-medium uppercase tracking-wide text-muted-foreground">
                        Soon
                      </span>
                    )}
                  </span>
                  <span className="mt-0.5 line-clamp-2 block text-xs text-muted-foreground">{t.tagline}</span>
                </span>
              </button>
            );
          })}
        </div>
      </aside>

      <section aria-live="polite" className="min-w-0 space-y-4">
        <header className="flex flex-wrap items-start gap-3">
          <span className="flex h-11 w-11 shrink-0 items-center justify-center rounded-xl bg-primary/10 text-primary">
            <type.icon className="h-5 w-5" aria-hidden />
          </span>
          <div className="min-w-0 flex-1">
            <div className="flex flex-wrap items-center gap-2">
              <h2 className="text-2xl font-bold tracking-tight text-foreground">{type.name}</h2>
              {type.available ? (
                <span className="inline-flex items-center gap-1 rounded-full bg-pass/10 px-2 py-0.5 text-xs font-semibold text-pass">
                  <CheckCircle2 className="h-3.5 w-3.5" aria-hidden /> Available
                </span>
              ) : (
                <span className="inline-flex items-center gap-1 rounded-full bg-muted px-2 py-0.5 text-xs font-semibold text-muted-foreground">
                  <Clock className="h-3.5 w-3.5" aria-hidden /> Coming in {type.roadmap}
                </span>
              )}
            </div>
            <p className="text-sm text-muted-foreground">{type.tagline}</p>
          </div>
        </header>
        {type.available ? (
          <SetupForm key={type.id} embedded focus={isFocus(type.id) ? type.id : "simulation"} />
        ) : (
          <ComingSoon type={type} />
        )}
      </section>
    </div>
  );
}
