"use client";

import { useRef, useState } from "react";
import { FileJson, Info, Upload, X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import type { RagOptions, RagToolDefinition } from "@/lib/engine/types";
import { Segmented } from "./controls";

export const DEFAULT_RAG: RagOptions = {
  speed: "fast",
  use_llm: false,
  tool_definitions: [],
  require_citations: false,
};

// The keys the engine reads beside a reply, top level or next to it
const EVIDENCE_KEYS = ["sources", "citations", "retrieved_documents", "source_documents", "tool_calls", "function_calls"];

/** Parse a tool definitions file: a JSON list, or {"tools": [...]}. */
export function parseToolDefinitions(text: string): RagToolDefinition[] {
  const data = JSON.parse(text);
  const list = Array.isArray(data) ? data : Array.isArray(data?.tools) ? data.tools : null;
  if (!list) throw new Error("Expected a JSON list of tools, or an object with a \"tools\" list.");
  if (list.length > 100) throw new Error("At most 100 tool definitions.");
  return list.map((d: unknown, i: number) => {
    if (!d || typeof d !== "object" || !String((d as { name?: unknown }).name ?? "").trim()) {
      throw new Error(`Tool ${i + 1} has no name.`);
    }
    return d as RagToolDefinition;
  });
}

/** RAG and tool evaluation settings: what is checked, and how. */
export function RagSettings({
  value,
  onChange,
  onError,
}: {
  value: RagOptions;
  onChange: (v: RagOptions) => void;
  onError: (message: string) => void;
}) {
  const fileRef = useRef<HTMLInputElement>(null);
  const [fileName, setFileName] = useState("");

  const load = async (file: File | undefined) => {
    if (!file) return;
    try {
      const tools = parseToolDefinitions(await file.text());
      onChange({ ...value, tool_definitions: tools });
      setFileName(file.name);
      onError("");
    } catch (e) {
      onError(`Tool definitions: ${e instanceof Error ? e.message : "could not read the file."}`);
    }
  };

  return (
    <div className="space-y-5">
      <div className="flex items-start gap-2 rounded-lg border bg-muted/30 p-3 text-sm">
        <Info className="mt-0.5 h-4 w-4 shrink-0 text-primary" aria-hidden />
        <p className="text-muted-foreground">
          Every reply is checked against what your bot retrieved and the tools it called. Return them in the same JSON
          response as the reply, under{" "}
          {EVIDENCE_KEYS.map((k, i) => (
            <span key={k}>
              {i > 0 && (i === EVIDENCE_KEYS.length - 1 ? " or " : ", ")}
              <code className="rounded bg-muted px-1 font-mono text-xs text-foreground">{k}</code>
            </span>
          ))}
          . They are kept with each reply for this run only. Without them, replies are checked against your documentation,
          and tool metrics are not scored.
        </p>
      </div>

      <div className="space-y-2">
        <p className="text-sm font-semibold text-foreground">How to check</p>
        <Segmented
          label="How to check"
          value={value.use_llm ? "model" : "rules"}
          onChange={(v) => onChange({ ...value, use_llm: v === "model", speed: v === "model" ? "standard" : "fast" })}
          options={[
            { id: "rules", label: "Rules only (free)" },
            { id: "model", label: "Rules + AI reading" },
          ]}
        />
        <p className="text-xs text-muted-foreground">
          {value.use_llm
            ? "Adds an AI reading where rules cannot judge: whether a reply really answers, and how conflicting sources were handled. Several AI calls per reply, on your judge model."
            : "Checks figures against sources, citations, tool errors, retries and permissions. An off-topic reply is caught; a subtly weak one needs the AI reading."}
        </p>
      </div>

      <div className="space-y-2">
        <p className="text-sm font-semibold text-foreground">
          Tool definitions <span className="font-normal text-muted-foreground">(optional)</span>
        </p>
        <p className="text-xs text-muted-foreground">
          A JSON list of your bot&apos;s tools: <code className="font-mono">name</code>,{" "}
          <code className="font-mono">required_params</code>, <code className="font-mono">requires_permission</code>,{" "}
          <code className="font-mono">has_side_effects</code>. Lets the run check parameters, order and permissions.
        </p>
        <input
          ref={fileRef}
          type="file"
          accept=".json,application/json"
          className="sr-only"
          aria-label="Tool definitions file"
          onChange={(e) => {
            load(e.target.files?.[0]);
            e.target.value = "";
          }}
        />
        {value.tool_definitions.length ? (
          <div className="flex flex-wrap items-center gap-2 rounded-lg border p-2 text-sm">
            <FileJson className="h-4 w-4 text-primary" aria-hidden />
            <span className="font-medium text-foreground">{fileName || "Tool definitions"}</span>
            <span className="text-muted-foreground">
              · {value.tool_definitions.length} tool{value.tool_definitions.length === 1 ? "" : "s"}:{" "}
              {value.tool_definitions
                .slice(0, 5)
                .map((t) => t.name)
                .join(", ")}
              {value.tool_definitions.length > 5 ? "…" : ""}
            </span>
            <Button
              type="button"
              size="sm"
              variant="ghost"
              className="ml-auto"
              aria-label="Remove tool definitions"
              onClick={() => {
                onChange({ ...value, tool_definitions: [] });
                setFileName("");
              }}
            >
              <X />
            </Button>
          </div>
        ) : (
          <Button type="button" size="sm" variant="outline" onClick={() => fileRef.current?.click()}>
            <Upload /> Upload tool definitions
          </Button>
        )}
      </div>

      <label className="flex items-start gap-3 text-sm">
        <Checkbox
          checked={value.require_citations}
          onCheckedChange={(c) => onChange({ ...value, require_citations: c === true })}
          className="mt-0.5"
        />
        <span>
          <span className="font-medium text-foreground">Every factual answer must cite a source</span>
          <span className="block text-xs text-muted-foreground">
            Off: citations are checked only when the bot gives them. On: a reply stating facts without a citation fails.
          </span>
        </span>
      </label>
    </div>
  );
}
