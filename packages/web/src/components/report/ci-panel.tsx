import { CheckCircle2, ExternalLink, GitBranch, XCircle } from "lucide-react";
import type { CITrigger, CIVerdict } from "@/lib/engine/types";
import { Panel } from "./parts";

/** A CI-started run: the gates it was held to, and the build it came from. */
export function CIPanel({ verdict, trigger }: { verdict: CIVerdict; trigger?: CITrigger | null }) {
  const link = (url: string | undefined, label: string) =>
    url && /^https?:\/\//.test(url) ? (
      <a href={url} target="_blank" rel="noreferrer" className="text-primary hover:underline">
        {label} <ExternalLink className="inline h-3 w-3" aria-hidden />
      </a>
    ) : null;
  return (
    <Panel
      title="CI gate"
      description={
        trigger ? (
          <span className="inline-flex flex-wrap items-center gap-x-3 gap-y-1">
            <span className="inline-flex items-center gap-1">
              <GitBranch className="h-3.5 w-3.5" aria-hidden />
              {trigger.branch || "—"}
              {trigger.commit && <span className="font-mono">· {trigger.commit.slice(0, 7)}</span>}
            </span>
            {link(trigger.pull_request, "Pull request")}
            {link(trigger.build_url, trigger.source === "github" ? "Actions run" : "Pipeline")}
          </span>
        ) : undefined
      }
    >
      <p className={`flex items-start gap-2 text-sm font-medium ${verdict.passed ? "text-pass" : "text-fail"}`}>
        {verdict.passed ? (
          <CheckCircle2 className="mt-0.5 h-4 w-4 shrink-0" aria-hidden />
        ) : (
          <XCircle className="mt-0.5 h-4 w-4 shrink-0" aria-hidden />
        )}
        <span>
          {verdict.passed ? "Passed" : verdict.exit_code === 2 ? "Did not finish" : "Gate failed"}
          <span className="font-normal text-muted-foreground"> — {verdict.headline}</span>
        </span>
      </p>
      {verdict.checks.length > 0 && (
        <div className="mt-3 overflow-x-auto">
          <table className="w-full min-w-[28rem] text-sm">
            <caption className="sr-only">Gates this run was held to</caption>
            <thead>
              <tr className="border-b text-left text-xs text-muted-foreground">
                <th scope="col" className="py-1.5 pr-3 font-medium">Gate</th>
                <th scope="col" className="px-3 py-1.5 font-medium">Result</th>
                <th scope="col" className="px-3 py-1.5 text-right font-medium">Actual</th>
                <th scope="col" className="py-1.5 pl-3 text-right font-medium">Limit</th>
              </tr>
            </thead>
            <tbody className="divide-y">
              {verdict.checks.map((c) => (
                <tr key={c.name}>
                  <td className="py-1.5 pr-3 text-foreground">
                    {c.name}
                    {c.detail && <span className="block text-xs text-muted-foreground">{c.detail}</span>}
                  </td>
                  <td className="px-3 py-1.5">
                    {c.passed ? (
                      <span className="inline-flex items-center gap-1 text-xs font-semibold text-pass">
                        <CheckCircle2 className="h-3.5 w-3.5" aria-hidden /> Held
                      </span>
                    ) : (
                      <span className="inline-flex items-center gap-1 text-xs font-semibold text-fail">
                        <XCircle className="h-3.5 w-3.5" aria-hidden /> Failed
                      </span>
                    )}
                  </td>
                  <td className="px-3 py-1.5 text-right tabular-nums">{c.actual}</td>
                  <td className="py-1.5 pl-3 text-right tabular-nums text-muted-foreground">{c.limit}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Panel>
  );
}
