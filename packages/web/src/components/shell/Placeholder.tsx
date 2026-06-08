export function Placeholder({ title, note }: { title: string; note: string }) {
  return (
    <div>
      <h1 className="text-2xl font-semibold tracking-tight">{title}</h1>
      <div className="mt-6 rounded-lg border border-dashed border-border bg-surface p-10 text-center">
        <p className="text-sm text-muted">{note}</p>
      </div>
    </div>
  );
}
