"use client";

import { Checkbox } from "@/components/ui/checkbox";
import type { EngineOptions, ScenarioTemplate, StressOptions, StressPatternId } from "@/lib/engine/types";
import { Segmented } from "./controls";

const CATEGORY_LABELS: Record<string, string> = {
  robustness: "Robustness",
  safety: "Safety",
  quality: "Quality",
  memory: "Memory",
  empathy: "Empathy",
};

export const categoryLabel = (c: string) => CATEGORY_LABELS[c] ?? (c ? c[0].toUpperCase() + c.slice(1) : "Other");

const DIFFICULTY_STYLE: Record<string, string> = {
  easy: "bg-pass/10 text-pass",
  medium: "bg-warn/10 text-warn",
  hard: "bg-fail/10 text-fail",
};

/** Scenario templates as checkbox cards, grouped by what they test. */
export function ScenarioPicker({
  scenarios,
  selected,
  onChange,
  customers,
}: {
  scenarios: ScenarioTemplate[] | undefined;
  selected: string[];
  onChange: (ids: string[]) => void;
  /** Simulated customers in the run; each runs one scenario */
  customers: number;
}) {
  if (!scenarios?.length) {
    return (
      <p className="rounded-lg border bg-muted/30 p-4 text-sm text-muted-foreground">
        Couldn&apos;t load scenarios from the engine. Update the engine to the latest version and reload this page.
      </p>
    );
  }
  const groups = new Map<string, ScenarioTemplate[]>();
  for (const s of scenarios) groups.set(s.category, [...(groups.get(s.category) ?? []), s]);
  const all = selected.length === scenarios.length;
  const toggle = (id: string, on: boolean) =>
    onChange(on ? [...selected, id] : selected.filter((x) => x !== id));

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="text-sm text-muted-foreground">
          <span className="font-semibold tabular-nums text-foreground">{selected.length}</span> of {scenarios.length} selected.
          Simulated customers take turns running them, so each one is tried on every run.
        </p>
        <button
          type="button"
          className="text-sm font-medium text-primary underline-offset-4 hover:underline"
          onClick={() => onChange(all ? [] : scenarios.map((s) => s.id))}
        >
          {all ? "Clear all" : "Select all"}
        </button>
      </div>
      {customers < selected.length && (
        <p role="note" className="rounded-md border border-warn/30 bg-warn/5 px-3 py-2 text-sm text-foreground">
          Each simulated customer runs one scenario, and this run has {customers}, so only {customers} of your{" "}
          {selected.length} scenarios would run. Choose a bigger test size below, or fewer scenarios.
        </p>
      )}
      {[...groups].map(([category, items]) => (
        <fieldset key={category} className="space-y-2">
          <legend className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">{categoryLabel(category)}</legend>
          <div className="grid gap-2 sm:grid-cols-2">
            {items.map((s) => {
              const on = selected.includes(s.id);
              return (
                <label
                  key={s.id}
                  className={`flex cursor-pointer gap-3 rounded-lg border p-3 transition-colors ${
                    on ? "border-primary/60 bg-primary/[0.04]" : "hover:bg-muted/40"
                  }`}
                >
                  <Checkbox checked={on} onCheckedChange={(v) => toggle(s.id, v === true)} className="mt-0.5" aria-label={s.name} />
                  <span className="min-w-0 flex-1">
                    <span className="flex items-center justify-between gap-2">
                      <span className="text-sm font-medium text-foreground">{s.name}</span>
                      <span
                        className={`rounded px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wide ${DIFFICULTY_STYLE[s.difficulty] ?? "bg-muted text-muted-foreground"}`}
                      >
                        {s.difficulty}
                      </span>
                    </span>
                    <span className="mt-0.5 block text-xs text-muted-foreground">{s.description}</span>
                  </span>
                </label>
              );
            })}
          </div>
        </fieldset>
      ))}
    </div>
  );
}

const TURN_CHOICES = ["20", "30", "45", "60"] as const;
const FACT_CHOICES = ["3", "5", "8", "10"] as const;
const CONTRADICTION_CHOICES = ["0", "2", "3", "5"] as const;

const FALLBACK_PATTERNS: NonNullable<EngineOptions["stress_patterns"]> = [
  { id: "fact_seeding", name: "Remember details", description: "The customer shares details early and asks for them later." },
  { id: "contradiction", name: "Notice contradictions", description: "The customer says one thing, then the opposite." },
  { id: "progressive_complexity", name: "Growing complexity", description: "Requests get harder as the conversation goes on." },
];

/** How long and how hard the memory stress conversations are. */
export function StressSettings({
  patterns: available,
  value,
  onChange,
}: {
  patterns: EngineOptions["stress_patterns"];
  value: StressOptions;
  onChange: (v: StressOptions) => void;
}) {
  const patterns = available?.length ? available : FALLBACK_PATTERNS;
  const has = (id: StressPatternId) => value.patterns.includes(id);
  const toggle = (id: StressPatternId, on: boolean) =>
    onChange({ ...value, patterns: on ? [...value.patterns, id] : value.patterns.filter((p) => p !== id) });

  return (
    <div className="space-y-5">
      <fieldset className="space-y-2">
        <legend className="text-sm font-semibold text-foreground">Conversation length</legend>
        <Segmented
          label="Conversation length"
          value={String(value.turns)}
          onChange={(v) => onChange({ ...value, turns: Number(v) })}
          options={TURN_CHOICES.map((t) => ({ id: t, label: `${t} messages` }))}
        />
        <p className="text-xs text-muted-foreground">
          Every conversation runs this long. Details are shared in the first sixth and asked for again from the halfway point.
        </p>
      </fieldset>

      <fieldset className="space-y-2">
        <legend className="text-sm font-semibold text-foreground">What to test</legend>
        <div className="grid gap-2 md:grid-cols-3">
          {patterns.map((p) => (
            <label key={p.id} className="flex cursor-pointer gap-3 rounded-lg border p-3 hover:bg-muted/40">
              <Checkbox checked={has(p.id)} onCheckedChange={(on) => toggle(p.id, on === true)} className="mt-0.5" aria-label={p.name} />
              <span>
                <span className="block text-sm font-medium text-foreground">{p.name}</span>
                <span className="block text-xs text-muted-foreground">{p.description}</span>
              </span>
            </label>
          ))}
        </div>
      </fieldset>

      <div className="grid gap-5 sm:grid-cols-2">
        {has("fact_seeding") && (
          <fieldset className="space-y-2">
            <legend className="text-sm font-semibold text-foreground">Details to remember</legend>
            <Segmented
              label="Details to remember"
              value={String(value.facts)}
              onChange={(v) => onChange({ ...value, facts: Number(v) })}
              options={FACT_CHOICES.map((f) => ({ id: f, label: f }))}
            />
            <p className="text-xs text-muted-foreground">The customer&apos;s own name and email, plus details such as a reference number, a city and the best time to call.</p>
          </fieldset>
        )}
        {has("contradiction") && (
          <fieldset className="space-y-2">
            <legend className="text-sm font-semibold text-foreground">Contradictions</legend>
            <Segmented
              label="Contradictions"
              value={String(value.contradictions)}
              onChange={(v) => onChange({ ...value, contradictions: Number(v) })}
              options={CONTRADICTION_CHOICES.map((c) => ({ id: c, label: c }))}
            />
            <p className="text-xs text-muted-foreground">Times the customer changes their story mid-conversation.</p>
          </fieldset>
        )}
      </div>
    </div>
  );
}

export const DEFAULT_STRESS: StressOptions = {
  turns: 30,
  patterns: ["fact_seeding", "contradiction", "progressive_complexity"],
  facts: 5,
  contradictions: 3,
};
