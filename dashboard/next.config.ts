import type { NextConfig } from "next";

const config: NextConfig = {
  reactStrictMode: true,
  poweredByHeader: false,
  devIndicators: false,
  output: "standalone",
  // The Slack avatar PNG is read at runtime by app/scout-icon.png/route.ts.
  outputFileTracingIncludes: { "/scout-icon.png": ["./assets/scout-icon-512.png"] },
};
export default config;
