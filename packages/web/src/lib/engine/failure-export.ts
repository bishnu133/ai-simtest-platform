import type { ReportResponse } from "./types";

export interface FailedReply {
  run_id: string;
  run_name: string;
  conversation_id: string;
  persona: string;
  persona_type: string;
  reply_number: number;
  customer_message: string;
  bot_reply: string;
  verdict: string;
  score: number | null;
  failed_checks: string;
  main_issue: string;
}

/** Every bot reply judged Failed, with the customer message it answered. */
export function failedReplies(data: ReportResponse): FailedReply[] {
  const rows: FailedReply[] = [];
  for (const jc of data.report.judged_conversations ?? []) {
    const turns = jc.conversation?.turns ?? [];
    const judged = new Map((jc.judged_turns ?? []).map((jt) => [jt.turn?.id, jt]));
    let replyNumber = 0;
    turns.forEach((turn, i) => {
      if ((turn.speaker ?? "").toLowerCase() === "user") return;
      replyNumber += 1;
      const jt = turn.id ? judged.get(turn.id) : undefined;
      if ((jt?.overall_label ?? "").toLowerCase() !== "fail") return;
      const asked = [...turns.slice(0, i)].reverse().find((t) => (t.speaker ?? "").toLowerCase() === "user");
      rows.push({
        run_id: data.simulation_id,
        run_name: data.name,
        conversation_id: jc.conversation?.id ?? "",
        persona: jc.persona?.name ?? "",
        persona_type: jc.persona?.persona_type ?? "",
        reply_number: replyNumber,
        customer_message: asked?.message ?? "",
        bot_reply: turn.message ?? "",
        verdict: "FAIL",
        score: jt?.overall_score ?? null,
        failed_checks: (jt?.judgments ?? [])
          .filter((j) => j.passed === false)
          .map((j) => j.judge_name)
          .filter(Boolean)
          .join("; "),
        main_issue: jt?.issues?.[0] ?? "",
      });
    });
  }
  return rows;
}

const COLUMNS: (keyof FailedReply)[] = [
  "run_id",
  "run_name",
  "conversation_id",
  "persona",
  "persona_type",
  "reply_number",
  "customer_message",
  "bot_reply",
  "verdict",
  "score",
  "failed_checks",
  "main_issue",
];

function csvCell(value: unknown): string {
  let s = value == null ? "" : String(value);
  // A cell a spreadsheet would run as a formula is kept as text
  if (/^[=+\-@\t\r]/.test(s)) s = `'${s}`;
  return /[",\n\r]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
}

export function toCsv(rows: FailedReply[]): string {
  return [COLUMNS.join(","), ...rows.map((r) => COLUMNS.map((c) => csvCell(r[c])).join(","))].join("\r\n") + "\r\n";
}

export function toJsonl(rows: FailedReply[]): string {
  return rows.map((r) => JSON.stringify(r)).join("\n") + (rows.length ? "\n" : "");
}

export function download(filename: string, text: string, type: string) {
  const url = URL.createObjectURL(new Blob([text], { type }));
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
