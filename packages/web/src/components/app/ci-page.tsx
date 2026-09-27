"use client";

import Link from "next/link";
import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import {
  CheckCircle2,
  Download,
  ExternalLink,
  GitBranch,
  KeyRound,
  ListRestart,
  Loader2,
  PlayCircle,
  ShieldAlert,
  XCircle,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Segmented } from "@/components/setup/controls";
import { useSuites } from "@/components/setup/regression-form";
import { engine, EngineError } from "@/lib/engine/client";
import { useRuns } from "@/lib/engine/queries";
import { relativeTime, STATUS_META } from "@/lib/engine/runs";
import type { CIRunRow } from "@/lib/engine/types";
import { useNow } from "./runs-table";
import { CopyButton } from "./test-launcher";

type Target = "github" | "gitlab" | "shell";
type What = "suite" | "setup";

const DOWNLOAD =
  `python -c "import os,httpx;r=httpx.get(os.environ['SIMTEST_ENGINE_URL'].rstrip('/')+'/wizard/ci/client.py',` +
  `headers={'Authorization':'Bearer '+os.environ.get('SIMTEST_API_TOKEN','')});r.raise_for_status();open('simtest_ci.py','w').write(r.text)"`;

interface Plan {
  what: What;
  suite: string;
  gates: { minPass: string; maxCritical: string; allowRegressions: string };
  dashboardUrl: string;
}

function command(p: Plan): string {
  const args = [p.what === "suite" ? `--suite ${p.suite || "SUITE_ID"}` : "--request simtest/run.json"];
  if (p.what === "setup") args.push("--doc-file docs/knowledge-base.md");
  if (p.gates.minPass.trim()) args.push(`--min-pass-rate ${(Number(p.gates.minPass) / 100).toFixed(2)}`);
  if (p.gates.maxCritical.trim()) args.push(`--max-critical ${Number(p.gates.maxCritical)}`);
  if (p.what === "suite" && Number(p.gates.allowRegressions) > 0) args.push(`--allow-regressions ${Number(p.gates.allowRegressions)}`);
  args.push("--junit simtest.xml");
  return `python simtest_ci.py ${args.join(" ")}`;
}

function pipeline(target: Target, p: Plan): string {
  const run = command(p);
  const dash = p.dashboardUrl ? p.dashboardUrl.replace(/\/$/, "") : "";
  if (target === "github") {
    return `name: AI SimTest
on:
  pull_request:
  workflow_dispatch:

jobs:
  simtest:
    runs-on: ubuntu-latest
    steps:
${p.what === "setup" ? "      - uses: actions/checkout@v4\n" : ""}      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
      - name: Run AI SimTest
        env:
          SIMTEST_ENGINE_URL: \${{ secrets.SIMTEST_ENGINE_URL }}
          SIMTEST_API_TOKEN: \${{ secrets.SIMTEST_API_TOKEN }}
          SIMTEST_BOT_ENDPOINT: \${{ vars.SIMTEST_BOT_ENDPOINT }}
          BOT_API_KEY: \${{ secrets.BOT_API_KEY }}${dash ? `\n          SIMTEST_DASHBOARD_URL: ${dash}` : ""}
        run: |
          pip install httpx
          ${DOWNLOAD}
          ${run}
      - name: Keep the JUnit report
        if: always()
        uses: actions/upload-artifact@v4
        with:
          name: simtest-junit
          path: simtest.xml
`;
  }
  if (target === "gitlab") {
    return `simtest:
  image: python:3.12-slim
  variables:
    # Set SIMTEST_ENGINE_URL, SIMTEST_API_TOKEN, SIMTEST_BOT_ENDPOINT and BOT_API_KEY
    # as masked CI/CD variables (Settings → CI/CD → Variables)${dash ? `\n    SIMTEST_DASHBOARD_URL: ${dash}` : ""}
  script:
    - pip install httpx
    - ${DOWNLOAD}
    - ${run}
  artifacts:
    when: always
    reports:
      junit: simtest.xml
  rules:
    - if: $CI_PIPELINE_SOURCE == "merge_request_event"
    - if: $CI_COMMIT_BRANCH == $CI_DEFAULT_BRANCH
`;
  }
  return `#!/usr/bin/env sh
# Any CI: export these from the CI's secret store, never hard-code them
#   SIMTEST_ENGINE_URL  SIMTEST_API_TOKEN  SIMTEST_BOT_ENDPOINT  BOT_API_KEY
set -e${dash ? `\nexport SIMTEST_DASHBOARD_URL=${dash}` : ""}
pip install httpx
${DOWNLOAD}
${run}
# exit 0: every gate held · 1: a gate failed · 2: the run could not finish
`;
}

function Verdict({ row }: { row: CIRunRow }) {
  if (!row.verdict) {
    const meta = STATUS_META[row.status];
    return (
      <span className="inline-flex items-center gap-1 text-xs font-semibold text-muted-foreground">
        {row.status === "running" ? <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden /> : null}
        {meta?.label ?? row.status}
      </span>
    );
  }
  return row.verdict.passed ? (
    <span className="inline-flex items-center gap-1 text-xs font-semibold text-pass">
      <CheckCircle2 className="h-3.5 w-3.5" aria-hidden /> Passed
    </span>
  ) : (
    <span className="inline-flex items-center gap-1 text-xs font-semibold text-fail">
      <XCircle className="h-3.5 w-3.5" aria-hidden /> {row.verdict.exit_code === 2 ? "Did not finish" : "Gate failed"}
    </span>
  );
}

function CIRuns({ rows }: { rows: CIRunRow[] }) {
  const now = useNow();
  if (!rows.length) {
    return <p className="text-sm text-muted-foreground">No CI runs yet. They appear here as pipelines start them.</p>;
  }
  return (
    <div className="overflow-x-auto">
      <table className="w-full min-w-[44rem] text-sm">
        <caption className="sr-only">Runs started by CI pipelines, newest first</caption>
        <thead>
          <tr className="border-b text-left text-xs text-muted-foreground">
            <th scope="col" className="py-2 pr-3 font-medium">Run</th>
            <th scope="col" className="px-3 py-2 font-medium">Build</th>
            <th scope="col" className="px-3 py-2 font-medium">Result</th>
            <th scope="col" className="py-2 pl-3 font-medium">Why</th>
          </tr>
        </thead>
        <tbody className="divide-y">
          {rows.map((r) => (
            <tr key={r.id} className="align-top">
              <td className="py-2 pr-3">
                <Link href={`/simulations/${r.id}`} className="font-medium text-primary hover:underline">
                  {r.name}
                </Link>
                <div className="text-xs text-muted-foreground" title={new Date(r.created_at).toLocaleString()}>
                  {relativeTime(r.created_at, now)}
                </div>
              </td>
              <td className="px-3 py-2 text-xs">
                <div className="flex items-center gap-1 text-foreground">
                  <GitBranch className="h-3.5 w-3.5 text-muted-foreground" aria-hidden />
                  {r.ci.branch || "—"}
                  {r.ci.commit && <span className="font-mono text-muted-foreground"> · {r.ci.commit.slice(0, 7)}</span>}
                </div>
                <div className="mt-0.5 flex flex-wrap gap-x-3">
                  {r.ci.pull_request && /^https?:\/\//.test(r.ci.pull_request) && (
                    <a href={r.ci.pull_request} target="_blank" rel="noreferrer" className="text-primary hover:underline">
                      Pull request <ExternalLink className="inline h-3 w-3" aria-hidden />
                    </a>
                  )}
                  {r.ci.build_url && /^https?:\/\//.test(r.ci.build_url) && (
                    <a href={r.ci.build_url} target="_blank" rel="noreferrer" className="text-primary hover:underline">
                      {r.ci.source === "github" ? "Actions run" : "Pipeline"} <ExternalLink className="inline h-3 w-3" aria-hidden />
                    </a>
                  )}
                </div>
              </td>
              <td className="px-3 py-2">
                <Verdict row={r} />
              </td>
              <td className="py-2 pl-3 text-xs text-muted-foreground">{r.verdict?.headline ?? ""}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function CIPage() {
  const { data, isPending, isError, error } = useQuery({ queryKey: ["ci"], queryFn: engine.getCI, refetchInterval: 15000 });
  const { data: suites } = useSuites();
  const { data: runs } = useRuns();
  const [target, setTarget] = useState<Target>("github");
  const [what, setWhat] = useState<What>("suite");
  const [suite, setSuite] = useState("");
  const [setupRun, setSetupRun] = useState("");
  const [minPass, setMinPass] = useState("");
  const [maxCritical, setMaxCritical] = useState("0");
  const [allowRegressions, setAllowRegressions] = useState("0");
  const [downloadError, setDownloadError] = useState("");
  const [origin] = useState(() => (typeof window === "undefined" ? "" : window.location.origin));

  // A saved setup can come from any finished simulation that is not a replay or a suite replay
  const setupRuns = useMemo(
    () => (runs ?? []).filter((r) => r.status === "completed" && !r.config.replay && !r.config.regression),
    [runs],
  );
  const chosenSuite = suite || suites?.[0]?.id || "";
  const plan: Plan = {
    what,
    suite: chosenSuite,
    gates: { minPass, maxCritical, allowRegressions },
    dashboardUrl: origin,
  };
  const code = pipeline(target, plan);

  const downloadSetup = async () => {
    setDownloadError("");
    const id = setupRun || setupRuns[0]?.simulation_id;
    if (!id) return;
    try {
      const body = await engine.getRunSetup(id);
      const url = URL.createObjectURL(new Blob([JSON.stringify(body, null, 2)], { type: "application/json" }));
      const a = document.createElement("a");
      a.href = url;
      a.download = "run.json";
      a.click();
      URL.revokeObjectURL(url);
    } catch (e) {
      setDownloadError(e instanceof EngineError ? e.message : "Could not download the setup.");
    }
  };

  return (
    <div className="mx-auto w-full max-w-5xl space-y-6 p-4 md:p-8">
      <div>
        <h2 className="text-2xl font-bold tracking-tight text-foreground md:text-3xl">CI pipelines</h2>
        <p className="text-sm text-muted-foreground">
          Gate every pull request on your bot: replay a regression suite (or a saved test) against the build, fail the job
          when something regressed, and see each run here and in the pipeline&apos;s test report.
        </p>
      </div>

      {isError && (
        <p role="alert" className="text-sm font-medium text-destructive">
          {error instanceof Error ? error.message : "Could not load CI information."}
        </p>
      )}
      {isPending ? (
        <div className="flex justify-center py-16">
          <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" aria-label="Loading" />
        </div>
      ) : data ? (
        <>
          {data.token_required ? (
            <p className="flex items-start gap-2 rounded-xl border border-pass/30 bg-pass/5 p-4 text-sm">
              <KeyRound className="mt-0.5 h-4 w-4 shrink-0 text-pass" aria-hidden />
              <span>
                The engine requires an API token on every request. Give pipelines the same token as{" "}
                <code className="font-mono text-xs">SIMTEST_API_TOKEN</code>, stored in the CI&apos;s secret store.
              </span>
            </p>
          ) : (
            <p className="flex items-start gap-2 rounded-xl border border-warn/30 bg-warn/5 p-4 text-sm">
              <ShieldAlert className="mt-0.5 h-4 w-4 shrink-0 text-warn" aria-hidden />
              <span>
                The engine takes requests without a token, so it only listens on this machine. For CI runners to reach it,
                restart it with a token and a reachable address:{" "}
                <code className="whitespace-nowrap font-mono text-xs">
                  SIMTEST_API_TOKEN=$(openssl rand -hex 24) API_HOST=0.0.0.0 simtest serve
                </code>
                , and set the same value as <code className="font-mono text-xs">ENGINE_API_TOKEN</code> for this dashboard.
              </span>
            </p>
          )}

          <section className="space-y-5 rounded-xl border bg-card p-5 shadow-xs">
            <h3 className="text-base font-semibold text-foreground">Set up a pipeline</h3>

            <div className="space-y-2">
              <p className="text-sm font-medium text-foreground">What each build runs</p>
              <Segmented
                label="What each build runs"
                value={what}
                onChange={setWhat}
                options={[
                  { id: "suite", label: "Replay a regression suite" },
                  { id: "setup", label: "Re-run a saved test" },
                ]}
              />
              {what === "suite" ? (
                suites?.length ? (
                  <div className="space-y-1">
                    <select
                      aria-label="Regression suite"
                      className="h-10 w-full max-w-md rounded-md border border-input bg-background px-3 text-sm"
                      value={chosenSuite}
                      onChange={(e) => setSuite(e.target.value)}
                    >
                      {suites.map((s) => (
                        <option key={s.id} value={s.id}>
                          {s.name} ({s.cases} cases)
                        </option>
                      ))}
                    </select>
                    <p className="text-xs text-muted-foreground">
                      Fast and repeatable: the same customer messages every build. Fails on cases that regressed; cases
                      already failing when the suite was saved are reported as known.
                    </p>
                  </div>
                ) : (
                  <p className="flex items-center gap-2 text-sm text-muted-foreground">
                    <ListRestart className="h-4 w-4" aria-hidden /> No suites yet.{" "}
                    <Link href="/runs" className="font-medium text-primary hover:underline">
                      Save one from a run
                    </Link>
                  </p>
                )
              ) : setupRuns.length ? (
                <div className="flex flex-wrap items-end gap-2">
                  <select
                    aria-label="Run to re-run"
                    className="h-10 w-full max-w-md rounded-md border border-input bg-background px-3 text-sm"
                    value={setupRun || setupRuns[0]?.simulation_id}
                    onChange={(e) => setSetupRun(e.target.value)}
                  >
                    {setupRuns.map((r) => (
                      <option key={r.simulation_id} value={r.simulation_id}>
                        {r.name}
                      </option>
                    ))}
                  </select>
                  <Button type="button" variant="outline" onClick={downloadSetup}>
                    <Download /> Download run.json
                  </Button>
                  <p className="w-full text-xs text-muted-foreground">
                    Commit it as <code className="font-mono">simtest/run.json</code>. It has no API keys. Each build
                    generates new customers from your documentation (<code className="font-mono">--doc-file</code> reads
                    it from the repo), so results vary more than a suite&apos;s.
                  </p>
                  {downloadError && (
                    <p role="alert" className="w-full text-sm text-destructive">
                      {downloadError}
                    </p>
                  )}
                </div>
              ) : (
                <p className="text-sm text-muted-foreground">No finished runs to re-run yet.</p>
              )}
            </div>

            <fieldset className="grid gap-3 sm:grid-cols-3">
              <legend className="mb-2 text-sm font-medium text-foreground">Fail the build when</legend>
              <div className="space-y-1">
                <Label htmlFor="ci-pass" className="text-xs text-muted-foreground">
                  Pass rate below (%)
                </Label>
                <Input id="ci-pass" type="number" min={0} max={100} placeholder="not checked" value={minPass} onChange={(e) => setMinPass(e.target.value)} />
              </div>
              <div className="space-y-1">
                <Label htmlFor="ci-crit" className="text-xs text-muted-foreground">
                  Critical failures above
                </Label>
                <Input id="ci-crit" type="number" min={0} placeholder="not checked" value={maxCritical} onChange={(e) => setMaxCritical(e.target.value)} />
              </div>
              {what === "suite" && (
                <div className="space-y-1">
                  <Label htmlFor="ci-reg" className="text-xs text-muted-foreground">
                    Regressed cases above
                  </Label>
                  <Input id="ci-reg" type="number" min={0} value={allowRegressions} onChange={(e) => setAllowRegressions(e.target.value)} />
                </div>
              )}
            </fieldset>

            <div className="space-y-2">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <Segmented
                  label="CI system"
                  value={target}
                  onChange={setTarget}
                  options={[
                    { id: "github", label: "GitHub Actions" },
                    { id: "gitlab", label: "GitLab CI" },
                    { id: "shell", label: "Any CI (shell)" },
                  ]}
                />
                <CopyButton text={code} />
              </div>
              <p className="text-xs text-muted-foreground">
                {target === "github"
                  ? "Save as .github/workflows/simtest.yml."
                  : target === "gitlab"
                    ? "Add to .gitlab-ci.yml; GitLab shows the JUnit report on the merge request."
                    : "Run as a step in Jenkins, CircleCI, Azure Pipelines or any CI."}{" "}
                The pipeline downloads a one-file client from the engine (it needs only httpx), so nothing else is
                installed and it always matches the engine.
              </p>
              <pre className="max-h-[28rem] overflow-auto rounded-lg border bg-muted/40 p-4 font-mono text-xs leading-relaxed text-foreground">
                {code}
              </pre>
            </div>

            <div className="space-y-2 text-sm">
              <p className="font-medium text-foreground">Secrets the pipeline needs</p>
              <ul className="space-y-1 text-muted-foreground">
                <li>
                  <code className="font-mono text-xs text-foreground">SIMTEST_ENGINE_URL</code>: the engine&apos;s address as the
                  runner reaches it (not this dashboard).
                </li>
                <li>
                  <code className="font-mono text-xs text-foreground">SIMTEST_API_TOKEN</code>: the engine&apos;s token.
                </li>
                <li>
                  <code className="font-mono text-xs text-foreground">SIMTEST_BOT_ENDPOINT</code>: the build to test, e.g. the
                  pull request&apos;s preview URL. Blank replays a suite against the bot it was saved from.
                </li>
                <li>
                  <code className="font-mono text-xs text-foreground">BOT_API_KEY</code>: only if your bot needs one.
                </li>
              </ul>
              <p className="flex items-center gap-1.5 text-xs text-muted-foreground">
                <PlayCircle className="h-3.5 w-3.5" aria-hidden /> Exit code 0 when every gate held, 1 when one failed, 2 when
                the run could not finish.
              </p>
            </div>
          </section>

          <section className="space-y-3 rounded-xl border bg-card p-5 shadow-xs">
            <h3 className="text-base font-semibold text-foreground">CI runs</h3>
            <CIRuns rows={data.runs} />
          </section>
        </>
      ) : null}
    </div>
  );
}
