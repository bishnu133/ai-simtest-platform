import {
  Bot,
  BookOpenCheck,
  FileClock,
  GitCompareArrows,
  GitPullRequest,
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
      { label: "Bots", href: "/bots", icon: Bot, status: "active" },
    ],
  },
  {
    heading: "Test library",
    items: [
      { label: "Regression suites", href: "/suites", icon: ListRestart, status: "active" },
      { label: "Model comparison", href: "/new?type=compare", icon: GitCompareArrows, status: "active" },
      { label: "Production replay", href: "/new?type=replay", icon: FileClock, status: "active" },
      { label: "Judge calibration", href: "/calibration", icon: BookOpenCheck, status: "active" },
    ],
  },
  {
    heading: "Configure",
    items: [
      { label: "Personas", href: "/personas", icon: Users, status: "active" },
      { label: "Policies & workflows", href: "/policies", icon: ScrollText, status: "soon" },
      { label: "Integrations", href: "/integrations", icon: Plug, status: "active" },
      { label: "CI pipelines", href: "/ci", icon: GitPullRequest, status: "active" },
      { label: "Settings", href: "/settings", icon: Settings, status: "active" },
    ],
  },
];

/** Page title for the top bar. */
export function titleFor(pathname: string): string {
  if (pathname === "/") return "Home";
  if (pathname.startsWith("/new")) return "New test";
  if (pathname.startsWith("/runs/compare")) return "Compare runs";
  if (pathname.startsWith("/runs")) return "Runs";
  if (pathname.startsWith("/suites")) return "Regression suites";
  if (pathname.startsWith("/calibration")) return "Judge calibration";
  if (pathname.startsWith("/integrations")) return "Integrations";
  if (pathname === "/ci" || pathname.startsWith("/ci/")) return "CI pipelines";
  if (pathname.startsWith("/settings")) return "Settings";
  if (pathname.startsWith("/bots")) return "Bots";
  if (pathname.startsWith("/personas")) return "Personas";
  if (pathname.startsWith("/simulations/")) return "Run";
  return "AI SimTest";
}
