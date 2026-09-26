"use client";

import { useEffect, useRef, useState, useSyncExternalStore, type DragEvent, type FormEvent } from "react";
import { useRouter } from "next/navigation";
import { useQueryClient } from "@tanstack/react-query";
import { ArrowRight, ChevronDown, FileText, Loader2, UploadCloud, X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { readSuggestedQualityThreshold, subscribeCalibration } from "@/lib/calibration";
import { engine, EngineError } from "@/lib/engine/client";
import { Checkbox } from "@/components/ui/checkbox";
import type { EngineOptions, RequestFormat } from "@/lib/engine/types";

// Design options, extended downward so a live demo can run small and fast
const PERSONA_OPTIONS = ["3", "5", "10", "20", "30", "40", "50", "75", "100"];
const TURN_OPTIONS = ["2", "3", "5", "10", "15", "20", "30", "40", "50"];
const PARALLEL_OPTIONS = ["1", "2", "3", "5", "10", "20"];
const MAX_FILE_BYTES = 2 * 1024 * 1024;

type SelectFieldProps = {
  id: string;
  label: string;
  value: string;
  options: string[];
  onChange: (value: string) => void;
};

function SelectField({ id, label, value, options, onChange }: SelectFieldProps) {
  return (
    <div className="space-y-3">
      <Label htmlFor={id} className="text-sm font-semibold">
        {label}
      </Label>
      <Select value={value} onValueChange={onChange}>
        <SelectTrigger id={id} className="h-11 w-full">
          <SelectValue />
        </SelectTrigger>
        <SelectContent>
          {options.map((opt) => (
            <SelectItem key={opt} value={opt}>
              {opt}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
    </div>
  );
}

// From this minimum up, users are held in the chat long past their goal
const LONG_MIN_TURNS = 8;

// The engine's default judges and weights (AutonomousOrchestrator._build_simulation_config)
const DEFAULT_WEIGHTS: [string, number][] = [
  ["grounding", 0.3],
  ["safety", 0.3],
  ["quality", 0.2],
  ["relevance", 0.2],
];

export function SetupForm({ embedded = false }: { embedded?: boolean }) {
  const router = useRouter();
  const queryClient = useQueryClient();
  const fileInputRef = useRef<HTMLInputElement>(null);

  const [name, setName] = useState("");
  const [endpoint, setEndpoint] = useState("");
  const [apiKey, setApiKey] = useState("");
  const [format, setFormat] = useState<RequestFormat>("openai");
  const [responsePath, setResponsePath] = useState("choices.0.message.content");
  const [personas, setPersonas] = useState("5");
  const [minTurns, setMinTurns] = useState("3");
  const [maxTurns, setMaxTurns] = useState("5");
  const [parallel, setParallel] = useState("2");
  const [contextFilename, setContextFilename] = useState("");
  const [contextContent, setContextContent] = useState("");
  const [showAdvanced, setShowAdvanced] = useState(false);
  const [options, setOptions] = useState<EngineOptions | null>(null);
  const [captureHeaders, setCaptureHeaders] = useState("");
  const [policy, setPolicy] = useState("");
  const [workflowMode, setWorkflowMode] = useState<"auto" | "choose" | "off">("auto");
  const [workflows, setWorkflows] = useState<string[]>([]);
  const [trackCost, setTrackCost] = useState(true);
  const [guardrailLlm, setGuardrailLlm] = useState(false);
  const [relevanceLlm, setRelevanceLlm] = useState(false);
  const [versionHeader, setVersionHeader] = useState("");
  const [infoUrl, setInfoUrl] = useState("");
  const [weights, setWeights] = useState<Record<string, string>>({});
  const [qualityThreshold, setQualityThreshold] = useState("");
  const [turnThreshold, setTurnThreshold] = useState("");
  const suggested = useSyncExternalStore(subscribeCalibration, readSuggestedQualityThreshold, () => null);

  useEffect(() => {
    // Built-in workflows/policies for the evaluation options; the form works without them
    engine.getOptions().then(setOptions).catch(() => setOptions(null));
  }, []);
  const [dragActive, setDragActive] = useState(false);
  const [error, setError] = useState("");
  const [submitting, setSubmitting] = useState(false);

  const handleFile = (file: File) => {
    if (!/\.(md|markdown|txt)$/i.test(file.name)) {
      setError("Please upload a Markdown (.md) file.");
      return;
    }
    if (file.size > MAX_FILE_BYTES) {
      setError("Context file must be 2 MB or smaller.");
      return;
    }
    const reader = new FileReader();
    reader.onload = (e) => {
      setContextFilename(file.name);
      setContextContent(String(e.target?.result ?? ""));
      setError("");
    };
    reader.onerror = () => setError("Could not read that file.");
    reader.readAsText(file);
  };

  const handleDrag = (e: DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    setDragActive(e.type === "dragenter" || e.type === "dragover");
  };

  const handleDrop = (e: DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    setDragActive(false);
    const file = e.dataTransfer.files?.[0];
    if (file) handleFile(file);
  };

  const handleSubmit = async (e: FormEvent) => {
    e.preventDefault();
    if (!/^https?:\/\/\S+$/.test(endpoint.trim())) {
      setError("Endpoint must be a valid HTTP/HTTPS URL.");
      return;
    }
    if (Number(minTurns) > Number(maxTurns)) {
      setError("Minimum turns cannot exceed maximum turns.");
      return;
    }
    if (workflowMode === "choose" && workflows.length === 0) {
      setError("Pick at least one workflow, or switch workflows to auto-detect.");
      return;
    }
    if (!contextContent.trim()) {
      setError("Upload a Markdown file describing your bot — the engine derives the test plan from it.");
      return;
    }

    setError("");
    setSubmitting(true);
    try {
      const sim = await engine.createSimulation({
        name: name.trim() || contextFilename.replace(/\.\w+$/, "") || "Web Simulation",
        bot_endpoint: endpoint.trim(),
        bot_api_key: apiKey.trim() || undefined,
        bot_request_format: format,
        bot_response_path: responsePath.trim() || "choices.0.message.content",
        documentation: contextContent,
        documentation_filename: contextFilename,
        num_personas: Number(personas),
        min_turns: Number(minTurns),
        max_turns: Number(maxTurns),
        max_parallel: Number(parallel),
        capture_response_headers: captureHeaders
          .split(",")
          .map((h) => h.trim())
          .filter(Boolean),
        policy: policy || null,
        workflows: workflowMode === "choose" ? workflows : [],
        no_workflow: workflowMode === "off",
        track_cost: trackCost,
        guardrail_llm: guardrailLlm,
        relevance_llm: relevanceLlm,
        quality_threshold: qualityThreshold.trim() === "" ? null : Number(qualityThreshold),
        turn_pass_threshold: turnThreshold.trim() === "" ? null : Number(turnThreshold),
        bot_version_header: versionHeader.trim() || null,
        bot_info_url: infoUrl.trim() || null,
        judge_weights: Object.fromEntries(
          Object.entries(weights)
            .filter(([, v]) => v.trim() !== "")
            .map(([k, v]) => [k, Number(v)]),
        ),
      });
      queryClient.invalidateQueries({ queryKey: ["runs"] });
      router.push(`/simulations/${sim.simulation_id}`);
    } catch (err) {
      setError(err instanceof EngineError ? err.message : "Could not start the simulation.");
      setSubmitting(false);
    }
  };

  return (
    <div className={embedded ? "max-w-4xl" : "max-w-3xl mx-auto py-8"}>
      {!embedded && (
        <div className="mb-10 text-center animate-in fade-in slide-in-from-bottom-4 duration-500">
          <h1 className="text-3xl md:text-4xl font-bold tracking-tight text-foreground mb-3">Welcome to AI SimTest</h1>
          <p className="text-lg md:text-xl text-muted-foreground">The future of QA Automation for AI conversations</p>
        </div>
      )}

      <Card
        className={`shadow-xs border-muted/60 py-0 gap-0 ${embedded ? "" : "animate-in fade-in slide-in-from-bottom-6 duration-700 delay-150 fill-mode-both"}`}
      >
        <CardHeader className="bg-muted/30 border-b py-6">
          <CardTitle>Simulation Configuration</CardTitle>
          <CardDescription>Set up the parameters for your chatbot testing simulation.</CardDescription>
        </CardHeader>
        <CardContent className="p-6 md:p-8">
          <form onSubmit={handleSubmit} className="space-y-8" noValidate>
            <div className="space-y-3">
              <Label htmlFor="endpoint" className="text-base font-semibold">
                Bot Endpoint
              </Label>
              <Input
                id="endpoint"
                type="url"
                placeholder="https://your-bot.com/api/chat"
                className="h-12 text-base"
                value={endpoint}
                onChange={(e) => setEndpoint(e.target.value)}
                required
              />
            </div>

            <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
              <SelectField id="personas" label="Personas to Generate" value={personas} options={PERSONA_OPTIONS} onChange={setPersonas} />
              <SelectField id="parallel" label="Parallel Conversations" value={parallel} options={PARALLEL_OPTIONS} onChange={setParallel} />
              <SelectField id="min-turns" label="Minimum Turns" value={minTurns} options={TURN_OPTIONS} onChange={setMinTurns} />
              <SelectField id="max-turns" label="Maximum Turns" value={maxTurns} options={TURN_OPTIONS} onChange={setMaxTurns} />
              {Number(minTurns) >= LONG_MIN_TURNS && (
                <p role="note" className="md:col-span-2 rounded-md border border-warn/30 bg-warn/5 px-3 py-2 text-sm text-foreground">
                  Every conversation will run at least {minTurns} turns. Simulated users are told not to wrap up before
                  then, so once their goal is met they invent follow-up questions, and those later replies usually fail.
                  To measure how conversations end naturally, keep the minimum low and raise only the maximum.
                </p>
              )}
            </div>

            <div className="space-y-3 pt-4 border-t">
              <Label className="text-base font-semibold">Upload Context</Label>
              <p className="text-sm text-muted-foreground">
                Provide domain knowledge, system prompts, or rules as a Markdown file.
              </p>

              {!contextFilename ? (
                <button
                  type="button"
                  className={`w-full border-2 border-dashed rounded-xl p-10 text-center transition-colors flex flex-col items-center justify-center gap-3 ${
                    dragActive
                      ? "border-primary bg-primary/5"
                      : "border-muted-foreground/25 hover:border-primary/50 hover:bg-muted/20"
                  }`}
                  onDragEnter={handleDrag}
                  onDragLeave={handleDrag}
                  onDragOver={handleDrag}
                  onDrop={handleDrop}
                  onClick={() => fileInputRef.current?.click()}
                >
                  <span className="p-4 bg-primary/10 rounded-full">
                    <UploadCloud className="w-8 h-8 text-primary" />
                  </span>
                  <span>
                    <span className="block font-medium text-foreground">Click to upload or drag and drop</span>
                    <span className="block text-sm text-muted-foreground mt-1">Markdown (.md) files only</span>
                  </span>
                </button>
              ) : (
                <div className="flex items-center justify-between p-4 border rounded-xl bg-muted/30">
                  <div className="flex items-center gap-3 min-w-0">
                    <div className="p-2 bg-primary/10 rounded-lg">
                      <FileText className="w-6 h-6 text-primary" />
                    </div>
                    <div className="min-w-0">
                      <p className="font-medium truncate">{contextFilename}</p>
                      <p className="text-xs text-muted-foreground">
                        Markdown file loaded · {contextContent.length.toLocaleString()} characters
                      </p>
                    </div>
                  </div>
                  <Button
                    type="button"
                    variant="ghost"
                    size="icon"
                    aria-label="Remove file"
                    onClick={() => {
                      setContextFilename("");
                      setContextContent("");
                    }}
                  >
                    <X className="w-5 h-5 text-muted-foreground" />
                  </Button>
                </div>
              )}
              <input
                type="file"
                className="hidden"
                accept=".md,.markdown,.txt,text/markdown"
                ref={fileInputRef}
                onChange={(e) => {
                  const file = e.target.files?.[0];
                  if (file) handleFile(file);
                  e.target.value = "";
                }}
              />
            </div>

            <div className="border-t pt-4">
              <button
                type="button"
                className="flex items-center gap-2 text-sm font-medium text-muted-foreground hover:text-foreground"
                onClick={() => setShowAdvanced((v) => !v)}
                aria-expanded={showAdvanced}
              >
                <ChevronDown className={`w-4 h-4 transition-transform ${showAdvanced ? "rotate-180" : ""}`} />
                Advanced: run name, bot authentication, request format &amp; evaluation
              </button>

              {showAdvanced && (
                <div className="grid grid-cols-1 md:grid-cols-2 gap-6 pt-6">
                  <div className="space-y-3">
                    <Label htmlFor="name" className="text-sm font-semibold">Run Name</Label>
                    <Input id="name" className="h-11" placeholder="Banking bot – release 2.3" value={name} onChange={(e) => setName(e.target.value)} />
                  </div>
                  <div className="space-y-3">
                    <Label htmlFor="api-key" className="text-sm font-semibold">Bot API Key (optional)</Label>
                    <Input
                      id="api-key"
                      type="password"
                      autoComplete="off"
                      className="h-11"
                      placeholder="Sent only to the engine"
                      value={apiKey}
                      onChange={(e) => setApiKey(e.target.value)}
                    />
                  </div>
                  <SelectField
                    id="format"
                    label="Request Format"
                    value={format}
                    options={["openai", "anthropic", "custom"]}
                    onChange={(v) => setFormat(v as RequestFormat)}
                  />
                  <div className="space-y-3">
                    <Label htmlFor="response-path" className="text-sm font-semibold">Response Path</Label>
                    <Input id="response-path" className="h-11 font-mono text-sm" value={responsePath} onChange={(e) => setResponsePath(e.target.value)} />
                  </div>

                  <div className="md:col-span-2 border-t pt-6">
                    <h3 className="text-sm font-semibold text-foreground">Evaluation</h3>
                    <p className="text-sm text-muted-foreground">Extra checks run after the simulation, same as the CLI.</p>
                  </div>
                  <div className="space-y-3">
                    <Label htmlFor="capture-headers" className="text-sm font-semibold">Response Headers to Capture</Label>
                    <Input
                      id="capture-headers"
                      className="h-11 font-mono text-sm"
                      placeholder="x-simbank-*, x-request-id"
                      value={captureHeaders}
                      onChange={(e) => setCaptureHeaders(e.target.value)}
                    />
                    <p className="text-xs text-muted-foreground">
                      Comma-separated; <code>*</code> allowed. Adds bot-label failure rates and judge calibration to the report.
                    </p>
                  </div>
                  <div className="space-y-3">
                    <Label htmlFor="policy" className="text-sm font-semibold">Compliance Policy</Label>
                    <select
                      id="policy"
                      className="h-11 w-full rounded-md border border-input bg-background px-3 text-sm"
                      value={policy}
                      onChange={(e) => setPolicy(e.target.value)}
                    >
                      <option value="">None</option>
                      {options?.policies.map((p) => (
                        <option key={p.id} value={p.id}>
                          {p.name}
                        </option>
                      ))}
                    </select>
                    <p className="text-xs text-muted-foreground">Scores the run against a built-in policy-as-code template.</p>
                  </div>
                  <fieldset className="space-y-3 md:col-span-2">
                    <legend className="text-sm font-semibold">Workflow Judging</legend>
                    <div className="flex flex-wrap gap-4 text-sm">
                      {(
                        [
                          ["auto", "Auto-detect from your docs"],
                          ["choose", "Choose workflows"],
                          ["off", "Off"],
                        ] as const
                      ).map(([value, label]) => (
                        <label key={value} className="inline-flex items-center gap-2">
                          <input
                            type="radio"
                            name="workflow-mode"
                            value={value}
                            checked={workflowMode === value}
                            onChange={() => setWorkflowMode(value)}
                            className="accent-[hsl(var(--primary))]"
                          />
                          {label}
                        </label>
                      ))}
                    </div>
                    {workflowMode === "choose" && (
                      <div className="grid gap-2 rounded-lg border p-3 sm:grid-cols-2">
                        {options?.workflows.length ? (
                          options.workflows.map((w) => (
                            <label key={w.id} className="flex items-center gap-2 text-sm">
                              <Checkbox
                                checked={workflows.includes(w.id)}
                                onCheckedChange={(on) =>
                                  setWorkflows((cur) => (on ? [...cur, w.id] : cur.filter((x) => x !== w.id)))
                                }
                              />
                              {w.name}
                              <span className="text-xs text-muted-foreground">{w.domain}</span>
                            </label>
                          ))
                        ) : (
                          <p className="text-sm text-muted-foreground">Couldn&apos;t load workflows from the engine.</p>
                        )}
                      </div>
                    )}
                  </fieldset>
                  <label className="flex items-center gap-2 text-sm md:col-span-2">
                    <Checkbox checked={trackCost} onCheckedChange={(on) => setTrackCost(on === true)} />
                    Track estimated LLM cost for this run
                  </label>
                  <div className="space-y-1 md:col-span-2">
                    <label className="flex items-center gap-2 text-sm">
                      <Checkbox checked={guardrailLlm} onCheckedChange={(on) => setGuardrailLlm(on === true)} />
                      Check every guardrail rule with an LLM
                    </label>
                    <p className="pl-6 text-xs text-muted-foreground">
                      Without it, rules no pattern covers are listed as not checked. Adds one model call per bot reply.
                    </p>
                  </div>
                  <div className="space-y-1 md:col-span-2">
                    <label className="flex items-center gap-2 text-sm">
                      <Checkbox checked={relevanceLlm} onCheckedChange={(on) => setRelevanceLlm(on === true)} />
                      Judge relevance with an LLM
                    </label>
                    <p className="pl-6 text-xs text-muted-foreground">
                      By default relevance compares wording, which cannot tell whether the question was answered. Adds one model call per bot reply.
                    </p>
                  </div>

                  <div className="md:col-span-2 border-t pt-6">
                    <h3 className="text-sm font-semibold text-foreground">Bot build</h3>
                    <p className="text-sm text-muted-foreground">
                      Records which build of the bot was tested, so a comparison with the previous run can tell a new build from a new test.
                    </p>
                  </div>
                  <div className="space-y-3">
                    <Label htmlFor="version-header" className="text-sm font-semibold">Version Header</Label>
                    <Input
                      id="version-header"
                      className="h-11 font-mono text-sm"
                      placeholder="x-bot-version"
                      value={versionHeader}
                      onChange={(e) => setVersionHeader(e.target.value)}
                    />
                    <p className="text-xs text-muted-foreground">A response header that carries the build. One header name.</p>
                  </div>
                  <div className="space-y-3">
                    <Label htmlFor="info-url" className="text-sm font-semibold">Bot Info URL</Label>
                    <Input
                      id="info-url"
                      type="url"
                      className="h-11 font-mono text-sm"
                      placeholder="https://bot.example.com/version"
                      value={infoUrl}
                      onChange={(e) => setInfoUrl(e.target.value)}
                    />
                    <p className="text-xs text-muted-foreground">
                      Used when there is no header: the engine fetches this page at the start and end of the run and records a hash of it.
                    </p>
                  </div>

                  <fieldset className="space-y-3 md:col-span-2 border-t pt-6">
                    <legend className="sr-only">Judge weights</legend>
                    <div>
                      <h3 className="text-sm font-semibold text-foreground">Judge Weights and Pass Marks</h3>
                      <p className="text-sm text-muted-foreground">
                        How much each judge counts towards a reply&apos;s score. Leave blank to keep the default.
                      </p>
                    </div>
                    <div className="grid grid-cols-2 gap-4 sm:grid-cols-4">
                      {DEFAULT_WEIGHTS.map(([judge, fallback]) => (
                        <div key={judge} className="space-y-1.5">
                          <Label htmlFor={`weight-${judge}`} className="text-xs font-medium capitalize">
                            {judge}
                          </Label>
                          <Input
                            id={`weight-${judge}`}
                            type="number"
                            inputMode="decimal"
                            min={0}
                            max={10}
                            step={0.05}
                            className="h-10 tabular-nums"
                            placeholder={fallback.toFixed(2)}
                            value={weights[judge] ?? ""}
                            onChange={(e) => setWeights((w) => ({ ...w, [judge]: e.target.value }))}
                          />
                        </div>
                      ))}
                    </div>
                    <div className="grid grid-cols-1 gap-4 pt-2 sm:grid-cols-2">
                      <div className="space-y-1.5">
                        <Label htmlFor="quality-threshold" className="text-xs font-medium">
                          Quality pass mark
                        </Label>
                        <Input
                          id="quality-threshold"
                          type="number"
                          inputMode="decimal"
                          min={0}
                          max={1}
                          step={0.05}
                          className="h-10 tabular-nums"
                          placeholder="0.60"
                          value={qualityThreshold}
                          onChange={(e) => setQualityThreshold(e.target.value)}
                        />
                        <p className="text-xs text-muted-foreground">
                          The score the quality judge needs to pass a reply.
                          {suggested !== null && qualityThreshold !== String(suggested) && (
                            <>
                              {" "}
                              <button
                                type="button"
                                className="font-medium text-primary underline-offset-4 hover:underline"
                                onClick={() => setQualityThreshold(String(suggested))}
                              >
                                Use {suggested.toFixed(2)} from your Judge review
                              </button>
                            </>
                          )}
                        </p>
                      </div>
                      <div className="space-y-1.5">
                        <Label htmlFor="turn-threshold" className="text-xs font-medium">
                          Reply pass mark
                        </Label>
                        <Input
                          id="turn-threshold"
                          type="number"
                          inputMode="decimal"
                          min={0}
                          max={1}
                          step={0.05}
                          className="h-10 tabular-nums"
                          placeholder="0.70"
                          value={turnThreshold}
                          onChange={(e) => setTurnThreshold(e.target.value)}
                        />
                        <p className="text-xs text-muted-foreground">
                          The weighted mean of all judges a reply needs to be labelled PASS.
                        </p>
                      </div>
                    </div>
                    <p className="text-xs text-muted-foreground">
                      Pass marks and weights are recorded with the run, so a run judged differently is not compared with the previous one as if nothing changed.
                    </p>
                  </fieldset>
                </div>
              )}
            </div>

            <div className="pt-2 flex flex-col sm:flex-row items-center justify-end gap-4">
              {error && (
                <p role="alert" className="text-destructive text-sm font-medium sm:mr-auto">
                  {error}
                </p>
              )}
              <Button
                type="submit"
                size="lg"
                className="w-full sm:w-auto h-12 px-8 text-base shadow-lg shadow-primary/20"
                disabled={!endpoint || !contextFilename || submitting}
              >
                {submitting ? <Loader2 className="w-5 h-5 animate-spin" /> : null}
                Start AI Simulation Setup
                {!submitting && <ArrowRight className="w-5 h-5" />}
              </Button>
            </div>
          </form>
        </CardContent>
      </Card>
    </div>
  );
}
