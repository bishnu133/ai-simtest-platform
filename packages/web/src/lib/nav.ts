import {
  BookOpenCheck,
  FileClock,
  GitCompareArrows,
  LayoutDashboard,
  ListChecks,
  ListRestart,
  Plug,
  PlusCircle,
  ScrollText,
  Settings,
  Users,
  type LucideIcon,
} from "lucide-react";

export type NavStatus = "active" | "soon";
export interface NavItem {
  label: string;
  href: string;
  icon: LucideIcon;
  status: NavStatus;
  /** Live count shown beside the item */
  badge?: "runs";
}
export interface NavGroup {
  heading: string;
  items: NavItem[];
}

export const NAV: NavGroup[] = [
  {
    heading: "Workspace",
    items: [
      { label: "Home", href: "/", icon: LayoutDashboard, status: "active" },
      { label: "New test", href: "/new", icon: PlusCircle, status: "active" },
      { label: "Runs", href: "/runs", icon: ListChecks, status: "active", badge: "runs" },
    ],
  },
  {
    heading: "Test library",
    items: [
      { label: "Model comparison", href: "/comparisons", icon: GitCompareArrows, status: "soon" },
      { label: "Regression suites", href: "/suites", icon: ListRestart, status: "soon" },
      { label: "Production replay", href: "/replay", icon: FileClock, status: "soon" },
      { label: "Judge calibration", href: "/calibration", icon: BookOpenCheck, status: "soon" },
    ],
  },
  {
    heading: "Configure",
    items: [
      { label: "Personas", href: "/personas", icon: Users, status: "soon" },
      { label: "Policies & workflows", href: "/policies", icon: ScrollText, status: "soon" },
      { label: "Integrations", href: "/integrations", icon: Plug, status: "soon" },
      { label: "Settings", href: "/settings", icon: Settings, status: "soon" },
    ],
  },
];

/** Page title for the top bar. */
export function titleFor(pathname: string): string {
  if (pathname === "/") return "Home";
  if (pathname.startsWith("/new")) return "New test";
  if (pathname.startsWith("/runs")) return "Runs";
  if (pathname.startsWith("/simulations/")) return "Run";
  return "AI SimTest";
}
