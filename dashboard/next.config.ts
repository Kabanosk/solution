import type { NextConfig } from "next";

const config: NextConfig = {
  reactStrictMode: true,
  poweredByHeader: false,
  devIndicators: false,
  output: "standalone",
  // These public brand assets are read at runtime by their route handlers.
  outputFileTracingIncludes: { "/scout-icon.png": ["./assets/scout-icon-512.png"], "/google-g.png": ["./assets/google-g.png"] },
};
export default config;
