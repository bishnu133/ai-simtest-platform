"use client";

import Link from "next/link";
import { useMemo, useState, type ReactNode } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Bell,
  CheckCircle2,
  CircleMinus,
  Hash,
  Loader2,
  Mail,
  Plus,
  Send,
  Trash2,
  Users,
  Webhook,
  XCircle,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { engine, EngineError } from "@/lib/engine/client";
import { relativeTime } from "@/lib/engine/runs";
import type {
  ChannelType,
  NotificationChannel,
  NotificationChannelInput,
  NotificationDelivery,
  NotificationSettings,
  NotificationTestResult,
} from "@/lib/engine/types";
import { useNow } from "./runs-table";

const TYPES: Record<
  ChannelType,
  { label: string; Icon: typeof Hash; urlLabel: string; urlHelp: string }
> = {
  slack: {
    label: "Slack",
    Icon: Hash,
    urlLabel: "Incoming webhook URL",
    urlHelp:
      "Slack → Apps → Incoming Webhooks → Add to a channel. Starts with https://hooks.slack.com/.",
  },
  teams: {
    label: "Microsoft Teams",
    Icon: Users,
    urlLabel: "Workflow webhook URL",
    urlHelp:
      "Teams → the channel → Workflows → “Post to a channel when a webhook request is received”.",
  },
  email: { label: "Email", Icon: Mail, urlLabel: "", urlHelp: "" },
  generic_webhook: {
    label: "Webhook",
    Icon: Webhook,
    urlLabel: "Endpoint URL",
    urlHelp:
      "Receives a JSON event (schema 1.0): event_type, severity, summary, details, links.",
  },
};

interface Hints {
  url_hint: string;
  url_set: boolean;
  password_set: boolean;
  password_hint: string;
}

const newId = () => `ch_${Math.random().toString(36).slice(2, 10)}`;

function toInput(c: NotificationChannel): NotificationChannelInput {
  // eslint-disable-next-line @typescript-eslint/no-unused-vars
  const { url_hint, url_set, password_set, password_hint, ...rest } = c;
  return { ...rest, url: "", password: "" };
}

function blank(type: ChannelType, events: string[]): NotificationChannelInput {
  return {
    id: newId(),
    name: TYPES[type].label,
    type,
    enabled: true,
    events,
    url: "",
    mention_on_critical: "",
    smtp_host: "",
    smtp_port: 587,
    use_tls: true,
    username: "",
    password: "",
    sender: "",
    recipients: [],
  };
}

function Status({
  d,
}: {
  d: Pick<NotificationDelivery, "status" | "http_status">;
}) {
  if (d.status === "success" || d.status === "dry_run")
    return (
      <span className="inline-flex items-center gap-1 text-xs font-semibold text-pass">
        <CheckCircle2 className="h-3.5 w-3.5" aria-hidden /> Delivered
      </span>
    );
  if (d.status === "failed")
    return (
      <span className="inline-flex items-center gap-1 text-xs font-semibold text-fail">
        <XCircle className="h-3.5 w-3.5" aria-hidden /> Failed
        {d.http_status ? ` (${d.http_status})` : ""}
      </span>
    );
  return (
    <span className="inline-flex items-center gap-1 text-xs font-semibold text-muted-foreground">
      <CircleMinus className="h-3.5 w-3.5" aria-hidden /> Held back
    </span>
  );
}

function Field({
  id,
  label,
  help,
  children,
}: {
  id: string;
  label: string;
  help?: ReactNode;
  children: ReactNode;
}) {
  return (
    <div className="space-y-1.5">
      <Label htmlFor={id} className="text-sm font-medium">
        {label}
      </Label>
      {children}
      {help && <p className="text-xs text-muted-foreground">{help}</p>}
    </div>
  );
}

function ChannelCard({
  channel,
  hints,
  events,
  saved,
  onChange,
  onRemove,
}: {
  channel: NotificationChannelInput;
  hints: Hints | undefined;
  events: NotificationSettings["events"];
  /** Saved exactly as shown: only then can a test message be sent */
  saved: boolean;
  onChange: (c: NotificationChannelInput) => void;
  onRemove: () => void;
}) {
  const t = TYPES[channel.type];
  const [testing, setTesting] = useState(false);
  const [result, setResult] = useState<NotificationTestResult | null>(null);
  const [testError, setTestError] = useState("");
  const set = (patch: Partial<NotificationChannelInput>) =>
    onChange({ ...channel, ...patch });
  const p = channel.id;

  const test = async () => {
    setTesting(true);
    setResult(null);
    setTestError("");
    try {
      setResult(await engine.testNotification(channel.id));
    } catch (e) {
      setTestError(
        e instanceof EngineError ? e.message : "Could not send the test.",
      );
    } finally {
      setTesting(false);
    }
  };

  const secretHelp = (set_: boolean, hint: string, what: string) =>
    set_
      ? `Saved${hint ? ` (${hint})` : ""}. Leave blank to keep it.`
      : `Or \${ENV_VAR} to read the ${what} from the engine's environment, so it is never stored.`;

  return (
    <li className="space-y-4 rounded-xl border bg-card p-5 shadow-xs">
      <div className="flex flex-wrap items-center gap-3">
        <span className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-primary/10 text-primary">
          <t.Icon className="h-4 w-4" aria-hidden />
        </span>
        <div className="min-w-0 flex-1">
          <Input
            aria-label="Channel name"
            className="h-9 max-w-xs font-medium"
            value={channel.name}
            onChange={(e) => set({ name: e.target.value })}
          />
          <p className="mt-0.5 text-xs text-muted-foreground">{t.label}</p>
        </div>
        <label className="flex items-center gap-2 text-sm">
          <Checkbox
            checked={channel.enabled}
            onCheckedChange={(c) => set({ enabled: c === true })}
          />
          On
        </label>
        <Button
          type="button"
          variant="ghost"
          size="sm"
          onClick={onRemove}
          aria-label={`Remove ${channel.name}`}
        >
          <Trash2 />
        </Button>
      </div>

      {channel.type === "email" ? (
        <div className="grid gap-3 md:grid-cols-2">
          <Field id={`${p}-host`} label="SMTP server">
            <Input
              id={`${p}-host`}
              value={channel.smtp_host}
              placeholder="smtp.example.com"
              onChange={(e) => set({ smtp_host: e.target.value })}
            />
          </Field>
          <Field
            id={`${p}-port`}
            label="Port"
            help="587 uses STARTTLS; 465 uses TLS from the start."
          >
            <Input
              id={`${p}-port`}
              type="number"
              min={1}
              max={65535}
              value={channel.smtp_port}
              onChange={(e) =>
                set({ smtp_port: Number(e.target.value) || 587 })
              }
            />
          </Field>
          <Field id={`${p}-user`} label="Username (optional)">
            <Input
              id={`${p}-user`}
              autoComplete="off"
              value={channel.username}
              onChange={(e) => set({ username: e.target.value })}
            />
          </Field>
          <Field
            id={`${p}-pass`}
            label="Password (optional)"
            help={secretHelp(
              !!hints?.password_set,
              hints?.password_hint ?? "",
              "password",
            )}
          >
            <Input
              id={`${p}-pass`}
              type="password"
              autoComplete="new-password"
              value={channel.password}
              placeholder={hints?.password_set ? "•••••••• (saved)" : ""}
              onChange={(e) => set({ password: e.target.value })}
            />
          </Field>
          <Field id={`${p}-from`} label="From">
            <Input
              id={`${p}-from`}
              value={channel.sender}
              placeholder="simtest@example.com"
              onChange={(e) => set({ sender: e.target.value })}
            />
          </Field>
          <Field
            id={`${p}-to`}
            label="To"
            help="Separate addresses with commas."
          >
            <Input
              id={`${p}-to`}
              value={channel.recipients.join(", ")}
              placeholder="qa-team@example.com"
              onChange={(e) =>
                set({
                  recipients: e.target.value
                    .split(",")
                    .map((a) => a.trim())
                    .filter(Boolean),
                })
              }
            />
          </Field>
          <label className="flex items-center gap-2 text-sm md:col-span-2">
            <Checkbox
              checked={channel.use_tls}
              onCheckedChange={(c) => set({ use_tls: c === true })}
            />
            Encrypt the connection (TLS)
          </label>
        </div>
      ) : (
        <div className="grid gap-3 md:grid-cols-[minmax(0,1fr)_14rem]">
          <Field
            id={`${p}-url`}
            label={t.urlLabel}
            help={
              <>
                {t.urlHelp}{" "}
                {secretHelp(!!hints?.url_set, hints?.url_hint ?? "", "URL")}
              </>
            }
          >
            <Input
              id={`${p}-url`}
              type="password"
              autoComplete="off"
              className="font-mono text-sm"
              value={channel.url}
              placeholder={
                hints?.url_set ? `${hints.url_hint} (saved)` : "https://…"
              }
              onChange={(e) => set({ url: e.target.value })}
            />
          </Field>
          {channel.type !== "generic_webhook" && (
            <Field
              id={`${p}-mention`}
              label="Mention on critical"
              help={
                channel.type === "slack"
                  ? "e.g. <!here> or <@U0123>"
                  : "e.g. a name"
              }
            >
              <Input
                id={`${p}-mention`}
                value={channel.mention_on_critical}
                onChange={(e) => set({ mention_on_critical: e.target.value })}
              />
            </Field>
          )}
        </div>
      )}

      <fieldset className="space-y-2">
        <legend className="text-sm font-medium text-foreground">
          Tell this channel when
        </legend>
        <div className="flex flex-wrap gap-x-5 gap-y-2">
          {events.map((ev) => (
            <label key={ev.id} className="flex items-center gap-2 text-sm">
              <Checkbox
                checked={channel.events.includes(ev.id)}
                onCheckedChange={(c) =>
                  set({
                    events:
                      c === true
                        ? [...channel.events, ev.id]
                        : channel.events.filter((x) => x !== ev.id),
                  })
                }
              />
              {ev.label}
            </label>
          ))}
        </div>
      </fieldset>

      <div className="flex flex-wrap items-center gap-3 border-t pt-3">
        <Button
          type="button"
          size="sm"
          variant="outline"
          onClick={test}
          disabled={!saved || testing}
        >
          {testing ? <Loader2 className="animate-spin" /> : <Send />} Send a
          test message
        </Button>
        {!saved && (
          <span className="text-xs text-muted-foreground">
            Save first to send a test.
          </span>
        )}
        {result && (
          <span
            role="status"
            className="flex flex-wrap items-center gap-2 text-sm"
          >
            <Status
              d={{
                status: result.delivered ? "success" : "failed",
                http_status: result.http_status ?? null,
              }}
            />
            {!result.delivered && result.problem && (
              <span className="text-muted-foreground">{result.problem}</span>
            )}
            {result.delivered && result.attempts && result.attempts > 1 && (
              <span className="text-xs text-muted-foreground">
                after {result.attempts} attempts
              </span>
            )}
          </span>
        )}
        {testError && (
          <span role="alert" className="text-sm text-destructive">
            {testError}
          </span>
        )}
      </div>
    </li>
  );
}

function Deliveries({ rows }: { rows: NotificationDelivery[] }) {
  const now = useNow();
  if (!rows.length)
    return <p className="text-sm text-muted-foreground">Nothing sent yet.</p>;
  return (
    <div className="overflow-x-auto">
      <table className="w-full min-w-[38rem] text-sm">
        <caption className="sr-only">
          Recent notifications, newest first
        </caption>
        <thead>
          <tr className="border-b text-left text-xs text-muted-foreground">
            <th scope="col" className="py-2 pr-3 font-medium">
              When
            </th>
            <th scope="col" className="px-3 py-2 font-medium">
              What
            </th>
            <th scope="col" className="px-3 py-2 font-medium">
              Channel
            </th>
            <th scope="col" className="py-2 pl-3 font-medium">
              Result
            </th>
          </tr>
        </thead>
        <tbody className="divide-y">
          {rows.map((r, i) => (
            <tr key={`${r.at}-${r.channel}-${i}`} className="align-top">
              <td
                className="py-2 pr-3 whitespace-nowrap text-muted-foreground"
                title={new Date(r.at).toLocaleString()}
              >
                {relativeTime(r.at, now)}
              </td>
              <td className="px-3 py-2">
                {r.event_type.replace(/_/g, " ")}
                {r.run_id && r.run_id !== "test" && (
                  <>
                    {" · "}
                    <Link
                      href={`/simulations/${r.run_id}`}
                      className="font-medium text-primary hover:underline"
                    >
                      run
                    </Link>
                  </>
                )}
              </td>
              <td className="px-3 py-2">{r.channel || "—"}</td>
              <td className="py-2 pl-3">
                <Status d={r} />
                {r.problem && (
                  <p className="mt-0.5 text-xs text-muted-foreground">
                    {r.problem}
                  </p>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function IntegrationsPage() {
  const { data, isPending, isError, error, dataUpdatedAt } = useQuery({
    queryKey: ["notifications"],
    queryFn: engine.getNotifications,
  });
  return (
    <div className="mx-auto w-full max-w-5xl space-y-6 p-4 md:p-8">
      <div>
        <h2 className="text-2xl font-bold tracking-tight text-foreground md:text-3xl">
          Integrations
        </h2>
        <p className="text-sm text-muted-foreground">
          Where AI SimTest tells your team about runs.
        </p>
      </div>
      {isError && (
        <p role="alert" className="text-sm font-medium text-destructive">
          {error instanceof Error
            ? error.message
            : "Could not load the settings."}
        </p>
      )}
      {isPending ? (
        <div className="flex justify-center py-16">
          <Loader2
            className="h-6 w-6 animate-spin text-muted-foreground"
            aria-label="Loading"
          />
        </div>
      ) : data ? (
        // A fresh form for each version of the saved settings
        <NotificationsForm key={dataUpdatedAt} data={data} />
      ) : null}
    </div>
  );
}

function NotificationsForm({ data }: { data: NotificationSettings }) {
  const queryClient = useQueryClient();
  const initial = useMemo(() => data.channels.map(toInput), [data]);
  const [enabled, setEnabled] = useState(data.enabled);
  const [dashboardUrl, setDashboardUrl] = useState(data.dashboard_url);
  const [channels, setChannels] = useState<NotificationChannelInput[]>(initial);
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState("");
  const hints: Record<string, Hints> = useMemo(
    () => Object.fromEntries(data.channels.map((c) => [c.id, c])),
    [data],
  );
  const savedJson = useMemo(
    () =>
      JSON.stringify({
        enabled: data.enabled,
        dashboardUrl: data.dashboard_url,
        inputs: initial,
      }),
    [data, initial],
  );
  const dirty =
    JSON.stringify({ enabled, dashboardUrl, inputs: channels }) !== savedJson;
  const savedIds = useMemo(
    () => new Set(data.channels.map((c) => c.id)),
    [data],
  );

  const save = async () => {
    setSaving(true);
    setSaveError("");
    try {
      const next = await engine.saveNotifications({
        enabled,
        dashboard_url: dashboardUrl.trim(),
        environment: data.environment,
        channels: channels.map((c) => ({
          ...c,
          name: c.name.trim(),
          url: c.url.trim(),
        })),
      });
      queryClient.setQueryData(["notifications"], next);
    } catch (e) {
      setSaveError(e instanceof EngineError ? e.message : "Could not save.");
      setSaving(false);
    }
  };

  return (
    <>
      <section className="space-y-5 rounded-xl border bg-card p-5 shadow-xs">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <h3 className="flex items-center gap-2 text-base font-semibold text-foreground">
              <Bell className="h-4 w-4 text-primary" aria-hidden />{" "}
              Notifications
            </h3>
            <p className="mt-0.5 text-sm text-muted-foreground">
              A message when a run finishes, fails, finds critical failures or
              regresses. Webhook URLs and passwords stay on the engine; this
              page never shows them again.
            </p>
          </div>
          <label className="flex items-center gap-2 text-sm font-medium">
            <Checkbox
              checked={enabled}
              onCheckedChange={(c) => setEnabled(c === true)}
            />
            Send notifications
          </label>
        </div>
        <div className="max-w-xl">
          <Field
            id="dashboard-url"
            label="Dashboard address, for “Open run” links"
            help={
              <>
                The address your team opens this dashboard at.{" "}
                <button
                  type="button"
                  className="font-medium text-primary hover:underline"
                  onClick={() => setDashboardUrl(window.location.origin)}
                >
                  Use this one
                </button>
              </>
            }
          >
            <Input
              id="dashboard-url"
              value={dashboardUrl}
              placeholder="https://simtest.example.com"
              onChange={(e) => setDashboardUrl(e.target.value)}
            />
          </Field>
        </div>
      </section>

      {channels.length > 0 && (
        <ul className="space-y-4" aria-label="Channels">
          {channels.map((c, i) => (
            <ChannelCard
              key={c.id}
              channel={c}
              hints={hints[c.id]}
              events={data.events}
              saved={savedIds.has(c.id) && !dirty}
              onChange={(next) =>
                setChannels(channels.map((x, j) => (j === i ? next : x)))
              }
              onRemove={() => setChannels(channels.filter((_, j) => j !== i))}
            />
          ))}
        </ul>
      )}

      <div className="flex flex-wrap items-center gap-2">
        <span className="text-sm text-muted-foreground">Add a channel:</span>
        {(Object.keys(TYPES) as ChannelType[]).map((type) => {
          const T = TYPES[type];
          return (
            <Button
              key={type}
              type="button"
              size="sm"
              variant="outline"
              onClick={() =>
                setChannels([
                  ...channels,
                  blank(
                    type,
                    data.events
                      .filter((e) => e.id !== "run_started")
                      .map((e) => e.id),
                  ),
                ])
              }
            >
              <Plus /> <T.Icon aria-hidden /> {T.label}
            </Button>
          );
        })}
      </div>

      <div className="sticky bottom-0 z-10 flex flex-wrap items-center gap-3 rounded-xl border bg-card/95 px-5 py-3 shadow-xs backdrop-blur">
        <Button type="button" onClick={save} disabled={saving || !dirty}>
          {saving ? <Loader2 className="animate-spin" /> : null} Save
        </Button>
        <span className="text-sm text-muted-foreground" role="status">
          {dirty ? "Unsaved changes." : "All changes saved."}
        </span>
        {saveError && (
          <span role="alert" className="text-sm font-medium text-destructive">
            {saveError}
          </span>
        )}
      </div>

      {data.problems.length > 0 && (
        <ul className="space-y-1 text-sm text-warn">
          {data.problems.map((p) => (
            <li key={p}>{p}</li>
          ))}
        </ul>
      )}

      <section className="space-y-3 rounded-xl border bg-card p-5 shadow-xs">
        <h3 className="text-base font-semibold text-foreground">
          Recently sent
        </h3>
        <Deliveries rows={data.deliveries} />
      </section>
    </>
  );
}
