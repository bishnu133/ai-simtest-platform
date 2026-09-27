"use client";

import { useRef, useState, type DragEvent } from "react";
import { FileText, ShieldCheck, ShieldAlert, UploadCloud, X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import type { ReplayFormat, ReplayOptions } from "@/lib/engine/types";
import { ChoiceCards, Segmented } from "./controls";

export const MAX_REPLAY_BYTES = 10 * 1024 * 1024;
const ACCEPT = ".json,.jsonl,.csv,.tsv,.txt,.md,.log";

const FORMAT_LABELS: Record<ReplayFormat, string> = {
  auto: "Detect from the file",
  jsonl: "JSON Lines (one conversation per line)",
  json: "JSON",
  csv: "CSV (conversation id, role, message)",
  tsv: "TSV",
  text: "Plain text transcript",
  markdown: "Markdown transcript",
};

const SAMPLES = ["all", "50", "100", "200", "500"] as const;

export const DEFAULT_REPLAY: ReplayOptions = {
  conversations: "",
  filename: "",
  format: "auto",
  privacy: "mask",
  resend: false,
  sample: null,
  min_turns: null,
  max_turns: null,
  contains: null,
};

/** A rough count of the conversations in an upload, for the summary line. */
export function countConversations(r: ReplayOptions): number | null {
  if (!r.conversations) return null;
  const text = r.conversations.trim();
  if (r.format === "jsonl" || (r.format === "auto" && /\.jsonl$/i.test(r.filename))) {
    return text.split("\n").filter((l) => l.trim()).length;
  }
  if (r.format === "json" || (r.format === "auto" && /\.json$/i.test(r.filename))) {
    try {
      const data = JSON.parse(text);
      if (Array.isArray(data)) return data.length;
      if (Array.isArray(data?.conversations)) return data.conversations.length;
      return 1;
    } catch {
      return null;
    }
  }
  return null;
}

/** Upload real conversations and choose how they are judged and protected. */
export function ReplaySource({
  value,
  onChange,
  formats,
  piiEngine,
  onError,
}: {
  value: ReplayOptions;
  onChange: (v: ReplayOptions) => void;
  formats: ReplayFormat[] | undefined;
  piiEngine: string | undefined;
  onError: (message: string) => void;
}) {
  const inputRef = useRef<HTMLInputElement>(null);
  const [drag, setDrag] = useState(false);
  const set = (patch: Partial<ReplayOptions>) => onChange({ ...value, ...patch });
  const count = countConversations(value);

  const readFile = (file: File) => {
    if (!/\.(jsonl?|csv|tsv|txt|md|log)$/i.test(file.name)) {
      onError("Upload a JSON, JSON Lines, CSV, TSV or text file of conversations.");
      return;
    }
    if (file.size > MAX_REPLAY_BYTES) {
      onError("Conversation files must be 10 MB or smaller. Split the export, or sample it first.");
      return;
    }
    const reader = new FileReader();
    reader.onload = (e) => {
      set({ conversations: String(e.target?.result ?? ""), filename: file.name });
      onError("");
    };
    reader.onerror = () => onError("Could not read that file.");
    reader.readAsText(file);
  };

  const onDrag = (e: DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    setDrag(e.type === "dragenter" || e.type === "dragover");
  };

  return (
    <div className="space-y-6">
      <div className="space-y-2">
        {!value.filename ? (
          <button
            type="button"
            className={`flex w-full flex-col items-center justify-center gap-2 rounded-xl border-2 border-dashed p-8 text-center transition-colors ${
              drag ? "border-primary bg-primary/5" : "border-border hover:border-primary/50 hover:bg-muted/30"
            }`}
            onDragEnter={onDrag}
            onDragLeave={onDrag}
            onDragOver={onDrag}
            onDrop={(e) => {
              onDrag(e);
              const file = e.dataTransfer.files?.[0];
              if (file) readFile(file);
            }}
            onClick={() => inputRef.current?.click()}
          >
            <span className="rounded-full bg-primary/10 p-3 text-primary">
              <UploadCloud className="h-6 w-6" aria-hidden />
            </span>
            <span className="font-medium text-foreground">Upload conversation logs</span>
            <span className="text-sm text-muted-foreground">JSON Lines, JSON, CSV or text transcripts · up to 10 MB</span>
          </button>
        ) : (
          <div className="flex items-center justify-between rounded-xl border bg-muted/20 p-4">
            <span className="flex min-w-0 items-center gap-3">
              <span className="rounded-lg bg-primary/10 p-2 text-primary">
                <FileText className="h-5 w-5" aria-hidden />
              </span>
              <span className="min-w-0">
                <span className="block truncate font-medium text-foreground">{value.filename}</span>
                <span className="text-xs text-muted-foreground">
                  {count != null ? `About ${count.toLocaleString()} conversations · ` : ""}
                  {(value.conversations.length / 1024).toFixed(0)} KB
                </span>
              </span>
            </span>
            <Button type="button" variant="ghost" size="icon" aria-label="Remove file" onClick={() => set({ conversations: "", filename: "" })}>
              <X />
            </Button>
          </div>
        )}
        <input
          ref={inputRef}
          type="file"
          accept={ACCEPT}
          className="hidden"
          aria-label="Conversation logs"
          onChange={(e) => {
            const file = e.target.files?.[0];
            if (file) readFile(file);
            e.target.value = "";
          }}
        />
        <p className="text-xs text-muted-foreground">
          Each conversation needs the customer&apos;s and the bot&apos;s messages. Common role names (user, customer,
          assistant, bot, agent) are recognised.
        </p>
      </div>

      <fieldset className="space-y-2">
        <legend className="text-sm font-semibold text-foreground">Personal data</legend>
        <ChoiceCards
          label="Personal data"
          columns={2}
          value={value.privacy}
          onChange={(privacy) => set({ privacy })}
          options={[
            {
              id: "mask",
              title: "Mask it",
              tag: "Recommended",
              icon: <ShieldCheck className="h-4 w-4" />,
              body: "Names, emails, phone, card and account numbers, ID numbers and dates of birth become [PERSON], [EMAIL_ADDRESS]… before anything reads them. Your uploaded file is not kept.",
            },
            {
              id: "detect",
              title: "Only report it",
              icon: <ShieldAlert className="h-4 w-4" />,
              body: "Counts personal data but leaves the text as it is. The judge models read it and the report shows it. Use only for data that is already anonymised.",
            },
          ]}
        />
        {piiEngine === "patterns" && value.privacy === "mask" && (
          <p role="note" className="rounded-md border border-warn/30 bg-warn/5 px-3 py-2 text-sm text-foreground">
            Your engine masks by pattern only: names are caught where people introduce themselves (&quot;my name is
            …&quot;) and places are not caught. For full masking, run this once on the engine and restart it:{" "}
            <code className="font-mono text-xs">simtest install-models</code>
          </p>
        )}
      </fieldset>

      <fieldset className="space-y-2">
        <legend className="text-sm font-semibold text-foreground">Which replies to judge</legend>
        <ChoiceCards
          label="Which replies to judge"
          columns={2}
          value={value.resend ? "resend" : "logged"}
          onChange={(v) => set({ resend: v === "resend" })}
          options={[
            {
              id: "logged",
              title: "The replies in the file",
              body: "How did the bot do with real customers? Nothing is sent to your bot.",
            },
            {
              id: "resend",
              title: "Your bot's replies today",
              body: "Sends each customer's messages to your bot again, in order, and judges the new replies. Shows whether a new build handles real traffic better.",
            },
          ]}
        />
        {value.resend && value.privacy === "mask" && (
          <p className="text-xs text-muted-foreground">
            Your bot receives the masked messages, so a customer who gave an order number sends [ACCOUNT_NUMBER] instead.
          </p>
        )}
      </fieldset>

      <div className="grid gap-5 md:grid-cols-2">
        <fieldset className="space-y-2">
          <legend className="text-sm font-semibold text-foreground">How many to judge</legend>
          <Segmented
            label="How many to judge"
            value={value.sample ? String(value.sample) : "all"}
            onChange={(v) => set({ sample: v === "all" ? null : Number(v) })}
            options={SAMPLES.map((s) => ({ id: s, label: s === "all" ? "All" : s }))}
          />
          <p className="text-xs text-muted-foreground">A random sample keeps a large export quick and cheap.</p>
        </fieldset>
        <div className="space-y-2">
          <Label htmlFor="replay-contains" className="text-sm font-semibold">
            Only conversations mentioning <span className="font-normal text-muted-foreground">(optional)</span>
          </Label>
          <Input
            id="replay-contains"
            className="h-10"
            placeholder="refund"
            value={value.contains ?? ""}
            onChange={(e) => set({ contains: e.target.value || null })}
          />
        </div>
        <div className="space-y-2">
          <Label htmlFor="replay-min" className="text-sm font-semibold">
            Skip conversations shorter than <span className="font-normal text-muted-foreground">(messages)</span>
          </Label>
          <Input
            id="replay-min"
            type="number"
            inputMode="numeric"
            min={1}
            className="h-10 tabular-nums"
            placeholder="No limit"
            value={value.min_turns ?? ""}
            onChange={(e) => set({ min_turns: e.target.value ? Number(e.target.value) : null })}
          />
        </div>
        <div className="space-y-2">
          <Label htmlFor="replay-format" className="text-sm font-semibold">
            File format
          </Label>
          <select
            id="replay-format"
            className="h-10 w-full rounded-md border border-input bg-background px-3 text-sm"
            value={value.format}
            onChange={(e) => set({ format: e.target.value as ReplayFormat })}
          >
            {(formats?.length ? formats : (Object.keys(FORMAT_LABELS) as ReplayFormat[])).map((f) => (
              <option key={f} value={f}>
                {FORMAT_LABELS[f] ?? f}
              </option>
            ))}
          </select>
        </div>
      </div>
    </div>
  );
}
