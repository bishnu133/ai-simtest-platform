import type { NextConfig } from "next";

// Dev only: hostnames besides localhost that may load the dev server's scripts.
// Next.js blocks the rest, and a page opened at e.g. http://127.0.2.2:3000 (the
// "Network" address it prints) then renders but never runs: spinners, and
// "Connecting..." in the sidebar. Every 127.x address is this machine; add
// others (a LAN IP, a tunnel) with DEV_ALLOWED_ORIGINS=host1,host2.
const allowedDevOrigins = [
  "127.*.*.*",
  "[::1]",
  ...(process.env.DEV_ALLOWED_ORIGINS ?? "")
    .split(",")
    .map((h) => h.trim())
    .filter(Boolean),
];

const nextConfig: NextConfig = {
  allowedDevOrigins,
  // Pages replaced by the workspace (home dashboard, runs list, new-test launcher)
  async redirects() {
    return [
      { source: "/overview", destination: "/", permanent: false },
      { source: "/dashboard", destination: "/", permanent: false },
      { source: "/simulations", destination: "/runs", permanent: false },
      { source: "/conversations", destination: "/runs", permanent: false },
      { source: "/comparisons", destination: "/new?type=compare", permanent: false },
    ];
  },
};

export default nextConfig;
