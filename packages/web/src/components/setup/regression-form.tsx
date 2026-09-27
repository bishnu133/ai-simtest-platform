"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState, type FormEvent } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowRight, CheckCircle2, ListRestart, Loader2, ShieldCheck } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { engine, EngineError } from "@/lib/engine/client";
import { plural } from "@/lib/engine/report";
import type { RequestFormat, SuiteSummary } from "@/lib/engine/types";
import { Section } from "./controls";
import { SuiteVerdict } from "./suite-verdict";

export function useSuites() {
  return useQuery({ queryKey: ["suites"], queryFn: () => engine.listSuites().then((r) => r.suites) });
}

/** Replay a saved regression suite against a bot. No approval steps: the suite froze them. */
export function RegressionForm({ initialSuite }: { initialSuite?: string }) {
  const router = useRouter();
  const queryClient = useQueryClient();
  const { data: suites, isPending, isError } = useSuites();
  const [picked, setPicked] = useState<string | null>(initialSuite ?? null);
  const suite: SuiteSummary | undefined = suites?.find((s) => s.id === picked) ?? suites?.[0];
  const [endpoint, setEndpoint] = useState<string | null>(null);
  const [apiKey, setApiKey] = useState("");
  const [model, setModel] = useState("");
  const [format, setFormat] = useState<RequestFormat>("openai");
  const [name, setName] = useState("");
  const [error, setError] = useState("");
  const [submitting, setSubmitting] = useState(false);
  // The suite's own bot until the tester types another
  const target = endpoint ?? suite?.source.bot_endpoint ?? "";

  if (isPending) {
    return (
      <div className="flex justify-center rounded-xl border bg-card py-16">
        <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" aria-label="Loading suites" />
      </div>
    );
  }
  if (isError || !suites?.length) {
    return (
      <div className="space-y-3 rounded-xl border bg-card p-6 text-sm shadow-xs">
        <p className="font-semibold text-foreground">{isError ? "Couldn't load suites from the engine." : "No regression suites yet."}</p>
        <p className="text-muted-foreground">
          Open a finished run, choose <span className="font-medium text-foreground">Save as regression suite</span> in its
          header, and its failing conversations become a suite you can replay on every new build.
        </p>
        <Button asChild variant="outline" size="sm">
          <Link href="/runs">Go to runs</Link>
        </Button>
      </div>
    );
  }

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    if (!suite) return;
    if (!/^https?:\/\/\S+$/.test(target.trim())) {
      setError("Enter the bot's endpoint: an http(s) URL.");
      return;
    }
    setError("");
    setSubmitting(true);
    try {
      const sim = await engine.createSimulation({
        name: name.trim() || `${suite.name} — replay`,
        mode: "partial",
        auto_approve: true,
        bot_endpoint: target.trim(),
        bot_api_key: apiKey.trim() || undefined,
        bot_request_format: format,
        bot_response_path: "choices.0.message.content",
        bot_model: model.trim() || null,
        documentation: "",
        documentation_filename: "suite.md",
        num_personas: Math.max(1, Math.min(200, suite.cases)),
        min_turns: 1,
        max_turns: 15,
        max_parallel: 5,
        regression: { suite_id: suite.id },
      });
      queryClient.invalidateQueries({ queryKey: ["runs"] });
      router.push(`/simulations/${sim.simulation_id}`);
    } catch (err) {
      setError(err instanceof EngineError ? err.message : "Could not start the replay.");
      setSubmitting(false);
    }
  };

  return (
    <form onSubmit={submit} noValidate className="max-w-4xl rounded-xl border bg-card shadow-xs">
      <Section n={1} title="Which suite?" description="Saved from earlier runs. Each keeps its failing conversations and how they were judged.">
        <div role="radiogroup" aria-label="Regression suite" className="grid gap-2">
          {suites.map((s) => {
            const active = s.id === suite?.id;
            return (
              <button
                key={s.id}
                type="button"
                role="radio"
                aria-checked={active}
                onClick={() => setPicked(s.id)}
                className={`flex flex-col gap-1 rounded-lg border p-3 text-left transition-colors sm:flex-row sm:items-center sm:justify-between ${
                  active ? "border-primary bg-primary/[0.06] ring-1 ring-primary/30" : "hover:bg-muted/40"
                }`}
              >
                <span className="min-w-0">
                  <span className="flex items-center gap-2 font-medium text-foreground">
                    <ListRestart className="h-4 w-4 shrink-0 text-primary" aria-hidden />
                    <span className="truncate">{s.name}</span>
                    {active && <CheckCircle2 className="h-4 w-4 shrink-0 text-primary" aria-hidden />}
                  </span>
                  <span className="block text-xs text-muted-foreground">
                    {plural(s.cases, "case")}: {s.failing_cases} failing, {s.warning_cases} warnings, {s.guard_cases} guards · from{" "}
                    {s.source.run_name ?? "a run"}
                  </span>
                </span>
                <span className="shrink-0">
                  {s.last_run ? <SuiteVerdict run={s.last_run} /> : <span className="text-xs text-muted-foreground">Not replayed yet</span>}
                </span>
              </button>
            );
          })}
        </div>
        {suite && (
          <p className="mt-3 flex items-start gap-2 text-xs text-muted-foreground">
            <ShieldCheck className="mt-0.5 h-3.5 w-3.5 shrink-0 text-primary" aria-hidden />
            Judged exactly as the original run: its documentation, {plural(suite.criteria, "success criterion", "success criteria")},{" "}
            {plural(suite.guardrails, "guardrail")} and pass marks. No approval steps.
          </p>
        )}
      </Section>

      <Section n={2} title="Which bot?" description="The same customer messages go to this endpoint, in order.">
        <div className="grid gap-4 md:grid-cols-2">
          <div className="space-y-2 md:col-span-2">
            <Label htmlFor="reg-endpoint" className="text-sm font-semibold">
              Bot endpoint
            </Label>
            <Input id="reg-endpoint" type="url" className="h-11" value={target} onChange={(e) => setEndpoint(e.target.value)} />
            {suite?.source.bot_endpoint && target.trim() !== suite.source.bot_endpoint && (
              <p className="text-xs text-muted-foreground">The suite was saved from {suite.source.bot_endpoint}.</p>
            )}
          </div>
          <div className="space-y-2">
            <Label htmlFor="reg-key" className="text-sm font-semibold">
              API key <span className="font-normal text-muted-foreground">(if your bot needs one)</span>
            </Label>
            <Input
              id="reg-key"
              type="password"
              autoComplete="off"
              className="h-10"
              placeholder="Sent only to the engine, never stored"
              value={apiKey}
              onChange={(e) => setApiKey(e.target.value)}
            />
          </div>
          <div className="space-y-2">
            <Label htmlFor="reg-model" className="text-sm font-semibold">
              Model <span className="font-normal text-muted-foreground">(optional)</span>
            </Label>
            <Input id="reg-model" className="h-10 font-mono text-sm" placeholder="Blank: your endpoint decides" value={model} onChange={(e) => setModel(e.target.value)} />
          </div>
          <div className="space-y-2">
            <Label htmlFor="reg-format" className="text-sm font-semibold">
              Request format
            </Label>
            <select
              id="reg-format"
              className="h-10 w-full rounded-md border border-input bg-background px-3 text-sm"
              value={format}
              onChange={(e) => setFormat(e.target.value as RequestFormat)}
            >
              <option value="openai">openai</option>
              <option value="anthropic">anthropic</option>
              <option value="custom">custom</option>
            </select>
          </div>
          <div className="space-y-2">
            <Label htmlFor="reg-name" className="text-sm font-semibold">
              Run name <span className="font-normal text-muted-foreground">(optional)</span>
            </Label>
            <Input id="reg-name" className="h-10" placeholder={suite ? `${suite.name} — replay` : ""} value={name} onChange={(e) => setName(e.target.value)} />
          </div>
        </div>
      </Section>

      <div className="sticky bottom-0 z-10 flex flex-col gap-3 rounded-b-xl border-t bg-card/95 px-6 py-4 backdrop-blur md:flex-row md:items-center md:justify-between md:px-8">
        <div className="text-sm">
          <p className="font-medium text-foreground">
            {suite ? `${plural(suite.cases, "saved conversation")} · replayed, judged, compared reply by reply` : ""}
          </p>
          <p className="text-xs text-muted-foreground">About $0.03 per reply judged · runs hands-off</p>
          {error && (
            <p role="alert" className="mt-1 text-sm font-medium text-destructive">
              {error}
            </p>
          )}
        </div>
        <Button type="submit" size="lg" className="h-11 px-6" disabled={submitting || !suite}>
          {submitting ? <Loader2 className="h-5 w-5 animate-spin" /> : null}
          Replay suite
          {!submitting && <ArrowRight className="h-5 w-5" />}
        </Button>
      </div>
    </form>
  );
}
