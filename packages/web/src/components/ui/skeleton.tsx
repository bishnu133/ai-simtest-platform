/** Grey placeholder blocks in the page's rough shape, while its data loads. */
export function LoadingBlocks({ variant = "list" }: { variant?: "list" | "dashboard" }) {
  return (
    <div role="status" aria-label="Loading" className="w-full space-y-4 motion-safe:animate-pulse">
      {variant === "dashboard" && (
        <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
          {[0, 1, 2, 3].map((i) => (
            <div key={i} className="h-28 rounded-xl border bg-muted/50" />
          ))}
        </div>
      )}
      <div className="h-10 w-1/3 rounded-lg bg-muted/60" />
      <div className="space-y-2 rounded-xl border bg-card p-4">
        {[0, 1, 2, 3, 4].map((i) => (
          <div key={i} className="flex items-center gap-3">
            <div className="h-8 w-8 shrink-0 rounded-full bg-muted/70" />
            <div className="h-3 flex-1 rounded bg-muted/70" style={{ maxWidth: `${90 - i * 9}%` }} />
            <div className="h-3 w-16 rounded bg-muted/50" />
          </div>
        ))}
      </div>
    </div>
  );
}
