"use client";

import { useRouter } from "next/navigation";
import { useEffect, useRef, useState, useSyncExternalStore } from "react";
import { useQuery } from "@tanstack/react-query";
import { Dialog as DialogPrimitive } from "radix-ui";
import { Bot, CornerDownLeft, FileText, Monitor, Moon, Search, Sun, type LucideIcon } from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuLabel,
  DropdownMenuRadioGroup,
  DropdownMenuRadioItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { engine } from "@/lib/engine/client";
import { useRuns } from "@/lib/engine/queries";
import { relativeTime } from "@/lib/engine/runs";
import { useNow } from "./runs-table";
import { NAV } from "@/lib/nav";
import { TEST_TYPES } from "@/lib/test-types";
import { readTheme, setTheme, subscribeTheme, type ThemeChoice } from "@/lib/theme";

interface Command {
  id: string;
  group: string;
  label: string;
  hint?: string;
  icon: LucideIcon;
  run: () => void;
}

const isMac = () => typeof navigator !== "undefined" && /Mac|iPhone|iPad/.test(navigator.platform);

/** Ctrl+K / ⌘K: jump to a page, start a test type, open a recent run or a saved bot. */
export function CommandPalette() {
  const router = useRouter();
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [active, setActive] = useState(0);
  const listRef = useRef<HTMLUListElement>(null);
  const { data: runs } = useRuns();
  const now = useNow();
  const { data: bots } = useQuery({ queryKey: ["bots"], queryFn: engine.listBots, retry: false, enabled: open });
  const mac = useSyncExternalStore(
    () => () => {},
    isMac,
    () => false,
  );

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        setOpen((o) => !o);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  const go = (href: string) => () => {
    setOpen(false);
    router.push(href);
  };

  const commands: Command[] = (() => {
    const pages = NAV.flatMap((g) => g.items)
      .filter((i) => i.status === "active" && !i.href.includes("?"))
      .map((i) => ({ id: `page-${i.href}`, group: "Go to", label: i.label, icon: i.icon, run: go(i.href) }));
    const tests = TEST_TYPES.filter((t) => t.available).map((t) => ({
      id: `test-${t.id}`,
      group: "Start a test",
      label: t.name,
      hint: t.tagline,
      icon: t.icon,
      run: go(t.id === "calibration" ? "/calibration" : `/new?type=${t.id}`),
    }));
    const botRows = (bots?.bots ?? []).map((b) => ({
      id: `bot-${b.id}`,
      group: "Test a saved bot",
      label: b.name,
      hint: b.host,
      icon: Bot,
      run: go(`/new?bot=${encodeURIComponent(b.id)}`),
    }));
    const runRows = (runs ?? []).slice(0, 8).map((r) => ({
      id: `run-${r.simulation_id}`,
      group: "Recent runs",
      label: r.name,
      hint: `${relativeTime(r.created_at, now)} · ${r.summary?.pass_rate != null ? `${Math.round(r.summary.pass_rate * 100)}% passed` : r.status}`,
      icon: FileText,
      run: go(`/simulations/${r.simulation_id}`),
    }));
    return [...pages, ...tests, ...botRows, ...runRows];
  })();

  const q = query.trim().toLowerCase();
  const shown = q ? commands.filter((c) => `${c.label} ${c.hint ?? ""} ${c.group}`.toLowerCase().includes(q)) : commands;
  const current = Math.min(active, Math.max(shown.length - 1, 0));

  useEffect(() => {
    listRef.current?.querySelector(`[data-index="${current}"]`)?.scrollIntoView({ block: "nearest" });
  }, [current]);

  const onKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "ArrowDown") {
      e.preventDefault();
      setActive((current + 1) % Math.max(shown.length, 1));
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setActive((current - 1 + shown.length) % Math.max(shown.length, 1));
    } else if (e.key === "Enter") {
      e.preventDefault();
      shown[current]?.run();
    }
  };

  let lastGroup = "";
  return (
    <>
      <button
        type="button"
        onClick={() => setOpen(true)}
        className="hidden h-9 w-64 items-center gap-2 rounded-lg border bg-muted/40 px-3 text-sm text-muted-foreground transition-colors hover:bg-muted md:flex"
      >
        <Search className="h-4 w-4" aria-hidden />
        <span className="flex-1 text-left">Search or jump to…</span>
        <kbd className="rounded border bg-card px-1.5 py-0.5 font-sans text-[10px] font-medium">{mac ? "⌘K" : "Ctrl K"}</kbd>
      </button>
      <Button variant="ghost" size="icon" className="md:hidden" aria-label="Search" onClick={() => setOpen(true)}>
        <Search />
      </Button>
      <DialogPrimitive.Root
        open={open}
        onOpenChange={(o) => {
          setOpen(o);
          if (!o) {
            setQuery("");
            setActive(0);
          }
        }}
      >
        <DialogPrimitive.Portal>
          <DialogPrimitive.Overlay className="fixed inset-0 z-50 bg-black/40 backdrop-blur-[2px] data-[state=open]:animate-in data-[state=open]:fade-in-0" />
          <DialogPrimitive.Content
            className="fixed left-1/2 top-[12vh] z-50 w-[min(36rem,calc(100vw-2rem))] -translate-x-1/2 overflow-hidden rounded-xl border bg-popover text-popover-foreground shadow-2xl data-[state=open]:animate-in data-[state=open]:fade-in-0 data-[state=open]:zoom-in-95"
            onKeyDown={onKeyDown}
          >
            <DialogPrimitive.Title className="sr-only">Search or jump to</DialogPrimitive.Title>
            <DialogPrimitive.Description className="sr-only">
              Type to filter pages, test types, saved bots and recent runs. Arrow keys to move, Enter to open.
            </DialogPrimitive.Description>
            <div className="flex items-center gap-2 border-b px-4">
              <Search className="h-4 w-4 text-muted-foreground" aria-hidden />
              <input
                autoFocus
                value={query}
                onChange={(e) => {
                  setQuery(e.target.value);
                  setActive(0);
                }}
                placeholder="Search pages, tests, bots and runs…"
                className="h-12 flex-1 bg-transparent text-sm outline-none placeholder:text-muted-foreground"
                role="combobox"
                aria-expanded
                aria-controls="palette-list"
                aria-activedescendant={shown[current] ? `cmd-${shown[current].id}` : undefined}
              />
            </div>
            <ul id="palette-list" ref={listRef} role="listbox" className="max-h-[50vh] overflow-y-auto p-2">
              {shown.length === 0 && <li className="px-3 py-8 text-center text-sm text-muted-foreground">Nothing matches “{query}”.</li>}
              {shown.map((c, i) => {
                const header = c.group !== lastGroup ? c.group : null;
                lastGroup = c.group;
                return (
                  <li key={c.id} role="presentation">
                    {header && <p className="px-3 pb-1 pt-3 text-[11px] font-semibold uppercase tracking-wider text-muted-foreground first:pt-1">{header}</p>}
                    <div
                      id={`cmd-${c.id}`}
                      role="option"
                      aria-selected={i === current}
                      data-index={i}
                      onMouseMove={() => setActive(i)}
                      onClick={c.run}
                      className={`flex cursor-pointer items-center gap-3 rounded-lg px-3 py-2 text-sm ${
                        i === current ? "bg-primary/10 text-foreground" : "text-foreground/90"
                      }`}
                    >
                      <c.icon className={`h-4 w-4 shrink-0 ${i === current ? "text-primary" : "text-muted-foreground"}`} aria-hidden />
                      <span className="min-w-0 flex-1 truncate">
                        {c.label}
                        {c.hint && <span className="ml-2 text-xs text-muted-foreground">{c.hint}</span>}
                      </span>
                      {i === current && <CornerDownLeft className="h-3.5 w-3.5 text-muted-foreground" aria-hidden />}
                    </div>
                  </li>
                );
              })}
            </ul>
          </DialogPrimitive.Content>
        </DialogPrimitive.Portal>
      </DialogPrimitive.Root>
    </>
  );
}

const THEME_ICON: Record<ThemeChoice, LucideIcon> = { light: Sun, dark: Moon, system: Monitor };

export function ThemeMenu() {
  const theme = useSyncExternalStore(subscribeTheme, readTheme, () => "system" as ThemeChoice);
  const Icon = THEME_ICON[theme];
  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button variant="ghost" size="icon" aria-label="Theme">
          <Icon />
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" className="w-40">
        <DropdownMenuLabel>Theme</DropdownMenuLabel>
        <DropdownMenuSeparator />
        <DropdownMenuRadioGroup value={theme} onValueChange={(v) => setTheme(v as ThemeChoice)}>
          <DropdownMenuRadioItem value="light">
            <Sun className="h-4 w-4" /> Light
          </DropdownMenuRadioItem>
          <DropdownMenuRadioItem value="dark">
            <Moon className="h-4 w-4" /> Dark
          </DropdownMenuRadioItem>
          <DropdownMenuRadioItem value="system">
            <Monitor className="h-4 w-4" /> System
          </DropdownMenuRadioItem>
        </DropdownMenuRadioGroup>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
