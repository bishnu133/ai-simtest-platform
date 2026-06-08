export type NavStatus = "active" | "soon";
export interface NavItem { label: string; href: string; status: NavStatus; }
export interface NavGroup { heading: string; items: NavItem[]; }

export const NAV: NavGroup[] = [
  { heading: "Overview", items: [
    { label: "Get started", href: "/overview", status: "active" },
    { label: "Dashboard", href: "/dashboard", status: "active" },
  ]},
  { heading: "Evaluation", items: [
    { label: "Simulations", href: "/simulations", status: "soon" },
    { label: "Conversations", href: "/conversations", status: "active" },
    { label: "Personas", href: "/personas", status: "soon" },
    { label: "Judges & Metrics", href: "/judges", status: "soon" },
    { label: "Policies", href: "/policies", status: "soon" },
  ]},
  { heading: "Output", items: [
    { label: "Comparisons", href: "/comparisons", status: "active" },
    { label: "Reports", href: "/reports", status: "soon" },
    { label: "Datasets & Exports", href: "/datasets", status: "soon" },
  ]},
  { heading: "Setup", items: [
    { label: "Integrations", href: "/integrations", status: "soon" },
    { label: "Settings", href: "/settings", status: "soon" },
  ]},
];
