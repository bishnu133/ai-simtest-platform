"use client";

import { Plus, Trash2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import type { CompareOptions, CompareTarget } from "@/lib/engine/types";

export const MAX_TARGETS = 3;

export const DEFAULT_COMPARE: CompareOptions = {
  baseline_name: "Current",
  targets: [{ name: "Challenger", bot_endpoint: "", bot_api_key: "" }],
};

const sameOrigin = (a: string, b: string) => {
  try {
    const x = new URL(a);
    const y = new URL(b);
    return x.origin === y.origin;
  } catch {
    return false;
  }
};

/** The main bot's name, and up to three more bots to run the same customers against. */
export function CompareTargets({
  value,
  onChange,
  mainEndpoint,
  mainModel,
}: {
  value: CompareOptions;
  onChange: (v: CompareOptions) => void;
  mainEndpoint: string;
  mainModel: string;
}) {
  const setTarget = (i: number, patch: Partial<CompareTarget>) =>
    onChange({ ...value, targets: value.targets.map((t, j) => (j === i ? { ...t, ...patch } : t)) });
  const add = () =>
    onChange({
      ...value,
      targets: [...value.targets, { name: `Challenger ${value.targets.length + 1}`, bot_endpoint: "", bot_api_key: "" }],
    });
  const remove = (i: number) => onChange({ ...value, targets: value.targets.filter((_, j) => j !== i) });

  return (
    <div className="space-y-4">
      <div className="max-w-sm space-y-2">
        <Label htmlFor="baseline-name" className="text-sm font-semibold">
          Name for your bot <span className="font-normal text-muted-foreground">(the endpoint under Your bot)</span>
        </Label>
        <Input
          id="baseline-name"
          className="h-10"
          maxLength={60}
          value={value.baseline_name}
          onChange={(e) => onChange({ ...value, baseline_name: e.target.value })}
        />
      </div>

      <ol className="space-y-3">
        {value.targets.map((t, i) => {
          const inheritsKey = !t.bot_api_key && mainEndpoint && t.bot_endpoint && sameOrigin(t.bot_endpoint, mainEndpoint);
          return (
            <li key={i} className="space-y-3 rounded-lg border p-4">
              <div className="flex items-center justify-between gap-2">
                <span className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">Bot {i + 2}</span>
                {value.targets.length > 1 && (
                  <Button type="button" variant="ghost" size="xs" onClick={() => remove(i)} aria-label={`Remove ${t.name || `bot ${i + 2}`}`}>
                    <Trash2 /> Remove
                  </Button>
                )}
              </div>
              <div className="grid gap-3 md:grid-cols-[minmax(0,12rem)_minmax(0,1fr)]">
                <div className="space-y-1.5">
                  <Label htmlFor={`target-name-${i}`} className="text-xs font-medium">
                    Name
                  </Label>
                  <Input
                    id={`target-name-${i}`}
                    className="h-10"
                    maxLength={60}
                    placeholder="GPT-4.1 prompt v2"
                    value={t.name}
                    onChange={(e) => setTarget(i, { name: e.target.value })}
                  />
                </div>
                <div className="space-y-1.5">
                  <Label htmlFor={`target-endpoint-${i}`} className="text-xs font-medium">
                    Endpoint
                  </Label>
                  <Input
                    id={`target-endpoint-${i}`}
                    type="url"
                    className="h-10"
                    placeholder="https://staging.your-bot.com/v1/chat/completions"
                    value={t.bot_endpoint}
                    onChange={(e) => setTarget(i, { bot_endpoint: e.target.value })}
                  />
                </div>
                <div className="space-y-1.5">
                  <Label htmlFor={`target-model-${i}`} className="text-xs font-medium">
                    Model <span className="font-normal text-muted-foreground">(optional)</span>
                  </Label>
                  <Input
                    id={`target-model-${i}`}
                    className="h-10 font-mono text-sm"
                    placeholder={mainModel || "gpt-4.1-mini"}
                    value={t.model ?? ""}
                    onChange={(e) => setTarget(i, { model: e.target.value })}
                  />
                </div>
                <div className="space-y-1.5">
                  <Label htmlFor={`target-key-${i}`} className="text-xs font-medium">
                    API key <span className="font-normal text-muted-foreground">(optional)</span>
                  </Label>
                  <Input
                    id={`target-key-${i}`}
                    type="password"
                    autoComplete="off"
                    className="h-10"
                    placeholder="Sent only to the engine, never stored"
                    value={t.bot_api_key ?? ""}
                    onChange={(e) => setTarget(i, { bot_api_key: e.target.value })}
                  />
                  <p className="text-xs text-muted-foreground">
                    {inheritsKey
                      ? "Blank: uses your bot's key, because this is the same server."
                      : "Blank: no key. Your bot's key is only reused for an endpoint on the same server."}
                  </p>
                </div>
              </div>
            </li>
          );
        })}
      </ol>

      {value.targets.length < MAX_TARGETS && (
        <Button type="button" variant="outline" size="sm" onClick={add}>
          <Plus /> Add another bot
        </Button>
      )}
      <p className="text-xs text-muted-foreground">
        Every bot meets the same approved customers and is judged the same way, so results pair up customer by customer. To
        compare models behind one endpoint, use the same endpoint with a different model name (sent as &quot;model&quot; in
        the request). Blank model: the same as your bot.
      </p>
    </div>
  );
}
