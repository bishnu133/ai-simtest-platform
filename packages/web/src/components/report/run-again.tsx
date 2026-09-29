"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Loader2, RotateCcw } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from "@/components/ui/sheet";
import { engine, EngineError } from "@/lib/engine/client";
import { botHost } from "@/lib/engine/runs";
import type { SimulationStatus } from "@/lib/engine/types";
import { toast } from "@/lib/toast";

/** Start the same test again, and optionally keep it as a template to run on a schedule. */
export function RunAgainButton({ status }: { status: SimulationStatus }) {
  const router = useRouter();
  const queryClient = useQueryClient();
  const [open, setOpen] = useState(false);
  const [key, setKey] = useState("");
  const [keep, setKeep] = useState(false);
  const [name, setName] = useState(status.name);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const { data: bots } = useQuery({ queryKey: ["bots"], queryFn: engine.listBots, retry: false, enabled: open });
  const bot = bots?.bots.find((b) => b.id === status.config.bot_id);
  const cfg = status.config;
  if (cfg.replay) return null; // a replay judges uploaded logs: they are not kept

  const start = async () => {
    setBusy(true);
    setError("");
    try {
      let sim;
      if (keep) {
        // Started from the new template, so the run shows up as its latest
        const template = await engine.createTemplate(name.trim() || status.name, status.simulation_id);
        queryClient.invalidateQueries({ queryKey: ["templates"] });
        toast(`Saved “${template.name}” to Templates`);
        sim = await engine.runTemplate(template.id, key.trim() || undefined);
      } else {
        sim = await engine.rerun(status.simulation_id, key.trim() || undefined);
      }
      queryClient.invalidateQueries({ queryKey: ["runs"] });
      router.push(`/simulations/${sim.simulation_id}`);
    } catch (e) {
      setError(e instanceof EngineError ? e.message : "Could not start the test.");
      setBusy(false);
    }
  };

  return (
    <>
      <Button onClick={() => setOpen(true)}>
        <RotateCcw /> Run again
      </Button>
      <Sheet open={open} onOpenChange={setOpen}>
        <SheetContent side="right" className="flex w-full flex-col gap-0 overflow-y-auto p-0 sm:max-w-md">
          <SheetHeader className="border-b p-6 text-left">
            <SheetTitle>Run this test again</SheetTitle>
            <SheetDescription>Same bot, documents, customers and checks. A fresh set of conversations.</SheetDescription>
          </SheetHeader>
          <div className="flex-1 space-y-5 p-6 text-sm">
            <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-2 rounded-lg bg-muted/40 p-4">
              <dt className="text-muted-foreground">Bot</dt>
              <dd className="truncate font-medium text-foreground">{bot?.name ?? botHost(cfg.bot_endpoint)}</dd>
              <dt className="text-muted-foreground">Customers</dt>
              <dd className="font-medium text-foreground">
                {cfg.num_personas}, up to {cfg.max_turns} messages
              </dd>
              <dt className="text-muted-foreground">Documents</dt>
              <dd className="truncate font-medium text-foreground">{cfg.documentation_filename || "—"}</dd>
              <dt className="text-muted-foreground">Review</dt>
              <dd className="font-medium text-foreground">{cfg.auto_approve ? "Hands-off" : "You review each step"}</dd>
            </dl>
            {bot?.key_env ? (
              <p className="text-xs text-muted-foreground">
                The bot&apos;s key is read from <span className="font-mono">{bot.key_env}</span> on the engine.
              </p>
            ) : (
              <div className="space-y-1.5">
                <Label htmlFor="rerun-key" className="text-sm font-semibold">
                  Bot API key <span className="font-normal text-muted-foreground">(if your bot needs one)</span>
                </Label>
                <Input
                  id="rerun-key"
                  type="password"
                  autoComplete="off"
                  className="h-10"
                  placeholder="Keys are never saved, so enter it again"
                  value={key}
                  onChange={(e) => setKey(e.target.value)}
                />
              </div>
            )}
            <div className="space-y-2 rounded-lg border p-4">
              <label className="flex items-start gap-2">
                <Checkbox checked={keep} onCheckedChange={(v) => setKeep(v === true)} className="mt-0.5" />
                <span>
                  <span className="font-medium text-foreground">Also save as a template</span>
                  <span className="block text-xs text-muted-foreground">Run it again with one click, or on a schedule, from Templates.</span>
                </span>
              </label>
              {keep && (
                <Input aria-label="Template name" className="h-9" value={name} onChange={(e) => setName(e.target.value)} />
              )}
            </div>
            {error && (
              <p role="alert" className="font-medium text-destructive">
                {error}
              </p>
            )}
          </div>
          <div className="flex justify-end gap-2 border-t p-4">
            <Button variant="ghost" onClick={() => setOpen(false)}>
              Cancel
            </Button>
            <Button onClick={start} disabled={busy}>
              {busy ? <Loader2 className="animate-spin" /> : <RotateCcw />} Start
            </Button>
          </div>
        </SheetContent>
      </Sheet>
    </>
  );
}
