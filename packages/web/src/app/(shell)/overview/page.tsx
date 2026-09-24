const STEPS = [
  { n: 1, title: "Connect your bot", body: "Add an endpoint and authentication, or pick a connected integration." },
  { n: 2, title: "Choose personas, judges & policies", body: "Start from smart defaults tuned to your bot's domain — adjust anytime." },
  { n: 3, title: "Run your first simulation", body: "Launch a guided run and watch conversations evaluate live." },
];

export default function OverviewPage() {
  return (
    <div className="mx-auto max-w-3xl">
      <p className="text-xs font-medium uppercase tracking-wider text-primary">Get started</p>
      <h1 className="mt-1 text-2xl font-semibold tracking-tight">Test your AI before your users do.</h1>
      <p className="mt-2 text-sm text-muted-foreground">
        Run realistic, persona-driven conversations against your bot, evaluate every response, and catch failures before production.
      </p>
      <ol className="mt-8 space-y-3">
        {STEPS.map((s) => (
          <li key={s.n} className="flex gap-4 rounded-lg border border-border bg-card p-4">
            <span className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-background font-mono text-sm text-muted-foreground">{s.n}</span>
            <div>
              <h2 className="text-sm font-medium">{s.title}</h2>
              <p className="mt-0.5 text-sm text-muted-foreground">{s.body}</p>
            </div>
          </li>
        ))}
      </ol>
    </div>
  );
}
