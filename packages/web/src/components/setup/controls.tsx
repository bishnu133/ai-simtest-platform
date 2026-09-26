"use client";

import type { ReactNode } from "react";
import { CheckCircle2 } from "lucide-react";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";

/** Large selectable cards, one choice (radio semantics). */
export function ChoiceCards<T extends string>({
  label,
  value,
  onChange,
  options,
  columns = 3,
}: {
  label: string;
  value: T;
  onChange: (v: T) => void;
  options: { id: T; title: string; body?: string; tag?: string; icon?: ReactNode }[];
  columns?: 2 | 3 | 4;
}) {
  const grid = { 2: "sm:grid-cols-2", 3: "md:grid-cols-3", 4: "sm:grid-cols-2 lg:grid-cols-4" }[columns];
  return (
    <div role="radiogroup" aria-label={label} className={`grid gap-3 ${grid}`}>
      {options.map((o) => {
        const active = o.id === value;
        return (
          <button
            key={o.id}
            type="button"
            role="radio"
            aria-checked={active}
            onClick={() => onChange(o.id)}
            className={`relative flex h-full flex-col gap-1.5 rounded-xl border p-4 text-left transition-colors ${
              active ? "border-primary bg-primary/[0.06] ring-1 ring-primary/30" : "bg-card hover:border-primary/40 hover:bg-muted/40"
            }`}
          >
            <span className="flex items-center gap-2">
              {o.icon && (
                <span className={`flex h-8 w-8 items-center justify-center rounded-lg ${active ? "bg-primary text-primary-foreground" : "bg-primary/10 text-primary"}`}>
                  {o.icon}
                </span>
              )}
              <span className="flex-1 font-semibold text-foreground">{o.title}</span>
              {active && <CheckCircle2 className="h-4 w-4 text-primary" aria-hidden />}
            </span>
            {o.tag && <span className="text-[11px] font-semibold uppercase tracking-wide text-primary">{o.tag}</span>}
            {o.body && <span className="text-sm text-muted-foreground">{o.body}</span>}
          </button>
        );
      })}
    </div>
  );
}

/** A compact segmented control (radio semantics). */
export function Segmented<T extends string>({
  label,
  value,
  onChange,
  options,
}: {
  label: string;
  value: T;
  onChange: (v: T) => void;
  options: { id: T; label: string }[];
}) {
  return (
    <div role="radiogroup" aria-label={label} className="inline-flex flex-wrap gap-1 rounded-lg bg-muted p-1">
      {options.map((o) => {
        const active = o.id === value;
        return (
          <button
            key={o.id}
            type="button"
            role="radio"
            aria-checked={active}
            onClick={() => onChange(o.id)}
            className={`rounded-md px-3 py-1.5 text-sm font-medium transition-colors ${
              active ? "bg-card text-foreground shadow-xs" : "text-muted-foreground hover:text-foreground"
            }`}
          >
            {o.label}
          </button>
        );
      })}
    </div>
  );
}

/** One item per line, returned as a list. */
export function ListEditor({
  id,
  label,
  hint,
  placeholder,
  value,
  onChange,
}: {
  id: string;
  label: string;
  hint: string;
  placeholder: string;
  value: string;
  onChange: (v: string) => void;
}) {
  const count = value.split("\n").filter((l) => l.trim()).length;
  return (
    <div className="space-y-2">
      <div className="flex items-baseline justify-between gap-2">
        <Label htmlFor={id} className="text-sm font-semibold">
          {label}
        </Label>
        <span className="text-xs tabular-nums text-muted-foreground">{count} item{count === 1 ? "" : "s"}</span>
      </div>
      <Textarea id={id} rows={4} placeholder={placeholder} value={value} onChange={(e) => onChange(e.target.value)} />
      <p className="text-xs text-muted-foreground">{hint}</p>
    </div>
  );
}

export const lines = (text: string) => text.split("\n").map((l) => l.trim()).filter(Boolean);

/** A titled block inside the setup card. */
export function Section({ n, title, description, children }: { n: number; title: string; description?: string; children: ReactNode }) {
  return (
    <section className="space-y-4 border-t px-6 py-6 first:border-t-0 md:px-8" aria-labelledby={`setup-${n}`}>
      <div className="flex gap-3">
        <span className="mt-0.5 flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-primary/10 text-xs font-semibold text-primary">
          {n}
        </span>
        <div>
          <h3 id={`setup-${n}`} className="text-base font-semibold text-foreground">
            {title}
          </h3>
          {description && <p className="text-sm text-muted-foreground">{description}</p>}
        </div>
      </div>
      <div className="md:pl-9">{children}</div>
    </section>
  );
}
