"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useState, useSyncExternalStore, type ReactNode } from "react";
import { Bot, Menu, PanelLeftClose, PanelLeftOpen, Plus } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Sheet, SheetContent, SheetTitle } from "@/components/ui/sheet";
import { useEngineHealth, useRuns } from "@/lib/engine/queries";
import { isActive, needsReview } from "@/lib/engine/runs";
import { NAV, titleFor, type NavItem } from "@/lib/nav";

// Sidebar collapsed state, per browser. A convenience only: unreadable
// storage just means the sidebar starts expanded.
const COLLAPSE_KEY = "simtest.sidebarCollapsed";
const COLLAPSE_EVENT = "simtest-sidebar";

function readCollapsed(): boolean {
  try {
    return localStorage.getItem(COLLAPSE_KEY) === "1";
  } catch {
    return false;
  }
}

function subscribeCollapsed(onChange: () => void) {
  window.addEventListener(COLLAPSE_EVENT, onChange);
  return () => window.removeEventListener(COLLAPSE_EVENT, onChange);
}

function setCollapsed(value: boolean) {
  try {
    localStorage.setItem(COLLAPSE_KEY, value ? "1" : "0");
  } catch {
    /* not persisted */
  }
  window.dispatchEvent(new Event(COLLAPSE_EVENT));
}

function isCurrent(item: NavItem, pathname: string) {
  if (item.href === "/") return pathname === "/";
  if (item.href === "/runs") return pathname.startsWith("/runs") || pathname.startsWith("/simulations");
  return pathname === item.href || pathname.startsWith(item.href + "/");
}

function Brand({ compact }: { compact?: boolean }) {
  return (
    <Link href="/" className="flex items-center gap-2.5" aria-label="AI SimTest home">
      <span className="rounded-lg bg-primary p-1.5">
        <Bot className="h-5 w-5 text-primary-foreground" aria-hidden />
      </span>
      {!compact && <span className="text-lg font-bold tracking-tight text-foreground">AI SimTest</span>}
    </Link>
  );
}

function NavList({ compact, onNavigate }: { compact?: boolean; onNavigate?: () => void }) {
  const pathname = usePathname();
  const { data: runs } = useRuns();
  const active = runs?.filter(isActive).length ?? 0;
  const review = runs?.filter(needsReview).length ?? 0;

  return (
    <nav className="flex-1 space-y-5 overflow-y-auto px-3 py-4" aria-label="Main">
      {NAV.map((group) => (
        <div key={group.heading}>
          {!compact && (
            <p className="px-2 pb-1.5 text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">
              {group.heading}
            </p>
          )}
          <ul className="space-y-0.5">
            {group.items.map((item) => {
              const Icon = item.icon;
              if (item.status === "soon") {
                return (
                  <li key={item.label}>
                    <span
                      className={`flex cursor-default select-none items-center gap-2.5 rounded-lg px-2 py-2 text-sm text-muted-foreground/60 ${compact ? "justify-center" : ""}`}
                      title={`${item.label} — coming soon`}
                    >
                      <Icon className="h-4 w-4 shrink-0" aria-hidden />
                      {!compact && (
                        <>
                          <span className="flex-1 truncate">{item.label}</span>
                          <span className="rounded bg-muted px-1.5 py-0.5 text-[10px] font-medium uppercase tracking-wide text-muted-foreground">
                            Soon
                          </span>
                        </>
                      )}
                      {compact && <span className="sr-only">{item.label} (coming soon)</span>}
                    </span>
                  </li>
                );
              }
              const current = isCurrent(item, pathname);
              const count = item.badge === "runs" ? active : 0;
              return (
                <li key={item.label}>
                  <Link
                    href={item.href}
                    onClick={onNavigate}
                    aria-current={current ? "page" : undefined}
                    title={compact ? item.label : undefined}
                    className={`group flex items-center gap-2.5 rounded-lg px-2 py-2 text-sm transition-colors ${
                      compact ? "justify-center" : ""
                    } ${
                      current
                        ? "bg-primary/10 font-semibold text-primary"
                        : "text-foreground/80 hover:bg-muted hover:text-foreground"
                    }`}
                  >
                    <Icon className="h-4 w-4 shrink-0" aria-hidden />
                    {!compact && <span className="flex-1 truncate">{item.label}</span>}
                    {!compact && count > 0 && (
                      <span
                        className={`rounded-full px-1.5 py-0.5 text-[10px] font-semibold tabular-nums ${review ? "bg-warn/15 text-warn" : "bg-primary/15 text-primary"}`}
                        title={review ? `${review} waiting for your review` : `${count} running`}
                      >
                        {count}
                      </span>
                    )}
                    {compact && count > 0 && <span className="sr-only">({count} active)</span>}
                  </Link>
                </li>
              );
            })}
          </ul>
        </div>
      ))}
    </nav>
  );
}

function EngineStatus({ compact }: { compact?: boolean }) {
  const { isSuccess, isError, isPending } = useEngineHealth();
  const label = isPending ? "Connecting…" : isSuccess ? "Engine connected" : "Engine unreachable";
  const dot = isPending ? "bg-muted-foreground" : isSuccess ? "bg-pass" : "bg-fail";
  return (
    <div
      className={`flex items-center gap-2 text-xs text-muted-foreground ${compact ? "justify-center" : ""}`}
      role="status"
      title={isError ? "Start it with: API_HOST=127.0.0.1 API_PORT=8100 simtest serve" : label}
    >
      <span className={`h-2 w-2 shrink-0 rounded-full ${dot}`} aria-hidden />
      <span className={compact ? "sr-only" : ""}>{label}</span>
    </div>
  );
}

export function AppShell({ children }: { children: ReactNode }) {
  const pathname = usePathname();
  const collapsed = useSyncExternalStore(subscribeCollapsed, readCollapsed, () => false);
  const [mobileOpen, setMobileOpen] = useState(false);

  return (
    <div className="flex h-dvh overflow-hidden bg-background">
      <aside
        className={`hidden shrink-0 flex-col border-r bg-sidebar transition-[width] duration-200 lg:flex ${collapsed ? "w-[68px]" : "w-64"}`}
      >
        <div className={`flex h-16 items-center border-b px-4 ${collapsed ? "justify-center" : ""}`}>
          <Brand compact={collapsed} />
        </div>
        <NavList compact={collapsed} />
        <div className={`space-y-3 border-t p-3 ${collapsed ? "flex flex-col items-center" : ""}`}>
          <EngineStatus compact={collapsed} />
          <Button
            variant="ghost"
            size="sm"
            className={collapsed ? "" : "w-full justify-start"}
            onClick={() => setCollapsed(!collapsed)}
            aria-label={collapsed ? "Expand sidebar" : "Collapse sidebar"}
          >
            {collapsed ? <PanelLeftOpen /> : <PanelLeftClose />}
            {!collapsed && "Collapse"}
          </Button>
        </div>
      </aside>

      <Sheet open={mobileOpen} onOpenChange={setMobileOpen}>
        <SheetContent side="left" className="flex w-72 flex-col p-0">
          <SheetTitle className="sr-only">Navigation</SheetTitle>
          <div className="flex h-16 items-center border-b px-4">
            <Brand />
          </div>
          <NavList onNavigate={() => setMobileOpen(false)} />
          <div className="border-t p-3">
            <EngineStatus />
          </div>
        </SheetContent>
      </Sheet>

      <div className="flex min-w-0 flex-1 flex-col">
        <header className="flex h-16 shrink-0 items-center justify-between gap-3 border-b bg-card px-4 md:px-6">
          <div className="flex min-w-0 items-center gap-2">
            <Button variant="ghost" size="icon" className="lg:hidden" onClick={() => setMobileOpen(true)} aria-label="Open navigation">
              <Menu />
            </Button>
            <span className="lg:hidden">
              <Brand compact />
            </span>
            <h1 className="truncate text-base font-semibold text-foreground">{titleFor(pathname)}</h1>
          </div>
          <div className="flex items-center gap-3">
            <span className="hidden rounded-md bg-muted px-2 py-1 text-xs font-medium text-muted-foreground md:inline">
              QA Automation Workspace
            </span>
            {!pathname.startsWith("/new") && (
              <Button asChild size="sm">
                <Link href="/new">
                  <Plus /> New test
                </Link>
              </Button>
            )}
          </div>
        </header>
        <main className="flex-1 overflow-y-auto">{children}</main>
      </div>
    </div>
  );
}
