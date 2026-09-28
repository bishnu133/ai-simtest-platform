"use client";

import Link from "next/link";
import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { BookmarkPlus, Check, Loader2, Plus, Trash2, Users } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { Sheet, SheetContent, SheetDescription, SheetTitle } from "@/components/ui/sheet";
import { toast } from "@/lib/toast";
import { engine, EngineError } from "@/lib/engine/client";
import type { PersonaInput, PersonaKind, SavedPersona, SeenPersona } from "@/lib/engine/types";
import { Segmented } from "@/components/setup/controls";

export const KIND: Record<PersonaKind, { label: string; avatar: string; chip: string }> = {
  standard: { label: "Standard", avatar: "bg-primary/10 text-primary", chip: "bg-primary/10 text-primary" },
  edge_case: { label: "Edge case", avatar: "bg-warn/15 text-warn", chip: "bg-warn/10 text-warn" },
  adversarial: { label: "Adversarial", avatar: "bg-fail/10 text-fail", chip: "bg-fail/10 text-fail" },
};

function kindOf(t: string | undefined): PersonaKind {
  return t === "edge_case" || t === "adversarial" ? t : "standard";
}

export function personaInitials(name: string) {
  const parts = name.split(/\s+/).filter(Boolean);
  return ((parts[0]?.[0] ?? "?") + (parts[1]?.[0] ?? "")).toUpperCase();
}

function toInput(p: PersonaInput): PersonaInput {
  return {
    name: p.name,
    role: p.role || "customer",
    goals: p.goals?.length ? p.goals : ["Get help"],
    tone: p.tone || "neutral",
    persona_type: kindOf(p.persona_type),
    technical_level: p.technical_level,
    special_characteristics: p.special_characteristics ?? [],
    adversarial_tactics: p.adversarial_tactics ?? null,
    topics: p.topics ?? [],
    system_prompt: p.system_prompt ?? "",
  };
}

function PersonaCard({ p, children, footer }: { p: PersonaInput; children?: React.ReactNode; footer: React.ReactNode }) {
  const kind = KIND[kindOf(p.persona_type)];
  return (
    <li className="flex flex-col gap-3 rounded-xl border bg-card p-5 shadow-xs transition-shadow hover:shadow-sm">
      <div className="flex items-start gap-3">
        <span className={`flex h-11 w-11 shrink-0 items-center justify-center rounded-full text-sm font-bold ${kind.avatar}`} aria-hidden>
          {personaInitials(p.name)}
        </span>
        <div className="min-w-0 flex-1">
          <h3 className="truncate font-semibold text-foreground">{p.name}</h3>
          <p className="truncate text-xs text-muted-foreground">{p.role}</p>
          <div className="mt-1.5 flex flex-wrap gap-1.5">
            <span className={`rounded-full px-2 py-0.5 text-[11px] font-medium ${kind.chip}`}>{kind.label}</span>
            {p.tone && p.tone !== "neutral" && (
              <span className="rounded-full bg-muted px-2 py-0.5 text-[11px] font-medium text-muted-foreground">{p.tone}</span>
            )}
          </div>
        </div>
      </div>
      <p className="line-clamp-2 text-sm text-muted-foreground" title={p.goals?.join(" · ")}>
        <span className="font-medium text-foreground">Wants to: </span>
        {p.goals?.[0] ?? "—"}
      </p>
      {children}
      <div className="mt-auto flex flex-wrap items-center gap-2 border-t pt-3">{footer}</div>
    </li>
  );
}

function Outcomes({ s }: { s: SeenPersona }) {
  const total = s.pass + s.warning + s.fail;
  if (!total) return <p className="text-xs text-muted-foreground">In {s.runs} run{s.runs === 1 ? "" : "s"}, not judged yet</p>;
  return (
    <div className="space-y-1">
      <div className="flex h-1.5 overflow-hidden rounded-full bg-muted" aria-hidden>
        <span className="bg-fail" style={{ width: `${(s.fail / total) * 100}%` }} />
        <span className="bg-warn" style={{ width: `${(s.warning / total) * 100}%` }} />
        <span className="bg-pass" style={{ width: `${(s.pass / total) * 100}%` }} />
      </div>
      <p className="text-xs text-muted-foreground">
        {s.fail > 0 && <span className="font-medium text-fail">{s.fail} failed · </span>}
        {s.warning > 0 && <span className="font-medium text-warn">{s.warning} warning · </span>}
        {s.pass} passed, in {s.runs} run{s.runs === 1 ? "" : "s"}
      </p>
    </div>
  );
}

export function PersonasPage() {
  const queryClient = useQueryClient();
  const { data, isPending, isError, error } = useQuery({ queryKey: ["personas"], queryFn: engine.listPersonas });
  // null until a tab is picked: then the page chooses (see below)
  const [view, setView] = useState<"library" | "seen" | null>(null);
  const [creating, setCreating] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);
  const [message, setMessage] = useState("");
  const library = data?.library ?? [];
  const seen = data?.seen ?? [];
  const savedNames = new Set(library.map((p) => p.name.toLowerCase()));
  // First visit: nothing saved yet, but runs have met customers worth keeping
  const shown = view ?? (library.length === 0 && seen.length > 0 ? "seen" : "library");

  const saveSeen = async (s: SeenPersona) => {
    setView("seen"); // stay here while saving several
    setBusy(s.name);
    setMessage("");
    try {
      await engine.savePersona({ ...toInput(s), from_run: s.last_run_id });
      await queryClient.invalidateQueries({ queryKey: ["personas"] });
      toast(`${s.name} saved to your personas`);
    } catch (e) {
      setMessage(e instanceof EngineError ? e.message : "Could not save the persona.");
    } finally {
      setBusy(null);
    }
  };
  const remove = async (p: SavedPersona) => {
    setBusy(p.id);
    try {
      await engine.deletePersona(p.id);
      await queryClient.invalidateQueries({ queryKey: ["personas"] });
      toast(`Deleted ${p.name}`, "info");
    } finally {
      setBusy(null);
    }
  };

  return (
    <div className="mx-auto w-full max-w-6xl space-y-6 p-4 md:p-8">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h2 className="text-2xl font-bold tracking-tight text-foreground md:text-3xl">Personas</h2>
          <p className="text-sm text-muted-foreground">The customers who talk to your bot. Keep the ones that matter and add them to any test.</p>
        </div>
        <Button onClick={() => setCreating(true)}>
          <Plus /> New persona
        </Button>
      </div>

      <Segmented
        label="Which personas"
        value={shown}
        onChange={setView}
        options={[
          { id: "library", label: `Your personas${library.length ? ` · ${library.length}` : ""}` },
          { id: "seen", label: `Seen in your tests${seen.length ? ` · ${seen.length}` : ""}` },
        ]}
      />

      {isError && (
        <p role="alert" className="text-sm font-medium text-destructive">
          {error instanceof Error ? error.message : "Could not load personas."}
        </p>
      )}
      {message && (
        <p role="alert" className="text-sm font-medium text-destructive">
          {message}
        </p>
      )}

      {isPending ? (
        <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
          {[0, 1, 2].map((i) => (
            <div key={i} className="h-48 animate-pulse rounded-xl border bg-muted/40" />
          ))}
        </div>
      ) : shown === "library" ? (
        library.length === 0 ? (
          <Empty
            title="No saved personas yet"
            body="Save the customers your tests met from “Seen in your tests”, or write your own. Saved personas can join any test."
            action={
              <Button onClick={() => setCreating(true)}>
                <Plus /> New persona
              </Button>
            }
          />
        ) : (
          <ul className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
            {library.map((p) => (
              <PersonaCard
                key={p.id}
                p={p}
                footer={
                  <>
                    <Button asChild size="sm">
                      <Link href={`/new?persona=${encodeURIComponent(p.id)}`}>Use in a test</Link>
                    </Button>
                    <Button
                      size="icon"
                      variant="ghost"
                      className="ml-auto"
                      aria-label={`Delete ${p.name}`}
                      disabled={busy === p.id}
                      onClick={() => remove(p)}
                    >
                      <Trash2 />
                    </Button>
                  </>
                }
              />
            ))}
          </ul>
        )
      ) : seen.length === 0 ? (
        <Empty title="No tests yet" body="Personas appear here once a test has run: the hardest customers first." />
      ) : (
        <>
          <p className="text-sm text-muted-foreground">Hardest first: the customers who most often got a failing reply.</p>
          <ul className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
            {seen.map((s) => {
              const saved = savedNames.has(s.name.toLowerCase());
              return (
                <PersonaCard
                  key={`${s.name}-${s.persona_type}`}
                  p={s}
                  footer={
                    <>
                      <Button size="sm" variant={saved ? "ghost" : "outline"} disabled={saved || busy === s.name} onClick={() => saveSeen(s)}>
                        {saved ? <Check /> : busy === s.name ? <Loader2 className="animate-spin" /> : <BookmarkPlus />}
                        {saved ? "Saved" : "Save to my personas"}
                      </Button>
                      {s.last_run_id && (
                        <Link href={`/simulations/${s.last_run_id}`} className="ml-auto text-xs font-medium text-primary hover:underline">
                          Latest run
                        </Link>
                      )}
                    </>
                  }
                >
                  <Outcomes s={s} />
                </PersonaCard>
              );
            })}
          </ul>
        </>
      )}

      <Sheet open={creating} onOpenChange={setCreating}>
        <SheetContent side="right" className="flex w-full flex-col gap-0 overflow-y-auto p-0 sm:max-w-lg">
          {creating && (
            <PersonaForm
              onDone={() => {
                setCreating(false);
                setView("library");
              }}
            />
          )}
        </SheetContent>
      </Sheet>
    </div>
  );
}

function Empty({ title, body, action }: { title: string; body: string; action?: React.ReactNode }) {
  return (
    <div className="flex flex-col items-center gap-3 rounded-xl border border-dashed bg-card px-6 py-14 text-center">
      <span className="flex h-14 w-14 items-center justify-center rounded-2xl bg-primary/10 text-primary">
        <Users className="h-7 w-7" aria-hidden />
      </span>
      <h3 className="text-lg font-semibold text-foreground">{title}</h3>
      <p className="max-w-md text-sm text-muted-foreground">{body}</p>
      {action}
    </div>
  );
}

function PersonaForm({ onDone }: { onDone: () => void }) {
  const queryClient = useQueryClient();
  const [name, setName] = useState("");
  const [role, setRole] = useState("");
  const [goals, setGoals] = useState("");
  const [tone, setTone] = useState("");
  const [kind, setKind] = useState<PersonaKind>("standard");
  const [traits, setTraits] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const lines = (t: string) =>
    t
      .split("\n")
      .map((l) => l.trim())
      .filter(Boolean);

  const save = async () => {
    setSaving(true);
    setError("");
    try {
      await engine.savePersona({
        name: name.trim(),
        role: role.trim() || "customer",
        goals: lines(goals),
        tone: tone.trim() || "neutral",
        persona_type: kind,
        special_characteristics: kind === "adversarial" ? [] : lines(traits),
        adversarial_tactics: kind === "adversarial" ? lines(traits) : null,
      });
      await queryClient.invalidateQueries({ queryKey: ["personas"] });
      toast(`${name.trim()} saved`);
      onDone();
    } catch (e) {
      setError(e instanceof EngineError ? e.message : "Could not save the persona.");
      setSaving(false);
    }
  };

  return (
    <>
      <div className="border-b p-6">
        <SheetTitle className="text-lg font-semibold text-foreground">New persona</SheetTitle>
        <SheetDescription className="text-sm text-muted-foreground">A customer your bot must handle well. The AI plays them in every test you add them to.</SheetDescription>
      </div>
      <div className="flex-1 space-y-5 p-6">
        <div className="grid gap-4 sm:grid-cols-2">
          <div className="space-y-1.5">
            <Label htmlFor="p-name" className="text-sm font-semibold">
              Name
            </Label>
            <Input id="p-name" className="h-10" placeholder="Maya Chen" value={name} onChange={(e) => setName(e.target.value)} />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="p-role" className="text-sm font-semibold">
              Who they are
            </Label>
            <Input id="p-role" className="h-10" placeholder="Small business owner" value={role} onChange={(e) => setRole(e.target.value)} />
          </div>
        </div>
        <div className="space-y-2">
          <p className="text-sm font-semibold text-foreground">Type</p>
          <Segmented
            label="Persona type"
            value={kind}
            onChange={setKind}
            options={[
              { id: "standard", label: "Standard" },
              { id: "edge_case", label: "Edge case" },
              { id: "adversarial", label: "Adversarial" },
            ]}
          />
        </div>
        <div className="space-y-1.5">
          <Label htmlFor="p-goals" className="text-sm font-semibold">
            What they want <span className="font-normal text-muted-foreground">(one per line)</span>
          </Label>
          <Textarea
            id="p-goals"
            rows={3}
            placeholder={"Dispute a duplicate card charge\nGet the refund timeline in writing"}
            value={goals}
            onChange={(e) => setGoals(e.target.value)}
          />
        </div>
        <div className="space-y-1.5">
          <Label htmlFor="p-tone" className="text-sm font-semibold">
            Tone
          </Label>
          <Input id="p-tone" className="h-10" placeholder="Impatient, short messages" value={tone} onChange={(e) => setTone(e.target.value)} />
        </div>
        <div className="space-y-1.5">
          <Label htmlFor="p-traits" className="text-sm font-semibold">
            {kind === "adversarial" ? "Tactics" : "Traits"} <span className="font-normal text-muted-foreground">(optional, one per line)</span>
          </Label>
          <Textarea
            id="p-traits"
            rows={3}
            placeholder={kind === "adversarial" ? "Claims to be a bank employee\nAsks for another customer's balance" : "Not a native English speaker\nSwitches topic mid-way"}
            value={traits}
            onChange={(e) => setTraits(e.target.value)}
          />
        </div>
        {error && (
          <p role="alert" className="text-sm font-medium text-destructive">
            {error}
          </p>
        )}
      </div>
      <div className="flex items-center justify-end gap-2 border-t p-4">
        <Button variant="ghost" onClick={onDone}>
          Cancel
        </Button>
        <Button onClick={save} disabled={saving || !name.trim() || lines(goals).length === 0}>
          {saving && <Loader2 className="animate-spin" />} Save persona
        </Button>
      </div>
    </>
  );
}
