import Link from "next/link";
import {
  Activity,
  AlertOctagon,
  AlertTriangle,
  BookOpen,
  CheckCircle,
  Gauge,
  MessageSquare,
  ShieldAlert,
  Target,
  type LucideIcon,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { judgeLabel, pct, responsesJudged, scoreTone } from "@/lib/engine/report";
import type { ReportResponse } from "@/lib/engine/types";

const JUDGE_ICONS: Record<string, LucideIcon> = {
  safety: ShieldAlert,
  grounding: BookOpen,
  relevance: CheckCircle,
  quality: Gauge,
};

type Metric = { title: string; value: string; icon: LucideIcon; tone: string };

export function BasicReport({ data }: { data: ReportResponse }) {
  const { report } = data;
  const summary = report.summary ?? {};

  const metrics: Metric[] = [
    { title: "Total Conversations", value: String(summary.total_conversations ?? 0), icon: MessageSquare, tone: "text-secondary" },
    { title: "Total Responses Judged", value: String(responsesJudged(report)), icon: Activity, tone: "text-secondary" },
    { title: "Overall Score", value: pct(summary.average_score), icon: Target, tone: scoreTone(summary.average_score) },
    { title: "Pass Rate", value: pct(summary.pass_rate), icon: CheckCircle, tone: scoreTone(summary.pass_rate) },
    ...Object.entries(report.score_by_judge ?? {}).map(([judge, score]) => ({
      title: `${judgeLabel(judge)} Score`,
      value: pct(score),
      icon: JUDGE_ICONS[judge] ?? Gauge,
      tone: scoreTone(score),
    })),
    {
      title: "Failed Responses",
      value: String(summary.critical_failures ?? 0),
      icon: AlertOctagon,
      tone: summary.critical_failures ? "text-fail" : "text-pass",
    },
    { title: "Warnings", value: String(summary.warnings ?? 0), icon: AlertTriangle, tone: summary.warnings ? "text-warn" : "text-pass" },
  ];

  return (
    <div className="py-6 space-y-8 animate-in fade-in slide-in-from-bottom-4 duration-500">
      <div className="flex flex-col md:flex-row md:items-end justify-between gap-4 border-b pb-6">
        <div className="space-y-2">
          <h1 className="text-3xl font-bold tracking-tight text-foreground">Simulation Report</h1>
          <p className="text-lg text-muted-foreground">
            High-level results from <span className="font-medium text-foreground">{data.name}</span>.
          </p>
        </div>
        <Button asChild size="lg" className="shadow-lg shadow-primary/20">
          <Link href={`/simulations/${data.simulation_id}/report`}>View Detailed Report</Link>
        </Button>
      </div>

      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4 md:gap-6">
        {metrics.map((m) => (
          <Card key={m.title} className="border-muted/60 shadow-sm hover:shadow-md transition-shadow">
            <CardHeader className="flex flex-row items-center justify-between pb-0">
              <CardTitle className="text-sm font-medium text-muted-foreground">{m.title}</CardTitle>
              <m.icon className={`w-5 h-5 ${m.tone}`} />
            </CardHeader>
            <CardContent>
              <div className="text-3xl font-bold tabular-nums">{m.value}</div>
            </CardContent>
          </Card>
        ))}
      </div>

      {summary.execution_time_seconds !== undefined && (
        <p className="text-sm text-muted-foreground">
          {summary.total_personas ?? 0} personas · completed in {Math.round(summary.execution_time_seconds)}s
        </p>
      )}
    </div>
  );
}
