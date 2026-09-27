"use client";

import Link from "next/link";
import { useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { CheckCircle2, ListRestart, Loader2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from "@/components/ui/sheet";
import { Segmented } from "@/components/setup/controls";
import { engine, EngineError } from "@/lib/engine/client";
import { plural } from "@/lib/engine/report";
import type { SuiteSummary } from "@/lib/engine/types";

const GUARDS = ["0", "3", "5", "10"] as const;

/** Freeze this run's failures (and a few clean conversations) as a regression suite. */
export function SaveSuiteButton({ simulationId, runName }: { simulationId: string; runName: string }) {
  const queryClient = useQueryClient();
  const [open, setOpen] = useState(false);
  const [name, setName] = useState(`${runName} — regression`);
  const [warnings, setWarnings] = useState(true);
  const [guards, setGuards] = useState<(typeof GUARDS)[number]>("5");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [saved, setSaved] = useState<SuiteSummary | null>(null);

  const save = async () => {
    setSaving(true);
    setError("");
    try {
      const suite = await engine.saveSuite(simulationId, {
        name: name.trim(),
        include_warnings: warnings,
        include_passing: Number(guards),
      });
      setSaved(suite);
      queryClient.invalidateQueries({ queryKey: ["suites"] });
    } catch (e) {
      setError(e instanceof EngineError ? e.message : "Could not save the suite.");
    } finally {
      setSaving(false);
    }
  };

  return (
    <>
      <Button variant="outline" onClick={() => { setSaved(null); setOpen(true); }}>
        <ListRestart /> Save as regression suite
      </Button>
      <Sheet open={open} onOpenChange={setOpen}>
        <SheetContent side="right" className="w-full overflow-y-auto sm:max-w-md">
          <SheetHeader className="pr-6 text-left">
            <SheetTitle>Save as regression suite</SheetTitle>
            <SheetDescription>
              The failing conversations are saved with how they were judged: this run&apos;s documentation, approved criteria,
              guardrails and pass marks. Replay the suite on every new build.
            </SheetDescription>
          </SheetHeader>
          {saved ? (
            <div className="mt-6 space-y-4 text-sm">
              <p className="flex items-start gap-2 font-medium text-foreground">
                <CheckCircle2 className="mt-0.5 h-4 w-4 shrink-0 text-pass" aria-hidden />
                Saved &ldquo;{saved.name}&rdquo;: {plural(saved.cases, "case")} ({saved.failing_cases} failing,{" "}
                {saved.warning_cases} warnings, {plural(saved.guard_cases, "guard")}).
              </p>
              <div className="flex flex-wrap gap-2">
                <Button asChild>
                  <Link href={`/new?type=regression&suite=${encodeURIComponent(saved.id)}`}>Replay it now</Link>
                </Button>
                <Button asChild variant="outline">
                  <Link href="/suites">All suites</Link>
                </Button>
              </div>
            </div>
          ) : (
            <div className="mt-6 space-y-5">
              <div className="space-y-2">
                <Label htmlFor="suite-name" className="text-sm font-semibold">
                  Name
                </Label>
                <Input id="suite-name" className="h-10" maxLength={120} value={name} onChange={(e) => setName(e.target.value)} />
              </div>
              <label className="flex cursor-pointer gap-3 rounded-lg border p-3 hover:bg-muted/40">
                <Checkbox checked={warnings} onCheckedChange={(v) => setWarnings(v === true)} className="mt-0.5" />
                <span>
                  <span className="block text-sm font-medium text-foreground">Include warnings</span>
                  <span className="block text-xs text-muted-foreground">Conversations whose replies were borderline, not failed.</span>
                </span>
              </label>
              <fieldset className="space-y-2">
                <legend className="text-sm font-semibold text-foreground">Clean conversations to keep as guards</legend>
                <Segmented label="Guards" value={guards} onChange={setGuards} options={GUARDS.map((g) => ({ id: g, label: g }))} />
                <p className="text-xs text-muted-foreground">
                  Only something that passed can regress. Guards catch a new build breaking what worked; edge-case and
                  adversarial customers are picked first.
                </p>
              </fieldset>
              {error && (
                <p role="alert" className="text-sm font-medium text-destructive">
                  {error}
                </p>
              )}
              <Button onClick={save} disabled={saving}>
                {saving && <Loader2 className="animate-spin" />} Save suite
              </Button>
            </div>
          )}
        </SheetContent>
      </Sheet>
    </>
  );
}
