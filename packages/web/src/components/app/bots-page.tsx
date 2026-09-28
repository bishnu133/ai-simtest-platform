"use client";

import Link from "next/link";
import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Bot, ChevronDown, KeyRound, Loader2, Pencil, PlugZap, Plus, RefreshCw, Trash2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Sheet, SheetContent, SheetDescription, SheetTitle } from "@/components/ui/sheet";
import { toast } from "@/lib/toast";
import { engine, EngineError } from "@/lib/engine/client";
import { relativeTime } from "@/lib/engine/runs";
import type { BotCheckResult, RequestFormat, SavedBot, SavedBotInput } from "@/lib/engine/types";
import { BotCheckResultPanel } from "@/components/setup/bot-check";
import { Segmented } from "@/components/setup/controls";
import { useNow } from "./runs-table";

export function initials(name: string) {
  const parts = name.split(/[\s_-]+/).filter(Boolean);
  return ((parts[0]?.[0] ?? "?") + (parts[1]?.[0] ?? "")).toUpperCase();
}

/** Green, red or grey dot with a word, from the bot's last check. */
export function HealthPill({ bot, now }: { bot: SavedBot; now: number }) {
  const c = bot.last_check;
  if (!c)
    return (
      <span className="inline-flex items-center gap-1.5 rounded-full bg-muted px-2 py-0.5 text-xs font-medium text-muted-foreground">
        <span className="h-1.5 w-1.5 rounded-full bg-muted-foreground/60" aria-hidden /> Not checked
      </span>
    );
  return (
    <span
      className={`inline-flex items-center gap-1.5 rounded-full px-2 py-0.5 text-xs font-medium ${c.ok ? "bg-pass/10 text-pass" : "bg-fail/10 text-fail"}`}
      title={`${c.ok ? "Answered" : c.problem ?? "Failed"} · checked ${relativeTime(c.at, now)}`}
    >
      <span className={`h-1.5 w-1.5 rounded-full ${c.ok ? "bg-pass" : "bg-fail"}`} aria-hidden />
      {c.ok ? `Healthy · ${((c.latency_ms ?? 0) / 1000).toFixed(1)}s` : "Not answering"}
    </span>
  );
}

export function BotsPage() {
  const { data, isPending, isError, error } = useQuery({ queryKey: ["bots"], queryFn: engine.listBots });
  const [editing, setEditing] = useState<SavedBot | "new" | null>(null);
  const bots = data?.bots ?? [];

  return (
    <div className="mx-auto w-full max-w-5xl space-y-6 p-4 md:p-8">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h2 className="text-2xl font-bold tracking-tight text-foreground md:text-3xl">Bots</h2>
          <p className="text-sm text-muted-foreground">Connect a bot once, then pick it for every test.</p>
        </div>
        {bots.length > 0 && (
          <Button onClick={() => setEditing("new")}>
            <Plus /> Connect a bot
          </Button>
        )}
      </div>
      {isError && (
        <p role="alert" className="text-sm font-medium text-destructive">
          {error instanceof Error ? error.message : "Could not load your bots."}
        </p>
      )}
      {isPending ? (
        <div className="grid gap-4 md:grid-cols-2">
          {[0, 1].map((i) => (
            <div key={i} className="h-44 animate-pulse rounded-xl border bg-muted/40" />
          ))}
        </div>
      ) : bots.length === 0 && !isError ? (
        <div className="flex flex-col items-center gap-3 rounded-xl border border-dashed bg-card px-6 py-14 text-center">
          <span className="flex h-14 w-14 items-center justify-center rounded-2xl bg-primary/10 text-primary">
            <Bot className="h-7 w-7" aria-hidden />
          </span>
          <h3 className="text-lg font-semibold text-foreground">Connect your first bot</h3>
          <p className="max-w-md text-sm text-muted-foreground">
            Tell us where your chatbot is and check it answers. After that, starting a test is picking it from a list.
          </p>
          <Button onClick={() => setEditing("new")}>
            <Plus /> Connect a bot
          </Button>
        </div>
      ) : (
        <ul className="grid gap-4 md:grid-cols-2">
          {bots.map((b) => (
            <BotCard key={b.id} bot={b} onEdit={() => setEditing(b)} />
          ))}
        </ul>
      )}
      <Sheet open={editing !== null} onOpenChange={(open) => !open && setEditing(null)}>
        <SheetContent side="right" className="flex w-full flex-col gap-0 overflow-y-auto p-0 sm:max-w-lg">
          {editing !== null && (
            <BotForm key={editing === "new" ? "new" : editing.id} bot={editing === "new" ? null : editing} onDone={() => setEditing(null)} />
          )}
        </SheetContent>
      </Sheet>
    </div>
  );
}

function BotCard({ bot, onEdit }: { bot: SavedBot; onEdit: () => void }) {
  const queryClient = useQueryClient();
  const now = useNow();
  const [checking, setChecking] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const pass = bot.latest_run?.headline?.pass_rate;

  const check = async () => {
    setChecking(true);
    try {
      await engine.checkBot({
        bot_id: bot.id,
        bot_endpoint: bot.bot_endpoint,
        bot_format: bot.bot_request_format,
        bot_response_path: bot.bot_response_path,
        bot_model: bot.bot_model,
      });
    } finally {
      setChecking(false);
      queryClient.invalidateQueries({ queryKey: ["bots"] });
    }
  };
  const remove = async () => {
    await engine.deleteBot(bot.id);
    queryClient.invalidateQueries({ queryKey: ["bots"] });
    toast(`Deleted ${bot.name}. Its runs are kept.`, "info");
  };

  return (
    <li className="flex flex-col gap-4 rounded-xl border bg-card p-5 shadow-xs transition-shadow hover:shadow-sm">
      <div className="flex items-start gap-3">
        <span className="flex h-11 w-11 shrink-0 items-center justify-center rounded-xl bg-primary/10 text-sm font-bold text-primary">
          {initials(bot.name)}
        </span>
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <h3 className="truncate font-semibold text-foreground">{bot.name}</h3>
            <HealthPill bot={bot} now={now} />
          </div>
          <p className="truncate font-mono text-xs text-muted-foreground" title={bot.bot_endpoint}>
            {bot.bot_endpoint}
          </p>
        </div>
      </div>
      <dl className="grid grid-cols-2 gap-3 text-xs">
        <div>
          <dt className="text-muted-foreground">Latest test</dt>
          <dd className="font-medium text-foreground">
            {bot.latest_run ? (
              <Link href={`/simulations/${bot.latest_run.id}`} className="hover:text-primary hover:underline">
                {pass != null ? `${Math.round(pass * 100)}% passed` : bot.latest_run.status} ·{" "}
                {relativeTime(bot.latest_run.created_at, now)}
              </Link>
            ) : (
              "None yet"
            )}
          </dd>
        </div>
        <div>
          <dt className="text-muted-foreground">API key</dt>
          <dd className="flex items-center gap-1 font-medium text-foreground">
            <KeyRound className="h-3 w-3 text-muted-foreground" aria-hidden />
            {bot.key_env ? (
              <span className={bot.key_env_set ? "" : "text-warn"} title={bot.key_env_set ? "Set on the engine" : "Not set on the engine"}>
                <span className="font-mono">{bot.key_env}</span>
                {!bot.key_env_set && " (not set)"}
              </span>
            ) : (
              "Typed on each test"
            )}
          </dd>
        </div>
      </dl>
      {bot.last_check && !bot.last_check.ok && bot.last_check.problem && (
        <p className="rounded-lg bg-fail/5 px-3 py-2 text-xs text-fail">{bot.last_check.problem}</p>
      )}
      <div className="mt-auto flex flex-wrap items-center gap-2 border-t pt-4">
        <Button asChild size="sm">
          <Link href={`/new?bot=${encodeURIComponent(bot.id)}`}>New test</Link>
        </Button>
        <Button size="sm" variant="outline" onClick={check} disabled={checking}>
          {checking ? <Loader2 className="animate-spin" /> : <RefreshCw />} Check
        </Button>
        <Button size="sm" variant="ghost" onClick={onEdit}>
          <Pencil /> Edit
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
            <Button size="icon" variant="ghost" aria-label={`Delete ${bot.name}`} onClick={() => setConfirming(true)}>
              <Trash2 />
            </Button>
          )}
        </span>
      </div>
    </li>
  );
}

const FORMATS: { id: RequestFormat; label: string }[] = [
  { id: "openai", label: "OpenAI" },
  { id: "anthropic", label: "Anthropic" },
  { id: "custom", label: "Custom" },
];

export function BotForm({ bot, onDone }: { bot: SavedBot | null; onDone: (saved?: SavedBot) => void }) {
  const queryClient = useQueryClient();
  const [form, setForm] = useState<SavedBotInput>(() => ({
    name: bot?.name ?? "",
    bot_endpoint: bot?.bot_endpoint ?? "",
    bot_request_format: bot?.bot_request_format ?? "openai",
    bot_response_path: bot?.bot_response_path ?? "choices.0.message.content",
    bot_model: bot?.bot_model ?? "",
    bot_version_header: bot?.bot_version_header ?? "",
    bot_info_url: bot?.bot_info_url ?? "",
    key_env: bot?.key_env ?? "",
  }));
  const [keySource, setKeySource] = useState<"typed" | "env">(bot?.key_env ? "env" : "typed");
  const [checkKey, setCheckKey] = useState("");
  const [more, setMore] = useState(false);
  const [checking, setChecking] = useState(false);
  const [result, setResult] = useState<BotCheckResult | null>(null);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const set = (patch: Partial<SavedBotInput>) => {
    setForm((f) => ({ ...f, ...patch }));
    setResult(null);
  };

  const check = async () => {
    if (!/^https?:\/\/\S+$/.test(form.bot_endpoint.trim())) {
      setError("Enter the bot's endpoint: an http(s) URL.");
      return;
    }
    setError("");
    setChecking(true);
    try {
      setResult(
        await engine.checkBot({
          bot_id: bot?.id ?? null,
          bot_endpoint: form.bot_endpoint.trim(),
          bot_api_key: checkKey.trim() || undefined,
          bot_format: form.bot_request_format,
          bot_response_path: form.bot_response_path.trim() || "choices.0.message.content",
          bot_model: form.bot_model?.trim() || null,
        }),
      );
    } catch (e) {
      setResult({ ok: false, problem: "The check couldn't run.", fix: e instanceof EngineError ? e.message : "Is the engine running?" });
    } finally {
      setChecking(false);
    }
  };

  const save = async () => {
    setSaving(true);
    setError("");
    const body: SavedBotInput = {
      ...form,
      name: form.name.trim() || hostName(form.bot_endpoint),
      bot_endpoint: form.bot_endpoint.trim(),
      bot_response_path: form.bot_response_path.trim() || "choices.0.message.content",
      bot_model: form.bot_model?.trim() || null,
      bot_version_header: form.bot_version_header?.trim() || null,
      bot_info_url: form.bot_info_url?.trim() || null,
      key_env: keySource === "env" ? form.key_env.trim() : "",
    };
    try {
      const saved = bot ? await engine.updateBot(bot.id, body) : await engine.createBot(body);
      await queryClient.invalidateQueries({ queryKey: ["bots"] });
      toast(bot ? `Saved changes to ${saved.name}` : `${saved.name} is connected`);
      onDone(saved);
    } catch (e) {
      setError(e instanceof EngineError ? e.message : "Could not save the bot.");
      setSaving(false);
    }
  };

  return (
    <>
      <div className="border-b p-6">
        <SheetTitle className="text-lg font-semibold text-foreground">{bot ? `Edit ${bot.name}` : "Connect a bot"}</SheetTitle>
        <SheetDescription className="text-sm text-muted-foreground">
          Where your chatbot is and how to talk to it. Check it answers before you save.
        </SheetDescription>
      </div>
      <div className="flex-1 space-y-5 p-6">
        <div className="space-y-1.5">
          <Label htmlFor="bot-name" className="text-sm font-semibold">
            Name
          </Label>
          <Input id="bot-name" className="h-10" placeholder="Banking assistant" value={form.name} onChange={(e) => set({ name: e.target.value })} />
        </div>
        <div className="space-y-1.5">
          <Label htmlFor="bot-endpoint" className="text-sm font-semibold">
            Endpoint
          </Label>
          <Input
            id="bot-endpoint"
            type="url"
            className="h-10"
            placeholder="https://your-bot.com/v1/chat/completions"
            value={form.bot_endpoint}
            onChange={(e) => set({ bot_endpoint: e.target.value })}
          />
        </div>
        <div className="space-y-2">
          <p className="text-sm font-semibold text-foreground">API key</p>
          <Segmented
            label="Where the key comes from"
            value={keySource}
            onChange={setKeySource}
            options={[
              { id: "typed", label: "Type it on each test" },
              { id: "env", label: "Engine environment variable" },
            ]}
          />
          {keySource === "env" ? (
            <div className="space-y-1">
              <Input
                aria-label="Environment variable name"
                className="h-10 font-mono text-sm"
                placeholder="MY_BOT_KEY"
                value={form.key_env}
                onChange={(e) => set({ key_env: e.target.value.toUpperCase() })}
              />
              <p className="text-xs text-muted-foreground">
                Set it where the engine runs (e.g. in its .env). Bot keys are never saved by AI SimTest.
              </p>
            </div>
          ) : (
            <p className="text-xs text-muted-foreground">Bot keys are never saved: the test form asks for it each time, if your bot needs one.</p>
          )}
        </div>
        <div className="rounded-lg border">
          <button
            type="button"
            className="flex w-full items-center justify-between px-4 py-3 text-sm font-medium text-foreground"
            aria-expanded={more}
            onClick={() => setMore((m) => !m)}
          >
            Connection details
            <ChevronDown className={`h-4 w-4 transition-transform ${more ? "rotate-180" : ""}`} aria-hidden />
          </button>
          {more && (
            <div className="space-y-4 border-t p-4">
              <div className="space-y-1.5">
                <p className="text-sm font-medium">Request format</p>
                <Segmented label="Request format" value={form.bot_request_format} onChange={(v) => set({ bot_request_format: v })} options={FORMATS} />
              </div>
              <Field id="bot-path" label="Where the reply is in the response" value={form.bot_response_path} onChange={(v) => set({ bot_response_path: v })} mono />
              <Field id="bot-model" label="Model (sent as “model”)" placeholder="Blank: your endpoint decides" value={form.bot_model ?? ""} onChange={(v) => set({ bot_model: v })} mono />
              <Field id="bot-info" label="Version page" placeholder="https://your-bot.com/knowledge" value={form.bot_info_url ?? ""} onChange={(v) => set({ bot_info_url: v })} />
              <Field id="bot-header" label="Version header" placeholder="x-bot-version" value={form.bot_version_header ?? ""} onChange={(v) => set({ bot_version_header: v })} mono />
            </div>
          )}
        </div>
        <div className="space-y-2 rounded-lg bg-muted/40 p-4">
          <div className="flex flex-col gap-2 sm:flex-row">
            {(keySource === "typed" || !form.key_env) && (
              <Input
                type="password"
                autoComplete="off"
                aria-label="Key for this check only"
                className="h-10 bg-card"
                placeholder="Key for this check only (optional)"
                value={checkKey}
                onChange={(e) => setCheckKey(e.target.value)}
              />
            )}
            <Button type="button" variant="outline" className="h-10 shrink-0 bg-card" onClick={check} disabled={checking || !form.bot_endpoint.trim()}>
              {checking ? <Loader2 className="animate-spin" /> : <PlugZap />} Test connection
            </Button>
          </div>
          {result && (
            <BotCheckResultPanel
              result={result}
              onUsePath={(p) => {
                set({ bot_response_path: p });
                setMore(true);
              }}
            />
          )}
        </div>
        {error && (
          <p role="alert" className="text-sm font-medium text-destructive">
            {error}
          </p>
        )}
      </div>
      <div className="flex items-center justify-end gap-2 border-t p-4">
        <Button variant="ghost" onClick={() => onDone()}>
          Cancel
        </Button>
        <Button onClick={save} disabled={saving || !form.bot_endpoint.trim() || (keySource === "env" && !form.key_env.trim())}>
          {saving && <Loader2 className="animate-spin" />} {bot ? "Save changes" : "Save bot"}
        </Button>
      </div>
    </>
  );
}

function Field({
  id,
  label,
  value,
  onChange,
  placeholder,
  mono,
}: {
  id: string;
  label: string;
  value: string;
  onChange: (v: string) => void;
  placeholder?: string;
  mono?: boolean;
}) {
  return (
    <div className="space-y-1.5">
      <Label htmlFor={id} className="text-sm font-medium">
        {label}
      </Label>
      <Input id={id} className={`h-10 ${mono ? "font-mono text-sm" : ""}`} placeholder={placeholder} value={value} onChange={(e) => onChange(e.target.value)} />
    </div>
  );
}

function hostName(endpoint: string) {
  try {
    return new URL(endpoint).host;
  } catch {
    return "My bot";
  }
}
