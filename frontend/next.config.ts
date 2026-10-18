import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  cacheComponents: true,
  partialPrefetching: true,
  // Do not generate AGENTS.md during development.
  agentRules: false,
  output: "standalone",
};

export default nextConfig;
