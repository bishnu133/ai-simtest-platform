"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { CalendarClock, Loader2, Play, Trash2, TriangleAlert } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Sheet, SheetContent, SheetDescription, SheetTitle } from "@/components/ui/sheet";
import { LoadingBlocks } from "@/components/ui/skeleton";
import { Segmented } from "@/components/setup/controls";
import { engine, EngineError } from "@/lib/engine/client";
import { botHost, relativeTime } from "@/lib/engine/runs";
import type { TemplateSchedule, TestTemplate } from "@/lib/engine/types";
import { toast } from "@/lib/toast";
import { useNow } from "./runs-table";

const KIND: Record<string, string> = {
  simulation: "Persona simulation",
  scenarios: "Scenario packs",
  stress: "Memory stress",
  compare: "Model comparison",
  regression: "Regression suite",
  rag: "RAG & tools",
};
const DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"];

function when(iso: string, now: number) {
  const d = new Date(iso);
  const time = d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  const days = Math.round((new Date(d).setHours(0, 0, 0, 0) - new Date(now).setHours(0, 0, 0, 0)) / 86_400_000);
  return days === 0 ? `today at ${time}` : days === 1 ? `tomorrow at ${time}` : `${d.toLocaleDateString([], { weekday: "long" })} at ${time}`;
}

export function TemplatesPage() {
  const { data, isPending, isError, error } = useQuery({
    queryKey: ["templates"],
    queryFn: engine.listTemplates,
    refetchInterval: 30_000,
  });
  const [scheduling, setScheduling] = useState<TestTemplate | null>(null);
  const templates = data?.templates ?? [];

  return (
    <div className="mx-auto w-full max-w-5xl space-y-6 p-4 md:p-8">
      <div>
        <h2 className="text-2xl font-bold tracking-tight text-foreground md:text-3xl">Templates &amp; schedules</h2>
        <p className="text-sm text-muted-foreground">Tests you run again and again: start one with a click, or let it run on a schedule.</p>
      </div>
      {isError && (
        <p role="alert" className="text-sm font-medium text-destructive">
          {error instanceof Error ? error.message : "Could not load templates."}
        </p>
      )}
      {isPending ? (
        <LoadingBlocks />
      ) : templates.length === 0 && !isError ? (
        <div className="flex flex-col items-center gap-3 rounded-xl border border-dashed bg-card px-6 py-14 text-center">
          <span className="flex h-14 w-14 items-center justify-center rounded-2xl bg-primary/10 text-primary">
            <CalendarClock className="h-7 w-7" aria-hidden />
          </span>
          <h3 className="text-lg font-semibold text-foreground">No templates yet</h3>
          <p className="max-w-md text-sm text-muted-foreground">
            Open any finished run, click <span className="font-medium text-foreground">Run again</span> and tick{" "}
            <span className="font-medium text-foreground">Also save as a template</span>. It then lives here, ready to run or schedule.
          </p>
          <Button asChild variant="outline">
            <Link href="/runs">Go to runs</Link>
          </Button>
        </div>
      ) : (
        <ul className="grid gap-4 md:grid-cols-2">
          {templates.map((t) => (
            <TemplateCard key={t.id} t={t} onSchedule={() => setScheduling(t)} />
          ))}
        </ul>
      )}
      <Sheet open={scheduling !== null} onOpenChange={(o) => !o && setScheduling(null)}>
        <SheetContent side="right" className="flex w-full flex-col gap-0 overflow-y-auto p-0 sm:max-w-md">
          {scheduling && <ScheduleForm key={scheduling.id} t={scheduling} onDone={() => setScheduling(null)} />}
        </SheetContent>
      </Sheet>
    </div>
  );
}

function TemplateCard({ t, onSchedule }: { t: TestTemplate; onSchedule: () => void }) {
  const router = useRouter();
  const queryClient = useQueryClient();
  const now = useNow();
  const [asking, setAsking] = useState(false);
  const [key, setKey] = useState("");
  const [busy, setBusy] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const [error, setError] = useState("");
  const pass = t.last_run?.headline?.pass_rate;

  const run = async () => {
    setBusy(true);
    setError("");
    try {
      const sim = await engine.runTemplate(t.id, key.trim() || undefined);
      queryClient.invalidateQueries({ queryKey: ["runs"] });
      router.push(`/simulations/${sim.simulation_id}`);
    } catch (e) {
      setError(e instanceof EngineError ? e.message : "Could not start the test.");
      setBusy(false);
    }
  };
  const remove = async () => {
    await engine.deleteTemplate(t.id);
    queryClient.invalidateQueries({ queryKey: ["templates"] });
    toast(`Deleted ${t.name}. Its runs are kept.`, "info");
  };

  return (
    <li className="flex flex-col gap-4 rounded-xl border bg-card p-5 shadow-xs transition-shadow hover:shadow-sm">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <h3 className="truncate font-semibold text-foreground">{t.name}</h3>
          <p className="truncate text-xs text-muted-foreground">
            {KIND[t.summary.kind] ?? "Test"} · {t.bot_name ?? botHost(t.summary.bot_endpoint)} · {t.summary.num_personas} customers
          </p>
        </div>
        <span
          className={`inline-flex shrink-0 items-center gap-1 rounded-full px-2 py-0.5 text-xs font-medium ${
            t.schedule.enabled ? "bg-primary/10 text-primary" : "bg-muted text-muted-foreground"
          }`}
        >
          <CalendarClock className="h-3 w-3" aria-hidden /> {t.schedule_text}
        </span>
      </div>
      <dl className="grid grid-cols-2 gap-3 text-xs">
        <div>
          <dt className="text-muted-foreground">Last run</dt>
          <dd className="font-medium text-foreground">
            {t.last_run ? (
              <Link href={`/simulations/${t.last_run.id}`} className="hover:text-primary hover:underline">
                {pass != null ? `${Math.round(pass * 100)}% passed` : t.last_run.status}
                {t.last_run_at && ` · ${relativeTime(t.last_run_at, now)}`}
              </Link>
            ) : (
              "Not run yet"
            )}
          </dd>
        </div>
        <div>
          <dt className="text-muted-foreground">Next run</dt>
          <dd className="font-medium text-foreground">{t.next_run_at ? when(t.next_run_at, now) : "—"}</dd>
        </div>
      </dl>
      {(t.schedule_warning || t.last_problem) && (
        <p className="flex items-start gap-1.5 rounded-lg bg-warn/10 px-3 py-2 text-xs text-foreground">
          <TriangleAlert className="mt-0.5 h-3.5 w-3.5 shrink-0 text-warn" aria-hidden />
          {t.last_problem || t.schedule_warning}
        </p>
      )}
      {asking && (
        <div className="flex gap-2">
          <Input
            type="password"
            autoComplete="off"
            aria-label="Bot API key"
            className="h-9"
            placeholder="Bot API key, if it needs one"
            value={key}
            onChange={(e) => setKey(e.target.value)}
          />
          <Button size="sm" className="h-9" onClick={run} disabled={busy}>
            {busy ? <Loader2 className="animate-spin" /> : <Play />} Start
          </Button>
        </div>
      )}
      {error && (
        <p role="alert" className="text-xs font-medium text-destructive">
          {error}
        </p>
      )}
      <div className="mt-auto flex flex-wrap items-center gap-2 border-t pt-4">
        {!asking && (
          <Button size="sm" onClick={t.key_from_env ? run : () => setAsking(true)} disabled={busy}>
            {busy ? <Loader2 className="animate-spin" /> : <Play />} Run now
          </Button>
        )}
        <Button size="sm" variant="outline" onClick={onSchedule}>
          <CalendarClock /> {t.schedule.enabled ? "Edit schedule" : "Schedule"}
        </Button>
        <span className="ml-auto">
          {confirming ? (
            <span className="flex items-center gap-1">
              <Button size="sm" variant="destructive" onClick={remove}>
                Delete
              </Button>
              <Button size="sm" variant="ghost" onClick={() => setConfirming(false)}>
                Keep
              </Button>
            </span>
          ) : (
            <Button size="icon" variant="ghost" aria-label={`Delete ${t.name}`} onClick={() => setConfirming(true)}>
              <Trash2 />
            </Button>
          )}
        </span>
      </div>
    </li>
  );
}

function ScheduleForm({ t, onDone }: { t: TestTemplate; onDone: () => void }) {
  const queryClient = useQueryClient();
  const [s, setS] = useState<TemplateSchedule>({ ...t.schedule, enabled: true });
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");

  const save = async (schedule: TemplateSchedule) => {
    setSaving(true);
    setError("");
    try {
      await engine.updateTemplate(t.id, { schedule });
      await queryClient.invalidateQueries({ queryKey: ["templates"] });
      toast(schedule.enabled ? `${t.name} is scheduled` : `Schedule for ${t.name} turned off`);
      onDone();
    } catch (e) {
      setError(e instanceof EngineError ? e.message : "Could not save the schedule.");
      setSaving(false);
    }
  };

  return (
    <>
      <div className="border-b p-6">
        <SheetTitle className="text-lg font-semibold text-foreground">Schedule {t.name}</SheetTitle>
        <SheetDescription className="text-sm text-muted-foreground">
          Runs hands-off at the engine&apos;s local time. Your notification channels hear when it finishes.
        </SheetDescription>
      </div>
      <div className="flex-1 space-y-5 p-6">
        <div className="space-y-2">
          <p className="text-sm font-semibold text-foreground">How often</p>
          <Segmented
            label="How often"
            value={s.cadence}
            onChange={(cadence) => setS({ ...s, cadence })}
            options={[
              { id: "daily", label: "Every day" },
              { id: "weekdays", label: "Weekdays" },
              { id: "weekly", label: "Once a week" },
            ]}
          />
        </div>
        {s.cadence === "weekly" && (
          <div className="space-y-1.5">
            <Label htmlFor="sched-day" className="text-sm font-semibold">
              Day
            </Label>
            <select
              id="sched-day"
              className="h-10 w-full rounded-md border border-input bg-background px-3 text-sm"
              value={s.weekday}
              onChange={(e) => setS({ ...s, weekday: Number(e.target.value) })}
            >
              {DAYS.map((d, i) => (
                <option key={d} value={i}>
                  {d}
                </option>
              ))}
            </select>
          </div>
        )}
        <div className="space-y-1.5">
          <Label htmlFor="sched-time" className="text-sm font-semibold">
            Time
          </Label>
          <Input id="sched-time" type="time" className="h-10 w-40" value={s.time} onChange={(e) => setS({ ...s, time: e.target.value })} />
        </div>
        {!t.key_from_env && (
          <p className="flex items-start gap-1.5 rounded-lg bg-warn/10 px-3 py-2 text-xs text-foreground">
            <TriangleAlert className="mt-0.5 h-3.5 w-3.5 shrink-0 text-warn" aria-hidden />
            No one is there to type the bot&apos;s API key. If your bot needs one, run this test from a saved bot whose key comes
            from an environment variable, and save it as a template again.
          </p>
        )}
        {error && (
          <p role="alert" className="text-sm font-medium text-destructive">
            {error}
          </p>
        )}
      </div>
      <div className="flex items-center gap-2 border-t p-4">
        {t.schedule.enabled && (
          <Button variant="ghost" className="text-destructive" onClick={() => save({ ...t.schedule, enabled: false })} disabled={saving}>
            Turn off
          </Button>
        )}
        <span className="ml-auto flex gap-2">
          <Button variant="ghost" onClick={onDone}>
            Cancel
          </Button>
          <Button onClick={() => save({ ...s, enabled: true })} disabled={saving}>
            {saving && <Loader2 className="animate-spin" />} Save schedule
          </Button>
        </span>
      </div>
    </>
  );
}
