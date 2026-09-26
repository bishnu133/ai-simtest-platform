import type { NextConfig } from "next";

const nextConfig: NextConfig = {
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
