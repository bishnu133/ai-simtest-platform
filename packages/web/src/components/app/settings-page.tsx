"use client";

import { LoadingBlocks } from "@/components/ui/skeleton";
import { useMemo, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Bot, CheckCircle2, Cpu, Gavel, KeyRound, Loader2, PlugZap, TriangleAlert, Users, XCircle } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { toast } from "@/lib/toast";
import { engine, EngineError } from "@/lib/engine/client";
import type { AIModelCheck, AIProvider, AIProviderId, AIRole, AIRoleId, AISettingsView } from "@/lib/engine/types";

const ROLE_ICONS: Record<AIRoleId, typeof Users> = { simulator: Users, setup: Bot, judge: Gavel };
const PROVIDER_LABELS: Record<string, string> = {
  openai: "OpenAI",
  anthropic: "Anthropic",
  google: "Google Gemini",
  ollama: "Local (Ollama)",
  other: "Other provider",
};

export function SettingsPage() {
  const { data, isPending, isError, error, dataUpdatedAt } = useQuery({
    queryKey: ["ai-settings"],
    queryFn: engine.getAISettings,
  });
  // Kept above the form: a save remounts the form, and its check should survive that
  const [checks, setChecks] = useState<Record<string, AIModelCheck>>({});
  const [checking, setChecking] = useState(false);
  const [checkError, setCheckError] = useState("");
  const runCheck = async () => {
    setChecking(true);
    setCheckError("");
    setChecks({});
    try {
      const { results } = await engine.checkAIModels();
      setChecks(Object.fromEntries(results.map((r) => [r.role, r])));
    } catch (e) {
      setCheckError(e instanceof EngineError ? e.message : "Could not run the check.");
    } finally {
      setChecking(false);
    }
  };
  return (
    <div className="mx-auto w-full max-w-4xl space-y-6 p-4 md:p-8">
      <div>
        <h2 className="text-2xl font-bold tracking-tight text-foreground md:text-3xl">Settings</h2>
        <p className="text-sm text-muted-foreground">The AI models that run your tests, and the keys they use.</p>
      </div>
      {isError && (
        <p role="alert" className="text-sm font-medium text-destructive">
          {error instanceof Error ? error.message : "Could not load the settings."}
        </p>
      )}
      {isPending ? (
        <LoadingBlocks />
      ) : data ? (
        // A fresh form for each version of the saved settings
        <AIForm
          key={dataUpdatedAt}
          data={data}
          checks={checks}
          checking={checking}
          checkError={checkError}
          onCheck={runCheck}
          onEdit={() => setChecks({})}
        />
      ) : null}
    </div>
  );
}

function CheckLine({ check }: { check: AIModelCheck | undefined }) {
  if (!check) return null;
  return check.ok ? (
    <p className="flex items-center gap-1.5 text-xs font-medium text-pass">
      <CheckCircle2 className="h-3.5 w-3.5" aria-hidden /> Working · answered in {((check.latency_ms ?? 0) / 1000).toFixed(1)}s
    </p>
  ) : (
    <p className="flex items-start gap-1.5 text-xs font-medium text-fail">
      <XCircle className="mt-px h-3.5 w-3.5 shrink-0" aria-hidden /> {check.problem}
    </p>
  );
}

function RoleRow({
  role,
  value,
  onChange,
  check,
  listId,
}: {
  role: AIRole;
  value: string;
  onChange: (v: string) => void;
  check: AIModelCheck | undefined;
  listId: string;
}) {
  const Icon = ROLE_ICONS[role.id];
  return (
    <li className="grid gap-3 p-5 md:grid-cols-[minmax(0,1fr)_minmax(0,1fr)] md:items-start md:gap-6">
      <div className="flex gap-3">
        <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-primary/10 text-primary">
          <Icon className="h-4 w-4" aria-hidden />
        </span>
        <div className="min-w-0">
          <Label htmlFor={`model-${role.id}`} className="text-sm font-semibold text-foreground">
            {role.label}
          </Label>
          <p className="text-xs text-muted-foreground">{role.about}</p>
        </div>
      </div>
      <div className="space-y-1.5">
        <Input
          id={`model-${role.id}`}
          list={listId}
          className="h-10 font-mono text-sm"
          placeholder={role.default}
          value={value}
          onChange={(e) => onChange(e.target.value)}
          autoComplete="off"
          spellCheck={false}
        />
        <p className="text-xs text-muted-foreground">
          {value.trim() ? (
            <>
              {PROVIDER_LABELS[providerOf(value)]}
              {role.default && value.trim() !== role.default && " · clear it to use the engine default"}
            </>
          ) : (
            <>Engine default ({PROVIDER_LABELS[providerOf(role.default)]})</>
          )}
        </p>
        <CheckLine check={check} />
      </div>
    </li>
  );
}

function providerOf(model: string): string {
  const m = model.trim().toLowerCase();
  if (m.startsWith("ollama/") || m.startsWith("ollama_chat/")) return "ollama";
  if (m.startsWith("gemini/") || m.startsWith("google/") || m.startsWith("vertex")) return "google";
  if (m.includes("claude") || m.startsWith("anthropic/")) return "anthropic";
  if (m.startsWith("openai/") || /^(gpt|o\d|chatgpt)/.test(m)) return "openai";
  return "other";
}

function KeySource({ p, clearing }: { p: AIProvider; clearing: boolean }) {
  if (clearing)
    return <span className="rounded-full bg-muted px-2 py-0.5 text-[11px] font-medium text-muted-foreground">Removed on save</span>;
  if (p.source === "dashboard")
    return (
      <span className="rounded-full bg-primary/10 px-2 py-0.5 text-[11px] font-medium text-primary">
        Saved here{p.hint && ` · ${p.hint}`}
      </span>
    );
  if (p.source === "env")
    return (
      <span className="rounded-full bg-muted px-2 py-0.5 text-[11px] font-medium text-muted-foreground">
        From the engine&apos;s .env{p.hint && ` · ${p.hint}`}
      </span>
    );
  return <span className="rounded-full bg-muted px-2 py-0.5 text-[11px] font-medium text-muted-foreground/80">Not set</span>;
}

function AIForm({
  data,
  checks,
  checking,
  checkError,
  onCheck,
  onEdit,
}: {
  data: AISettingsView;
  checks: Record<string, AIModelCheck>;
  checking: boolean;
  checkError: string;
  onCheck: () => void;
  /** Results no longer describe the form once it changes */
  onEdit: () => void;
}) {
  const queryClient = useQueryClient();
  const savedModels = useMemo(
    () => Object.fromEntries(data.roles.map((r) => [r.id, r.source === "dashboard" ? r.model : ""])) as Record<AIRoleId, string>,
    [data],
  );
  const [models, setModels] = useState<Record<AIRoleId, string>>(savedModels);
  const [keys, setKeys] = useState<Partial<Record<AIProviderId, string>>>({});
  const [clear, setClear] = useState<AIProviderId[]>([]);
  const [ollama, setOllama] = useState(data.ollama_base_url);
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState("");

  const dirty =
    JSON.stringify(models) !== JSON.stringify(savedModels) ||
    Object.values(keys).some((k) => k?.trim()) ||
    clear.length > 0 ||
    ollama.trim() !== data.ollama_base_url;

  // Which roles lean on each provider, as the form stands
  const usedBy = (pid: string) =>
    data.roles.filter((r) => providerOf(models[r.id].trim() || r.default) === pid).map((r) => r.label);
  const usesOllama = data.roles.some((r) => providerOf(models[r.id].trim() || r.default) === "ollama");

  const save = async () => {
    setSaving(true);
    setMessage("");
    try {
      const fresh = await engine.saveAISettings({
        models,
        keys: Object.fromEntries(Object.entries(keys).filter(([, v]) => v?.trim())),
        ollama_base_url: ollama.trim(),
        clear,
      });
      // Remounts this form with the saved values, then checks them
      queryClient.setQueryData(["ai-settings"], fresh);
      toast("Settings saved. Checking each model…", "info");
      onCheck();
    } catch (e) {
      setMessage(e instanceof EngineError ? e.message : "Could not save.");
      setSaving(false);
    }
  };

  const listId = "model-suggestions";
  return (
    <div className="space-y-6">
      <datalist id={listId}>
        {Object.entries(data.suggestions).flatMap(([pid, list]) =>
          list.map((m) => <option key={m} value={m} label={PROVIDER_LABELS[pid]} />),
        )}
      </datalist>

      {data.problems.length > 0 && (
        <div role="alert" className="flex gap-3 rounded-xl border border-warn/40 bg-warn/10 p-4 text-sm text-foreground">
          <TriangleAlert className="mt-0.5 h-4 w-4 shrink-0 text-warn" aria-hidden />
          <div className="space-y-1">
            <p className="font-semibold">Tests can&apos;t run yet</p>
            {data.problems.map((p) => (
              <p key={p} className="text-muted-foreground">
                {p}
              </p>
            ))}
          </div>
        </div>
      )}

      <section className="rounded-xl border bg-card shadow-xs" aria-labelledby="models-heading">
        <div className="flex flex-wrap items-center justify-between gap-3 border-b p-5">
          <div>
            <h3 id="models-heading" className="font-semibold text-foreground">
              AI models
            </h3>
            <p className="text-sm text-muted-foreground">Any model LiteLLM supports. Start typing for suggestions.</p>
          </div>
          <Button
            type="button"
            variant="outline"
            onClick={onCheck}
            disabled={checking || saving || dirty}
            title={dirty ? "Save your changes first" : "Send each model one tiny request"}
          >
            {checking ? <Loader2 className="animate-spin" /> : <PlugZap />} Test connection
          </Button>
        </div>
        <ul className="divide-y">
          {data.roles.map((r) => (
            <RoleRow
              key={r.id}
              role={r}
              value={models[r.id]}
              onChange={(v) => {
                setModels((m) => ({ ...m, [r.id]: v }));
                onEdit();
              }}
              check={checks[r.id]}
              listId={listId}
            />
          ))}
        </ul>
      </section>

      <section className="rounded-xl border bg-card shadow-xs" aria-labelledby="keys-heading">
        <div className="border-b p-5">
          <h3 id="keys-heading" className="font-semibold text-foreground">
            API keys
          </h3>
          <p className="text-sm text-muted-foreground">
            A key saved here replaces the one in the engine&apos;s .env. Keys stay on the engine&apos;s machine in an
            owner-only file and are never shown again.
          </p>
        </div>
        <ul className="divide-y">
          {data.providers.map((p) => {
            const users = usedBy(p.id);
            const clearing = clear.includes(p.id);
            const missing = users.length > 0 && !p.set && !keys[p.id]?.trim();
            return (
              <li key={p.id} className="grid gap-3 p-5 md:grid-cols-[minmax(0,1fr)_minmax(0,1fr)] md:items-center md:gap-6">
                <div className="flex gap-3">
                  <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-muted text-muted-foreground">
                    <KeyRound className="h-4 w-4" aria-hidden />
                  </span>
                  <div className="min-w-0 space-y-1">
                    <div className="flex flex-wrap items-center gap-2">
                      <Label htmlFor={`key-${p.id}`} className="text-sm font-semibold text-foreground">
                        {p.label}
                      </Label>
                      <KeySource p={p} clearing={clearing} />
                    </div>
                    <p className={`text-xs ${missing ? "font-medium text-warn" : "text-muted-foreground"}`}>
                      {users.length ? `Used by ${users.join(", ")}` : "Not used by any model"}
                      {missing && " · needs a key"}
                    </p>
                  </div>
                </div>
                <div className="flex gap-2">
                  <Input
                    id={`key-${p.id}`}
                    type="password"
                    autoComplete="off"
                    className="h-10"
                    placeholder={p.set ? "Paste a new key to replace it" : `Paste your ${p.label} key`}
                    value={keys[p.id] ?? ""}
                    onChange={(e) => setKeys((k) => ({ ...k, [p.id]: e.target.value }))}
                  />
                  {p.source === "dashboard" && (
                    <Button
                      type="button"
                      variant="ghost"
                      className="shrink-0"
                      onClick={() => setClear((c) => (clearing ? c.filter((x) => x !== p.id) : [...c, p.id]))}
                    >
                      {clearing ? "Keep" : "Remove"}
                    </Button>
                  )}
                </div>
              </li>
            );
          })}
          <li className="grid gap-3 p-5 md:grid-cols-[minmax(0,1fr)_minmax(0,1fr)] md:items-center md:gap-6">
            <div className="flex gap-3">
              <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-muted text-muted-foreground">
                <Cpu className="h-4 w-4" aria-hidden />
              </span>
              <div className="min-w-0 space-y-1">
                <Label htmlFor="ollama-url" className="text-sm font-semibold text-foreground">
                  Local models (Ollama)
                </Label>
                <p className="text-xs text-muted-foreground">
                  {usesOllama ? "In use. " : ""}No key needed: models named ollama/… run on this address.
                </p>
              </div>
            </div>
            <Input
              id="ollama-url"
              type="url"
              className="h-10 font-mono text-sm"
              placeholder={data.ollama_default}
              value={ollama}
              onChange={(e) => setOllama(e.target.value)}
            />
          </li>
        </ul>
      </section>

      <div className="flex flex-wrap items-center justify-end gap-3">
        {(message || checkError) && (
          <p role="alert" className="mr-auto text-sm font-medium text-destructive">
            {message || checkError}
          </p>
        )}
        {dirty && (
          <p className="text-xs text-muted-foreground">Saving checks each model with one tiny request.</p>
        )}
        <Button type="button" onClick={save} disabled={!dirty || saving}>
          {saving && <Loader2 className="animate-spin" />} Save
        </Button>
      </div>
    </div>
  );
}
