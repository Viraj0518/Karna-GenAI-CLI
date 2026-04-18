import type { NextConfig } from "next";

// Static export for Cloudflare Pages hosting.
// `next build` emits to `out/` when `output: 'export'` is set.
const config: NextConfig = {
  output: "export",
  images: { unoptimized: true },
  trailingSlash: true,
};

export default config;
