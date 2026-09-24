"use client";

import { useMemo, useState } from "react";
import Link from "next/link";
import { ChevronDown, Download, FileText, RotateCcw, TriangleAlert } from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { engine } from "@/lib/engine/client";
import { formatDuration, plural } from "@/lib/engine/report";
import type { ReportResponse, SimulationStatus } from "@/lib/engine/types";
import { ConversationsTab } from "./conversations-tab";
import { FailuresTab } from "./failures-tab";
import { InputsTab } from "./inputs-tab";
import { OverviewTab } from "./overview-tab";
import { ReviewTab } from "./review-tab";
import { TranscriptSheet } from "./transcript-sheet";

const EXPORT_LABELS: Record<string, string> = {
  jsonl: "Conversations dataset (JSONL)",
  csv: "Turn-level results (CSV)",
  summary: "Run summary (JSON)",
  coverage: "Coverage (JSON)",
  cost: "Cost breakdown (JSON)",
  compliance: "Policy compliance (JSON)",
  signature: "Behavioral signature (JSON)",
  audit_trail: "Approval audit trail (JSON)",
  human_review: "Judge review labels (JSON)",
  golden_set: "Judge golden set (JSON)",
};

const GOLDEN_JUDGES = new Set(["quality", "relevance", "grounding", "safety"]);

export type ReportTab = "overview" | "failures" | "conversations" | "review" | "inputs";

export function ReportView({
  data,
  status,
  initialTab = "overview",
}: {
  data: ReportResponse;
  status: SimulationStatus;
  initialTab?: ReportTab;
}) {
  const [tab, setTab] = useState<ReportTab>(initialTab);
  const [openId, setOpenId] = useState<string | null>(null);
  const { report } = data;
  const summary = report.summary ?? {};
  const byId = useMemo(
    () => new Map((report.judged_conversations ?? []).map((jc) => [jc.conversation?.id ?? "", jc])),
    [report.judged_conversations],
  );
  // Judge review writes its label files after the report was loaded
  const [reviewExports, setReviewExports] = useState<string[]>([]);
  const exportsList = [...new Set([...data.exports, ...reviewExports])];
  const downloads = exportsList.filter((f) => EXPORT_LABELS[f]);
  const finished = new Date(status.updated_at);

  return (
    <div className="space-y-6 py-4 animate-in fade-in duration-500">
      <header className="flex flex-col gap-4 border-b pb-6 lg:flex-row lg:items-end lg:justify-between">
        <div className="min-w-0 space-y-1">
          <p className="text-xs font-semibold uppercase tracking-wider text-primary">Simulation report</p>
          <h1 className="text-2xl font-bold tracking-tight text-foreground md:text-3xl">{data.name}</h1>
          <p className="text-sm text-muted-foreground">
            {plural(summary.total_personas ?? 0, "persona")} · {plural(summary.total_conversations ?? 0, "conversation")} ·{" "}
            {formatDuration(summary.execution_time_seconds)} · finished{" "}
            {finished.toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" })}
          </p>
          <p className="truncate font-mono text-xs text-muted-foreground" title={status.config.bot_endpoint}>
            {status.config.bot_endpoint}
            {data.analysis?.bot_build?.build && (
              <span title={data.analysis.bot_build.source}> · build {data.analysis.bot_build.build}</span>
            )}
          </p>
          {data.analysis?.bot_build && !data.analysis.bot_build.build && (
            <p className="flex items-start gap-1.5 text-xs text-warn">
              <TriangleAlert className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden />
              <span>
                Bot build not recorded{data.analysis.bot_build.source ? ` (${data.analysis.bot_build.source})` : ""}, so this
                report can&apos;t be tied to a bot version. Set a Bot Info URL or version header in Setup → Advanced; the URL
                is remembered for this bot.
              </span>
            </p>
          )}
        </div>
        <div className="flex flex-wrap items-center gap-2">
          {data.exports.includes("html") && (
            <Button asChild variant="outline">
              <a href={engine.exportUrl(data.simulation_id, "html")} download>
                <FileText /> HTML report
              </a>
            </Button>
          )}
          {downloads.length > 0 && (
            <DropdownMenu>
              <DropdownMenuTrigger asChild>
                <Button variant="outline">
                  <Download /> Export <ChevronDown />
                </Button>
              </DropdownMenuTrigger>
              <DropdownMenuContent align="end" className="w-64">
                <DropdownMenuLabel>Download run artefacts</DropdownMenuLabel>
                <DropdownMenuSeparator />
                {downloads.map((fmt) => (
                  <DropdownMenuItem key={fmt} asChild>
                    <a href={engine.exportUrl(data.simulation_id, fmt)} download>
                      {EXPORT_LABELS[fmt]}
                    </a>
                  </DropdownMenuItem>
                ))}
              </DropdownMenuContent>
            </DropdownMenu>
          )}
          <Button asChild>
            <Link href="/">
              <RotateCcw /> New test
            </Link>
          </Button>
        </div>
      </header>

      <Tabs value={tab} onValueChange={(v) => setTab(v as ReportTab)}>
        <TabsList className="h-10 w-full justify-start overflow-x-auto sm:w-auto">
          <TabsTrigger value="overview" className="px-4">Overview</TabsTrigger>
          <TabsTrigger value="failures" className="px-4">
            Failures <span className="ml-1.5 tabular-nums text-muted-foreground">{report.failure_patterns?.length ?? 0}</span>
          </TabsTrigger>
          <TabsTrigger value="conversations" className="px-4">
            Conversations <span className="ml-1.5 tabular-nums text-muted-foreground">{report.judged_conversations?.length ?? 0}</span>
          </TabsTrigger>
          <TabsTrigger value="review" className="px-4">Judge review</TabsTrigger>
          <TabsTrigger value="inputs" className="px-4">Inputs</TabsTrigger>
        </TabsList>
        <TabsContent value="overview" className="mt-6">
          <OverviewTab data={data} onGoToFailures={() => setTab("failures")} onOpenConversation={setOpenId} />
        </TabsContent>
        <TabsContent value="failures" className="mt-6">
          <FailuresTab data={data} onOpenConversation={setOpenId} />
        </TabsContent>
        <TabsContent value="conversations" className="mt-6">
          <ConversationsTab data={data} onOpen={setOpenId} />
        </TabsContent>
        <TabsContent value="review" className="mt-6">
          <ReviewTab simulationId={data.simulation_id} onLabelled={(judge) =>
              // The engine writes a golden set only for judges it has golden-set categories for
              setReviewExports((cur) => [...new Set([...cur, "human_review", ...(GOLDEN_JUDGES.has(judge) ? ["golden_set"] : [])])])
            } />
        </TabsContent>
        <TabsContent value="inputs" className="mt-6">
          <InputsTab data={data} />
        </TabsContent>
      </Tabs>

      <TranscriptSheet conversation={openId ? byId.get(openId) ?? null : null} onClose={() => setOpenId(null)} />
    </div>
  );
}
