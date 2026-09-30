"use client";

import Link from "next/link";
import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { CheckCircle2, Copy, FileCode2, Loader2, Pencil, Plus, ScrollText, ShieldCheck, Trash2, TriangleAlert, Workflow, XCircle } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { Sheet, SheetContent, SheetDescription, SheetTitle } from "@/components/ui/sheet";
import { LoadingBlocks } from "@/components/ui/skeleton";
import { Segmented } from "@/components/setup/controls";
import { engine, EngineError } from "@/lib/engine/client";
import { judgeLabel } from "@/lib/engine/report";
import type { LibraryCheck, LibraryItem, LibraryKind } from "@/lib/engine/types";
import { toast } from "@/lib/toast";

type Rec = Record<string, unknown>;
const str = (v: unknown) => (typeof v === "string" ? v : v == null ? "" : String(v));
const num = (v: unknown) => (typeof v === "number" ? v : Number(v ?? 0));
const list = (v: unknown): Rec[] => (Array.isArray(v) ? (v.filter((x) => x && typeof x === "object") as Rec[]) : []);
const words = (v: unknown): string[] => (Array.isArray(v) ? v.map(str).filter(Boolean) : []);
const pctOf = (v: unknown) => `${Math.round(num(v) * 100)}%`;

const SEVERITY: Record<string, string> = {
  critical: "bg-fail/10 text-fail",
  high: "bg-warn/10 text-warn",
  medium: "bg-primary/10 text-primary",
  low: "bg-muted text-muted-foreground",
  info: "bg-muted text-muted-foreground",
};

function severityLabel(v: unknown) {
  const s = str(v).toLowerCase() || "high";
  return s[0].toUpperCase() + s.slice(1);
}

function who(judge: string) {
  return judge === "overall" ? "all checks" : judgeLabel(judge).toLowerCase();
}

/** A policy rule's condition in a sentence a tester can check against a report. */
export function ruleInWords(rule: Rec): string {
  const j = who(str(rule.judge) || "overall");
  const t = rule.threshold;
  const match = (rule.match ?? {}) as Rec;
  const phrases = [...words(match.patterns), ...words(match.phrases), ...words(match.values)];
  const quoted = phrases.length ? `: ${phrases.slice(0, 4).map((p) => `“${p}”`).join(", ")}${phrases.length > 4 ? "…" : ""}` : "";
  const tags = words(match.issue_tags).map((t) => t.replace(/_/g, " "));
  const about = tags.length ? ` about ${tags.slice(0, 4).join(", ")}${tags.length > 4 ? "…" : ""}` : quoted;
  // "At most 0 …" reads as "No …"
  const upTo = (n: number, what: string) => (n === 0 ? `No ${what}` : `At most ${n} ${what}`);
  switch (str(rule.condition)) {
    case "min_score":
      return `Average ${j} score at least ${pctOf(t)}`;
    case "min_pass_rate":
      return `At least ${pctOf(t)} of replies pass ${j}`;
    case "max_failure_rate":
      return `At most ${pctOf(t)} of replies fail ${j}`;
    case "zero_critical":
      return `No critical ${j} failures`;
    case "max_critical_count":
      return upTo(num(t), `critical ${j} failures`);
    case "max_warnings":
      return upTo(num(t), `${j} warnings`);
    case "max_failed_conversations":
      return upTo(num(t), `conversations failing ${j}`);
    case "max_failed_turns":
      return upTo(num(t), `replies failing ${j}`);
    case "max_matching_failures":
      return `${upTo(num(t), `${j} failures`)}${about}`;
    case "must_contain":
      return `Replies must include${quoted || " the listed text"}`;
    case "must_not_contain":
      return `Replies must never include${quoted || " the listed text"}`;
    case "requires_disclaimer":
      return `Replies must carry a disclaimer${quoted}`;
    case "requires_citation":
      return "Replies must cite their source";
    case "requires_escalation":
      return "The bot must hand over to a person when needed";
    case "requires_refusal":
      return "The bot must refuse requests it shouldn't serve";
    case "required_workflow_pass_rate":
      return `At least ${pctOf(t)} of workflows completed correctly`;
    case "required_rag_metric":
      return `Retrieval quality at least ${pctOf(t)}`;
    case "required_tool_metric":
      return `Tool use quality at least ${pctOf(t)}`;
    case "max_score_stddev":
      return `${j} scores stay consistent (spread at most ${num(t)})`;
    case "max_regression_delta":
      return `${j} drops at most ${pctOf(t)} from the previous run`;
    default:
      return `${str(rule.condition).replace(/_/g, " ")}${t != null ? ` ${num(t)}` : ""} (${j})`;
  }
}

const HARD_RULE: Record<string, string> = {
  forbidden_phrase: "Must never say",
  required_phrase: "Must say",
  forbidden_topic: "Must not discuss",
  required_topic: "Must cover",
  max_turns: "Resolve within",
  must_escalate: "Must hand over to a person",
  must_not_escalate: "Must not hand over to a person",
};

function PolicyDetail({ d }: { d: Rec }) {
  const groups = [
    ...(list(d.rules).length ? [{ name: "", rules: list(d.rules) }] : []),
    ...list(d.controls).map((c) => ({ name: str(c.name), rules: list(c.rules) })),
  ];
  return (
    <div className="space-y-5">
      {groups.map((g, gi) => (
        <section key={gi} className="space-y-2">
          {g.name && <h4 className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">{g.name}</h4>}
          <ul className="space-y-2">
            {g.rules.map((r, i) => (
              <li key={str(r.id) || i} className="rounded-lg border bg-card p-3">
                <div className="flex flex-wrap items-center gap-2">
                  <span className="font-medium text-foreground">{str(r.name) || str(r.id)}</span>
                  <span className={`rounded-full px-2 py-0.5 text-[11px] font-medium ${SEVERITY[str(r.severity).toLowerCase()] ?? SEVERITY.info}`}>
                    {severityLabel(r.severity)}
                  </span>
                </div>
                <p className="mt-1 text-sm text-foreground/90">{ruleInWords(r)}</p>
                {str(r.remediation) && <p className="mt-1 text-xs text-muted-foreground">If it fails: {str(r.remediation)}</p>}
              </li>
            ))}
          </ul>
        </section>
      ))}
    </div>
  );
}

function WorkflowDetail({ d }: { d: Rec }) {
  const steps = [...list(d.steps)].sort((a, b) => num(a.order ?? 99) - num(b.order ?? 99));
  const rules = list(d.hard_rules);
  const success = list(d.success_conditions);
  return (
    <div className="space-y-5">
      {words(d.activation_hints).length > 0 && (
        <p className="text-sm text-muted-foreground">
          Checked in conversations about: {words(d.activation_hints).map((h) => `“${h}”`).join(", ")}
        </p>
      )}
      <section className="space-y-2">
        <h4 className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">Steps the bot should take</h4>
        <ol className="space-y-2">
          {steps.map((s, i) => (
            <li key={str(s.id) || i} className="flex gap-3 rounded-lg border bg-card p-3">
              <span className="flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-primary/10 text-xs font-semibold text-primary">
                {i + 1}
              </span>
              <span className="min-w-0">
                <span className="font-medium text-foreground">{str(s.name)}</span>
                {s.required === false && <span className="ml-2 text-xs text-muted-foreground">optional</span>}
                {str(s.description) && <span className="block text-sm text-muted-foreground">{str(s.description)}</span>}
              </span>
            </li>
          ))}
        </ol>
      </section>
      {rules.length > 0 && (
        <section className="space-y-2">
          <h4 className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">Rules</h4>
          <ul className="space-y-2">
            {rules.map((r, i) => {
              const vals = [...words(r.values), ...(str(r.value) ? [str(r.value)] : [])];
              return (
                <li key={str(r.id) || i} className="rounded-lg border bg-card p-3 text-sm">
                  <span className="font-medium text-foreground">{str(r.name)}</span>
                  <span className={`ml-2 rounded-full px-2 py-0.5 text-[11px] font-medium ${SEVERITY[str(r.severity).toLowerCase()] ?? SEVERITY.high}`}>
                    {severityLabel(r.severity)}
                  </span>
                  <span className="block text-muted-foreground">
                    {HARD_RULE[str(r.rule_type)] ?? str(r.rule_type)}
                    {vals.length > 0 &&
                      (str(r.rule_type) === "max_turns" ? ` ${vals[0]} turns` : `: ${vals.slice(0, 5).map((v) => `“${v}”`).join(", ")}`)}
                  </span>
                </li>
              );
            })}
          </ul>
        </section>
      )}
      {success.length > 0 && (
        <section className="space-y-1">
          <h4 className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">Done when</h4>
          <ul className="list-disc space-y-0.5 pl-5 text-sm text-foreground/90">
            {success.map((c, i) => (
              <li key={i}>{str(c.description) || str(c.name) || str(c.id)}</li>
            ))}
          </ul>
        </section>
      )}
    </div>
  );
}

export function PoliciesPage() {
  const [kind, setKind] = useState<LibraryKind>("policies");
  const { data, isPending, isError, error } = useQuery({ queryKey: ["library", kind], queryFn: () => engine.listLibrary(kind) });
  const [open, setOpen] = useState<LibraryItem | null>(null);
  const [editing, setEditing] = useState<{ yaml: string; replaceId?: string; title: string } | null>(null);
  const items = data?.items ?? [];
  const noun = kind === "policies" ? "policy" : "workflow";

  const copyOf = (item: LibraryItem) => ({
    // A copy gets its own name (and id), so it's saved beside the original
    yaml: item.yaml
      .replace(/^name:\s*(.*)$/m, (_, n: string) => `name: ${n.replace(/^['"]|['"]$/g, "")} (my copy)`)
      .replace(/^id:\s*(.*)$/m, (_, n: string) => `id: ${n}_copy`),
    title: `Copy of ${item.name}`,
  });

  return (
    <div className="mx-auto w-full max-w-6xl space-y-6 p-4 md:p-8">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h2 className="text-2xl font-bold tracking-tight text-foreground md:text-3xl">Policies &amp; workflows</h2>
          <p className="text-sm text-muted-foreground">
            The rules a release must meet, and the step-by-step processes your bot must follow.
          </p>
        </div>
        <Button onClick={() => setEditing({ yaml: data?.starter ?? "", title: `New ${noun}` })} disabled={!data}>
          <Plus /> New {noun}
        </Button>
      </div>

      <Segmented
        label="Show"
        value={kind}
        onChange={(k) => {
          setKind(k);
          setOpen(null);
        }}
        options={[
          { id: "policies", label: "Compliance policies" },
          { id: "workflows", label: "Business workflows" },
        ]}
      />

      {isError && (
        <p role="alert" className="text-sm font-medium text-destructive">
          {error instanceof Error ? error.message : "Could not load them."}
        </p>
      )}
      {isPending ? (
        <LoadingBlocks />
      ) : (
        <ul className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
          {items.map((item) => {
            const Icon = kind === "policies" ? ShieldCheck : Workflow;
            const s = item.summary;
            return (
              <li key={item.id}>
                <button
                  type="button"
                  onClick={() => setOpen(item)}
                  className="flex h-full w-full flex-col gap-3 rounded-xl border bg-card p-5 text-left shadow-xs transition-shadow hover:shadow-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                >
                  <span className="flex items-start gap-3">
                    <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-lg bg-primary/10 text-primary">
                      <Icon className="h-5 w-5" aria-hidden />
                    </span>
                    <span className="min-w-0 flex-1">
                      <span className="block font-semibold text-foreground">{item.name}</span>
                      <span className="mt-1 flex flex-wrap gap-1.5">
                        <span
                          className={`rounded-full px-2 py-0.5 text-[11px] font-medium ${
                            item.source === "custom" ? "bg-primary/10 text-primary" : "bg-muted text-muted-foreground"
                          }`}
                        >
                          {item.source === "custom" ? "Yours" : "Built-in"}
                        </span>
                        {s.area && <span className="rounded-full bg-muted px-2 py-0.5 text-[11px] font-medium capitalize text-muted-foreground">{s.area}</span>}
                      </span>
                    </span>
                  </span>
                  <span className="line-clamp-2 text-sm text-muted-foreground">{item.description}</span>
                  <span className="mt-auto text-xs text-muted-foreground">
                    {kind === "policies"
                      ? `${s.rules ?? 0} rules${s.critical ? ` · ${s.critical} critical` : ""}`
                      : `${s.steps ?? 0} steps${s.hard_rules ? ` · ${s.hard_rules} rules` : ""}`}
                  </span>
                </button>
              </li>
            );
          })}
        </ul>
      )}

      <Sheet open={open !== null} onOpenChange={(o) => !o && setOpen(null)}>
        <SheetContent side="right" className="flex w-full flex-col gap-0 overflow-y-auto p-0 sm:max-w-xl">
          {open && (
            <>
              <div className="space-y-1 border-b p-6">
                <SheetTitle className="text-lg font-semibold text-foreground">{open.name}</SheetTitle>
                <SheetDescription className="text-sm text-muted-foreground">{open.description}</SheetDescription>
              </div>
              <div className="flex-1 p-6">{kind === "policies" ? <PolicyDetail d={open.definition} /> : <WorkflowDetail d={open.definition} />}</div>
              <div className="flex flex-wrap items-center gap-2 border-t p-4">
                <Button asChild>
                  <Link href={`/new?${kind === "policies" ? "policy" : "workflow"}=${encodeURIComponent(open.id)}`}>Use in a test</Link>
                </Button>
                {open.source === "custom" ? (
                  <Button variant="outline" onClick={() => setEditing({ yaml: open.yaml, replaceId: open.id, title: `Edit ${open.name}` })}>
                    <Pencil /> Edit
                  </Button>
                ) : (
                  <Button variant="outline" onClick={() => setEditing(copyOf(open))}>
                    <Copy /> Copy and edit
                  </Button>
                )}
                {open.source === "custom" && <DeleteButton kind={kind} item={open} onDone={() => setOpen(null)} />}
              </div>
            </>
          )}
        </SheetContent>
      </Sheet>

      <Sheet open={editing !== null} onOpenChange={(o) => !o && setEditing(null)}>
        <SheetContent side="right" className="flex w-full flex-col gap-0 overflow-y-auto p-0 sm:max-w-2xl">
          {editing && (
            <Editor
              key={editing.title}
              kind={kind}
              initial={editing.yaml}
              replaceId={editing.replaceId}
              title={editing.title}
              onDone={(saved) => {
                setEditing(null);
                if (saved) setOpen(saved);
              }}
            />
          )}
        </SheetContent>
      </Sheet>
    </div>
  );
}

function DeleteButton({ kind, item, onDone }: { kind: LibraryKind; item: LibraryItem; onDone: () => void }) {
  const queryClient = useQueryClient();
  const [confirming, setConfirming] = useState(false);
  if (!confirming)
    return (
      <Button variant="ghost" className="ml-auto text-destructive" onClick={() => setConfirming(true)}>
        <Trash2 /> Delete
      </Button>
    );
  return (
    <span className="ml-auto flex items-center gap-1">
      <Button
        variant="destructive"
        onClick={async () => {
          await engine.deleteLibraryItem(kind, item.id);
          await queryClient.invalidateQueries({ queryKey: ["library", kind] });
          queryClient.invalidateQueries({ queryKey: ["options"] });
          toast(`Deleted ${item.name}`, "info");
          onDone();
        }}
      >
        Delete
      </Button>
      <Button variant="ghost" onClick={() => setConfirming(false)}>
        Keep
      </Button>
    </span>
  );
}

function Editor({
  kind,
  initial,
  replaceId,
  title,
  onDone,
}: {
  kind: LibraryKind;
  initial: string;
  replaceId?: string;
  title: string;
  onDone: (saved?: LibraryItem) => void;
}) {
  const queryClient = useQueryClient();
  const [yaml, setYaml] = useState(initial);
  const [check, setCheck] = useState<LibraryCheck | null>(null);
  const [busy, setBusy] = useState<"check" | "save" | null>(null);
  const [error, setError] = useState("");

  const runCheck = async () => {
    setBusy("check");
    setError("");
    try {
      setCheck(await engine.checkLibraryItem(kind, yaml));
    } catch (e) {
      setError(e instanceof EngineError ? e.message : "Could not check it.");
    } finally {
      setBusy(null);
    }
  };
  const save = async () => {
    setBusy("save");
    setError("");
    try {
      const saved = await engine.saveLibraryItem(kind, yaml, replaceId);
      await queryClient.invalidateQueries({ queryKey: ["library", kind] });
      queryClient.invalidateQueries({ queryKey: ["options"] });
      toast(`${saved.name} saved. Pick it on New test.`);
      onDone(saved);
    } catch (e) {
      setError(e instanceof EngineError ? e.message : "Could not save it.");
      setBusy(null);
    }
  };

  return (
    <>
      <div className="border-b p-6">
        <SheetTitle className="flex items-center gap-2 text-lg font-semibold text-foreground">
          <FileCode2 className="h-5 w-5 text-primary" aria-hidden /> {title}
        </SheetTitle>
        <SheetDescription className="text-sm text-muted-foreground">
          Written in the same YAML the CLI reads ({kind === "policies" ? "--policy" : "--workflow"} my_file.yaml). Check it, then save.
        </SheetDescription>
      </div>
      <div className="flex-1 space-y-3 p-6">
        <Textarea
          aria-label="Definition (YAML)"
          spellCheck={false}
          className="min-h-[420px] font-mono text-[13px] leading-relaxed"
          value={yaml}
          onChange={(e) => {
            setYaml(e.target.value);
            setCheck(null);
          }}
        />
        {check &&
          (check.ok ? (
            <div role="status" className="space-y-1 rounded-lg border border-pass/30 bg-pass/5 px-3.5 py-2.5 text-sm">
              <p className="flex items-center gap-1.5 font-semibold text-pass">
                <CheckCircle2 className="h-4 w-4" aria-hidden /> Looks good: {check.name}
                {kind === "policies"
                  ? ` · ${check.summary?.rules ?? 0} rules`
                  : ` · ${check.summary?.steps ?? 0} steps${check.summary?.hard_rules ? `, ${check.summary.hard_rules} rules` : ""}`}
              </p>
              {check.warnings.map((w) => (
                <p key={w} className="flex items-start gap-1.5 text-xs text-foreground">
                  <TriangleAlert className="mt-0.5 h-3.5 w-3.5 shrink-0 text-warn" aria-hidden /> {w}
                </p>
              ))}
            </div>
          ) : (
            <p role="alert" className="flex items-start gap-1.5 rounded-lg border border-fail/30 bg-fail/5 px-3.5 py-2.5 text-sm text-fail">
              <XCircle className="mt-0.5 h-4 w-4 shrink-0" aria-hidden /> {check.problem}
            </p>
          ))}
        {error && (
          <p role="alert" className="text-sm font-medium text-destructive">
            {error}
          </p>
        )}
        <details className="rounded-lg border p-3 text-sm">
          <summary className="cursor-pointer font-medium text-foreground">
            <ScrollText className="mr-1 inline h-4 w-4 text-muted-foreground" aria-hidden /> What can go in it?
          </summary>
          {kind === "policies" ? (
            <div className="mt-2 space-y-1 text-muted-foreground">
              <p>
                Each rule names a check (<code>judge</code>: safety, quality, grounding, relevance, overall, workflow, rag, tool), a{" "}
                <code>condition</code> and a <code>severity</code> (critical, high, medium, low).
              </p>
              <p>
                Common conditions: <code>min_score</code>, <code>min_pass_rate</code>, <code>max_failure_rate</code> (with a{" "}
                <code>threshold</code> from 0 to 1), <code>zero_critical</code>, <code>must_not_contain</code>,{" "}
                <code>requires_disclaimer</code>, <code>requires_escalation</code>.
              </p>
            </div>
          ) : (
            <div className="mt-2 space-y-1 text-muted-foreground">
              <p>
                <code>steps</code> are what the bot should do, in <code>order</code>; <code>detection_hints</code> are words that show a
                step happened. <code>activation_hints</code> decide which conversations the workflow applies to.
              </p>
              <p>
                <code>hard_rules</code> types: <code>forbidden_phrase</code>, <code>required_phrase</code>, <code>forbidden_topic</code>,{" "}
                <code>required_topic</code>, <code>max_turns</code>, <code>must_escalate</code>, <code>must_not_escalate</code>.
              </p>
            </div>
          )}
        </details>
      </div>
      <div className="flex items-center justify-end gap-2 border-t p-4">
        <Button variant="ghost" onClick={() => onDone()}>
          Cancel
        </Button>
        <Button variant="outline" onClick={runCheck} disabled={busy !== null || !yaml.trim()}>
          {busy === "check" && <Loader2 className="animate-spin" />} Check
        </Button>
        <Button onClick={save} disabled={busy !== null || !yaml.trim()}>
          {busy === "save" && <Loader2 className="animate-spin" />} Save
        </Button>
      </div>
    </>
  );
}
