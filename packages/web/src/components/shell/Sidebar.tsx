"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { NAV } from "@/lib/nav";

export function Sidebar() {
  const pathname = usePathname();
  return (
    <aside className="flex w-64 shrink-0 flex-col border-r border-border bg-surface">
      <div className="flex h-14 items-center gap-2 border-b border-border px-5">
        <span className="flex h-7 w-7 items-center justify-center rounded-md bg-ink font-mono text-xs font-semibold text-surface">ST</span>
        <span className="text-sm font-semibold tracking-tight">SimTest</span>
      </div>
      <nav className="flex-1 space-y-6 overflow-y-auto px-3 py-4">
        {NAV.map((group) => (
          <div key={group.heading}>
            <p className="px-2 pb-1.5 text-[11px] font-medium uppercase tracking-wider text-muted">{group.heading}</p>
            <ul className="space-y-0.5">
              {group.items.map((item) => {
                if (item.status === "soon") {
                  return (
                    <li key={item.label}>
                      <span className="flex cursor-default select-none items-center justify-between rounded-md px-2 py-1.5 text-sm text-muted/55">
                        {item.label}
                        <span className="text-[10px] uppercase tracking-wide text-muted/45">soon</span>
                      </span>
                    </li>
                  );
                }
                const active = pathname === item.href || pathname.startsWith(item.href + "/");
                return (
                  <li key={item.label}>
                    <Link
                      href={item.href}
                      className={`block rounded-md px-2 py-1.5 text-sm transition-colors ${active ? "bg-ink font-medium text-surface" : "text-ink/80 hover:bg-bg"}`}
                    >
                      {item.label}
                    </Link>
                  </li>
                );
              })}
            </ul>
          </div>
        ))}
      </nav>
    </aside>
  );
}
