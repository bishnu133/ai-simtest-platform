"use client";

import { useCallback, useEffect, useState } from "react";
import { Check, Loader2, X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { EngineError, engine } from "@/lib/engine/client";
import { judgeLabel, pct } from "@/lib/engine/report";
import type { ReviewItem, ReviewResponse, ReviewSummary } from "@/lib/engine/types";
import { EmptyNote, Panel, StatTile } from "./parts";

const score = (v: number | null | undefined) => (v === null || v === undefined ? "—" : v.toFixed(2));

function SummaryPanel({ summary }: { summary: ReviewSummary }) {
  const boundary = summary.judge_boundary;
  return (
    <div className="space-y-3">
      <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
        <StatTile label="Labelled" value={summary.labelled} sub="replies you reviewed" />
        <StatTile
          label="Agreement"
          value={pct(summary.agreement_rate)}
          sub={summary.labelled ? `${summary.agree} of ${summary.labelled} match the judge` : "label replies below"}
        />
        <StatTile label="Judge too strict" value={summary.judge_too_strict} sub="failed, you would pass" />
        <StatTile label="Judge too lenient" value={summary.judge_too_lenient} sub="passed, you would fail" />
      </div>
      <p className="rounded-lg border-l-2 border-primary bg-muted/30 px-3 py-2 text-sm text-foreground">{summary.verdict}</p>
      <p className="text-xs text-muted-foreground">
        On this run the judge&apos;s lowest passing score was {score(boundary.lowest_passing_score)} and its highest
        failing score was {score(boundary.highest_failing_score)}.
        {summary.suggested_threshold &&
          ` A pass mark of ${summary.suggested_threshold.threshold.toFixed(2)} would agree with ${pct(
            summary.suggested_threshold.agreement,
          )} of your labels. This is based only on the replies you labelled, so label more before changing the rubric.`}
      </p>
    </div>
  );
}

function ItemCard({
  item,
  saving,
  onLabel,
}: {
  item: ReviewItem;
  saving: boolean;
  onLabel: (value: boolean | null) => void;
}) {
  const agrees = item.human_pass === null ? null : item.human_pass === item.judge_passed;
  return (
    <li className="rounded-lg border bg-card p-4">
      <div className="mb-3 flex flex-wrap items-center justify-between gap-2 text-xs text-muted-foreground">
        <span>
          {item.persona || item.conversation_id} · reply {item.turn_index + 1}
        </span>
        {agrees !== null && (
          <span className={agrees ? "font-medium text-pass" : "font-medium text-warn"}>
            {agrees ? "You agree with the judge" : "You disagree with the judge"}
          </span>
        )}
      </div>
      <div className="space-y-2 text-sm">
        {item.user_message && (
          <p className="text-muted-foreground">
            <span className="font-medium text-foreground">User:</span> {item.user_message}
          </p>
        )}
        <p className="whitespace-pre-line text-foreground">
          <span className="font-medium">Bot:</span> {item.bot_reply}
        </p>
      </div>
      <div className="mt-3 rounded-md bg-muted/40 p-3 text-sm">
        <span className={`font-semibold ${item.judge_passed ? "text-pass" : "text-fail"}`}>
          Judge: {item.judge_passed ? "Pass" : "Fail"} · {item.judge_score.toFixed(2)}
        </span>
        {item.judge_message && <p className="mt-1 text-muted-foreground">{item.judge_message}</p>}
      </div>
      <div className="mt-3 flex items-center gap-2" role="group" aria-label="Your verdict">
        <span className="mr-1 text-xs text-muted-foreground">Your verdict:</span>
        <Button
          size="sm"
          variant={item.human_pass === true ? "default" : "outline"}
          aria-pressed={item.human_pass === true}
          disabled={saving}
          onClick={() => onLabel(item.human_pass === true ? null : true)}
        >
          <Check /> Pass
        </Button>
        <Button
          size="sm"
          variant={item.human_pass === false ? "destructive" : "outline"}
          aria-pressed={item.human_pass === false}
          disabled={saving}
          onClick={() => onLabel(item.human_pass === false ? null : false)}
        >
          <X /> Fail
        </Button>
        {saving && <Loader2 className="h-4 w-4 animate-spin text-muted-foreground" aria-label="Saving" />}
      </div>
    </li>
  );
}

/** Label a sample of judged replies to see whether a judge agrees with a person. */
export function ReviewTab({ simulationId, onLabelled }: { simulationId: string; onLabelled?: (judge: string) => void }) {
  const [judge, setJudge] = useState("quality");
  const [data, setData] = useState<ReviewResponse | null>(null);
  const [error, setError] = useState("");
  const [saving, setSaving] = useState<string | null>(null);

  const fetchReview = useCallback(
    (name: string) =>
      engine
        .getReview(simulationId, name)
        .then((res) => {
          setError("");
          setData(res);
          setJudge(res.judge);
        })
        .catch((e) => setError(e instanceof EngineError ? e.message : "Could not load the review sample.")),
    [simulationId],
  );

  useEffect(() => {
    fetchReview("quality");
  }, [fetchReview]);

  const load = (name: string) => {
    setError("");
    fetchReview(name);
  };

  const label = async (item: ReviewItem, value: boolean | null) => {
    setSaving(item.key);
    try {
      const summary = await engine.postReview(simulationId, { judge, key: item.key, human_pass: value });
      onLabelled?.(judge);
      setData((d) =>
        d && {
          ...d,
          summary,
          items: d.items.map((i) => (i.key === item.key ? { ...i, human_pass: value } : i)),
        },
      );
    } catch (e) {
      setError(e instanceof EngineError ? e.message : "Could not save the label.");
    } finally {
      setSaving(null);
    }
  };

  return (
    <div className="space-y-6">
      <Panel
        title="Review a judge"
        description="Mark a sample of replies as pass or fail yourself. The sample is mostly the judge's failures plus some passes, and stays the same for this run. Labels are saved with the run and exported as a golden set."
        action={
          data && data.judges.length > 1 ? (
            <div className="flex flex-wrap gap-1" role="group" aria-label="Judge">
              {data.judges.map((j) => (
                <Button
                  key={j}
                  size="xs"
                  variant={judge === j ? "default" : "outline"}
                  aria-pressed={judge === j}
                  onClick={() => load(j)}
                >
                  {judgeLabel(j)}
                </Button>
              ))}
            </div>
          ) : undefined
        }
      >
        {error && (
          <p role="alert" className="mb-3 text-sm text-destructive">
            {error}
          </p>
        )}
        {data ? <SummaryPanel summary={data.summary} /> : !error && <EmptyNote>Loading the sample…</EmptyNote>}
      </Panel>

      {data &&
        (data.items.length ? (
          <ul className="space-y-3">
            {data.items.map((item) => (
              <ItemCard key={item.key} item={item} saving={saving === item.key} onLabel={(v) => label(item, v)} />
            ))}
          </ul>
        ) : (
          <EmptyNote>The {judgeLabel(judge)} judge has no replies to review in this run.</EmptyNote>
        ))}
    </div>
  );
}
