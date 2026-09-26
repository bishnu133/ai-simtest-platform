"use client";

import { useEffect, useRef, useState, useSyncExternalStore, type DragEvent, type FormEvent, type ReactNode } from "react";
import { useRouter } from "next/navigation";
import { useQueryClient } from "@tanstack/react-query";
import {
  ArrowRight,
  ChevronDown,
  FileText,
  Hand,
  ListChecks,
  Loader2,
  Radar,
  Sparkles,
  UploadCloud,
  X,
  Zap,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { readSuggestedQualityThreshold, subscribeCalibration } from "@/lib/calibration";
import { engine, EngineError } from "@/lib/engine/client";
import type { CompareOptions, EngineOptions, ReplayOptions, RequestFormat, RunMode, StressOptions } from "@/lib/engine/types";
import { ChoiceCards, ListEditor, Section, Segmented, lines } from "./setup/controls";
import { CompareTargets, DEFAULT_COMPARE } from "./setup/compare-targets";
import { DEFAULT_REPLAY, ReplaySource, countConversations } from "./setup/replay-source";
import { DEFAULT_STRESS, ScenarioPicker, StressSettings } from "./setup/test-focus";
import { MODES, REVIEW_STEPS, SIZES, STRICTNESS, estimate, money, type SizeId, type StrictnessId } from "./setup/presets";

const PERSONA_OPTIONS = ["3", "5", "10", "20", "30", "40", "50", "75", "100"];
const TURN_OPTIONS = ["2", "3", "5", "10", "15", "20", "30", "40", "50"];
const PARALLEL_OPTIONS = ["1", "2", "3", "5", "10", "20"];
const MAX_FILE_BYTES = 2 * 1024 * 1024;
// From this minimum up, users are held in the chat long past their goal
const LONG_MIN_TURNS = 8;
// The engine's default judges and weights (AutonomousOrchestrator._build_simulation_config)
const DEFAULT_WEIGHTS: [string, number][] = [
  ["grounding", 0.3],
  ["safety", 0.3],
  ["quality", 0.2],
  ["relevance", 0.2],
];
const MODE_ICONS: Record<RunMode, ReactNode> = {
  partial: <FileText className="h-4 w-4" />,
  auto: <Radar className="h-4 w-4" />,
  manual: <ListChecks className="h-4 w-4" />,
};

function SelectField({
  id,
  label,
  value,
  options,
  onChange,
}: {
  id: string;
  label: string;
  value: string;
  options: string[];
  onChange: (value: string) => void;
}) {
  return (
    <div className="space-y-2">
      <Label htmlFor={id} className="text-sm font-semibold">
        {label}
      </Label>
      <Select value={value} onValueChange={onChange}>
        <SelectTrigger id={id} className="h-10 w-full">
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

function Toggle({
  checked,
  onChange,
  title,
  body,
}: {
  checked: boolean;
  onChange: (v: boolean) => void;
  title: string;
  body: string;
}) {
  return (
    <label className="flex cursor-pointer gap-3 rounded-lg border p-3 hover:bg-muted/40">
      <Checkbox checked={checked} onCheckedChange={(on) => onChange(on === true)} className="mt-0.5" />
      <span>
        <span className="block text-sm font-medium text-foreground">{title}</span>
        <span className="block text-xs text-muted-foreground">{body}</span>
      </span>
    </label>
  );
}

function Disclosure({ label, children }: { label: string; children: ReactNode }) {
  const [open, setOpen] = useState(false);
  return (
    <div className="rounded-lg border">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        className="flex w-full items-center justify-between px-4 py-3 text-sm font-medium text-foreground hover:bg-muted/40"
      >
        {label}
        <ChevronDown className={`h-4 w-4 text-muted-foreground transition-transform ${open ? "rotate-180" : ""}`} />
      </button>
      {open && <div className="border-t p-4">{children}</div>}
    </div>
  );
}

/** What the run concentrates on, on top of the persona simulation. */
export type TestFocus = "simulation" | "scenarios" | "stress" | "replay" | "compare";

export function SetupForm({ embedded = false, focus = "simulation" }: { embedded?: boolean; focus?: TestFocus }) {
  const router = useRouter();
  const queryClient = useQueryClient();
  const fileInputRef = useRef<HTMLInputElement>(null);

  // 1. How the test is built
  const [mode, setMode] = useState<RunMode>("partial");
  const [handsOff, setHandsOff] = useState(false);
  // 2. The bot
  const [name, setName] = useState("");
  const [endpoint, setEndpoint] = useState("");
  const [apiKey, setApiKey] = useState("");
  const [infoUrl, setInfoUrl] = useState("");
  const [format, setFormat] = useState<RequestFormat>("openai");
  const [responsePath, setResponsePath] = useState("choices.0.message.content");
  const [versionHeader, setVersionHeader] = useState("");
  const [botModel, setBotModel] = useState("");
  // 3. What it knows
  const [contextFilename, setContextFilename] = useState("");
  const [contextContent, setContextContent] = useState("");
  const [criteria, setCriteria] = useState("");
  const [rules, setRules] = useState("");
  const [topics, setTopics] = useState("");
  // 4. Size
  // Scenario runs start at Standard so every scenario gets a customer
  const [size, setSize] = useState<SizeId>(focus === "scenarios" ? "standard" : "quick");
  const [personas, setPersonas] = useState("20");
  const [minTurns, setMinTurns] = useState("3");
  const [maxTurns, setMaxTurns] = useState("10");
  const [parallel, setParallel] = useState("5");
  // 5. What to check
  const [options, setOptions] = useState<EngineOptions | null>(null);
  const [workflowMode, setWorkflowMode] = useState<"auto" | "choose" | "off">("auto");
  const [workflows, setWorkflows] = useState<string[]>([]);
  const [policy, setPolicy] = useState("");
  const [guardrailLlm, setGuardrailLlm] = useState(false);
  const [relevanceLlm, setRelevanceLlm] = useState(false);
  const [trackCost, setTrackCost] = useState(true);
  // 6. Strictness (+ expert overrides)
  const [strictness, setStrictness] = useState<StrictnessId>("standard");
  const [qualityThreshold, setQualityThreshold] = useState("");
  const [turnThreshold, setTurnThreshold] = useState("");
  const [weights, setWeights] = useState<Record<string, string>>({});
  const [captureHeaders, setCaptureHeaders] = useState("");
  const suggested = useSyncExternalStore(subscribeCalibration, readSuggestedQualityThreshold, () => null);

  // Scenario packs / memory stress (test types built on the simulation)
  const [scenarioIds, setScenarioIds] = useState<string[] | null>(null);
  const [stress, setStress] = useState<StressOptions>(DEFAULT_STRESS);
  const [replay, setReplay] = useState<ReplayOptions>(DEFAULT_REPLAY);
  const [compare, setCompare] = useState<CompareOptions>(DEFAULT_COMPARE);
  // Every bot in a comparison meets the same customers
  const bots = focus === "compare" ? 1 + compare.targets.length : 1;
  const replayCount = focus === "replay" ? countConversations(replay) : null;

  const [dragActive, setDragActive] = useState(false);
  const [error, setError] = useState("");
  const [submitting, setSubmitting] = useState(false);

  useEffect(() => {
    // Built-in workflows/policies for "What to check"; the form works without them
    engine.getOptions().then(setOptions).catch(() => setOptions(null));
  }, []);

  const preset = SIZES.find((s) => s.id === size)!;
  const run =
    size === "custom"
      ? { personas: Number(personas), minTurns: Number(minTurns), maxTurns: Number(maxTurns), parallel: Number(parallel) }
      : { personas: preset.personas, minTurns: preset.minTurns, maxTurns: preset.maxTurns, parallel: Number(parallel) || preset.parallel };
  // Every scenario is picked until the tester changes it
  const chosenScenarios = scenarioIds ?? options?.scenarios?.map((s) => s.id) ?? [];
  // The engine lengthens conversations to what the scenarios and stress test need
  const scenarioTurns =
    focus === "scenarios"
      ? Math.max(0, ...(options?.scenarios ?? []).filter((s) => chosenScenarios.includes(s.id)).map((s) => s.min_turns))
      : 0;
  const lengths =
    focus === "stress"
      ? { min: stress.turns, max: Math.max(run.maxTurns, stress.turns) }
      : { min: Math.max(run.minTurns, scenarioTurns), max: Math.max(run.maxTurns, scenarioTurns) };
  const strict = STRICTNESS.find((s) => s.id === strictness)!;
  const single = estimate(run.personas, lengths.min, lengths.max, guardrailLlm, relevanceLlm);
  const cost = { replies: single.replies * bots, low: single.low * bots, high: single.high * bots };
  // Manual runs skip review of whatever the tester wrote themselves; a
  // replay has real customers, so no test plan or personas to review
  const reviewSteps =
    (mode === "manual"
      ? REVIEW_STEPS.manual - [criteria, rules, topics].filter((t) => lines(t).length > 0).length
      : REVIEW_STEPS[mode]) - (focus === "replay" ? 2 : 0);

  // Scenario, stress and replay runs get their own section after the mode;
  // a replay's size is the file, so it has no size section
  const step = focus === "simulation" ? 0 : 1;
  const afterSize = focus === "replay" ? -1 : 0;

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

  const chooseSize = (id: SizeId) => {
    setSize(id);
    const p = SIZES.find((s) => s.id === id)!;
    if (id !== "custom") setParallel(String(p.parallel));
  };

  const handleSubmit = async (e: FormEvent) => {
    e.preventDefault();
    if (!/^https?:\/\/\S+$/.test(endpoint.trim())) {
      setError("Enter your bot's endpoint: an http(s) URL.");
      return;
    }
    if (run.minTurns > run.maxTurns) {
      setError("Minimum messages cannot exceed maximum messages.");
      return;
    }
    if (workflowMode === "choose" && workflows.length === 0) {
      setError("Pick at least one workflow, or switch workflows to auto-detect.");
      return;
    }
    if (focus === "scenarios" && chosenScenarios.length === 0) {
      setError("Pick at least one scenario to run.");
      return;
    }
    if (focus === "compare") {
      const names = [compare.baseline_name, ...compare.targets.map((t) => t.name)].map((n) => n.trim().toLowerCase());
      if (names.some((n) => !n) || new Set(names).size !== names.length) {
        setError("Give every bot in the comparison its own name.");
        return;
      }
      if (compare.targets.some((t) => !/^https?:\/\/\S+$/.test(t.bot_endpoint.trim()))) {
        setError("Enter an http(s) endpoint for every bot you compare.");
        return;
      }
    }
    if (focus === "replay" && !replay.conversations.trim()) {
      setError("Upload the conversations to replay.");
      return;
    }
    if (focus === "stress" && stress.patterns.length === 0) {
      setError("Pick at least one thing for the memory test to check.");
      return;
    }
    if (mode === "partial" && !contextContent.trim()) {
      setError("Upload the Markdown your bot answers from — the test is built from it.");
      return;
    }
    if (mode === "manual" && !contextContent.trim() && lines(criteria).length === 0) {
      setError("Add at least one success criterion, or upload your bot's documentation.");
      return;
    }

    setError("");
    setSubmitting(true);
    const quality = qualityThreshold.trim() !== "" ? Number(qualityThreshold) : strict.quality;
    const reply = turnThreshold.trim() !== "" ? Number(turnThreshold) : strict.reply;
    try {
      const sim = await engine.createSimulation({
        name: name.trim() || contextFilename.replace(/\.\w+$/, "") || "Web Simulation",
        mode,
        auto_approve: handsOff,
        bot_endpoint: endpoint.trim(),
        bot_api_key: apiKey.trim() || undefined,
        bot_request_format: format,
        bot_response_path: responsePath.trim() || "choices.0.message.content",
        documentation: mode === "auto" ? "" : contextContent,
        documentation_filename: contextFilename || "context.md",
        success_criteria: mode === "manual" ? lines(criteria) : [],
        guardrail_rules: mode === "manual" ? lines(rules) : [],
        topics: mode === "manual" ? lines(topics) : [],
        num_personas: run.personas,
        min_turns: run.minTurns,
        max_turns: run.maxTurns,
        max_parallel: run.parallel,
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
        quality_threshold: quality,
        turn_pass_threshold: reply,
        bot_version_header: versionHeader.trim() || null,
        bot_info_url: infoUrl.trim() || null,
        judge_weights: Object.fromEntries(
          Object.entries(weights)
            .filter(([, v]) => v.trim() !== "")
            .map(([k, v]) => [k, Number(v)]),
        ),
        scenarios: focus === "scenarios" ? chosenScenarios : [],
        stress: focus === "stress" ? stress : null,
        replay: focus === "replay" ? replay : null,
        bot_model: botModel.trim() || null,
        compare:
          focus === "compare"
            ? {
                baseline_name: compare.baseline_name.trim(),
                targets: compare.targets.map((t) => ({
                  name: t.name.trim(),
                  bot_endpoint: t.bot_endpoint.trim(),
                  bot_api_key: t.bot_api_key?.trim() || undefined,
                  model: t.model?.trim() || null,
                })),
              }
            : null,
      });
      queryClient.invalidateQueries({ queryKey: ["runs"] });
      router.push(`/simulations/${sim.simulation_id}`);
    } catch (err) {
      setError(err instanceof EngineError ? err.message : "Could not start the test.");
      setSubmitting(false);
    }
  };

  const upload = (
    <div className="space-y-2">
      {!contextFilename ? (
        <button
          type="button"
          className={`flex w-full flex-col items-center justify-center gap-2 rounded-xl border-2 border-dashed p-8 text-center transition-colors ${
            dragActive ? "border-primary bg-primary/5" : "border-border hover:border-primary/50 hover:bg-muted/30"
          }`}
          onDragEnter={handleDrag}
          onDragLeave={handleDrag}
          onDragOver={handleDrag}
          onDrop={handleDrop}
          onClick={() => fileInputRef.current?.click()}
        >
          <span className="rounded-full bg-primary/10 p-3 text-primary">
            <UploadCloud className="h-6 w-6" aria-hidden />
          </span>
          <span className="font-medium text-foreground">Click to upload or drag and drop</span>
          <span className="text-sm text-muted-foreground">Markdown (.md), up to 2 MB</span>
        </button>
      ) : (
        <div className="flex items-center justify-between rounded-xl border bg-muted/20 p-4">
          <span className="flex min-w-0 items-center gap-3">
            <span className="rounded-lg bg-primary/10 p-2 text-primary">
              <FileText className="h-5 w-5" aria-hidden />
            </span>
            <span className="min-w-0">
              <span className="block truncate font-medium text-foreground">{contextFilename}</span>
              <span className="text-xs text-muted-foreground">{contextContent.length.toLocaleString()} characters</span>
            </span>
          </span>
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
            <X />
          </Button>
        </div>
      )}
      <input
        ref={fileInputRef}
        type="file"
        accept=".md,.markdown,.txt"
        className="hidden"
        onChange={(e) => {
          const file = e.target.files?.[0];
          if (file) handleFile(file);
          e.target.value = "";
        }}
      />
    </div>
  );

  return (
    <div className={embedded ? "max-w-4xl" : "mx-auto max-w-3xl py-8"}>
      <form onSubmit={handleSubmit} noValidate className="rounded-xl border bg-card shadow-xs">
        <Section n={1} title="How should we build the test?" description="Where the test plan comes from, and whether you review it.">
          <div className="space-y-4">
            <ChoiceCards
              label="How the test is built"
              value={mode}
              onChange={setMode}
              options={MODES.map((m) => ({ id: m.id, title: m.title, body: m.body, tag: m.needs, icon: MODE_ICONS[m.id] }))}
            />
            <div className="flex flex-col gap-2 rounded-lg bg-muted/40 p-3 sm:flex-row sm:items-center sm:justify-between">
              <Segmented
                label="Review"
                value={handsOff ? "off" : "review"}
                onChange={(v) => setHandsOff(v === "off")}
                options={[
                  { id: "review", label: "Review each step" },
                  { id: "off", label: "Run hands-off" },
                ]}
              />
              <p className="flex items-start gap-1.5 text-xs text-muted-foreground sm:max-w-sm">
                {handsOff ? (
                  <>
                    <Zap className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden /> Every AI proposal is approved automatically — good
                    for repeat runs and CI. The report&apos;s Inputs tab shows what was used.
                  </>
                ) : (
                  <>
                    <Hand className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden /> You approve or edit each proposal (
                    {reviewSteps} short steps) before any conversation starts.
                  </>
                )}
              </p>
            </div>
          </div>
        </Section>

        {focus === "scenarios" && (
          <Section n={2} title="Which scenarios?" description="Structured situations every run puts your bot through, each scored on its own.">
            <ScenarioPicker scenarios={options?.scenarios} selected={chosenScenarios} onChange={setScenarioIds} customers={run.personas} />
          </Section>
        )}
        {focus === "replay" && (
          <Section n={2} title="Your conversations" description="Real conversations from production, judged the same way as a simulation.">
            <ReplaySource value={replay} onChange={setReplay} formats={options?.replay_formats} piiEngine={options?.pii_engine} onError={setError} />
          </Section>
        )}
        {focus === "stress" && (
          <Section n={2} title="Memory stress" description="Long conversations that check whether your bot keeps track of what the customer told it.">
            <StressSettings patterns={options?.stress_patterns} value={stress} onChange={setStress} />
          </Section>
        )}

        <Section n={focus === "compare" ? 2 : 2 + step} title="Your bot" description="Where to reach it. Only the endpoint is required.">
          <div className="grid gap-4 md:grid-cols-2">
            <div className="space-y-2 md:col-span-2">
              <Label htmlFor="endpoint" className="text-sm font-semibold">
                Bot Endpoint
              </Label>
              <Input
                id="endpoint"
                type="url"
                placeholder="https://your-bot.com/v1/chat/completions"
                className="h-11"
                value={endpoint}
                onChange={(e) => setEndpoint(e.target.value)}
                required
              />
              {focus === "replay" && (
                <p className="text-xs text-muted-foreground">
                  The bot these conversations came from: runs of the same bot are compared over time.
                  {replay.resend ? " The customers' messages are sent here." : " Nothing is sent to it."}
                </p>
              )}
            </div>
            <div className="space-y-2">
              <Label htmlFor="api-key" className="text-sm font-semibold">
                API key <span className="font-normal text-muted-foreground">(if your bot needs one)</span>
              </Label>
              <Input
                id="api-key"
                type="password"
                autoComplete="off"
                className="h-10"
                placeholder="Sent only to the engine, never stored"
                value={apiKey}
                onChange={(e) => setApiKey(e.target.value)}
              />
            </div>
            <div className="space-y-2">
              <Label htmlFor="name" className="text-sm font-semibold">
                Run name <span className="font-normal text-muted-foreground">(optional)</span>
              </Label>
              <Input id="name" className="h-10" placeholder="Banking bot – release 2.3" value={name} onChange={(e) => setName(e.target.value)} />
            </div>
            <div className="space-y-2 md:col-span-2">
              <Label htmlFor="info-url" className="text-sm font-semibold">
                Version page <span className="font-normal text-muted-foreground">(recommended)</span>
              </Label>
              <Input
                id="info-url"
                type="url"
                className="h-10"
                placeholder="https://your-bot.com/knowledge"
                value={infoUrl}
                onChange={(e) => setInfoUrl(e.target.value)}
              />
              <p className="text-xs text-muted-foreground">
                A page that changes when your bot changes, such as its knowledge endpoint. Each report records its fingerprint,
                so results are tied to a bot version. Remembered for this bot.
              </p>
            </div>
            <div className="md:col-span-2">
              <Disclosure label="Connection details">
                <div className="grid gap-4 md:grid-cols-2">
                  <SelectField
                    id="format"
                    label="Request format"
                    value={format}
                    options={["openai", "anthropic", "custom"]}
                    onChange={(v) => setFormat(v as RequestFormat)}
                  />
                  <div className="space-y-2">
                    <Label htmlFor="response-path" className="text-sm font-semibold">
                      Where the reply is in the response
                    </Label>
                    <Input
                      id="response-path"
                      className="h-10 font-mono text-sm"
                      value={responsePath}
                      onChange={(e) => setResponsePath(e.target.value)}
                    />
                  </div>
                  <div className="space-y-2">
                    <Label htmlFor="bot-model" className="text-sm font-semibold">
                      Model <span className="font-normal text-muted-foreground">(sent as &quot;model&quot;)</span>
                    </Label>
                    <Input
                      id="bot-model"
                      className="h-10 font-mono text-sm"
                      placeholder="Blank: your endpoint decides"
                      value={botModel}
                      onChange={(e) => setBotModel(e.target.value)}
                    />
                  </div>
                  <div className="space-y-2">
                    <Label htmlFor="version-header" className="text-sm font-semibold">
                      Version header <span className="font-normal text-muted-foreground">(instead of a version page)</span>
                    </Label>
                    <Input
                      id="version-header"
                      className="h-10 font-mono text-sm"
                      placeholder="x-bot-version"
                      value={versionHeader}
                      onChange={(e) => setVersionHeader(e.target.value)}
                    />
                  </div>
                </div>
              </Disclosure>
            </div>
          </div>
        </Section>

        {focus === "compare" && (
          <Section n={3} title="Bots to compare" description="The same customers, judged the same way, against each bot.">
            <CompareTargets value={compare} onChange={setCompare} mainEndpoint={endpoint.trim()} mainModel={botModel.trim()} />
          </Section>
        )}

        <Section
          n={3 + step}
          title="What your bot knows"
          description={
            mode === "partial"
              ? "The Markdown your bot answers from. Every judge treats it as the ground truth."
              : mode === "auto"
                ? "Nothing to upload."
                : "Write what the test should check. Documentation is optional but makes answers checkable."
          }
        >
          {mode === "auto" ? (
            <div className="flex gap-3 rounded-lg border bg-muted/30 p-4 text-sm text-muted-foreground">
              <Sparkles className="mt-0.5 h-4 w-4 shrink-0 text-primary" aria-hidden />
              <p>
                AI SimTest will chat with your bot (about 12 messages) to learn what it does, then show you what it found
                {handsOff ? " and carry on automatically" : " before building the test"}. Answers can&apos;t be checked against a
                document, so grounding is weaker than with documentation.
              </p>
            </div>
          ) : mode === "partial" ? (
            upload
          ) : (
            <div className="space-y-5">
              <div className="grid gap-5 md:grid-cols-2">
                <div className="md:col-span-2">
                  <ListEditor
                    id="criteria"
                    label="Success criteria"
                    hint="One per line. Every reply is judged against these."
                    placeholder={"Quotes fees exactly as published\nNever asks for a PIN, password or OTP"}
                    value={criteria}
                    onChange={setCriteria}
                  />
                </div>
                <ListEditor
                  id="rules"
                  label="Guardrails (optional)"
                  hint="Things the bot must never do. One per line."
                  placeholder={"Never guarantee that an application will be approved"}
                  value={rules}
                  onChange={setRules}
                />
                <ListEditor
                  id="topics"
                  label="Topics to test (optional)"
                  hint="What the simulated customers should ask about."
                  placeholder={"Opening a savings account\nLost or stolen card"}
                  value={topics}
                  onChange={setTopics}
                />
              </div>
              <div className="space-y-2">
                <p className="text-sm font-semibold text-foreground">
                  Documentation <span className="font-normal text-muted-foreground">(optional)</span>
                </p>
                {upload}
              </div>
            </div>
          )}
        </Section>

        {focus !== "replay" && (
        <Section n={4 + step} title="How big a test?" description="More simulated customers find rarer problems, and take longer.">
          <div className="space-y-4">
            <ChoiceCards
              label="Test size"
              value={size}
              onChange={chooseSize}
              columns={4}
              options={SIZES.map((s) => ({ id: s.id, title: s.label, body: s.hint }))}
            />
            {focus !== "simulation" && lengths.min > run.minTurns && (
              <p role="note" className="text-sm text-muted-foreground">
                {focus === "stress"
                  ? `Each conversation runs ${stress.turns} messages, as set in Memory stress; the size sets how many customers.`
                  : `The scenarios you picked need at least ${lengths.min} messages, so conversations run at least that long.`}
              </p>
            )}
            {size === "custom" && (
              <div className="grid gap-4 rounded-lg border p-4 sm:grid-cols-2 lg:grid-cols-4">
                <SelectField id="personas" label="Simulated customers" value={personas} options={PERSONA_OPTIONS} onChange={setPersonas} />
                <SelectField id="min-turns" label="Minimum messages" value={minTurns} options={TURN_OPTIONS} onChange={setMinTurns} />
                <SelectField id="max-turns" label="Maximum messages" value={maxTurns} options={TURN_OPTIONS} onChange={setMaxTurns} />
                <SelectField id="parallel" label="At the same time" value={parallel} options={PARALLEL_OPTIONS} onChange={setParallel} />
                {Number(minTurns) >= LONG_MIN_TURNS && (
                  <p role="note" className="rounded-md border border-warn/30 bg-warn/5 px-3 py-2 text-sm text-foreground sm:col-span-2 lg:col-span-4">
                    Every conversation will run at least {minTurns} messages. Simulated customers are told not to wrap up before
                    then, so once their goal is met they invent follow-up questions, and those later replies usually fail. To see
                    how conversations end naturally, keep the minimum low and raise only the maximum.
                  </p>
                )}
              </div>
            )}
          </div>
        </Section>
        )}

        <Section n={5 + step + afterSize} title="What to check" description="Every reply is judged for quality, grounding and safety. Add more here.">
          <div className="space-y-5">
            <fieldset className="space-y-2">
              <legend className="text-sm font-semibold text-foreground">Business workflows</legend>
              <Segmented
                label="Business workflows"
                value={workflowMode}
                onChange={setWorkflowMode}
                options={[
                  { id: "auto", label: "Detect from your docs" },
                  { id: "choose", label: "Choose" },
                  { id: "off", label: "Off" },
                ]}
              />
              {workflowMode === "choose" && (
                <div className="grid gap-2 rounded-lg border p-3 sm:grid-cols-2">
                  {options?.workflows.length ? (
                    options.workflows.map((w) => (
                      <label key={w.id} className="flex items-center gap-2 text-sm">
                        <Checkbox
                          checked={workflows.includes(w.id)}
                          onCheckedChange={(on) => setWorkflows((cur) => (on ? [...cur, w.id] : cur.filter((x) => x !== w.id)))}
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
              <p className="text-xs text-muted-foreground">Step-by-step processes such as opening an account or blocking a card.</p>
            </fieldset>
            <div className="max-w-sm space-y-2">
              <Label htmlFor="policy" className="text-sm font-semibold">
                Compliance policy
              </Label>
              <select
                id="policy"
                className="h-10 w-full rounded-md border border-input bg-background px-3 text-sm"
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
            </div>
            <div className="grid gap-3 md:grid-cols-2">
              <Toggle
                checked={guardrailLlm}
                onChange={setGuardrailLlm}
                title="Check every guardrail with AI"
                body="Rules no pattern can check are judged by a model. Adds one AI call per reply."
              />
              <Toggle
                checked={relevanceLlm}
                onChange={setRelevanceLlm}
                title="Check each question was answered"
                body="A model reads whether the reply answered the question, not just matched its words. Adds one AI call per reply."
              />
            </div>
          </div>
        </Section>

        <Section n={6 + step + afterSize} title="How strict?" description="How good a reply has to be to count as a pass.">
          <div className="space-y-3">
            <Segmented
              label="Strictness"
              value={strictness}
              onChange={setStrictness}
              options={STRICTNESS.map((s) => ({ id: s.id, label: s.label }))}
            />
            <p className="text-sm text-muted-foreground">{strict.hint}</p>
            {suggested !== null && qualityThreshold !== String(suggested) && (
              <p className="text-sm">
                <button
                  type="button"
                  className="font-medium text-primary underline-offset-4 hover:underline"
                  onClick={() => setQualityThreshold(String(suggested))}
                >
                  Use the quality pass mark {suggested.toFixed(2)} from your Judge review
                </button>
              </p>
            )}
            {qualityThreshold !== "" && (
              <p className="text-xs text-muted-foreground">
                Quality pass mark set to {Number(qualityThreshold).toFixed(2)} (Expert settings).
              </p>
            )}
          </div>
        </Section>

        <div className="border-t px-6 py-4 md:px-8">
          <Disclosure label="Expert settings">
            <div className="space-y-5">
              <div className="grid gap-4 sm:grid-cols-2">
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
                    placeholder={(strict.quality ?? 0.6).toFixed(2)}
                    value={qualityThreshold}
                    onChange={(e) => setQualityThreshold(e.target.value)}
                  />
                  <p className="text-xs text-muted-foreground">The score the quality judge needs to pass a reply. Overrides the strictness preset.</p>
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
                    placeholder={(strict.reply ?? 0.7).toFixed(2)}
                    value={turnThreshold}
                    onChange={(e) => setTurnThreshold(e.target.value)}
                  />
                  <p className="text-xs text-muted-foreground">The weighted mean of all judges a reply needs to be labelled PASS.</p>
                </div>
              </div>
              <fieldset className="space-y-2">
                <legend className="text-sm font-semibold text-foreground">Judge weights</legend>
                <p className="text-xs text-muted-foreground">How much each judge counts towards a reply&apos;s score. Blank keeps the default.</p>
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
              </fieldset>
              <div className="grid gap-4 sm:grid-cols-2">
                <div className="space-y-1.5">
                  <Label htmlFor="capture-headers" className="text-xs font-medium">
                    Response headers to record
                  </Label>
                  <Input
                    id="capture-headers"
                    className="h-10 font-mono text-sm"
                    placeholder="x-simbank-*, x-request-id"
                    value={captureHeaders}
                    onChange={(e) => setCaptureHeaders(e.target.value)}
                  />
                  <p className="text-xs text-muted-foreground">Comma-separated; * allowed. Adds the bot&apos;s own labels to the report.</p>
                </div>
                {size !== "custom" && (
                  <SelectField id="parallel-expert" label="Conversations at the same time" value={parallel} options={PARALLEL_OPTIONS} onChange={setParallel} />
                )}
              </div>
              <label className="flex items-center gap-2 text-sm">
                <Checkbox checked={trackCost} onCheckedChange={(on) => setTrackCost(on === true)} />
                Track estimated LLM cost for this run
              </label>
            </div>
          </Disclosure>
        </div>

        <div className="sticky bottom-0 z-10 flex flex-col gap-3 rounded-b-xl border-t bg-card/95 px-6 py-4 backdrop-blur md:flex-row md:items-center md:justify-between md:px-8">
          <div className="text-sm">
            {focus === "replay" ? (
              <>
                <p className="font-medium text-foreground">
                  {replay.filename
                    ? `${replay.sample ? `A sample of ${replay.sample}` : replayCount != null ? `${replayCount.toLocaleString()}` : "All"} real conversations`
                    : "No conversations uploaded yet"}{" "}
                  · {replay.resend ? "re-sent to your bot" : "replies from the file"} ·{" "}
                  {replay.privacy === "mask" ? "personal data masked" : "personal data reported only"}
                </p>
                <p className="text-xs text-muted-foreground" title="Rough estimate from earlier runs; the report shows the actual cost.">
                  About $0.03 per bot reply judged · {handsOff ? "runs hands-off" : `${reviewSteps} review steps`}
                </p>
              </>
            ) : (
              <>
                <p className="font-medium text-foreground">
                  {run.personas} customers{bots > 1 ? ` × ${bots} bots` : ""} ·{" "}
                  {focus === "stress" ? `${stress.turns} messages each` : `up to ${lengths.max} messages`}
                  {focus === "scenarios" && ` · ${chosenScenarios.length} scenario${chosenScenarios.length === 1 ? "" : "s"}`} · about{" "}
                  {cost.replies.toLocaleString()} bot replies
                </p>
                <p className="text-xs text-muted-foreground" title="Rough estimate from earlier runs; the report shows the actual cost.">
                  Rough cost {money(cost.low)}–{money(cost.high)} · {handsOff ? "runs hands-off" : `${reviewSteps} review steps`}
                </p>
              </>
            )}
            {error && (
              <p role="alert" className="mt-1 text-sm font-medium text-destructive">
                {error}
              </p>
            )}
          </div>
          <Button type="submit" size="lg" className="h-11 px-6" disabled={submitting}>
            {submitting ? <Loader2 className="h-5 w-5 animate-spin" /> : null}
            Start test
            {!submitting && <ArrowRight className="h-5 w-5" />}
          </Button>
        </div>
      </form>
    </div>
  );
}
