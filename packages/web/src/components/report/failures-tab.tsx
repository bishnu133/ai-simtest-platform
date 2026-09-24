"use client";

import { useMemo, useState } from "react";
import { ChevronDown, MessageSquareText } from "lucide-react";
import { Button } from "@/components/ui/button";
import { isCritical, pct, plural, shortTitle } from "@/lib/engine/report";
import type { FailurePattern, ReportResponse, TriageItem } from "@/lib/engine/types";
import { EmptyNote, Panel, SeverityBadge } from "./parts";

const SEVERITY_ORDER = ["critical", "high", "medium", "low"];

// The engine prefixes recommendations with a traffic-light emoji and/or a level label
const EMOJI_LEVELS: Record<string, string> = { "🔴": "critical", "🟠": "high", "🟡": "medium", "🟢": "low" };

function parseRecommendation(r: string): { severity?: string; text: string } {
  const emoji = Object.keys(EMOJI_LEVELS).find((e) => r.trimStart().startsWith(e));
  let text = emoji ? r.trimStart().slice(emoji.length).trim() : r.trim();
  let severity = emoji ? EMOJI_LEVELS[emoji] : undefined;
  const labelled = text.match(/^(CRITICAL|HIGH|MEDIUM|LOW|INFO)\s*:\s*([\s\S]*)$/);
  if (labelled) {
    severity = labelled[1].toLowerCase();
    text = labelled[2];
  }
  return { severity, text };
}

function ConversationLinks({ ids, onOpen }: { ids: string[]; onOpen: (id: string) => void }) {
  if (!ids.length) return null;
  return (
    <div className="flex flex-wrap items-center gap-2">
      <span className="text-xs text-muted-foreground">Seen in:</span>
      {ids.slice(0, 6).map((id, i) => (
        <Button key={id} variant="outline" size="xs" onClick={() => onOpen(id)}>
          <MessageSquareText /> Conversation {i + 1}
        </Button>
      ))}
      {ids.length > 6 && <span className="text-xs text-muted-foreground">+{ids.length - 6} more</span>}
    </div>
  );
}

function Expandable({ summary, children }: { summary: React.ReactNode; children: React.ReactNode }) {
  const [open, setOpen] = useState(false);
  return (
    <div className="rounded-lg border bg-card">
      <button
        type="button"
        className="flex w-full items-start gap-3 p-4 text-left"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
      >
        <div className="min-w-0 flex-1">{summary}</div>
        <ChevronDown className={`mt-1 h-4 w-4 shrink-0 text-muted-foreground transition-transform ${open ? "rotate-180" : ""}`} />
      </button>
      {open && <div className="space-y-3 border-t px-4 py-3">{children}</div>}
    </div>
  );
}

function TriageRow({ item, onOpen }: { item: TriageItem; onOpen: (id: string) => void }) {
  return (
    <Expandable
      summary={
        <div className="flex items-start gap-3">
          <span className="mt-0.5 flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-secondary text-xs font-semibold text-secondary-foreground tabular-nums">
            {item.rank}
          </span>
          <div className="min-w-0 flex-1 space-y-1">
            <div className="flex flex-wrap items-center gap-2">
              <span className="font-medium text-foreground">{shortTitle(item.title, 140)}</span>
              <SeverityBadge severity={item.severity} />
            </div>
            <div className="text-xs text-muted-foreground">
              {plural(item.frequency, "occurrence")} ·{" "}
              {item.affected_conversations
                ? `${plural(item.affected_conversations, "conversation")} (${pct(item.reach)})`
                : `reaches ${pct(item.reach)} of conversations`}
            </div>
          </div>
        </div>
      }
    >
      <p className="whitespace-pre-line text-sm text-foreground/80">{item.detail || item.title}</p>
      <ConversationLinks ids={item.conversation_ids} onOpen={onOpen} />
    </Expandable>
  );
}

function PatternRow({ pattern, onOpen }: { pattern: FailurePattern; onOpen: (id: string) => void }) {
  const full = pattern.description || pattern.pattern_name || "";
  return (
    <Expandable
      summary={
        <div className="space-y-1">
          <div className="flex flex-wrap items-center gap-2">
            <span className="font-medium text-foreground">{shortTitle(full)}</span>
            <SeverityBadge severity={pattern.severity} />
          </div>
          <div className="text-xs text-muted-foreground">{plural(pattern.frequency ?? 0, "occurrence")}</div>
        </div>
      }
    >
      <p className="whitespace-pre-line text-sm text-foreground/80">{full}</p>
      <ConversationLinks ids={pattern.example_conversation_ids ?? []} onOpen={onOpen} />
    </Expandable>
  );
}

export function FailuresTab({ data, onOpenConversation }: { data: ReportResponse; onOpenConversation: (id: string) => void }) {
  const { report, analysis = {} } = data;
  const [severity, setSeverity] = useState<string>("all");

  const patterns = useMemo(
    () =>
      [...(report.failure_patterns ?? [])].sort(
        (a, b) =>
          SEVERITY_ORDER.indexOf((a.severity ?? "low").toLowerCase()) -
            SEVERITY_ORDER.indexOf((b.severity ?? "low").toLowerCase()) || (b.frequency ?? 0) - (a.frequency ?? 0),
      ),
    [report.failure_patterns],
  );
  const counts = useMemo(() => {
    const c: Record<string, number> = {};
    for (const p of patterns) c[(p.severity ?? "low").toLowerCase()] = (c[(p.severity ?? "low").toLowerCase()] ?? 0) + 1;
    return c;
  }, [patterns]);
  const visible = severity === "all" ? patterns : patterns.filter((p) => (p.severity ?? "low").toLowerCase() === severity);
  const critical = patterns.filter((p) => isCritical(p.severity)).length;

  return (
    <div className="space-y-6">
      <Panel
        title="Fix these first"
        description="Ranked by severity, frequency and reach — the same queue as the HTML report."
      >
        {analysis.fix_first?.length ? (
          <div className="space-y-2">
            {analysis.fix_first.map((item) => (
              <TriageRow key={item.rank} item={item} onOpen={onOpenConversation} />
            ))}
          </div>
        ) : (
          <EmptyNote>No ranked failures for this run.</EmptyNote>
        )}
      </Panel>

      <Panel
        title="All failure patterns"
        description={`${plural(patterns.length, "pattern")} clustered from judge findings${critical ? ` · ${critical} critical` : ""}.`}
        action={
          <div className="flex flex-wrap gap-1" role="group" aria-label="Filter by severity">
            {["all", ...SEVERITY_ORDER.filter((s) => counts[s])].map((s) => (
              <Button
                key={s}
                size="xs"
                variant={severity === s ? "default" : "outline"}
                onClick={() => setSeverity(s)}
                aria-pressed={severity === s}
                className="capitalize"
              >
                {s} {s === "all" ? patterns.length : counts[s]}
              </Button>
            ))}
          </div>
        }
      >
        {visible.length ? (
          <div className="space-y-2">
            {visible.map((p, i) => (
              <PatternRow key={`${p.pattern_name}-${i}`} pattern={p} onOpen={onOpenConversation} />
            ))}
          </div>
        ) : (
          <EmptyNote>No recurring failure patterns — nice.</EmptyNote>
        )}
      </Panel>

      <Panel title="Recommendations">
        {report.recommendations?.length ? (
          <ul className="space-y-2.5 text-sm text-foreground">
            {report.recommendations.map((r, i) => {
              const { severity, text } = parseRecommendation(r);
              return (
                <li key={i} className="flex items-start gap-2.5">
                  {severity ? <SeverityBadge severity={severity} /> : <span className="mt-2 h-1.5 w-1.5 shrink-0 rounded-full bg-muted-foreground" />}
                  <span>{text}</span>
                </li>
              );
            })}
          </ul>
        ) : (
          <EmptyNote>No recommendations generated.</EmptyNote>
        )}
      </Panel>
    </div>
  );
}
