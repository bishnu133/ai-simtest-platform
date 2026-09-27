import Link from "next/link";
import { Bell, CheckCircle2, XCircle } from "lucide-react";
import type { NotificationDelivery } from "@/lib/engine/types";

/** Who was told about this run, and whether it reached them. */
export function RunNotifications({ rows }: { rows: NotificationDelivery[] }) {
  const failed = rows.filter((r) => r.status === "failed");
  return (
    <section aria-label="Notifications about this run" className="rounded-xl border bg-card px-5 py-3 text-sm shadow-xs">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
        <Bell className="h-4 w-4 text-primary" aria-hidden />
        <span className="font-medium text-foreground">Notifications</span>
        {rows.map((r, i) => (
          <span key={`${r.channel}-${r.event_type}-${i}`} className="inline-flex items-center gap-1 text-muted-foreground">
            {r.status === "failed" ? (
              <XCircle className="h-3.5 w-3.5 text-fail" aria-label="failed" />
            ) : (
              <CheckCircle2 className="h-3.5 w-3.5 text-pass" aria-label="delivered" />
            )}
            {r.channel || "channel"}: {r.event_type.replace(/_/g, " ")}
          </span>
        ))}
        <Link href="/integrations" className="ml-auto text-xs font-medium text-primary hover:underline">
          Settings
        </Link>
      </div>
      {failed.length > 0 && failed[0].problem && <p className="mt-1 text-xs text-fail">{failed[0].problem}</p>}
    </section>
  );
}
