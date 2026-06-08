export function Topbar() {
  // env badge + workspace are hardcoded for dev; B.0c wires these from the auth/proxy layer.
  return (
    <header className="flex h-14 shrink-0 items-center justify-between border-b border-border bg-surface px-6">
      <div className="flex items-center gap-2 text-sm">
        <span className="font-medium">Default Workspace</span>
        <span className="text-muted">/</span>
        <span className="rounded-full border border-border px-2 py-0.5 text-xs text-muted">development</span>
      </div>
      <span className="flex items-center gap-1.5 text-xs text-muted">
        <span className="h-1.5 w-1.5 rounded-full bg-pass" />
        dev-auth · dev_actor_1
      </span>
    </header>
  );
}
